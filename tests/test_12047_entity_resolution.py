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

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.entity_identity import AmbiguousEntityName, resolve_entity_id
from tortoise.entity_resolver import (
    EntityScanUnavailable,
    apply_entity_merge,
    block_key,
    entity_candidates,
    entity_pair_for,
    find_live_holder,
    follow_resolved_from,
    normalize_entity_name,
    resolve_entity_name,
    similarity,
)
from tortoise.exceptions import NameAlreadyHeld
from tortoise.sdk import _existing_entity_ids

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

    def test_the_lexical_length_prune_does_not_narrow_the_semantic_pool(
            self, sdk_factory):
        """round-3 P2: the fuzzy rung's length prune ran BEFORE the vector lookup.

        Its bound is lexical (difflib's ratio is <= 2*min/(sum)), so using it to
        skip the SEMANTIC lookup dropped pairs the vectors rank — a pair inside
        one block, scored 1.0 by the encoder, never looked at. That is an
        undeclared bound and the same under-report this module exists to remove,
        one rung further out; production pipelines keep per-rung candidate pools
        for exactly this reason.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "abc corp", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-b",
                    "abc corporation international holdings",
                    "subjectKind", "other")
        g = sdk._get_proj().g
        left = normalize_entity_name("abc corp")
        right = normalize_entity_name("abc corporation international holdings")
        # Preconditions, asserted so the pin cannot go vacuous: ONE block (else
        # no pair is ever considered), the fuzzy rung misses it, and the very
        # length ratio the prune uses WOULD have dropped the pair.
        assert block_key(left) == block_key(right) == "abc"
        assert similarity(left, right) < 0.60, "the FUZZY rung must miss this pair"
        assert 2 * min(len(left), len(right)) < 0.60 * (len(left) + len(right)), (
            "this pair must fall under the pre-fix length prune")

        def _identical_vectors(texts):
            return ([[1.0, 0.0, 0.0] for _ in texts], True)

        rows = entity_candidates(g, encode_fn=_identical_vectors)
        assert [r["method"] for r in rows] == ["semantic"], (
            f"the lexical prune narrowed the semantic pool: {rows}")
        assert rows[0]["semantic_similarity"] == pytest.approx(1.0)
        assert rows[0]["auto_mergeable"] is False


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

    def test_a_reject_verdict_survives_a_full_wipe_and_replay(self, tmp_path):
        """`derived == replay(journal)` for the SET-VALUED reject verdict (F1).

        The verdict became a LIST (`dedup_rejected_targets`), and that list is NEW
        state: if it existed only on the node, a rebuild would drop every
        rejection and the pairs the reviewer had answered would come back as
        fresh candidates — the queue reporting a decision it no longer holds.
        """
        from tortoise.sdk import TortoiseSDK

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            sdk.create_subject("plate one", "other")
            sdk.create_subject("plate wun", "other")
            row = sdk.list_dedup_candidates(candidate_type="entity")[0]
            sdk.approve_merge(row["candidate_id"], action="reject")
            g = sdk._get_proj().g
            q = ("MATCH (s:Subject) RETURN s.id, s.dedup_reviewed, "
                 "s.dedup_rejected_targets ORDER BY s.id")
            before = [list(r) for r in g.query(q).result_set]
            assert any(r[1] == "reject" for r in before), (
                "the reject wrote no verdict — nothing to replay")
            assert any(r[2] for r in before), (
                "the reject wrote no pair list — nothing to replay")

            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            after = [list(r) for r in g.query(q).result_set]
            assert after == before, (
                "the reject verdict did not replay: dedup_rejected_targets is "
                "live-only state and a rebuild drops the answered pairs")
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
        # Distinct role name: a role whose fold collides with the holder's would
        # resolve ONTO the holder — a self edge, refused by `wire_holds_role` (N7).
        sdk.create_subject("the operator role", "role", roleHolder=person["id"])
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


class TestSecondReviewRoundBounded:
    """Pins for the SECOND review round measured on this branch's own head.

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
        # #12047 review F1: the re-offered row above is only useful if it is
        # ACTIONABLE. `_pair_reviewed` suppresses the reviewed COMBINATION, so
        # the queue legitimately re-offers the candidate against a DIFFERENT
        # survivor — and the merge's flip-refusal used to be CANDIDATE-scoped,
        # so this exact state left the row permanently displayed and permanently
        # un-actionable (reject answered `already_reviewed`, merge raised).
        merged = sdk.approve_merge("sub-c", action="merge")
        assert merged["survived_id"] == "sub-b", merged
        folded = sdk._get_proj().g.query(
            "MATCH (s:Subject {id:'sub-c'}) RETURN s.resolved_from"
        ).result_set[0][0]
        assert folded == "sub-b", (
            f"the merge folded into {folded!r}, not the survivor the queue showed")

    def test_the_ingest_guard_asks_the_writers_question(self, sdk_factory):
        """P1: the Phase-1 probe matched raw ``name``; the writer matches the union
        (#12047 review round 9).

        A fold-only holder was invisible to the guard, so a bundle passed Phase 1
        and the write died in step 4 with the earlier items already committed —
        the partial-mutation class that guard exists to prevent. It also refused
        legitimate bundles after an exact-twin merge.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "Acme", "subjectKind", "other",
                    ts="2026-01-01", canonical_name="acme")
        _create_raw(sdk, "Subject", "sub-b", "The Acme Holdings", "subjectKind",
                    "other", ts="2026-01-02", canonical_name="acme")
        with pytest.raises(AmbiguousEntityName):
            sdk.create_subject("Acme")
        exists, _t, _op, _st, count, merges, _tid = sdk._entity_name_holders(
            "Subject", "Acme")
        assert exists and count == 2 and not merges, (
            f"the Phase-1 guard disagrees with the write: count={count} "
            f"merges={merges}")

    def test_a_fold_only_match_is_not_reported_as_created(self, sdk_factory):
        """P2: `_existing_entity_ids` probed raw `name` only (#12047 round 9).

        A fold-only match reported no holder, so `created.entities` counted 1 on
        every re-ingest forever — the #9964 review P2 defect its own docstring
        says it fixes.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "The Acme", "subjectKind",
                    "other", ts="2026-01-01", canonical_name="acme")
        got = _existing_entity_ids(sdk._get_proj().g, "subject", {}, "acme")
        assert [str(x) for x in got if x] == ["sub-a"], got

    def test_a_string_aliases_row_does_not_disable_entity_writes(self, sdk_factory):
        """P2: the new `aliases` arm required a List, and `aliases` is writable as
        a bare string (#12047 review round 9).

        One such row made EVERY entity write raise a driver type error — a public
        write that disables the whole write path. The arm is type-guarded now, so
        the string simply does not participate.
        """
        sdk = sdk_factory()
        sdk.create_subject("Foo", "other", aliases="a bare string")
        made = sdk.create_subject("Bar", "other")
        assert made["id"]
        g = sdk._get_proj().g
        assert find_live_holder(g, "Subject", normalize_entity_name("Foo"))

    def test_a_merge_does_not_resolve_onto_a_retired_survivor(self, sdk_factory):
        """P2: the collapse could hand back a TERMINAL survivor (#12047 round 9).

        A merged-away carrier stays live, so once the SURVIVOR is retired the name
        resolved onto the buried incarnation — `resolve_entity_id` returned a
        `superseded` id against its own D2 rule, and a create MERGEd onto it
        instead of minting the new incarnation R5 requires.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "Bob Smyth", "subjectKind",
                    "naturalPerson", ts="2026-01-01")
        _create_raw(sdk, "Subject", "sub-b", "Bob Smith", "subjectKind",
                    "naturalPerson", ts="2026-01-02")
        sdk.approve_merge("sub-b", action="merge")
        sdk.update_entity("sub-a", status="superseded")
        g = sdk._get_proj().g
        assert resolve_entity_id(
            g, "Subject", "Bob Smith",
            name_props=("name", "canonical_name")) is None, (
            "a retired survivor was handed back as a live holder")
        minted = sdk.create_subject("Bob Smith")
        assert minted["id"] != "sub-a", (
            "the create MERGEd onto the retired incarnation instead of minting")
        assert minted.get("status") != "superseded"

    def test_rejecting_the_re_offered_pair_records_that_pair(self, sdk_factory):
        """P2: the reject early return was CANDIDATE-scoped (#12047 review F1).

        `_pair_reviewed` suppresses one COMBINATION, so after rejecting (c,a) the
        queue re-offers (c,b). The `reviewed == action` short-circuit answered
        that fresh row `already_reviewed` about a pair nobody had reviewed and
        left it queued forever. The verdict is set-valued now, so both pairs stay
        suppressed without the second rejection un-suppressing the first.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "aaa one", "subjectKind", "other",
                    ts="2026-01-01")
        _create_raw(sdk, "Subject", "sub-b", "aaa wun", "subjectKind", "other",
                    ts="2026-01-02")
        _create_raw(sdk, "Subject", "sub-c", "aaa one", "subjectKind", "other",
                    ts="2026-01-03", dedup_reviewed="reject",
                    dedup_target_id="sub-a")
        pairs = {(r["candidate_id"], r["existing_id"]) for r in
                 sdk.list_dedup_candidates(candidate_type="entity")}
        assert ("sub-c", "sub-b") in pairs, pairs
        verdict = sdk.approve_merge("sub-c", action="reject")
        assert not verdict.get("already_reviewed"), (
            f"a pair nobody had reviewed answered 'already reviewed': {verdict}")
        assert verdict.get("target_id") == "sub-b", (
            f"the reject did not name the pair it decided: {verdict}")
        after = {(r["candidate_id"], r["existing_id"]) for r in
                 sdk.list_dedup_candidates(candidate_type="entity")}
        assert ("sub-c", "sub-b") not in after, (
            f"the pair just rejected is still offered: {after}")
        assert ("sub-c", "sub-a") not in after, (
            "recording the second rejection un-suppressed the first one")
        again = sdk.approve_merge("sub-c", action="reject")
        assert again.get("already_reviewed"), (
            f"re-rejecting with no pair left is not idempotent: {again}")

    def test_a_merged_name_resolves_on_the_read_path_too(self, sdk_factory):
        """P2: `resolve_entity_id` counted merged-away carriers (#12047 review F2).

        The merge is non-destructive, so both nodes keep matching the raw name.
        The CREATE path collapsed them (round-4 finding 1) and the READ path did
        not, so after merging an exact twin every by-name read — including the
        `roleHolder` verb this PR adds — refused the name the merge had just
        unified as ambiguous.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "Acme Owner", "subjectKind",
                    "naturalPerson", ts="2026-01-01")
        _create_raw(sdk, "Subject", "sub-b", "Acme Owner", "subjectKind",
                    "naturalPerson", ts="2026-01-02")
        row = next(r for r in sdk.list_dedup_candidates(candidate_type="entity")
                   if r["candidate_id"] == "sub-b")
        assert row["existing_id"] == "sub-a", row
        sdk.approve_merge("sub-b", action="merge")
        g = sdk._get_proj().g
        assert resolve_entity_id(
            g, "Subject", "Acme Owner",
            name_props=("name", "canonical_name")) == "sub-a", (
            "a name the merge just unified still answers 'ambiguous' on the read path")
        role = sdk.create_subject("owner role", "role", roleHolder="Acme Owner")
        assert role["id"]
        wired = g.query(
            "MATCH (:Subject {id:'sub-a'})-[r:holdsRole]->(:Subject) "
            "RETURN count(r)").result_set[0][0]
        assert wired >= 1, (
            "roleHolder did not resolve the merged name onto its survivor")

    def test_a_VARIANT_merge_resolves_on_the_read_path_too(self, sdk_factory):
        """P2: the collapse was gated on `len(by_name) > 1` (#12047 round 8).

        An exact-twin merge leaves BOTH nodes matching the raw name, so the gate
        fired and the read collapsed. A VARIANT merge (the fuzzy band — most of
        the queue) leaves the retired carrier as the only row matching its own
        spelling: `len == 1`, the gate never fired, and the read returned the
        CARRIER while every write hopped to the survivor. An approval filed under
        the merged-away spelling then attached to the retired node.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "Bob Smyth", "subjectKind",
                    "naturalPerson", ts="2026-01-01")
        _create_raw(sdk, "Subject", "sub-b", "Bob Smith", "subjectKind",
                    "naturalPerson", ts="2026-01-02")
        row = next(r for r in sdk.list_dedup_candidates(candidate_type="entity")
                   if r["candidate_id"] == "sub-b")
        assert row["existing_id"] == "sub-a", row
        sdk.approve_merge("sub-b", action="merge")
        g = sdk._get_proj().g
        assert resolve_entity_id(
            g, "Subject", "Bob Smith",
            name_props=("name", "canonical_name")) == "sub-a", (
            "the read path resolved the merged-away spelling onto the retired carrier")
        assert sdk.create_subject("Bob Smith")["id"] == "sub-a", (
            "the write path and the read path disagree about a merged spelling")

    def test_the_fold_resolver_also_collapses_a_merged_carrier(self, sdk_factory):
        """P3: `resolve_fold_name` was a THIRD uncollapsed by-name resolver (round 8).

        For an exact twin it saw TWO holders and took the NON-EXEMPT
        `ambiguous-entity-name` shape, which fails `rebuild_all` — so the queue's
        own repair made the journal un-rebuildable.
        """
        from tortoise.entity_identity import resolve_fold_name

        sdk = sdk_factory()
        _create_raw(sdk, "Object", "obj-a", "Acme Owner", "objectKind",
                    "organization", ts="2026-01-01")
        _create_raw(sdk, "Object", "obj-b", "Acme Owner", "objectKind",
                    "organization", ts="2026-01-02")
        row = next(r for r in sdk.list_dedup_candidates(candidate_type="entity")
                   if r["candidate_id"] == "obj-b")
        assert row["existing_id"] == "obj-a", row
        sdk.approve_merge("obj-b", action="merge")
        g = sdk._get_proj().g
        assert resolve_fold_name(g, "Object", "Acme Owner") == "obj-a", (
            "the fold resolver still counts the merged-away carrier")
        assert resolve_fold_name(g, "Object", "Acme Owner", mention=True) == "obj-a", (
            "a mention of a merged name must fold onto the survivor too")

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
        # ⛔ `create_entity` directly, NOT the `create_subject` alias (#12047
        # review F5): the alias normalises its `subjectKind` PARAMETER
        # (`subjectKind = named_kind(subjectKind)`) before the prop ever reaches
        # the door, so the gate was only ever exercised with an
        # already-normalised value and the branch this test exists for went
        # unverified.
        created = sdk.create_entity("subject", "owner", subjectKind=" role ",
                                    roleHolder="Daniel")
        # `create_entity` returns `{"node": …}`; the legacy aliases unwrap it via
        # `_flat_entity_result`. This test deliberately uses the RAW door (the
        # point of it), so it unwraps explicitly.
        assert created["node"]["id"]
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

    def test_the_docs_miner_does_not_hand_a_raw_canonical_name(self):
        """P0: a raw name in `canonical_name` is a second key space in one property.

        `EventAPI.add_object` supplies the FOLD itself and only `setdefault`s a
        caller's value, so this door passing the raw surface form WON over the
        fold: a docs-mined Object named "The Owner" carried
        `canonical_name="The Owner"`, the resolver's folded probe missed it, and
        the next write minted the twin #12047 exists to prevent. A source-level
        pin, because the alternative is driving the whole mining pipeline to
        observe one keyword argument.
        """
        import ast as _ast
        import inspect as _inspect

        import tortoise.docs_miner as _docs_miner

        offenders = []
        for node in _ast.walk(_ast.parse(_inspect.getsource(_docs_miner))):
            if not isinstance(node, _ast.Call):
                continue
            func = node.func
            if not (isinstance(func, _ast.Attribute) and func.attr == "add_object"):
                continue
            offenders += [_ast.unparse(kw.value) for kw in node.keywords
                          if kw.arg == "canonical_name"]
        assert not offenders, (
            f"the docs miner passes canonical_name={offenders} — pass nothing "
            f"and let the ONE writer (`EventAPI.add_object`) fold it")

    def test_the_create_funnel_fills_the_folded_key(self, sdk_factory):
        """P1: two doors reached the writer without the door helper, so they wrote
        no folded key at all and the deterministic rung could not see their nodes."""
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
class TestSecondReviewRound:
    """Regression pins for the second review round (all were measured defects)."""

    # ── N1: one folded identity, TWO carriers, EVERY spelling refuses ────

    def _two_folded_carriers(self, sdk):
        """Two LIVE carriers of ONE folded identity: `an owner` (canonical_name
        `owner`) and the raw node `owner` (name only) — the pre-#3590 shape."""
        sdk.create_subject("an owner", "role")          # stamps canonical_name
        _create_raw(sdk, "Subject", "sub-raw", "owner", "subjectKind", "role")

    def test_the_raw_spelling_does_not_evade_the_folded_count(self, sdk_factory):
        """N1: a raw hit short-circuited the folded count, so the create that
        spelled the name in its folded form silently picked one of two carriers
        while the same create spelled differently refused."""
        sdk = sdk_factory()
        self._two_folded_carriers(sdk)
        for spelling in ("owner", "the owner", "OWNER", "an owner"):
            with pytest.raises(AmbiguousEntityName):
                sdk.create_subject(spelling, "role")
        assert _count(sdk, "Subject", subjectKind="role") == 2

    def test_a_single_entity_matching_both_arms_is_not_ambiguous(self, sdk_factory):
        """The union must dedupe: one node holding both spellings is ONE holder."""
        sdk = sdk_factory()
        sdk.create_subject("owner", "role")
        again = sdk.create_subject("the owner", "role")  # idempotent re-mention
        assert _count(sdk, "Subject", subjectKind="role") == 1
        assert again["id"]

    def test_the_role_door_sees_the_same_ambiguity(self, sdk_factory):
        self._two_folded_carriers(sdk := sdk_factory())
        with pytest.raises(AmbiguousEntityName):
            sdk.create_subject("a role", "role", roleHolder="owner")

    # ── N2: a `semantic` queue row is mergeable ──────────────────────────

    def test_a_semantic_row_can_be_merged_back(self, sdk_factory, monkeypatch):
        """N2: `entity_pair_for` hard-coded `include_semantic=False`, so the queue
        offered a semantic row the merge then refused as "no pair".

        The seam is `tortoise.embeddings._encode` (imported lazily inside
        `_semantic_matrix`) — patching anything else is a dead pin (review F-D).
        The two names are in ONE block ('qua') because the semantic rung is
        block-scoped by design; their folds share no word, so no deterministic or
        fuzzy rung can pair them.
        """
        import tortoise.embeddings as emb

        def fake_encode(texts):
            # Distinct-but-identical vectors → cosine 1.0 for every pair.
            return [[1.0, 0.0, 0.0] for _ in texts], False

        monkeypatch.setattr(emb, "_encode", fake_encode, raising=False)
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "quarterly revenue", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-b", "quasar rebound", "subjectKind", "other")

        rows = sdk.list_dedup_candidates(candidate_type="entity")
        semantic = [r for r in rows if r["method"] == "semantic"]
        assert semantic, (
            "the semantic rung produced no row with a working encoder and "
            f"same-block names — queue was {rows!r}")
        assert semantic[0]["auto_mergeable"] is False, (
            "a semantic row must never be auto-mergeable (advisory only)")
        pair = entity_pair_for(sdk._get_proj().g, semantic[0]["candidate_id"])
        assert pair, "the merge could not re-derive a row the queue just displayed"
        assert pair["method"] == "semantic"
        out = sdk.approve_merge(semantic[0]["candidate_id"], "merge")
        assert out["action"] == "merge" and out["method"] == "semantic"

    # ── N3: a rename maintains the folded key, and collides loudly ───────

    def test_a_rename_maintains_the_folded_key(self, sdk_factory):
        """N3: `canonical_name` was written once at create and never again, so a
        rename left the NEW spelling invisible to the deterministic rung — and a
        create of it minted a twin, the damage this issue removes."""
        sdk = sdk_factory()
        obj = sdk.create_object("Acme Corp", "document")
        oid = obj["id"]
        sdk.update_entity(oid, name="Acme Corporation")
        props = sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) RETURN properties(o)",
            params={"id": oid}).result_set[0][0]
        assert props["name"] == "Acme Corporation"
        assert props["canonical_name"] == "acme corporation"
        # …so the loud spelling resolves onto the renamed entity, not a twin.
        again = sdk.create_object("ACME CORPORATION", "document")
        assert again["id"] == oid
        assert _count(sdk, "Object") == 1

    def test_a_rename_onto_a_folded_collision_refuses(self, sdk_factory):
        sdk = sdk_factory()
        first = sdk.create_object("Acme Corp", "document")
        second = sdk.create_object("Beta Inc", "document")
        with pytest.raises(NameAlreadyHeld):
            sdk.update_entity(second["id"], name="the acme corp")
        props = sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) RETURN properties(o)",
            params={"id": second["id"]}).result_set[0][0]
        assert props["name"] == "Beta Inc", "the refused rename must not have landed"
        assert first["id"] != second["id"]

    # ── N4: a truncated pair window REFUSES, it does not answer "no pair" ─

    def test_the_pair_recompute_is_never_truncated(self, sdk_factory, monkeypatch):
        """N4/A8: a TRUNCATED recompute can answer "no pair" for a candidate that
        lies outside the window it chose — a report false for the graph it
        describes (#8018). The queue is a paged read; the verdict is not.

        Pinned by shrinking the QUEUE's page to 1 and then merging a candidate
        that page never showed — the recompute must still find its pair.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Object", "obj-x", "Acme Corporation", "objectKind", "document")
        _create_raw(sdk, "Object", "obj-y", "Acme Corporaton", "objectKind", "document")
        g = sdk._get_proj().g
        page = sdk.list_dedup_candidates(candidate_type="entity", limit=1)
        assert page and page[0]["candidate_id"] == "obj-y"
        assert entity_pair_for(g, "obj-y") is not None, (
            "the merge recompute lost a pair that the queue's own row named")
        verdict = sdk.approve_merge("obj-y", action="merge")
        assert verdict["survived_id"] == "obj-x"

    # ── N6: a successful folded resolve leaves NO phantom refusal ────────

    def test_a_successful_folded_holder_leaves_no_refusal_behind(self, sdk_factory):
        """N6: the speculative raw-name probe recorded `no-live-holder` before the
        folded retry succeeded, so the R8 record held a refusal for a resolve
        that did not fail."""
        from tortoise.entity_identity import non_folded_entries

        sdk = sdk_factory()
        sdk.create_subject("owner", "naturalPerson")
        role = sdk.create_subject("the operator", "role")
        from tortoise.entity_resolver import wire_holds_role

        wire_holds_role(sdk, "the owner", role["id"])  # raw spelling misses, fold hits
        entries = non_folded_entries()
        phantom = [e for e in entries
                   if e[0] in {"no-live-holder", "unaddressable"}
                   and "the owner" in str(e[1])]
        assert not phantom, f"a successful resolve recorded a refusal: {phantom}"

    # ── N7: a role is not its own holder ─────────────────────────────────

    def test_a_role_cannot_hold_itself(self, sdk_factory):
        sdk = sdk_factory()
        person = sdk.create_subject("Dana", "naturalPerson")
        # `Dana` names the person, so the role create resolves ONTO the person and
        # the holder is that same node — the self edge the guard exists for.
        with pytest.raises(ValueError, match="cannot hold itself"):
            sdk.create_subject("Dana", "role", roleHolder="Dana")
        assert sdk._get_proj().g.query(
            "MATCH (:Subject)-[r:holdsRole]->(:Subject) RETURN count(r)"
        ).result_set[0][0] == 0
        # ⛔ round-3 P1: the refusal must run BEFORE the write. The role create
        # FOLDS ONTO the person, and a post-write refusal left the person stored
        # as `subjectKind='role'` (plus a `SubjectAdded` journal line) while the
        # caller was told the create failed — the F5 atomicity class, one rung out.
        kind = sdk._get_proj().g.query(
            "MATCH (s:Subject {id:$id}) RETURN s.subjectKind",
            params={"id": person["id"]}).result_set
        assert kind == [["naturalPerson"]], (
            f"a REFUSED create rewrote the holder's kind: {kind}")
        log = sdk._get_event_log()
        if log is not None:
            journal = [e for e in log.read_all() if e.get("type") == "SubjectAdded"]
            assert len(journal) == 1, (
                f"a REFUSED create journaled a SubjectAdded line: {journal}")
        assert _count(sdk, "Subject") == 1

    # ── N8: every producer folds the key it writes ───────────────────────

    def test_the_docs_miner_producer_folds_canonical_name(self, sdk_factory):
        """N8: `docs_miner` passed the raw surface form as `canonical_name`, so a
        docs-mined Object was invisible to the folded rung and a create of the
        folded spelling minted a twin."""
        from tortoise.api import EventAPI

        class _Log:
            def __init__(self):
                self.events = []

            def append(self, event):  # pragma: no cover - shape only
                self.events.append(event)

        sdk = sdk_factory()
        proj = sdk._get_proj()
        api = EventAPI(_Log(), initiated_by="extractor", agent_id="test",
                       projection=proj)
        oid = api.add_object("The Pricing Memo", "document")
        props = proj.g.query(
            "MATCH (o:Object {id:$id}) RETURN properties(o)",
            params={"id": oid}).result_set[0][0]
        assert props["name"] == "The Pricing Memo"      # surface form preserved
        assert props["canonical_name"] == "pricing memo"  # one fold for the key
        # …so the folded spelling resolves onto it instead of minting a twin.
        again = sdk.create_object("the pricing memo", "document")
        assert again["id"] == oid
        assert _count(sdk, "Object") == 1


class TestThirdReviewRound:
    """Regression pins for the third review round (F-A..F-F, F-H)."""

    # ── F-A (P0): a re-mention must not rewrite the entity's own key ──────

    def test_a_re_mention_of_the_merged_away_spelling_keeps_the_survivors_key(
            self, sdk_factory):
        """F-A: `canonical_name` rode the extra-props lane (`SET n += $extra`),
        which applies ON MATCH — so creating the MERGED-AWAY spelling resolved
        onto the survivor and then overwrote the survivor's own folded key with
        the alias's. The next create of the survivor's own name then minted the
        duplicate this issue exists to remove."""
        from tortoise.entity_resolver import apply_entity_merge

        sdk = sdk_factory()
        survivor = sdk.create_object("Acme Corp", "document")["id"]
        twin = sdk.create_object("Acme Corporaton", "document")["id"]
        assert twin != survivor
        apply_entity_merge(sdk, candidate_id=twin, survivor_id=survivor,
                           method="fuzzy", similarity=0.9)

        def props(i):
            rows = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.name, o.canonical_name",
                params={"id": i}).result_set
            return rows[0]

        assert props(survivor) == ["Acme Corp", "acme corp"]
        again = sdk.create_object("Acme Corporaton", "document")  # the alias
        assert again["id"] == survivor, "the merged-away spelling must hop"
        assert props(survivor) == ["Acme Corp", "acme corp"], (
            "a re-mention rewrote the survivor's resolution key")
        # …and the survivor is STILL addressable under its own spelling.
        assert sdk.create_object("acme corp", "document")["id"] == survivor

    def test_a_plain_re_mention_does_not_rewrite_the_key(self, sdk_factory):
        sdk = sdk_factory()
        oid = sdk.create_object("Acme Corp", "document")["id"]
        sdk.create_object("ACME CORPORATION", "document")  # mints its own entity
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) RETURN o.name, o.canonical_name",
            params={"id": oid}).result_set
        assert rows[0] == ["Acme Corp", "acme corp"]

    # ── F-B (P1): id-less holders are COUNTED, never collapsed ───────────

    def test_two_id_less_holders_refuse_and_get_no_shared_id(self, sdk_factory):
        """F-B: the union's Python dedupe keyed id-less rows on `id(None)` — one
        constant — so two distinct legacy rows collapsed into one, the 2+-holder
        refusal was skipped, and the adopt stamped ONE id onto BOTH nodes."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (:Subject {name:'Ghost3'}) CREATE (:Subject {name:'Ghost3'})")
        assert _count(sdk, "Subject") == 2
        with pytest.raises(AmbiguousEntityName):
            sdk.create_subject("Ghost3", "role")
        ids = g.query("MATCH (n:Subject) RETURN n.id").result_set
        assert [r[0] for r in ids] == [None, None], (
            f"an id was stamped onto an id-less holder despite the refusal: {ids}")

    # ── F-C (P2): a scan that cannot run still allows REJECT ─────────────

    def test_reject_survives_a_scan_that_cannot_run(self, sdk_factory, monkeypatch):
        """F-C: `entity_pair_for` can raise `EntityScanUnavailable`, and
        `approve_merge`'s reject arm only caught `ValueError` — so the reject
        verb died with the merge verb. A reject asserts "not duplicates" and
        needs no survivor; a MERGE must still fail loudly rather than guess.

        (The truncation axis this test used to pin is gone: the merge's
        re-derivation now runs with `limit=None`, so there is no window to be
        full of — see `test_the_merge_recomputation_is_untruncated`.)
        """
        import tortoise.entity_resolver as er

        sdk = sdk_factory()
        _create_raw(sdk, "Object", "obj-x", "Acme Corporation", "objectKind", "document")
        _create_raw(sdk, "Object", "obj-y", "Acme Corporaton", "objectKind", "document")

        def boom(*_a, **_kw):
            raise EntityScanUnavailable("scan unavailable (test)")

        monkeypatch.setattr(er, "entity_candidates", boom, raising=False)
        out = sdk.approve_merge("obj-x", "reject")
        assert out["action"] == "reject" and out["candidate_type"] == "entity"
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {id:'obj-x'}) RETURN o.dedup_reviewed", ).result_set
        assert rows[0][0] == "reject"
        with pytest.raises(EntityScanUnavailable):
            sdk.approve_merge("obj-y", "merge")

    def test_the_merge_reads_the_entity_not_a_point_that_shares_its_id(
            self, sdk_factory):
        """P3: the endpoint probes were unlabelled — `MATCH (s) WHERE s.id = $sid`.

        FalkorDB indexes are label-scoped, so an unlabelled id probe can use no
        label's `id` index (a whole-node scan per merge on the 872/44k-scale
        graphs this issue targets) — and, for an id carried by BOTH a `:Point` and
        an entity (a shape this PR declares reachable in `approve_merge`'s own
        note), `rows[0]` may be the Point, so the survivor's name/aliases and the
        fill-only decision about the candidate's key are taken from a node that is
        not the entity being merged. The probe is now `Object` then `Subject`.

        The Point is written FIRST so a creation-ordered scan reaches it first;
        the assertion is on the OUTCOME (the merge read the entity), which holds
        whichever row an unlabelled scan would have returned.
        """
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (:Point {id:'obj-y', name:'not the entity'})")
        _create_raw(sdk, "Object", "obj-x", "Acme Corporation", "objectKind", "document")
        _create_raw(sdk, "Object", "obj-y", "Acme Corporaton", "objectKind", "document")

        verdict = sdk.approve_merge("obj-y", action="merge")
        assert verdict["survived_id"] == "obj-x"
        candidate = g.query(
            "MATCH (o:Object {id:'obj-y'}) "
            "RETURN o.canonical_name, o.resolved_from").result_set
        assert candidate[0][0] == "acme corporaton", (
            f"the merge took the candidate's key from the WRONG node: {candidate}")
        assert candidate[0][1] == "obj-x"

    @pytest.mark.xfail(
        strict=True,
        reason="#12095: update_entity writes on EVERY label carrying the id, so the "
               "merge's own write stamps the shared-id :Point too")
    def test_the_merge_does_not_write_onto_a_point_that_shares_the_id(
            self, sdk_factory):
        """⛔ FILED, not fixed here — #12095, category A (silent corruption).

        The READ half above is label-scoped, but the merge's WRITE goes through
        `sdk.update_entity`, whose outer `for label, _prop in
        _CANONICAL_ENTITY_ID_PROPS:` loop (`sdk.py:45199`) writes on EVERY
        canonical label carrying the id — the `break` at `sdk.py:45377` exits only
        the INNER id-key loop. So a `:Point` sharing the entity's id gains
        `canonical_name`/`resolved_from` and then claims an entity identity it
        never had. Fixing it needs a label-restricted update seam = an SDK
        surface change (owner approval), so it is recorded, not invented.
        """
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (:Point {id:'obj-y', name:'not the entity'})")
        _create_raw(sdk, "Object", "obj-x", "Acme Corporation", "objectKind", "document")
        _create_raw(sdk, "Object", "obj-y", "Acme Corporaton", "objectKind", "document")
        sdk.approve_merge("obj-y", action="merge")
        point = g.query(
            "MATCH (p:Point {id:'obj-y'}) "
            "RETURN p.name, p.canonical_name, p.resolved_from").result_set
        assert point[0] == ["not the entity", None, None], (
            f"the merge wrote onto the :Point that shares the id: {point}")

    def test_the_merge_recomputation_is_untruncated(self, sdk_factory, monkeypatch):
        """The merge must re-derive the queue's pair from the WHOLE graph — a
        bounded window could answer "no pair" for a candidate outside it."""
        import tortoise.entity_resolver as er

        seen: list = []
        real = er.entity_candidates

        def spy(graph, **kw):
            seen.append(kw.get("limit"))
            return real(graph, **kw)

        monkeypatch.setattr(er, "entity_candidates", spy, raising=False)
        sdk = sdk_factory()
        _create_raw(sdk, "Object", "obj-x", "Acme Corporation", "objectKind", "document")
        _create_raw(sdk, "Object", "obj-y", "Acme Corporaton", "objectKind", "document")
        row = sdk.list_dedup_candidates(candidate_type="entity")[0]
        del seen[:]
        assert entity_pair_for(sdk._get_proj().g, row["candidate_id"])
        assert seen and seen[0] is None, f"the recompute was bounded: {seen}"

    # ── F1 (P1): the merge REPAIRS the name, it does not poison it ────────

    def test_a_merged_name_stays_writable_and_renameable(self, sdk_factory):
        """F1: `apply_entity_merge` leaves both nodes LIVE and stamps the
        candidate with the same folded key, so counting rows made the repair
        verb the poisoning verb — every later create of the name raised
        `AmbiguousEntityName` (the refusal fires before the `resolved_from` hop)
        and a rename onto the variant raised `NameAlreadyHeld`."""
        sdk = sdk_factory()
        # Raw rows: the two spellings are fold-equal, so the SDK door would
        # resolve them at create time and leave NO pair to review. The damaged
        # graphs have exactly this shape (written before any resolution existed).
        _create_raw(sdk, "Object", "obj-a", "The Owner", "objectKind", "document")
        _create_raw(sdk, "Object", "obj-b", "the owner", "objectKind", "document")
        a, b = "obj-a", "obj-b"
        row = next(r for r in sdk.list_dedup_candidates(candidate_type="entity")
                   if r["candidate_id"] == b)
        assert row["method"] == "exact", row
        out = sdk.approve_merge(row["candidate_id"], "merge")
        assert out["survived_id"] == a
        for spelling in ("The Owner", "the owner", "OWNER", "The  Owner"):
            assert sdk.create_object(spelling, "document")["id"] == a, (
                f"{spelling!r} no longer resolves onto the survivor after a merge")
        # …and the survivor may be renamed onto the variant's spelling.
        sdk.update_entity(a, name="THE OWNER ")
        assert _count(sdk, "Object") == 2, (
            "the merge must never delete either node")

    # ── F2 (P1): an ABSENT key is filled, a present one is never overwritten ──

    def test_a_pre_existing_row_gains_the_fold_it_never_had(self, sdk_factory):
        """F2: ON CREATE only left every pre-#12047 row without a key forever —
        the rung stayed blind on exactly the population this issue measured."""
        sdk = sdk_factory()
        _create_raw(sdk, "Object", "p-1", "The Owner", "objectKind", "document")
        sdk._get_proj().g.query(
            "MATCH (o:Object {id:'p-1'}) REMOVE o.canonical_name")
        assert sdk.create_object("The Owner", "document")["id"] == "p-1"
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {id:'p-1'}) RETURN o.canonical_name").result_set
        assert rows[0][0] == "owner", "the create did not fill the absent fold"
        assert sdk.create_object("the owner", "document")["id"] == "p-1"
        assert _count(sdk, "Object") == 1

    def test_an_adopted_id_less_row_gains_the_fold(self, sdk_factory):
        sdk = sdk_factory()
        sdk._get_proj().g.query(
            "CREATE (:Object {name:'Legacy Doc', objectKind:'document', "
            "status:'live'})")
        oid = sdk.create_object("Legacy Doc", "document")["id"]
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) RETURN o.canonical_name",
            params={"id": oid}).result_set
        assert rows[0][0] == "legacy doc"

    # ── F5 (P2): an article is a WORD, not a prefix ──────────────────────

    def test_a_hyphenated_name_keeps_its_first_token(self):
        """F5: `normalize("a-b")` was `"b"` — a different key from
        `_legacy_fold("a-b") == "ab"`, so the union of the two arms resolved a
        write of "a-b" onto an unrelated "AB" node: a merge the queue cannot
        show, because neither fold compares them equal."""
        assert normalize_entity_name("a-b") == "a b"
        assert normalize_entity_name("A-B") == "a b"
        assert normalize_entity_name("the-owner") == "the owner"
        # …while a real leading article is still not identity.
        assert normalize_entity_name("the owner") == "owner"
        assert normalize_entity_name("an apple") == "apple"
        assert normalize_entity_name("the_owner") == "owner"

    def test_a_hyphenated_name_does_not_resolve_onto_an_unrelated_node(
            self, sdk_factory):
        sdk = sdk_factory()
        ab = sdk.create_object("AB", "document")["id"]
        assert sdk.create_object("a-b", "document")["id"] != ab, (
            "a-name with a hyphen resolved onto an unrelated 'AB' node")

    # ── F-F (P2): a pre-#12047 row is REPAIRED by the review flow ────────

    def test_a_legacy_fold_row_is_queued_and_repaired_by_a_merge(self, sdk_factory):
        """F-F, as finally settled. `mining` wrote `extractor._canonical_name`
        before this PR — a DIFFERENT fold (`"The Owner"` → `"the owner"`, where
        the new fold gives `"owner"`) — so such a row is invisible to the create
        rung. Making the create rung match the old shape too was tried and
        WITHDRAWN: that shape strips punctuation without inserting a space, so its
        key for "a-b" is "ab" — the new fold's key for "AB" — and a create of
        "a-b" then resolved onto an unrelated "AB" node (a silent wrong write the
        queue can never show; round-4 finding 5).

        The repair path is the flow this PR exists for: `scan_entities` re-folds
        `canonical_name or name` in Python, so the pair IS queued, and the merge
        writes the new-shape key onto the node.
        """
        sdk = sdk_factory()
        obj = sdk.create_object("The Owner", "document")["id"]
        sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) SET o.canonical_name='the owner'",
            params={"id": obj})   # the pre-#12047 shape
        # A near-duplicate of the SAME legacy row, in the same block, so the
        # ladder scores it (their folds are "owner" and "owner doc").
        _create_raw(sdk, "Object", "obj-twin", "The Owner Doc",
                    "objectKind", "document")

        # 1) the queue sees the pair, because it folds `canonical_name or name`
        #    at READ time — the legacy row is found by the graph, not by the rung.
        rows = sdk.list_dedup_candidates(candidate_type="entity")
        pair = [r for r in rows
                if {r["candidate_id"], r["existing_id"]} == {obj, "obj-twin"}]
        assert pair, f"the legacy row is invisible to the queue: {rows!r}"

        # 2) the merge stamps the NEW-shape key on the retiree, so its spelling
        #    resolves onto the survivor afterwards (via the `resolved_from` hop).
        out = sdk.approve_merge(pair[0]["candidate_id"], "merge")
        surv = out["survived_id"]
        # The merge records the variant in the SURVIVOR's aliases, and the write
        # path honours them — so the legacy spelling resolves even after the
        # retired carrier is superseded (a terminal node is excluded from the
        # live-holder union, so the alias is the only remaining evidence).
        sdk.update_entity(out["candidate_id"], status="superseded")
        assert sdk.create_object("the owner", "document")["id"] == surv

    def test_the_alias_arm_survives_the_carriers_retirement(self, sdk_factory):
        """Finding 3: the write path read `name`/`canonical_name` only, so the
        merge's promise ("a later write of the variant resolves") held only while
        the retired carrier stayed LIVE — it died the moment it was superseded,
        which is the ordinary next step."""
        sdk = sdk_factory()
        a = sdk.create_object("Acme Corp", "document")["id"]
        b = sdk.create_object("Acme Corporaton", "document")
        if b["id"] == a:
            pytest.skip("resolved at create time — no merge to test")
        out = sdk.approve_merge(b["id"], "merge")
        assert out["survived_id"] == a
        sdk.update_entity(b["id"], status="superseded")
        assert sdk.create_object("Acme Corporaton", "document")["id"] == a
        assert _count(sdk, "Object") == 2, "the merge must not delete either node"

    # ── F-H (P2): a no-op re-write keeps a caller-supplied key ───────────

    def test_an_unchanged_name_does_not_clobber_a_caller_supplied_key(self, sdk_factory):
        sdk = sdk_factory()
        oid = sdk.create_object("Widget", "document")["id"]
        sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) SET o.canonical_name='my own key'",
            params={"id": oid})
        sdk.update_entity(oid, name="Widget", status="live")  # no-op re-write
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {id:$id}) RETURN o.canonical_name",
            params={"id": oid}).result_set
        assert rows[0][0] == "my own key", (
            "a no-op re-write replaced a caller-supplied canonical_name")


# ── #12120: the readers that still asked the raw `name` ───────────────────
#
# The write path was widened to the FOLDED UNION (`name` · `canonical_name` ·
# `aliases`, over the raw and the folded spelling). Four readers still asked the
# old raw-`name` question, so each could describe a different node than the
# write lands on. One pin per site; the first is the P1 (`derived ==
# replay(journal)`).


class TestThe12120RemainingReaders:
    def test_a_variant_spelling_event_folds_and_replays(self, tmp_path):
        """P1 — `resolve_fold_name` asked the raw name only.

        `create_object("Acme Corp")` then `create_object("The Acme Corp")` is ONE
        node that keeps the ORIGINAL raw name, so an event naming the variant no
        longer resolved: `_fold_object_status` recorded the NON-EXEMPT
        `unregistered-reference` and `rebuild_all` raised `NonFoldedEventsError`.
        On `main` the variant minted its own node, so the exposure was CREATED by
        widening the writer — legitimate public writes produced an un-rebuildable
        journal.
        """
        from tortoise.sdk import TortoiseSDK

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            first = sdk.create_object("Acme Corp", "document")["id"]
            # the PR's headline behaviour: the variant lands on the SAME node,
            # which keeps the caller's original surface form as `name`.
            assert sdk.create_object("The Acme Corp", "document")["id"] == first
            assert _count(sdk, "Object") == 1

            sdk.create_event("ev-1", "github.issue.open",
                             object="The Acme Corp", objectType="Object")

            g = sdk._get_proj().g
            live = g.query("MATCH (o:Object {id:$id}) RETURN o.status",
                           params={"id": first}).result_set
            assert live and live[0][0] == "in_progress", (
                "the variant spelling did not fold a status onto the node the "
                "create resolved onto — the fold recorded an identity miss")

            # A non-folded, non-exempt record makes the run boundary raise.
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

            replayed = g.query("MATCH (o:Object {id:$id}) RETURN o.status",
                               params={"id": first}).result_set
            assert replayed and replayed[0][0] == "in_progress", (
                "derived != replay: the status folded live but not on replay")
        finally:
            sdk.close()

    def test_the_docs_miner_resolves_a_fold_only_holder(self, sdk_factory):
        """P2 — `docs_miner._ensure_entities` read the raw name only.

        A fold-only holder was invisible to both of its probes, so the "minting
        is for a genuinely unheld name only" guard passed and the miner wrote a
        SECOND live node under the same folded key — after which
        `create_object(<either spelling>)` raised `AmbiguousEntityName`: the
        miner create-poisoned the name #12047 exists to make writable.
        """
        from tortoise.api import EventAPI
        from tortoise.docs_miner import _ensure_entities

        class _Log:
            def __init__(self):
                self.events = []

            def append(self, event):
                self.events.append(event)

        sdk = sdk_factory()
        proj = sdk._get_proj()
        api = EventAPI(_Log(), initiated_by="extractor", agent_id="test",
                       projection=proj)
        held = api.add_object("Acme Corp", "document")

        # "The Acme Corp" folds onto the existing node; the miner must NOT mint
        # a twin beside it.
        ids = _ensure_entities(api, proj, [{
            "name": "The Acme Corp", "object_kind": "document",
            "externalId": "docs:acme", "id": "mined-acme"}])
        assert ids == [held], (
            "the miner minted a twin beside a fold-only holder")
        assert _count(sdk, "Object") == 1

        # …and the name is still writable (the create-poisoning is the harm).
        assert sdk.create_object("Acme Corp", "document")["id"] == held
        assert sdk.create_object("the acme corp", "document")["id"] == held

    def test_the_onboarding_anchor_guard_sees_a_fold_collision(self, sdk_factory):
        """P2 — both anchor probes enumerated the raw `name` only.

        The writer resolves the folded union, so a fold-colliding anchor name was
        classified "safe to create" and the write landed on the OTHER org's node,
        rewriting its `org_id` (measured `org-1` → `org-2` on one node).
        """
        from tortoise.onboarding.seed import find_subject_by_name
        from tortoise.onboarding.state import _subject_anchor_name_taken

        sdk = sdk_factory()
        g = sdk._get_proj().g
        first = sdk.create_subject("Acme", subjectKind="organization",
                                   org_id="org-1")["id"]

        # A fold-colliding spelling must resolve to the held anchor, not read as
        # a free name.
        props = find_subject_by_name(g, "ACME")
        assert props is not None and props["id"] == first, (
            "the anchor guard read a fold-colliding name as free")
        assert _subject_anchor_name_taken(g, "ACME") is True, (
            "the carry's existence probe read a fold-colliding name as free")

        # The harm the guard prevents: the writer would have rewritten org_id.
        sdk.create_subject("ACME", subjectKind="organization", org_id="org-2")
        row = g.query("MATCH (s:Subject {id:$id}) RETURN s.org_id",
                      params={"id": first}).result_set
        assert row[0][0] == "org-2", (
            "fixture drift: the write no longer resolves onto the held anchor")
        assert _count(sdk, "Subject") == 1

    def test_a_dual_labelled_content_reject_emits_dedupe_rejected(self, sdk_factory):
        """P3 — the REJECT arm had no `None` sentinel fall-through.

        For an id that BOTH labels carry, a content reject was diverted into the
        entity branch: no `DedupeRejected` was emitted and `target_id` came back
        `None` instead of the Point's `dedup_target_id`. The MERGE arm already
        handed the verdict back.
        """
        sdk = sdk_factory()
        g = sdk._get_proj().g
        # A Point that is a dedup candidate, and an Object sharing its id.
        _create_raw(sdk, "Point", "dual-1", "Dual one", "pointKind", "statement",
                    dedup_candidate=True, dedup_target_id="prior-1", status="draft")
        g.query(
            "CREATE (o:Object {id:'dual-1', name:'Dual one', "
            "objectKind:'document', status:'live'})")

        # `_emit_event` writes a `:GraphEvent` row (the change feed), not an
        # `:Event` — count the record the content branch owns.
        before = g.query(
            "MATCH (e:GraphEvent) WHERE e.type = 'DedupeRejected' "
            "RETURN count(e)"
        ).result_set[0][0]

        out = sdk.approve_merge("dual-1", action="reject")

        assert out is not None, (
            "the content reject was swallowed by the entity branch")
        assert out["target_id"] == "prior-1", (
            f"the Point's dedup_target_id was not returned: {out}")
        after = g.query(
            "MATCH (e:GraphEvent) WHERE e.type = 'DedupeRejected' "
            "RETURN count(e)"
        ).result_set[0][0]
        assert after == before + 1, (
            "no DedupeRejected record was emitted for the content reject")


class TestThe12120CollapseIsNotTheHeldQuestion:
    """The V-GATE round on #12120 — two questions are not one.

    `_collapse_resolved_holders` follows a `resolved_from` hop and then re-applies
    a LIVE filter (round 9), so a **terminal survivor** reached through a
    merged-away carrier disappears from a collapsing read. That is correct for
    "which entity does this name land on" and WRONG for "is this name held at all
    / is any incarnation live" — the second answered "unheld" and turned a held
    name into a NON-exempt identity miss, failing `rebuild_all` on a journal the
    SDK itself produced.
    """

    def _merged_then_retired(self, sdk):
        """Two spellings merged onto one node, then BOTH incarnations retired."""
        survivor = sdk.create_object("Acme Corp", "document")["id"]
        candidate = sdk.create_object("Acme Corporaton", "document")["id"]
        assert candidate != survivor, "fixture drift: no merge to set up"
        sdk.approve_merge(candidate, action="merge")
        sdk.update_entity(survivor, status="superseded")
        sdk.update_entity(candidate, status="superseded")
        return survivor, candidate

    def test_a_retired_survivor_of_a_merge_keeps_the_bounded_exemption(self, tmp_path):
        """The exact regression: collapsing `_held` made a retired survivor vanish.

        ⚠️ Scope (round-4 finding 2): this is a MUTANT pin, not a pre-fix pin — with
        BOTH incarnations retired the pre-fix probes also answered
        `held=[candidate] live=[] -> mention=True`, so it is green on
        `e0fdf8df2b`. It catches the collapsing-`_held` mistake (the mutant), which
        is the regression it was written for; the shape the raw probe got WRONG is
        pinned by ``test_a_fold_only_terminal_holder_is_refused_not_minted``.
        """
        from tortoise.sdk import TortoiseSDK

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            survivor, _candidate = self._merged_then_retired(sdk)

            # A status-fold event naming the merged-away spelling. No live entity
            # may be resurrected (R5), so this is the bounded, non-fatal
            # `unfolded-mention` — NOT a missing creation event.
            sdk.create_event("ev-1", "github.issue.open",
                             object="Acme Corporaton", objectType="Object")

            # A non-exempt record would make the run boundary raise.
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

            row = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.status",
                params={"id": survivor}).result_set
            assert row and row[0][0] == "superseded", (
                "the retired incarnation was resurrected by the fold")
        finally:
            sdk.close()

    def test_the_docs_miner_refuses_a_terminal_holder_reached_through_a_hop(
            self, sdk_factory):
        """The miner's D2 refusal must survive the COLLAPSE: the holder is reachable
        only by hopping a merged-away carrier.

        ⚠️ Scope (round-4 finding 2): this pin is about the collapsing read losing
        a terminal survivor — **not** about the pre-fix raw probe. For this fixture
        the pre-fix miner also refused (its raw `{name: $name}` read finds the
        merged-away carrier and raises the same `terminal` message), so a docstring
        claiming "it would MINT" here would be false. The MINTING shape is pinned by
        :meth:`test_a_fold_only_terminal_holder_is_refused_not_minted`."""
        from tortoise.api import EventAPI
        from tortoise.docs_miner import _ensure_entities

        class _Log:
            def __init__(self):
                self.events = []

            def append(self, event):
                self.events.append(event)

        sdk = sdk_factory()
        proj = sdk._get_proj()
        api = EventAPI(_Log(), initiated_by="extractor", agent_id="test",
                       projection=proj)
        survivor = sdk.create_object("Acme Corp", "document")["id"]
        candidate = sdk.create_object("Acme Corporaton", "document")["id"]
        sdk.approve_merge(candidate, action="merge")
        sdk.update_entity(survivor, status="superseded")
        sdk.update_entity(candidate, status="superseded")
        before = _count(sdk, "Object")

        with pytest.raises(ValueError) as exc:
            _ensure_entities(api, proj, [{
                # ⛔ The MERGED-AWAY spelling, deliberately: for the survivor's
                # own spelling the collapsing read still finds the survivor, so
                # the collapse only loses the holder for the spelling whose ONLY
                # carriers hopped away. Pinning the survivor spelling left this
                # pin GREEN under the mutant (measured — a vacuous pin).
                "name": "Acme Corporaton", "object_kind": "document",
                "externalId": "docs:acme2", "id": "mined-acme2"}])
        assert "terminal" in str(exc.value), (
            f"the refusal did not name the terminal holder: {exc.value}")
        assert _count(sdk, "Object") == before, (
            "the miner minted a name-keyed Object beside a terminal holder")

    def test_a_fold_only_terminal_holder_is_refused_not_minted(self, sdk_factory):
        """The shape the PRE-FIX miner actually minted a twin on (round-4 finding 2).

        One Object, retired, addressed by the FOLDED spelling only: the pre-fix
        probe read the raw `name` (stored as "Acme Corp"), so the folded spelling
        "acme corp" found no holder, the guard passed, and `add_object` wrote a
        second node under the same folded key — the create-poisoning #12120 exists
        to remove. Measured on the pre-fix tree (`e0fdf8df2b`): `resolve -> None`,
        `holders -> []`; post-fix the same probe refuses. This pin is therefore RED
        on the pre-fix tree, which the merged-pair pin above is not.
        """
        from tortoise.api import EventAPI
        from tortoise.docs_miner import _ensure_entities

        class _Log:
            def __init__(self):
                self.events = []

            def append(self, event):
                self.events.append(event)

        sdk = sdk_factory()
        proj = sdk._get_proj()
        api = EventAPI(_Log(), initiated_by="extractor", agent_id="test",
                       projection=proj)
        holder = sdk.create_object("Acme Corp", "document")["id"]
        sdk.update_entity(holder, status="superseded")
        before = _count(sdk, "Object")

        with pytest.raises(ValueError) as exc:
            _ensure_entities(api, proj, [{
                # ⛔ The FOLD-ONLY spelling, deliberately: `name` holds "Acme
                # Corp", so nothing matches on the raw column.
                "name": "acme corp", "object_kind": "document",
                "externalId": "docs:acme3", "id": "mined-acme3"}])
        assert "terminal" in str(exc.value), (
            f"the refusal did not name the terminal holder: {exc.value}")
        assert _count(sdk, "Object") == before, (
            "the miner minted a name-keyed Object beside a fold-only terminal "
            "holder — the create-poisoning of an already-held folded key")


class TestThe12120RejectSentinelIsDefinitive:
    def test_an_unavailable_scan_still_writes_the_entity_rejection(
            self, sdk_factory, monkeypatch):
        """A dual-labelled id + an un-runnable scan must NOT hand the verdict to
        the content branch.

        The sentinel means "the scan ANSWERED: no entity pair". Folding
        `EntityScanUnavailable` / an ambiguous pair into it told the reject verb
        to abandon its own row — the entity rejection was never written, so the
        row stayed permanently re-offered and un-rejectable (the F1 class), and a
        `DedupeRejected` was emitted about a Point the reviewer's row was not
        about.
        """
        import tortoise.entity_resolver as er

        sdk = sdk_factory()
        g = sdk._get_proj().g
        _create_raw(sdk, "Point", "dual-2", "Dual two", "pointKind", "statement",
                    dedup_candidate=True, dedup_target_id="prior-2", status="draft")
        g.query(
            "CREATE (o:Object {id:'dual-2', name:'Dual two', "
            "objectKind:'document', status:'live'})")

        def boom(*_a, **_kw):
            raise EntityScanUnavailable("scan unavailable (test)")

        monkeypatch.setattr(er, "entity_candidates", boom, raising=False)

        before = g.query(
            "MATCH (e:GraphEvent) WHERE e.type = 'DedupeRejected' "
            "RETURN count(e)").result_set[0][0]

        out = sdk.approve_merge("dual-2", action="reject")

        assert out["candidate_type"] == "entity", (
            f"the entity rejection was diverted to the content branch: {out}")
        row = g.query(
            "MATCH (o:Object {id:'dual-2'}) RETURN o.dedup_reviewed"
        ).result_set
        assert row[0][0] == "reject", (
            "the entity rejection was never written — the row stays un-rejectable")
        after = g.query(
            "MATCH (e:GraphEvent) WHERE e.type = 'DedupeRejected' "
            "RETURN count(e)").result_set[0][0]
        assert after == before, (
            "a DedupeRejected was emitted about a Point the row was not about")


class TestThe12120ReadersOutsideTheEntityModule:
    """The by-name readers in the rest of the codebase.

    The #3633 sweep only matches literal Cypher, so a *Python* `resolve_entity_id`
    call asking the raw key space was invisible to it: the writer folds, the
    reader does not, and the reader describes a different node (or no node).
    Every one of those callers now goes through
    `entity_identity.resolve_entity_id_same_entity`.

    ⛔ SCOPE — this class pins TWO of the nine migrated callers (`belief_timeline`,
    `file_human_approval`). It used to claim the whole set, which made a revert of
    any of the other seven invisible; #12120 coverage-gap finding measured that (2 of 9) and
    the remaining seven are pinned in
    :class:`TestThe12120Finding4TheUnpinnedMigratedReaders` below. Do not widen
    this docstring without adding the pins.
    """

    def test_a_retired_survivor_with_a_LIVE_carrier_still_takes_the_exemption(
            self, tmp_path):
        """The mirror error of the un-collapsed read.

        `_held` must be un-collapsed ("is this name registered?"), but
        `_live_holders` must be COLLAPSED, because that is what
        `resolve_fold_name` actually answers ("can this name resolve to a live
        entity?"). Judging liveness on raw nodes contradicts the resolver: a
        merged-away carrier stays LIVE while its survivor is retired, so the raw
        read reported "a live holder exists — fail" for a name the resolver could
        not resolve to any live entity, recording a NON-exempt miss and making
        `rebuild_all` refuse the SDK's own journal.
        """
        from tortoise.sdk import TortoiseSDK

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            survivor = sdk.create_object("Acme Corp", "document")["id"]
            candidate = sdk.create_object("Acme Corporaton", "document")["id"]
            assert candidate != survivor, "fixture drift: no merge to set up"
            sdk.approve_merge(candidate, action="merge")
            # ONLY the survivor is retired — the carrier stays live (the merge is
            # non-destructive). This is the shape that used to fail.
            sdk.update_entity(survivor, status="superseded")
            carrier = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.status, o.resolved_from",
                params={"id": candidate}).result_set
            assert carrier and carrier[0][1] == survivor, (
                f"fixture drift: the carrier does not point at the survivor: {carrier}")
            assert carrier[0][0] not in (
                "superseded", "deprecated", "archived", "retracted"), (
                f"fixture drift: the carrier is not LIVE: {carrier}")

            sdk.create_event("ev-1", "github.issue.open",
                             object="Acme Corporaton", objectType="Object")

            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

            row = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.status",
                params={"id": survivor}).result_set
            assert row and row[0][0] == "superseded", (
                "the retired incarnation was resurrected by the fold")
        finally:
            sdk.close()

    def test_belief_timeline_answers_for_a_fold_colliding_spelling(self, sdk_factory):
        """`belief_timeline("the widget")` silently returned `[]` while
        `belief_timeline("Widget")` answered — an empty list read as "no beliefs",
        which is the exact class #12047 exists to remove."""
        sdk = sdk_factory()
        obj = sdk.create_object("Widget", "document")["id"]
        point = sdk.create_point("decision", "the widget ships in Q3")["id"]
        sdk.create_edge("aboutObject", point, obj)

        named = sdk.belief_timeline("Widget")
        folded = sdk.belief_timeline("the widget")
        assert named, "fixture drift: the belief did not attach to the Object"
        assert folded == named, (
            "a fold-colliding spelling answered with a different chain "
            f"({len(folded)} vs {len(named)} entries)")

    def test_file_human_approval_accepts_a_fold_colliding_approver(self, sdk_factory):
        """`file_human_approval` hard-refused a name the create door had accepted.

        The coordinate is the FOLDED form, which exists in neither stored
        property: `create_subject("Acme Corp")` stores `name='Acme Corp'` and
        `canonical_name='acme corp'`, so the input `"the acme corp"` matches no
        raw arm and only the union's folded spelling reaches the node. (Using the
        surface form as the coordinate left this pin GREEN under the mutant — the
        raw read hit `name` — measured.)
        """
        sdk = sdk_factory()
        sdk.create_subject("Acme Corp", subjectKind="naturalPerson")
        artifact = sdk.create_object("Spec", "document")["id"]
        claim = sdk.create_point("statement", "the spec is approved")["id"]

        out = sdk.file_human_approval(
            approver_id="the acme corp", artifact_id=artifact, point_ids=[claim])
        assert out["event_id"] and out["decision_point_id"], (
            f"the approval was not recorded: {out}")


class TestThe12120AnchorReaderCollapsesToo:
    """`find_subject_by_name` took the union but not the writer's COLLAPSE.

    `apply_entity_merge` leaves the candidate live with a folded key
    (`entity_resolver.apply_entity_merge`), so the union legitimately returns two
    rows for one entity and `create_subject` collapses them
    (`_live_holders_same_entity`). Counting rows made this guard raise
    `AmbiguousEntityName`, which `_classify_anchor` turns into `SubjectCollision`
    — so a re-seed of an org whose anchor was ever merged failed at a door that
    worked before the widening. The union without the collapse is the same
    reader/writer disagreement as #12120 itself, in the other direction.
    """

    def test_a_merged_pair_reads_as_one_anchor(self, sdk_factory):
        from tortoise.onboarding.seed import find_subject_by_name

        sdk = sdk_factory()
        proj = sdk._get_proj()
        anchor = sdk.create_subject("Acme Corp", "org")["id"]
        # the pre-#12047 damage shape the queue exists to merge: a second node
        # carrying the SAME folded key under a different surface form
        proj.g.query(
            "CREATE (:Subject {id:'sub-legacy', name:'The Acme Corp', "
            "subjectKind:'org', status:'live', canonical_name:'acme corp'})")
        apply_entity_merge(sdk, candidate_id="sub-legacy", survivor_id=anchor,
                           method="exact", similarity=1.0)

        props = find_subject_by_name(proj, "Acme Corp")
        assert props is not None and props["id"] == anchor, (
            f"the guard refused a merged pair the writer collapses: {props!r}")
        # …and the write path agrees on the same single node.
        assert sdk.create_subject("The Acme Corp", "org")["id"] == anchor
        assert _count(sdk, "Subject") == 2, "the merge must not delete either node"

    def test_a_second_distinct_holder_still_refuses(self, sdk_factory):
        """The collapse must not swallow a genuinely separate identity."""
        from tortoise.onboarding.seed import find_subject_by_name

        sdk = sdk_factory()
        proj = sdk._get_proj()
        anchor = sdk.create_subject("Acme Corp", "org")["id"]
        proj.g.query(
            "CREATE (:Subject {id:'sub-legacy', name:'The Acme Corp', "
            "subjectKind:'org', status:'live', canonical_name:'acme corp'})")
        apply_entity_merge(sdk, candidate_id="sub-legacy", survivor_id=anchor,
                           method="exact", similarity=1.0)
        # a THIRD node holding the same folded key but NOT merged away
        proj.g.query(
            "CREATE (:Subject {id:'sub-other', name:'ACME CORP', "
            "subjectKind:'org', status:'live', canonical_name:'acme corp'})")
        with pytest.raises(AmbiguousEntityName):
            find_subject_by_name(proj, "Acme Corp")


class TestThe12120FourthRoundReaders:
    """Round-4 review of the #12120 reader set — the two sites the sweep missed.

    Every pin here is RED on the tree before its own fix (measured with the
    reviewer's probes): the class exists because "the readers fold too" was true of
    nine sites and false of two.
    """

    def test_the_role_door_resolves_the_name_a_merge_recorded(self, sdk_factory):
        """P2: `resolve_role_holder` retried the folded spelling over
        `name`/`canonical_name` but never over the merge's `aliases`.

        `apply_entity_merge` records the variant's folded key in the SURVIVOR's
        `aliases`, so the retry could not see it: `create_subject("Acme
        Corporaton")` landed on the survivor while
        `create_entity(..., roleHolder="Acme Corporaton")` refused with "no live
        :Subject" AND recorded a non-folded refusal for a name the write door
        resolves — the #12120 reader/writer disagreement, one site short of the
        nine adopted readers.
        """
        from tortoise.entity_identity import (
            clear_non_folded_entries,
            non_folded_entries,
        )

        sdk = sdk_factory()
        survivor = sdk.create_subject("Acme Corp", "organization")["id"]
        candidate = sdk.create_subject("Acme Corporaton", "organization")["id"]
        assert candidate != survivor, "fixture drift: nothing to merge"
        sdk.approve_merge(candidate, action="merge")
        # The retired CARRIER only: the survivor stays live, which is the state a
        # re-review of the pair leaves behind (the merge is non-destructive).
        sdk.update_entity(candidate, status="superseded")
        clear_non_folded_entries()

        role = sdk.create_subject("the admin role", "role",
                                  roleHolder="Acme Corporaton")
        assert role["id"], "the role's own write failed"
        wired = sdk._get_proj().g.query(
            "MATCH (:Subject {id:$sid})-[r:holdsRole]->(:Subject) "
            "RETURN count(r)", params={"sid": survivor}).result_set[0][0]
        assert wired >= 1, (
            "roleHolder did not resolve the merged-away spelling onto its "
            "survivor — the role door and the write door disagree")
        assert [e for e in non_folded_entries()
                if e.name == "Acme Corporaton"] == [], (
            "a name the SDK resolves was recorded as an identity miss")

    def test_the_bundle_guard_answers_held_for_a_terminal_survivor(
            self, sdk_factory):
        """P3: the ANY-status arm of `_entity_name_holders` used the COLLAPSING read.

        `_collapse_resolved_holders` re-applies a LIVE filter once it follows a
        `resolved_from` hop, so a TERMINAL survivor reached through a merged-away
        carrier vanished and `exists_by_name` answered **False** for a name held
        by two nodes — contradicting the function's own docstring ("a
        TERMINAL-only reference keeps answering True") two lines above the call.
        """
        sdk = sdk_factory()
        survivor = sdk.create_object("Acme Corp", "document")["id"]
        candidate = sdk.create_object("Acme Corporaton", "document")["id"]
        assert candidate != survivor, "fixture drift: nothing to merge"
        sdk.approve_merge(candidate, action="merge")
        sdk.update_entity(survivor, status="superseded")
        sdk.update_entity(candidate, status="superseded")

        (exists, _terminal, _op, _unevaluable, live, _merges,
         _target) = sdk._entity_name_holders("Object", "Acme Corporaton")
        assert exists is True, (
            "a held name read as UNHELD through the collapsed ANY-status arm")
        assert live == 0, f"fixture drift: a live carrier appeared ({live})"

class TestThe12120AboutEdgeStubIsFoldAware:
    """#12120 finding 1 (P1): the about-edge Subject stub read a NARROWER key space
    than the writer, and the write it guarded was the poisoning one.

    ``projection/edges.py::_mint_subject_stub`` minted a ``:Subject`` stub
    unconditionally on the RAW spelling of a mention. The create door resolves on
    the FOLDED union (``name`` · ``canonical_name`` · ``aliases``, raw and
    folded), so a mention of an already-registered Subject under a VARIANT
    spelling (``"the owner"`` beside ``"The Owner"``) opened a SECOND live
    carrier with the same folded key. From then on ``resolve_or_mint`` refused
    EVERY spelling of that name — non-exempt ``ambiguous-name``, no by-name repair
    path — so ONE mention create-poisoned the entity, while ``origin/main`` still
    succeeded.

    The fix: the stub is RESOLVE-OR-MINT over the writer's key space, and it
    returns the node it ADDRESSED — so the callers (`wire_about_link` live,
    `resolve_structural_target` replay) wire the node the writer's key names
    instead of re-deriving a target from the mention's spelling.
    """

    def test_a_variant_mention_does_not_mint_a_twin(self, sdk_factory):
        """The measured failure: 2 live carriers, then every create of the name
        raised. Discriminating assertion: `create_subject("owner")` succeeds."""
        sdk = sdk_factory()
        try:
            owner = sdk.create_subject("The Owner")["id"]
            sdk.create_event("ev-12055-1", "github.issue.open",
                             aboutSubject="the owner")

            g = sdk._get_proj().g
            carriers = g.query(
                "MATCH (s:Subject) RETURN s.id, s.name, s.canonical_name"
            ).result_set
            assert len(carriers) == 1, (
                "the mention minted a SECOND live carrier for the folded name — "
                f"one mention create-poisons it: {carriers}")

            wired = g.query(
                "MATCH (e:Event)-[:aboutSubject]->(s:Subject) "
                "WHERE e.name = $e RETURN s.id",
                params={"e": "ev-12055-1"}).result_set
            assert wired == [[owner]], (
                "the mention did not wire onto the registered Subject "
                f"(wired {wired!r}, registered {owner!r})")

            # THE POISONING. Before the fix this raised `AmbiguousEntityName`
            # (`candidate_ids=['<ulid>', 'the owner']`, measured).
            again = sdk.create_subject("owner")
            assert again["id"] == owner, (
                "the folded spelling resolved to a different entity than the one "
                f"the mention wired: {again!r}")
        finally:
            sdk.close()

    def test_the_variant_mention_link_survives_a_rebuild(self, tmp_path):
        """Live and replay must agree (#9836): the journaled link carries the
        RESOLVED endpoint's id, so replay wires the same Subject instead of
        re-deriving a stub from the mention's spelling."""
        from tortoise.sdk import TortoiseSDK

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            owner = sdk.create_subject("The Owner")["id"]
            sdk.create_event("ev-12055-2", "github.issue.open",
                             aboutSubject="the owner")
            g = sdk._get_proj().g
            q = ("MATCH (e:Event)-[:aboutSubject]->(s:Subject) "
                 "WHERE e.name = $e RETURN s.id")
            before = [list(r) for r in g.query(
                q, params={"e": "ev-12055-2"}).result_set]
            assert before == [[owner]], f"fixture drift: {before!r}"

            # Raises `NonFoldedEventsError` if the fold records a non-exempt miss
            # for the mention — the shipped failure mode of this class.
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

            after = [list(r) for r in g.query(
                q, params={"e": "ev-12055-2"}).result_set]
            assert after == before, (
                "the aboutSubject link did not replay onto the same Subject "
                f"(live {before!r} vs replay {after!r})")

            # ⛔ THE RECORD ITSELF, not just the graph: deleting the fold retry
            # leaves the graph identical (the replay-side mint adopts the live
            # holder), so a graph-only assertion is GREEN under that mutant —
            # measured by review at this head. What the retry actually changes is
            # the journal shape, so that is what this pin must read.
            record = None
            for line in (events / "events.jsonl").read_text().splitlines():
                rec = json.loads(line)
                if rec.get("name") == "ev-12055-2" and rec.get("aboutLinks"):
                    record = rec
            assert record is not None, "fixture drift: no journaled about link"
            assert record["aboutLinks"][0] == {
                "rel": "aboutSubject",
                "how": "id",
                "targets": [owner],
                "target_label": "Subject",
                "resolve_key": "The Owner",
                "mint_if_absent": False,
            }, (
                "the journal pinned the mention's SPELLING instead of the resolved "
                f"endpoint — replay would have to re-derive it: {record['aboutLinks'][0]!r}")
            carriers = g.query("MATCH (s:Subject) RETURN s.id").result_set
            assert len(carriers) == 1, (
                f"the rebuild minted a stub beside the registered Subject: {carriers}")
        finally:
            sdk.close()

    def test_a_deleted_fold_target_is_not_fabricated_at_replay(self, tmp_path):
        """The record must name the RESOLVED endpoint, not the mention's spelling.

        This is the fabrication direction, and it is what the stub-fallback link
        shape costs: a record that journals `targets=[<spelling>]` with
        `mint_if_absent` cannot be told apart at replay from a mention nobody ever
        registered. Once the Subject it actually wired is hard-deleted, the
        replay's staleness test (keyed on the journaled value, here a SPELLING)
        does not fire, the value does not resolve, and the `mint_if_absent` repair
        MINTS a Subject stub the live graph never had — `derived == replay(journal)`
        broken by fabrication, in a rebuild. With the resolved id journaled, the
        hard delete is recognised and the link is simply skipped.
        """
        from tortoise.sdk import TortoiseSDK

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            owner = sdk.create_subject("The Owner")["id"]
            sdk.create_event("ev-12055-3", "github.issue.open",
                             aboutSubject="the owner")
            g = sdk._get_proj().g
            assert g.query(
                "MATCH (:Event)-[:aboutSubject]->(s:Subject) RETURN s.id"
            ).result_set == [[owner]], "fixture drift: the link was not wired"

            assert sdk.delete_entity(owner) is True, (
                "fixture drift: the Subject was not hard-deleted")

            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

            carriers = g.query("MATCH (s:Subject) RETURN s.id, s.name").result_set
            assert carriers == [], (
                "the rebuild FABRICATED a Subject for a mention whose target was "
                f"hard-deleted — the journal pinned the spelling, not the node: {carriers}")
            edges = g.query(
                "MATCH (:Event)-[:aboutSubject]->(s:Subject) RETURN s.id"
            ).result_set
            assert edges == [], (
                f"the rebuild wired an edge to a fabricated target: {edges!r}")
        finally:
            sdk.close()

    def test_a_variant_mention_skips_a_superseded_holder(self, sdk_factory):
        """The writer's question includes its LIVE filter; the spelling union does not.

        `apply_subject_supersession` leaves the buried incarnation carrying the same
        folded key with `status='superseded'`. An unfiltered fold retry attributed a
        variant mention to that dead incarnation — while the SAME record's `subject`
        fold records the name as having no live holder (`resolve_fold_name`) and
        `resolve_or_mint` would mint a new incarnation. One record, two answers.
        """
        sdk = sdk_factory()
        try:
            buried = sdk.create_subject("The Owner")["id"]
            assert sdk.update_entity(buried, status="superseded") is not False
            sdk.create_event("ev-12055-4", "github.issue.open",
                             aboutSubject="the owner")
            g = sdk._get_proj().g
            wired = g.query(
                "MATCH (e:Event)-[:aboutSubject]->(s:Subject) "
                "WHERE e.name = $e RETURN s.id",
                params={"e": "ev-12055-4"}).result_set
            assert [buried] not in wired, (
                "the mention was attributed to the SUPERSEDED incarnation: "
                f"{wired!r} (buried {buried!r})")
        finally:
            sdk.close()

    def test_a_variant_mention_collapses_a_merged_away_carrier(self, sdk_factory):
        """`apply_entity_merge` is non-destructive: BOTH nodes keep a matching key.

        The survivor gains the candidate's folded name in `aliases`, and the
        candidate keeps its own `canonical_name`, so the SPELLING union matches two
        rows. Collapsing onto the survivor is the writer's own rule
        (`_collapse_resolved_holders`); without it live wires and journals a second
        about edge onto the very duplicate the merge retired.
        """
        sdk = sdk_factory()
        try:
            survivor = sdk.create_subject("Acme Corp")["id"]
            candidate = sdk.create_subject("Acme Corporaton")["id"]
            assert candidate != survivor, "fixture drift: no pair to merge"
            sdk.approve_merge(candidate, action="merge")
            sdk.create_event("ev-12055-5", "github.issue.open",
                             aboutSubject="the acme corporaton")
            g = sdk._get_proj().g
            wired = g.query(
                "MATCH (e:Event)-[:aboutSubject]->(s:Subject) "
                "WHERE e.name = $e RETURN s.id",
                params={"e": "ev-12055-5"}).result_set
            assert wired == [[survivor]], (
                "the merge was not collapsed: expected one edge onto the survivor "
                f"{survivor!r}, got {wired!r} (candidate {candidate!r})")
        finally:
            sdk.close()

    def test_an_ambiguous_folded_name_mints_no_third_carrier(self, sdk_factory):
        """Route-then-refuse: two live carriers is not a choice the stub may make.

        An old-shape journal (``targets=[<mention spelling>]``, ``mint_if_absent``)
        replays through ``resolve_structural_target(mint=True)``. With the folded
        name already held twice, the stub must report "cannot address" and write
        NOTHING — minting there opens a THIRD carrier under the same folded key.
        """
        from tortoise.projection.edges import resolve_structural_target

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (:Subject {id:'dup-a', name:'Owner', "
                "                  canonical_name:'owner'}) "
                "CREATE (:Subject {id:'dup-b', name:'The Owner', "
                "                  canonical_name:'owner'})")
            # "OWNER" FOLDS to "owner" — the key both carriers store. (A
            # spelling that folds elsewhere is simply unheld, and the stub mints
            # for it by design; that is what made the first version of this pin
            # non-discriminating.)
            target = resolve_structural_target(
                g, "Subject", "OWNER", "aboutSubject", mint=True)
            assert target is None, (
                "the stub PICKED a carrier of an ambiguous folded name: "
                f"{target!r}")
            carriers = g.query("MATCH (s:Subject) RETURN s.id").result_set
            assert len(carriers) == 2, (
                f"the stub minted a THIRD carrier under a poisoned folded key: {carriers}")
        finally:
            sdk.close()


class TestThe12120Finding2TheSupersessionProbesAskTheWritersKeySpace:
    """The supersession fold resolved its endpoints on the RAW `name`.

    #12120 supersession-probe site (P2): every write that produces a supersession record
    resolves the names on the WRITER's folded union (`name` · `canonical_name` ·
    `aliases`, over the raw and the folded spelling). The fold's probes did not,
    so a fold-equivalent SUCCESSOR was reported `dangling successor` / fed an
    empty Subject lane while the superseded entity stayed `status='live'` and
    search-visible; a fold-equivalent REF was skipped with the wrong reason
    (`ref is not a Subject`, `not found`). Both are the reader-vs-writer key-space
    disagreement this issue is about, at the one place where a miss leaves data
    visibly wrong rather than just absent.

    Every coordinate below is a spelling present in NO stored raw property: the
    graph stores ``name='<Surface Form>'`` and ``canonical_name='<folded>'``, and
    the record names a fold-equivalent variant, so the pre-fix raw probe matches
    nothing (a surface-form coordinate would be GREEN under the revert).
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-f2",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    @staticmethod
    def _row(sdk, label, name):
        return sdk._get_proj().g.query(
            f"MATCH (n:{label} {{name:$n}}) RETURN n.id, n.status, n.supersededBy",
            params={"n": name}).result_set

    def test_a_fold_only_object_successor_folds_instead_of_dangling(self, sdk_factory):
        """`supersedes_by` = a fold-equivalent spelling of a live Object.

        Pre-fix: the Object successor probe (`MATCH (o:Object {name:$sb})`) found
        nothing, the Subject lane found nothing, and the record was skipped as a
        `dangling successor` — `Widget` stayed `live`. Post-fix the probe resolves
        on the writer's key space and the fold applies.
        """
        sdk = sdk_factory()
        try:
            sdk.create_object("Widget", "document")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "the new widget",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            assert skips == [], skips
            assert not any("dangling successor" in m for m in msgs), msgs
            rows = self._row(sdk, "Object", "Widget")
            # #12120: the fold records the SUCCESSOR's own spelling, not the
            # record's. `supersededBy` is read back BY NAME by every consumer
            # that is not this fold — audit's 4b/4c carrier probes,
            # `assembly._probe_visible_successors`, the renderer — and those
            # reads are RAW, while the probe that found this successor is the
            # writer's folded union. Recording "the new widget" would name NO
            # node to them: #5370's harm ("no successor record found" about a
            # live successor), re-created from the reader side.
            assert rows and tuple(rows[0][1:]) == ("superseded", "New Widget"), rows
            # 4b's anchor is an IMPL/NAND edge INTO the superseded Object, so the
            # fixture must give it one: without an input 4b emits NO row and its
            # assertion below is VACUOUS (it passes for any stored value — the
            # trap this pin fell into on its first version).
            _pt = sdk.create_point("statement", "the widget is the factor input")
            _wid = rows[0][0]
            sdk.create_operator("IMPL", _pt["id"], [_wid])
            from tortoise.audit import audit_graph
            issues = audit_graph(sdk._get_proj()).issues
            assert [i for i in issues
                    if i.issue_type == "superseded_by_unresolvable"] == [], (
                "a fold-variant supersession must not read as an unresolvable "
                "supersededBy")
            assert not [i for i in issues
                        if i.issue_type == "superseded_object_active_edges"
                        and "no OTHER Object answers its supersededBy"
                        in i.detail], (
                "4b must still find the successor the fold recorded")
        finally:
            sdk.close()

    def test_a_merge_hop_successor_re_ingest_is_a_silent_dedup(self, sdk_factory):
        """A successor reached through the merge hop stores a NON-fold-equal name.

        `_entity_rows` collapses a merged-away carrier onto its SURVIVOR, so a
        record whose successor is spelled `"Acme"` stores the survivor's own
        `"Acme Corporation"` — a name that folds DIFFERENTLY from the record's
        (`acme corporation` vs `acme`). Without the candidate-set arm of
        `_same_successor`, the keep-first guard reports every re-ingest of that
        unchanged record as a DIVERGENCE plus a `skipped` entry, which is the
        #5365 channel inverted. Reverting `_same_successor` to the raw/fold
        comparison alone reds this test on the `skips == []` assertion.
        """
        sdk = sdk_factory()
        try:
            sdk.create_object("Widget", "document")
            sdk.create_object("Acme Corporation", "document")
            # The post-merge shape `apply_entity_merge` produces: the survivor
            # carries the retired candidate's folded key in `aliases`, so the
            # successor probe resolves "Acme" onto THIS node.
            sdk._get_proj().g.query(
                "MATCH (o:Object {name:'Acme Corporation'}) "
                "SET o.aliases = ['acme']")
            record = {"superseded": "Widget", "supersedes_by": "Acme",
                      "evidence": "test"}
            applied, skips, msgs = self._apply(sdk, [dict(record)])
            assert applied == 1, (applied, skips, msgs)
            rows = self._row(sdk, "Object", "Widget")
            assert rows and tuple(rows[0][1:]) == (
                "superseded", "Acme Corporation"), rows
            # …and the SAME record again: idempotent, silent, no `skipped`.
            applied2, skips2, msgs2 = self._apply(sdk, [dict(record)])
            assert applied2 == 0, (applied2, skips2, msgs2)
            assert skips2 == [], (
                "a re-ingest of an unchanged record was reported as a "
                f"divergence: {skips2!r} (warnings: {msgs2!r})")
            assert not any("conflict" in m for m in msgs2), msgs2
        finally:
            sdk.close()

    def test_a_fold_only_object_ref_folds_instead_of_reporting_not_found(self, sdk_factory):
        """`superseded` = a fold-equivalent spelling of a live Object.

        The ref-side probe (`MATCH (o:Object) WHERE o.name = $ref`) matched nothing,
        so the record was dropped as `ref not found` (and, in the mixed-family
        case, with the wrong reason `ref is not a Subject`).
        """
        sdk = sdk_factory()
        try:
            sdk.create_object("Widget", "document")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "the widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            assert skips == [], skips
            rows = self._row(sdk, "Object", "Widget")
            assert rows and tuple(rows[0][1:]) == ("superseded", "New Widget"), rows
        finally:
            sdk.close()

    def test_a_fold_only_subject_successor_folds_the_subject(self, sdk_factory):
        """The Subject lane's successor probe — the #1423 path.

        A role/team/person successor with no Object of that name reaches the
        Subject lane; a fold-equivalent Subject successor name there was skipped
        as `dangling successor`, leaving the superseded Subject live.
        """
        sdk = sdk_factory()
        try:
            sdk.create_subject("Old Team", "organization")
            sdk.create_subject("New Team", "organization")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Team", "supersedes_by": "the new team",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            assert skips == [], skips
            assert not any("dangling successor" in m for m in msgs), msgs
            rows = self._row(sdk, "Subject", "Old Team")
            assert rows and tuple(rows[0][1:]) == ("superseded", "New Team"), rows
        finally:
            sdk.close()

    def test_a_fold_only_subject_ref_folds_the_subject(self, sdk_factory):
        """The Subject REF probe (`_apply_subject_supersession`).

        The ref is a fold-equivalent spelling of the Subject to supersede. Pre-fix
        the raw probe matched nothing and the record was skipped `ref is not a
        Subject`, so `Old Team` stayed live even though the successor resolved.
        """
        sdk = sdk_factory()
        try:
            sdk.create_subject("Old Team", "organization")
            sdk.create_subject("New Team", "organization")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "the old team", "supersedes_by": "New Team",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            assert skips == [], skips
            assert not any("ref is not a Subject" in m for m in msgs), msgs
            rows = self._row(sdk, "Subject", "Old Team")
            assert rows and tuple(rows[0][1:]) == ("superseded", "New Team"), rows
        finally:
            sdk.close()

    def test_a_collapsed_object_carrier_does_not_shadow_the_subject_lane(
            self, sdk_factory):
        """The `ref carrier's survivor is gone` report must not DROP a live fold.

        The Object REF arm matched a carrier whose merged survivor is gone, so
        `_entity_rows` collapses it away and the arm looks empty. Reporting that
        as "the ref carrier is gone" and skipping is only correct when NO other
        family can take the record. Here the ref is an EXACT `:Subject` and the
        successor name is carried by BOTH families (an Object routes the record
        into the Object lane), so the Subject fold still applies (`#1423`) —
        ungated, the report swallowed it and the Subject stayed `live` and
        search-visible. Measured: without the `_exact_subject_ref_ids` conjunct
        this test reds on `applied == 0` with
        `skips == ["ref carrier's survivor is gone"]`.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("New Widget", "document")   # Object-first routing
            sdk.create_subject("New Widget", "organization")
            sdk.create_subject("Old Team", "organization")  # the EXACT ref
            # An Object whose folded name IS the ref, collapsed onto a survivor
            # the graph no longer holds: the Object ref arm matches the row and
            # then drops it. Its carrier is TERMINAL — a group whose survivor is
            # gone contributes only its LIVE members (review round 10), so a live
            # carrier here would be addressable and would win the ref on the
            # usual Object-first precedence instead of exercising the defer.
            g.query(
                "CREATE (o:Object {id:'r10-gone-ref', name:'Old Team', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Even Older Team', "
                "canonical_name:'old team', "
                "resolved_from:'obj-12120-survivor-deleted'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Team", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            assert skips == [], skips
            assert not any("survivor is gone" in m for m in msgs), msgs
            rows = self._row(sdk, "Subject", "Old Team")
            assert rows and tuple(rows[0][1:]) == (
                "superseded", "New Widget"), rows
        finally:
            sdk.close()

    def test_the_object_lanes_ref_miss_fallback_also_folds_the_successor(
            self, sdk_factory):
        """The SECOND Subject-successor probe (the Object lane's ref-miss fallback).

        The successor name is carried by BOTH families, so the Object-FIRST probe
        routes the record into the Object lane; the ref then resolves as a Subject
        only, and the record is handed to the Subject lane through the fallback's
        own successor probe. That probe was raw too, so a fold-equivalent Subject
        successor left the Subject unsuperseded — the same harm as the lane's main
        probe, at a site only reachable through this routing.
        """
        sdk = sdk_factory()
        try:
            sdk.create_object("New Team", "document")   # Object-first routing
            sdk.create_subject("New Team", "organization")
            sdk.create_subject("Old Team", "organization")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Team", "supersedes_by": "the new team",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            assert skips == [], skips
            rows = self._row(sdk, "Subject", "Old Team")
            assert rows and tuple(rows[0][1:]) == ("superseded", "New Team"), rows
        finally:
            sdk.close()


class TestThe12120Finding1TheAboutAnchorAsksTheWritersKeySpace:
    """The SDK's local `about_entities` attachment loop resolved on the RAW name.

    #12120 about-anchor site (P2): its hosted twin (`hosted_api._execute_commit_writes`)
    and this loop both did `MATCH (o:Object {name:$n})` while every write resolves
    the folded union. A captured claim naming a fold-equivalent spelling of an
    already-registered Object therefore matched NOTHING and the `aboutObject` edge
    was silently dropped — no warning, no skip record, the attribution simply
    absent. (The hosted twin's own pin lives with its harness:
    `tests/test_commit_endpoint.py::TestThe12120HostedAboutObjectAnchor`.)
    """

    def test_capture_attaches_a_fold_colliding_about_name(
            self, tmp_path, monkeypatch):
        """A captured point naming `the widget` links to the Object `Widget`.

        The extractor is stubbed with a REAL `execute_embed` payload (the same
        seam `tests/test_status_folds_2729.py` uses), so this walks the capture
        path's own spelling rather than a hand-built dict. The entity is created
        in the SAME capture (entities precede points), which is the order the
        anchor depends on.
        """
        import tortoise.extractor_v2 as ev2
        from tortoise.sdk import TortoiseSDK

        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        embed = {
            "entities": [{"name": "Widget", "kind": "core:thing",
                          "lifecycle": "created", "supersedes": None,
                          "note": None}],
            "events": [],
            "points": [{"content": "the widget ships in Q3",
                        "pointKind": "statement",
                        "about_entities": ["the widget"]}],
            "operators": [], "chain_notes": [], "link_before_create": [],
        }
        payload = ev2.execute_embed(embed, {}, session_id="s-12120-f1")["payload"]
        monkeypatch.setattr(
            ev2, "extract_session_v2",
            lambda *a, **k: {"payload": payload, "errors": [], "warnings": []})

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            sdk._extract_session_v2(
                [{"role": "user", "content": "the widget ships in Q3"}],
                "s-12120-f1", "2026-01-01T00:00:00Z")
            g = sdk._get_proj().g
            obj = g.query("MATCH (o:Object {name:'Widget'}) RETURN o.id").result_set
            assert obj and obj[0][0], "fixture drift: the capture wrote no Object"
            linked = g.query(
                "MATCH (:Point)-[:aboutObject]->(o:Object {name:'Widget'}) "
                "RETURN count(o)").result_set[0][0]
            assert linked >= 1, (
                "the captured claim's fold-equivalent about name dropped its "
                "`aboutObject` edge (the anchor resolved on the raw name)")
        finally:
            sdk.close()


_PIPELINE_DECL = {
    "defaultStage": "stg_contact",
    "stages": [
        {"id": "stg_contact", "label": "Contact", "order": 1, "slot": "entry"},
        {"id": "stg_discussion", "label": "Discussion", "order": 2,
         "slot": "active"},
        {"id": "stg_won", "label": "Won", "order": 3, "slot": "terminal",
         "outcome": "won"},
    ],
}


class TestThe12120ReviewRound6:
    """Round 6: the never-guess COUNT must ask the writer's (LIVE-only) question,
    and a duplicate id must not make the merge hop order-dependent.

    `_live_holders_same_entity` is live-only, so the create door legitimately mints
    a second live node under one folded key beside a SUPERSEDED ex-incarnation.
    Counting rows without a status filter turned that dead incarnation into a
    "competing carrier" and refused a fold that used to apply — the same
    reader/writer disagreement, in the other direction.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r6",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_terminal_ex_incarnation_does_not_block_a_live_fold(self, sdk_factory):
        """A superseded node is not a competing carrier."""
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            dead = sdk.create_object("Acme Corp", "document")["id"]
            sdk.update_entity(dead, status="superseded")
            live = sdk.create_object("ACME CORP", "document")["id"]
            assert live != dead
            g.query("MATCH (o:Object {id:$id}) SET o.canonical_name='acme corp'",
                    params={"id": live})
            sdk.create_object("Gamma Ltd", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "ACME CORP", "supersedes_by": "Gamma Ltd",
                 "evidence": "test"}])
            assert applied == 1, (
                "a SUPERSEDED ex-incarnation was counted as a competing "
                f"carrier: {(applied, skips, msgs)!r}")
            row = g.query("MATCH (o:Object {id:$id}) RETURN o.status",
                          params={"id": live}).result_set
            assert row and row[0][0] == "superseded", row
            dead_row = g.query("MATCH (o:Object {id:$id}) RETURN o.supersededBy",
                               params={"id": dead}).result_set
            assert dead_row and dead_row[0][0] != "Gamma Ltd", dead_row
        finally:
            sdk.close()

    def test_a_terminal_ex_incarnation_does_not_block_a_live_subject_fold(
            self, sdk_factory):
        """The Subject twin of the same count."""
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            dead = sdk.create_subject("Acme Corp", "organization")["id"]
            g.query("MATCH (s:Subject {id:$id}) SET s.status='superseded', "
                    "s.supersededBy='Old Corp'", params={"id": dead})
            live = sdk.create_subject("ACME CORP", "organization")["id"]
            g.query("MATCH (s:Subject {id:$id}) SET s.canonical_name='acme corp'",
                    params={"id": live})
            sdk.create_subject("Gamma Ltd", "organization")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "ACME CORP", "supersedes_by": "Gamma Ltd",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            row = g.query("MATCH (s:Subject {id:$id}) RETURN s.status",
                          params={"id": live}).result_set
            assert row and row[0][0] == "superseded", row
        finally:
            sdk.close()

    def test_an_id_less_object_refusal_is_reported(self, sdk_factory):
        """The id-less Object drop reaches `skipped`, like its Subject twin."""
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (o:Object {name:'Legacy Thing', objectKind:'document', "
                "status:'live', canonical_name:'legacy thing'})")
            sdk.create_object("New Thing", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Legacy Thing", "supersedes_by": "New Thing",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips, (
                "the id-less Object refusal never reached the caller's skipped "
                f"list: {skips!r} (warnings: {msgs!r})")
            assert any("id-less" in str(s.get("reason", "")) for s in skips), skips
        finally:
            sdk.close()

    def test_a_nameless_id_less_object_refusal_is_reported(self, sdk_factory):
        """Round 7: the widened arm makes this branch REACHABLE, so it must report.

        Pre-widening the name arm was `o.name = $ref`, which cannot match a NULL
        name, so a node carrying only a folded key never arrived here; the arm now
        matches `canonical_name`/`aliases`, so a legacy Object with neither id nor
        name reaches the refusal — and dropping it silently would be the #5365 gap
        this lane closes everywhere else.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (o:Object {canonical_name:'legacy widget', "
                "status:'live'})")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Legacy Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips, (
                "the nameless id-less refusal never reached the caller's "
                f"skipped list: {skips!r} (warnings: {msgs!r})")
        finally:
            sdk.close()

    def test_a_duplicate_id_with_one_pointer_is_refused_either_way(
            self, sdk_factory):
        """The merge hop must not make the verdict depend on row order.

        Two nodes claim one id and exactly ONE carries `resolved_from`;
        `follow_resolved_from` reads a single row for the id, so the group key —
        and with it whether the never-guess refusal fires at all — used to depend
        on which row the backend returned first.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (s:Object {id:'r6-survivor', name:'Kept', "
                "objectKind:'document', status:'live'}) "
                "CREATE (a:Object {id:'r6-dup', name:'Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget', resolved_from:'r6-survivor'}) "
                "CREATE (b:Object {id:'r6-dup', name:'Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget'})")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (
                "a duplicated id was collapsed through the merge hop: "
                f"{(applied, skips, msgs)!r}")
            assert skips, f"the never-guess refusal did not report: {skips!r}"
            statuses = g.query(
                "MATCH (o:Object {id:'r6-dup'}) RETURN o.status").result_set
            assert all(r[0] != "superseded" for r in statuses), statuses
        finally:
            sdk.close()


class TestThe12120ReviewRound8:
    """Round 8: the visible-successor gate needed TWO levels, not one.

    `_entity_rows` deliberately collapses to the SURVIVOR's row, because folding
    onto a retired incarnation is the defect. But `has_visible_distinct` is a
    NODE-level question — `recall_state` and the render probe show a live carrier
    even when its entity's survivor has been retired. Asking it of the survivor
    alone refused a fold that the pre-widening raw arm applied, because that arm
    matched the live carrier directly.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r8",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_live_carrier_authorizes_a_fold_its_retired_survivor_cannot(
            self, sdk_factory):
        """Survivor retired, carrier live: the carrier is what recall_state shows."""
        from tortoise.entity_resolver import apply_entity_merge

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            survivor = sdk.create_object("Widget", "document")["id"]
            # The create door folds "The Widget" onto the live survivor, so the
            # second live node is minted directly (the door's own dedupe is what
            # makes a second live carrier legitimate at all — see round 6).
            carrier = "r8-carrier"
            g.query(
                "CREATE (o:Object {id:'r8-carrier', name:'The Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget'})")
            apply_entity_merge(sdk, candidate_id=carrier, survivor_id=survivor,
                               method="fuzzy", similarity=0.9)
            # Retire the SURVIVOR only — the carrier stays live, so
            # `recall_state` still shows "The Widget".
            sdk.update_entity(survivor, status="superseded")
            target = sdk.create_object("Old Co", "document")["id"]
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "The Widget",
                 "evidence": "test"}])
            assert applied == 1, (
                "a LIVE carrier was discarded in favour of its retired "
                f"survivor: {(applied, skips, msgs)!r}")
            row = g.query("MATCH (o:Object {id:$id}) RETURN o.status",
                          params={"id": target}).result_set
            assert row and row[0][0] == "superseded", row
        finally:
            sdk.close()

    def test_a_gone_survivor_on_the_terminal_target_path_refuses_not_raises(
            self, sdk_factory):
        """Round 9's `(None, key, members)` sentinel also reaches the TERMINAL arm.

        `_entity_rows` reports `(None, key, members)` for a group whose
        `resolved_from` survivor has been deleted (round 9), and round 9 adapted
        the duplicate-id fetch for it. The terminal-target arm was the SECOND
        consumer of the representative list, and it unpacked the representatives
        as `(id, name, status)` triples — so this shape raised `TypeError:
        cannot unpack non-iterable NoneType` out of `apply_supersessions`,
        aborting the WHOLE batch with the record in neither `skipped` nor a warn.
        On the hosted §6b path that is a deterministic payload which can never be
        ingested; on capture it drops every supersession in the payload behind a
        misleading warning.

        The fix reads the candidate spellings from the GROUPS (the carriers), so
        a sentinel cannot be unpacked at all. Reverting to the representative
        unpack reds this on the raise, before any assertion.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            # Terminal target: the re-record's DIVERGENT arm is the one that
            # reads the candidate spellings.
            sdk.create_object("Widget", "document")
            sdk.update_entity(
                g.query("MATCH (o:Object {name:'Widget'}) RETURN o.id")
                .result_set[0][0],
                status="superseded", supersededBy="Older Widget")
            # A LIVE successor carrier whose merged survivor is gone — the
            # round-9 sentinel shape, reached through the supported merge door.
            g.query(
                "CREATE (c:Object {id:'r11-gone-co', name:'Gone Co', "
                "objectKind:'document', status:'live', "
                "canonical_name:'gone co', "
                "resolved_from:'obj-12120-survivor-deleted'})")
            sdk.create_object("Alpha", "document")
            sdk.create_object("Later Co", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "Gone Co",
                 "evidence": "test"},
                {"superseded": "Alpha", "supersedes_by": "Later Co",
                 "evidence": "test"}])
            # The bad-shaped record is a REFUSAL: it reports and the batch goes
            # on, which is the whole contract (`apply_supersessions` promises one
            # `{ref, successor, reason}` dict for every record it did not apply).
            assert applied == 1, (applied, skips, msgs)
            assert [s["ref"] for s in skips] == ["Widget"], skips
            assert skips[0]["reason"] == "terminal object target", skips
            rows = g.query(
                "MATCH (o:Object {name:'Alpha'}) "
                "RETURN o.id, o.status, o.supersededBy").result_set
            assert rows and tuple(rows[0][1:]) == (
                "superseded", "Later Co"), (
                "the batch stopped at the bad record instead of refusing it: "
                f"{(applied, skips, msgs)!r}")
        finally:
            sdk.close()

    def test_a_retired_carrier_alone_still_refuses(self, sdk_factory):
        """The other side of the same rule: nothing visible under the name."""
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (o:Object {id:'r8-dead-carrier', name:'Ghost Widget', "
                "objectKind:'document', status:'superseded', "
                "canonical_name:'ghost widget'})")
            sdk.create_object("Old Co", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "Ghost Widget",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert [s["reason"] for s in skips] == ["no visible successor"], skips
        finally:
            sdk.close()


class TestThe12120ReviewRound9:
    """Round 9: the group-aware shape must survive corruption, a GONE survivor
    must not hide a live carrier, and a recorded spelling must resolve by name."""

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r9",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_duplicate_successor_id_applies_instead_of_raising(self, sdk_factory):
        """A duplicate SUCCESSOR id is not a refusal, and must never raise.

        The round-9 fix here was "a corrupt graph must be REFUSED, never an
        exception that aborts the rest of the batch". Round 15 showed the other
        half: on THIS fixture the fetched duplicate claimants are themselves live,
        id-bearing and distinct from the target, so they are a visible successor —
        the fold APPLIES. What must not happen is an exception, and what must not
        happen again is the sibling fixture's outcome (terminal claimants, where a
        fetch that discarded the carriers refused a fold `main` applied — see
        `TestThe12120ReviewRound15`). The ref-side duplicate-id refusal
        (`ambiguous object id`) is a different shape, pinned in
        `TestThe12120ReviewRound14`.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            # Two nodes claim one id, and neither is among the probe's rows for
            # the folded key — the ambiguity is reached only through the
            # survivor FETCH. A live carrier points at it.
            g.query(
                "CREATE (a:Object {id:'r9-dup', name:'Duplicated', "
                "objectKind:'document', status:'live'}) "
                "CREATE (b:Object {id:'r9-dup', name:'Duplicated', "
                "objectKind:'document', status:'live'}) "
                "CREATE (c:Object {id:'r9-carrier', name:'Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget', resolved_from:'r9-dup'})")
            sdk.create_object("Old Co", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "Widget",
                 "evidence": "test"}])
            # The record is NOT ambiguous: two live claimants of one id are a
            # visible successor in their own right, and `r9-carrier` is another, so
            # the fold must APPLY — and nothing may raise out of the batch. ⛔ This
            # fixture does NOT discriminate the round-15 `[*group, dup]` fix (the
            # claimants are live, so the gate applies with or without the
            # carriers); `TestThe12120ReviewRound15`'s terminal-claimant pin is
            # the one that does.
            assert applied == 1, (applied, skips, msgs)
            state = g.query("MATCH (o:Object {name:'Old Co'}) "
                            "RETURN o.status").result_set
            assert state and state[0][0] == "superseded", state
            # The rest of a batch must still be processed (no abort).
            applied2, skips2, msgs2 = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "Widget",
                 "evidence": "test"},
                {"superseded": "Missing Thing", "supersedes_by": "Widget",
                 "evidence": "test"}])
            assert applied2 + len(skips2) >= 1, (applied2, skips2, msgs2)
        finally:
            sdk.close()

    def test_a_gone_survivor_does_not_hide_its_live_carrier(self, sdk_factory):
        """`delete_entity(survivor)` leaves the carrier live and addressable."""
        from tortoise.entity_resolver import apply_entity_merge

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            survivor = sdk.create_object("Widget", "document")["id"]
            carrier = "r9-carrier-gone"
            g.query(
                "CREATE (o:Object {id:'r9-carrier-gone', name:'The Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget'})")
            apply_entity_merge(sdk, candidate_id=carrier, survivor_id=survivor,
                               method="fuzzy", similarity=0.9)
            assert sdk.delete_entity(survivor)
            target = sdk.create_object("Old Co", "document")["id"]
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "The Widget",
                 "evidence": "test"}])
            assert applied == 1, (
                "a live carrier was hidden by its deleted survivor: "
                f"{(applied, skips, msgs)!r}")
            row = g.query("MATCH (o:Object {id:$id}) RETURN o.status",
                          params={"id": target}).result_set
            assert row and row[0][0] == "superseded", row
        finally:
            sdk.close()

    def test_a_recorded_spelling_resolves_when_a_merged_carrier_exists(
            self, sdk_factory):
        """Two carriers under one folded key still record a NAME, not the raw input."""
        from tortoise.entity_resolver import apply_entity_merge

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            survivor = sdk.create_object("Widget", "document")["id"]
            g.query(
                "CREATE (o:Object {id:'r9-merged-carrier', name:'The Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget'})")
            apply_entity_merge(sdk, candidate_id="r9-merged-carrier",
                               survivor_id=survivor, method="fuzzy",
                               similarity=0.9)
            target = sdk.create_object("Old Co", "document")["id"]
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "WIDGET",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            rows = g.query("MATCH (o:Object {id:$id}) RETURN o.supersededBy",
                           params={"id": target}).result_set
            stored = rows[0][0] if rows else None
            raw = g.query("MATCH (o:Object) WHERE o.name = $n RETURN o.id",
                          params={"n": stored}).result_set
            assert raw, (
                f"supersededBy {stored!r} names NO node — every raw reader of "
                f"the fold (audit 4b/4c, assembly, the renderer) reports the "
                f"successor as missing (applied={applied}, skips={skips})")
            from tortoise.audit import audit_graph
            issues = audit_graph(sdk._get_proj()).issues
            assert [i for i in issues
                    if i.issue_type == "superseded_by_unresolvable"] == [], (
                "the recorded spelling must not read as unresolvable")
        finally:
            sdk.close()


class TestThe12120ReviewRound10:
    """Round 10: the REF side of the gone-survivor rule.

    Round 9 fixed the successor side (a gone survivor must not hide its live
    carrier). The REF probe kept dropping the group, so a record whose REF was
    such a carrier was skipped as "no addressable carrier" — about a node that
    sits in the graph with an id and a live status — while the raw arm applied
    it. The carrier must be addressable; a TERMINAL member still must not be.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r10",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def _orphaned_live_carrier(self, sdk, label="Object"):
        """A live carrier whose `resolved_from` survivor has been DELETED."""
        from tortoise.entity_resolver import apply_entity_merge

        g = sdk._get_proj().g
        if label == "Object":
            survivor = sdk.create_object("Widget", "document")["id"]
            g.query(
                "CREATE (o:Object {id:'r10-carrier', name:'The Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget'})")
            apply_entity_merge(sdk, candidate_id="r10-carrier",
                               survivor_id=survivor, method="fuzzy",
                               similarity=0.9)
            sdk.delete_entity(survivor)
            return "r10-carrier"
        survivor = sdk.create_subject("Acme Corp", "organization")["id"]
        g.query(
            "CREATE (s:Subject {id:'r10-subj-carrier', name:'The Acme Corp', "
            "subjectKind:'organization', status:'live', "
            "canonical_name:'acme corp'})")
        apply_entity_merge(sdk, candidate_id="r10-subj-carrier",
                           survivor_id=survivor, method="fuzzy",
                           similarity=0.9)
        sdk.delete_entity(survivor)
        return "r10-subj-carrier"

    def test_a_live_ref_carrier_beside_a_gone_survivor_is_addressable(
            self, sdk_factory):
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            carrier = self._orphaned_live_carrier(sdk)
            sdk.create_object("Gadget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "The Widget", "supersedes_by": "Gadget",
                 "evidence": "test"}])
            assert applied == 1, (
                "a LIVE ref carrier was refused because its survivor is gone: "
                f"{(applied, skips, msgs)!r}")
            row = g.query("MATCH (o:Object {id:$id}) RETURN o.status",
                          params={"id": carrier}).result_set
            assert row and row[0][0] == "superseded", row
        finally:
            sdk.close()

    def test_a_live_subject_ref_carrier_beside_a_gone_survivor_is_addressable(
            self, sdk_factory):
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            carrier = self._orphaned_live_carrier(sdk, label="Subject")
            sdk.create_subject("Other Team", "organization")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "The Acme Corp", "supersedes_by": "Other Team",
                 "evidence": "test"}])
            assert applied == 1, (
                f"the Subject twin refused a live carrier: "
                f"{(applied, skips, msgs)!r}")
            row = g.query("MATCH (s:Subject {id:$id}) RETURN s.status",
                          params={"id": carrier}).result_set
            assert row and row[0][0] == "superseded", row
        finally:
            sdk.close()


class TestThe12120ReviewRound3:
    """The three defects review round 3 found in the round-2 fix itself.

    Each is a rule that was right in the direction it was written for and wrong in
    the other: the family defer tested exactness on ONE side only, the collapse's
    per-row sentinel was recognised by a string PREFIX an entity id may itself
    carry, and the Subject lane's terminal refusal was the one drop that never
    reached the caller's `skipped` channel.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r3",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_fold_only_subject_ref_does_not_outvote_a_fold_only_object_ref(
            self, sdk_factory):
        """A fold-only TIE keeps the Object lane (its recorded precedence).

        Round 3: the defer tested exactness only on the Object side, so when the
        REF matched a fold-equal Object AND a fold-equal Subject, the Subject
        won — the documented Object-first lane choice inverted by a
        non-exactness, with nothing recording that a family was picked on a tie.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_subject("ACME", "organization")
            sdk.create_subject("New Acme", "organization")
            g.query(
                "CREATE (o:Object {id:'obj-12120-tie', name:'Acme!', "
                "objectKind:'document', status:'live', "
                "canonical_name:'acme'})")
            sdk.create_object("New Acme Thing", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "acme", "supersedes_by": "New Acme Thing",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            obj = g.query("MATCH (o:Object {id:'obj-12120-tie'}) "
                          "RETURN o.status").result_set
            assert obj and obj[0][0] == "superseded", obj
            subj = g.query("MATCH (s:Subject {name:'ACME'}) "
                           "RETURN s.status").result_set
            assert subj and subj[0][0] != "superseded", (
                f"a fold-only Subject outvoted the fold-only Object: {subj}")
        finally:
            sdk.close()

    def test_a_terminal_subject_target_is_reported(self, sdk_factory):
        """The Subject lane's keep-first refusal must reach `skipped` (#5365).

        Round 3: the divergent terminal branch warned and `return False`d, so a
        record the caller could not see in `applied` also never appeared in
        `skipped` — the one refusal shape that vanishes.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_subject("Acme Corp", "organization")
            sdk.create_subject("Other Corp", "organization")
            sdk.create_subject("New Acme", "organization")
            g.query(
                "MATCH (s:Subject {name:'Acme Corp'}) "
                "SET s.status='superseded', s.supersededBy='Other Corp'")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Acme Corp", "supersedes_by": "New Acme",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips, (
                "the terminal-target refusal never reached the caller's "
                f"skipped list: {skips!r} (warnings: {msgs!r})")
            row = g.query("MATCH (s:Subject {name:'Acme Corp'}) "
                          "RETURN s.supersededBy").result_set
            assert row and row[0][0] == "Other Corp", row
        finally:
            sdk.close()

    def test_a_carrier_key_that_looks_like_a_row_key_still_fetches_its_survivor(
            self, sdk_factory):
        """An entity id may itself start with `row:` — the sentinel is not a prefix.

        Round 3: the per-row sentinel was recognised with `key.startswith("row:")`,
        so a carrier pointing at a survivor whose real id starts with `row:`
        skipped the fetch and fell back to `group[0]` — the retired CARRIER — and
        the fold landed on exactly the node the collapse exists to avoid.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (s:Object {id:'row:survivor-12120', "
                "name:'Totally Different', objectKind:'document', "
                "status:'live', canonical_name:'other'}) "
                "CREATE (c:Object {id:'obj-12120-carrier', name:'Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget', "
                "resolved_from:'row:survivor-12120'})")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            rows = g.query(
                "MATCH (o:Object) WHERE o.id IN "
                "['row:survivor-12120','obj-12120-carrier'] "
                "RETURN o.id, o.status, o.supersededBy").result_set
            got = {r[0]: (r[1], r[2]) for r in rows}
            assert got.get("row:survivor-12120") == ("superseded", "New Widget"), (
                f"the fold landed on the retired carrier: {got}")
            assert got.get("obj-12120-carrier")[0] != "superseded", got
        finally:
            sdk.close()


    def test_a_duplicated_survivor_id_is_refused_through_the_fetch(
            self, sdk_factory):
        """Round 5: two nodes claiming the SURVIVOR's id are reached via the fetch.

        Neither duplicate is among the probe's rows, so the `_own_counts` pre-pass
        cannot see them; the fetch used to take `fetched[0]`, i.e. pick the
        representative by backend row order — the same order-dependent decision
        #2164 P2-2 removed (and here it decides whether ONE duplicate or BOTH are
        retired by the id-keyed fold).
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (a:Object {id:'dup-survivor-12120', name:'Alpha', "
                "objectKind:'document', status:'live'}) "
                "CREATE (b:Object {id:'dup-survivor-12120', name:'Beta', "
                "objectKind:'document', status:'live'}) "
                "CREATE (c:Object {id:'obj-12120-ref', name:'Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget', "
                "resolved_from:'dup-survivor-12120'})")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (
                "a duplicated survivor id was collapsed into one entity by the "
                f"fetch: {(applied, skips, msgs)!r}")
            assert skips, (
                f"the never-guess refusal did not report itself: {skips!r}")
            statuses = g.query(
                "MATCH (o:Object {id:'dup-survivor-12120'}) RETURN o.status"
            ).result_set
            assert all(r[0] != "superseded" for r in statuses), statuses
        finally:
            sdk.close()

    def test_a_TERMINAL_carrier_with_a_deleted_survivor_is_never_folded(
            self, sdk_factory):
        """Round 5: an unfetchable survivor must not resurrect a RETIRED carrier.

        The `resolved_from` pointer can outlive its target (the survivor was
        deleted). Falling back to the group's own row folded onto exactly the
        incarnation the merge retired; such a group contributes its LIVE members
        only (review round 10), and a carrier that is already terminal is dropped
        by `_ref_candidates`' terminal filter, so the ref probe has no
        addressable carrier and refuses `"ref carrier's survivor is gone"`.
        (Keep-first would refuse it too, but only once the ref resolved to one
        row — it does not here, which is why the pin asserts the REASON and not
        just the outcome: a regression that kept terminal members would satisfy
        `applied == 0` through the `"terminal object target"` arm and this pin
        would not notice.)

        (Round 10 is the other half of the same rule: a carrier that is still
        LIVE beside a gone survivor IS addressable, and is folded —
        `TestThe12120ReviewRound10`. This pin's fixture is therefore explicitly
        terminal; before round 10 it said `live` and passed only because the whole
        group was dropped regardless of status, which is the refusal round 10
        removed.)
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (c:Object {id:'obj-12120-orphan', name:'Old Team', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Even Older Team', "
                "canonical_name:'old team', resolved_from:'gone-survivor-12120'})")
            sdk.create_object("New Team", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Team", "supersedes_by": "New Team",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert [s.get("reason") for s in skips] == [
                "ref carrier's survivor is gone"], (
                "the refusal came from a different arm than the terminal filter "
                f"this pin names: {skips!r} (warnings: {msgs!r})")
            row = g.query(
                "MATCH (o:Object {id:'obj-12120-orphan'}) "
                "RETURN o.status, o.supersededBy"
            ).result_set
            assert row and row[0][1] == "Even Older Team", (
                f"the fold landed on the retired carrier: {row}")
        finally:
            sdk.close()

    def test_a_terminal_object_target_is_reported(self, sdk_factory):
        """Round 5: the Object lane's divergent keep-first refusal reports too.

        Its Subject twin (`\"terminal subject target\"`) already did; the Object
        arm warned and `continue`d, so a dropped record could not be told from an
        applied one through the caller's `skipped` channel (#5365).
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (o:Object {id:'obj-12120-term', name:'Widget', "
                "objectKind:'document', status:'superseded', "
                "canonical_name:'widget', supersededBy:'Older Widget'})")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips, (
                "the terminal-target refusal never reached the caller's "
                f"skipped list: {skips!r} (warnings: {msgs!r})")
            row = g.query(
                "MATCH (o:Object {id:'obj-12120-term'}) RETURN o.supersededBy"
            ).result_set
            assert row and row[0][0] == "Older Widget", row
        finally:
            sdk.close()


class TestThe12120Finding4TheUnpinnedMigratedReaders:
    """Pins for the seven migrated callers the class above claimed but did not hold.

    #12120 coverage-gap finding (P3): `TestThe12120ReadersOutsideTheEntityModule`'s docstring
    said every raw-key-space caller now goes through
    `resolve_entity_id_same_entity`, and pinned two of nine. A revert of any of the
    other seven was invisible. Each pin below uses a **folded-only** coordinate —
    an article/case variant present in no stored raw property — so it is RED under
    the raw resolver (a surface-form coordinate passes under the revert, which is
    how the first version of these pins was measured vacuous).
    """

    def test_provenance_resolves_a_fold_colliding_author(self, sdk_factory):
        """`provenance` author chain (`sdk.py`): `authoredBy` is a NAME here.

        The historical read was case-insensitive over the raw `name`, so a
        fold-equivalent spelling with a leading article still missed and the chain
        reported `subject: None` — "who decided this?" answered "nobody".
        """
        sdk = sdk_factory()
        try:
            author = sdk.create_subject("Acme Corp", "naturalPerson")["id"]
            pid = sdk.create_point("statement", "the spec is approved",
                                   authoredBy="the acme corp")["id"]
            chain = sdk.provenance(pid)
            assert chain.get("subject"), (
                "the fold-equivalent author resolved to no Subject: "
                f"{chain.get('subject')!r}")
            assert chain["subject"]["id"] == author, chain["subject"]
        finally:
            sdk.close()

    def test_compute_reputation_resolves_a_fold_colliding_subject(self, sdk_factory):
        """`compute_reputation` (`sdk.py`): a fold-only coordinate scored 0.5/0.

        The vacuous posterior is indistinguishable from "this subject has no
        outcomes" — the same absent-vs-unanswerable conflation #12047 removes.
        """
        sdk = sdk_factory()
        try:
            sdk.create_subject("Frank", "analyst")
            proj = sdk._get_proj()
            proj.apply({"type": "EventRecorded", "id": "ev-f4-rep",
                        "eventId": "ev-f4-rep", "eventKind": "analysis",
                        "subject": "Frank"})
            point = sdk.create_point("observation", "frank's claim")["id"]
            proj.g.query(
                "MATCH (e:Event {eventId:$eid}), (p:Point {id:$pid}) "
                "CREATE (e)-[:IMPL]->(p)",
                params={"eid": "ev-f4-rep", "pid": point})
            rep = sdk.compute_reputation("the frank")
            assert rep["total_events"] == 1, (
                f"the fold-equivalent subject scored nothing: {rep}")
            assert rep["outcomes"][0]["content"] == "frank's claim"
        finally:
            sdk.close()

    def test_get_owned_entities_resolves_a_fold_colliding_owner(self, sdk_factory):
        """`get_owned_entities` (`sdk.py`): a fold-only owner read back `[]`.

        An empty list reads as "this subject owns nothing", which is the
        governance answer a caller acts on — so a key-space miss must not produce
        it.
        """
        sdk = sdk_factory()
        try:
            owner = sdk.create_subject("Owner X", "organization")["id"]
            point = sdk.create_point("observation", "an owned thing")["id"]
            sdk._get_proj().create_owned_by(point, owner)
            owned = sdk.get_owned_entities("the owner x")
            assert any(e.get("id") == point for e in owned), (
                f"the fold-equivalent owner owned nothing: {owned}")
        finally:
            sdk.close()

    def test_subject_binding_resolves_a_fold_colliding_slot_target(self, sdk_factory):
        """`subject_binding._resolve_target`: a fold-only slot target was UNBOUND.

        The slot is then journaled as `unresolved` — a claim's declared subject
        silently not bound — which is the refusal path reserved for genuinely
        unresolvable names.
        """
        from tortoise.subject_binding import _resolve_target

        sdk = sdk_factory()
        try:
            target = sdk.create_subject("Acme Corp", "organization")["id"]
            resolved, kind = _resolve_target(
                sdk._get_proj(), "Subject", "the acme corp")
            assert resolved == target, (
                f"the fold-equivalent slot target was left unbound: {resolved!r}")
            assert kind == "organization", kind
        finally:
            sdk.close()

    def test_pipeline_current_stage_reads_a_fold_colliding_ref(self, sdk_factory):
        """`pipeline.current_stage`: a fold-only ref answered `{}`.

        `{}` reads as "this Object has no stage" — a wrong answer about a live
        Object the write door addresses by the same spelling.
        """
        from tortoise import pipeline as P

        sdk = sdk_factory()
        try:
            sdk.create_object("Deal R", "venture:deal")
            P.advance_stage(sdk, "Deal R", "venture:deal", "stg_contact",
                            declared=_PIPELINE_DECL)
            cur = P.current_stage(sdk._get_proj().g, "the deal r")
            assert cur.get("stageId") == "stg_contact", (
                f"the fold-equivalent ref read no stage: {cur}")
        finally:
            sdk.close()

    def test_pipeline_resolve_object_resolves_a_fold_colliding_ref(self, sdk_factory):
        """`pipeline._resolve_object`: a fold-only ref returned `None`.

        A stage move addressed by such a ref would then be refused as an unknown
        Object even though the same spelling is writable through the create door.
        """
        from tortoise import pipeline as P

        sdk = sdk_factory()
        try:
            sdk.create_object("Deal S", "venture:deal")
            P.advance_stage(sdk, "Deal S", "venture:deal", "stg_contact",
                            declared=_PIPELINE_DECL)
            resolved = P._resolve_object(sdk._get_proj().g, "the deal s")
            assert resolved is not None, (
                "the fold-equivalent ref resolved to no Object")
            assert resolved[1]["name"] == "Deal S", resolved[1]
        finally:
            sdk.close()

    def test_topic_stage_1a_refuses_a_fold_colliding_pair(self, sdk_factory):
        """`topic_summarization` stage 1a: the seed NAME was resolved on the raw key.

        Two live carriers of ONE folded key (raw `zq5510solo`, raw
        `The zq5510solo`, both `canonical_name='zq5510solo'`) make one entity in
        the writer's key space. The raw resolver resolved each enumerated name to
        ITSELF, so both carriers' Points seeded the neighborhood — the identity
        union this site's docstring forbids, one spelling-pair short of the
        same-raw-name case it already refused. Post-fix each name refuses and the
        neighborhood degrades instead of folding two identities.
        """
        from tortoise.topic_summarization import _retrieve_topic_neighborhood

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (o:Object {id:'obj-f4-a', name:'zq5510solo', "
                "objectKind:'org', status:'live', canonical_name:'zq5510solo'}) "
                "CREATE (o2:Object {id:'obj-f4-b', name:'The zq5510solo', "
                "objectKind:'org', status:'live', canonical_name:'zq5510solo'})")
            pa = sdk.create_point("statement", "unrelated alpha claim")["id"]
            pb = sdk.create_point("statement", "unrelated beta claim")["id"]
            for pid, oid in ((pa, "obj-f4-a"), (pb, "obj-f4-b")):
                g.query(
                    "MATCH (p:Point {id:$pid}), (o:Object {id:$oid}) "
                    "CREATE (p)-[:aboutObject]->(o)",
                    params={"pid": pid, "oid": oid})
            seeds, _meta, seed_count = _retrieve_topic_neighborhood(
                g, topic="zq5510solo", max_seeds=50)
            assert seed_count == 0, (
                "two carriers of ONE folded key seeded the neighborhood as two "
                f"identities: seeds={seeds!r}")
        finally:
            sdk.close()


class TestThe12120Finding2CollapsesMergedCarriersFirst:
    """The widened probe must ask the WRITER's entity question, and the writer
    COLLAPSES a merged-away carrier.

    `apply_entity_merge` is non-destructive: the retired candidate stays LIVE and
    keeps its own `canonical_name`, so a pair the queue exists to merge (the two
    nodes' folded keys are EQUAL — that is what `scan_entities` matches on) holds
    the SAME folded name on two nodes, one pointer apart. Widening the
    supersession probes without the collapse changed behaviour in both
    directions at once (measured on the first version of this fix):

    * the SUCCESSOR probe saw two "candidates" where there is one entity, so the
      Object lane's self-supersession guard (`all_candidates_are_target`) stopped
      firing and a target could be folded onto ITS OWN name — the burial the guard
      exists to prevent;
    * the REF probe saw two "carriers" where there is one entity and REFUSED a
      record that applied before the widening — the reader disagreeing with the
      writer in the opposite direction, which is the class #12120 removes.

    Both are one root: the probes must group by ENTITY (the `resolved_from` hop),
    not by node. A pair with NO merge pointer, or two hops to two DIFFERENT
    survivors, is still two entities and still refuses — the never-guess
    discipline is unchanged.
    """

    @staticmethod
    def _merged_pair(sdk):
        """A live survivor + a live merged-away carrier sharing ONE folded key."""
        survivor = sdk.create_object("Widget", "document")["id"]
        g = sdk._get_proj().g
        g.query(
            "CREATE (o:Object {id:'obj-12120-dup', name:'The Widget', "
            "objectKind:'document', status:'live', canonical_name:'widget'})")
        from tortoise.entity_resolver import apply_entity_merge

        apply_entity_merge(sdk, candidate_id="obj-12120-dup",
                           survivor_id=survivor, method="exact", similarity=1.0)
        carrier = g.query(
            "MATCH (o:Object {id:'obj-12120-dup'}) "
            "RETURN o.status, o.resolved_from").result_set
        assert carrier and carrier[0][1] == survivor, (
            f"fixture drift: no merge pointer on the carrier: {carrier}")
        assert carrier[0][0] not in (
            "superseded", "deprecated", "archived", "retracted"), (
            f"fixture drift: the carrier is not LIVE: {carrier}")
        return survivor

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-f2-review",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_merged_carrier_does_not_turn_a_self_fold_into_a_real_fold(
            self, sdk_factory):
        """`Widget` superseded by `Widget` + a merged carrier is STILL a no-op.

        The target is named by its own name, so every successor candidate is the
        target's own entity: the record must be skipped, never applied — an
        applied fold sets `supersededBy` to the node's own name and the node
        vanishes from `recall_state` while the journal records the burial.
        """
        sdk = sdk_factory()
        try:
            survivor = self._merged_pair(sdk)
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": survivor, "supersedes_by": "Widget",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips, (
                "the self-supersession drop was not reported to the caller's "
                f"skipped list (#5365): {skips!r}")
            row = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.status, o.supersededBy",
                params={"id": survivor}).result_set
            assert row and row[0][0] != "superseded", (
                f"the target was folded onto its own entity: {row}")
        finally:
            sdk.close()

    def test_a_merged_carrier_does_not_block_a_real_fold(self, sdk_factory):
        """A ref held by [survivor, merged carrier] is ONE entity: fold it.

        Pre-widening this applied (the raw probe matched the survivor alone);
        counting NODES instead of entities took that back, so the widened probe
        has to collapse before it refuses.
        """
        sdk = sdk_factory()
        try:
            survivor = self._merged_pair(sdk)
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            assert skips == [], skips
            row = sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) RETURN o.status, o.supersededBy",
                params={"id": survivor}).result_set
            assert row and tuple(row[0]) == ("superseded", "New Widget"), row
        finally:
            sdk.close()

    def test_two_unmerged_carriers_of_one_fold_still_refuse(self, sdk_factory):
        """The collapse must not swallow genuine ambiguity.

        Two live carriers of one folded key with NO merge pointer are two
        identities the graph never reconciled: the record still refuses (never-
        guess) and now REPORTS itself through the caller's `skipped` channel
        (#5365) instead of vanishing.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (a:Object {id:'obj-12120-a', name:'Widget', "
                "objectKind:'document', status:'live', canonical_name:'widget'}) "
                "CREATE (b:Object {id:'obj-12120-b', name:'The Widget', "
                "objectKind:'document', status:'live', canonical_name:'widget'})")
            sdk.create_object("New Widget", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips, (
                "the refusal was not reported to the caller's skipped list: "
                f"{skips!r}")
            statuses = g.query(
                "MATCH (o:Object) WHERE o.id IN ['obj-12120-a','obj-12120-b'] "
                "RETURN o.status").result_set
            assert all(r[0] != "superseded" for r in statuses), statuses
        finally:
            sdk.close()

    def test_a_duplicated_id_still_refuses(self, sdk_factory):
        """Two nodes claiming ONE `id` are corruption, not one entity.

        The collapse groups carriers of one folded key by the `resolved_from`
        hop, and `follow_resolved_from` returns the id UNCHANGED when there is no
        pointer — so keying the group on that return value folds two nodes that
        merely share an `id` into one "entity", and the caller's never-guess
        refusal (``len(by_name) > 1`` / ``len(by_id) > 1``, the raw-corruption
        shape this module already has a rule for) stops firing. Measured on the
        first version of the collapse: a fold that used to be REFUSED applied to
        BOTH nodes and the replay duplicated it. The collapse must key a row with
        NO pointer on the row's own identity.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (a:Object {id:'obj-12120-dup-id', name:'Frobnicator', "
                "objectKind:'document', status:'live', "
                "canonical_name:'frobnicator'}) "
                "CREATE (b:Object {id:'obj-12120-dup-id', name:'The Frobnicator', "
                "objectKind:'document', status:'live', "
                "canonical_name:'frobnicator'})")
            sdk.create_object("New Frobnicator", "document")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Frobnicator", "supersedes_by": "New Frobnicator",
                 "evidence": "test"}])
            assert applied == 0, (
                "two nodes claiming one id were collapsed into one entity and "
                f"both folded: {(applied, skips, msgs)!r}")
            assert skips, (
                "the never-guess refusal was not reported to the caller: "
                f"{skips!r}")
            statuses = g.query(
                "MATCH (o:Object {id:'obj-12120-dup-id'}) RETURN o.status"
            ).result_set
            assert all(r[0] != "superseded" for r in statuses), statuses
        finally:
            sdk.close()

    def test_a_fold_only_object_ref_never_outvotes_an_exact_subject_ref(
            self, sdk_factory):
        """Family routing: the record's own spelling chooses the family.

        A successor name carried by BOTH families routes the record Object-first
        (the pre-existing rule). Widening the probes then made a FOLD-ONLY Object
        match able to outvote an EXACT Subject ref, so
        `{superseded: "Acme Corp", supersedes_by: "New Acme"}` superseded the
        case-variant OBJECT `acme corp` and left the intended SUBJECT `Acme Corp`
        live — a silent wrong-node write that replays. The Object lane now defers
        to the Subject lane when its ref match is only fold-equivalent and the
        Subject family holds the ref.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_subject("Acme Corp", "organization")
            sdk.create_subject("New Acme", "organization")
            g.query(
                "CREATE (a:Object {id:'obj-12120-case-a', name:'acme corp', "
                "objectKind:'document', status:'live', canonical_name:'acme corp'}) "
                "CREATE (b:Object {id:'obj-12120-case-b', name:'new acme', "
                "objectKind:'document', status:'live', canonical_name:'new acme'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Acme Corp", "supersedes_by": "New Acme",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            subj = g.query(
                "MATCH (s:Subject {name:'Acme Corp'}) "
                "RETURN s.status, s.supersededBy").result_set
            assert subj and tuple(subj[0]) == ("superseded", "New Acme"), subj
            objs = g.query(
                "MATCH (o:Object) WHERE o.id IN "
                "['obj-12120-case-a','obj-12120-case-b'] "
                "RETURN o.status").result_set
            assert all(r[0] != "superseded" for r in objs), (
                f"the OBJECT twin was superseded instead: {objs}")
        finally:
            sdk.close()


class TestThe12120ReviewRound13:
    """The three defects review round 13 found in the round-12 fix itself.

    Each is a guard whose WRITTEN invariant was true only of the rows the probe
    happened to return: the duplicate-id pre-pass counted claimants among those
    rows while the hop reads the whole graph, the family defer tested exactness on
    the COLLAPSED representatives instead of the arm, and the supersession fold's
    one unreported refusal was the one outcome a caller can never re-derive.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r13",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_duplicate_id_outside_the_probe_does_not_decide_the_entity(
            self, sdk_factory):
        """The claimant count is the GRAPH's, not the probe's rows.

        `_entity_rows` keeps a duplicate-id entity apart BEFORE the
        `resolved_from` hop because `follow_resolved_from` reads ONE node's
        pointer for an id. Round 12 counted the claimants among `rows`, so a
        duplicate the probe did not return left the count at 1: the hop then read
        the OTHER claimant's pointer and the fold addressed a survivor no probe
        had returned — while the opposite creation order addressed the probed row.
        Measured pre-fix: the same logical graph grouped onto the survivor in one
        order and onto the row's own id in the other (#2164 P2-2).
        """
        from tortoise.commit_ops import _SUCCESSOR_PROJECTION, _entity_rows

        pointer = ("CREATE (:Object {id:'dup-r13', name:'Other', "
                   "objectKind:'thing', status:'live', "
                   "resolved_from:'surv-r13'})")
        probed = ("CREATE (:Object {id:'dup-r13', name:'Widget', "
                  "objectKind:'thing', status:'live'})")
        survivor = ("CREATE (:Object {id:'surv-r13', name:'Survivor', "
                    "objectKind:'thing', status:'live'})")
        for order in ((survivor, pointer, probed), (probed, survivor, pointer)):
            sdk = sdk_factory()
            try:
                g = sdk._get_proj().g
                for stmt in order:
                    g.query(stmt)
                rows = g.query(
                    "MATCH (o:Object) WHERE o.name = 'Widget' "
                    "RETURN o.id, o.name, o.status").result_set
                assert len(rows) == 1, rows
                out = _entity_rows(g, "Object", [tuple(r) for r in rows],
                                   id_idx=0, projection=_SUCCESSOR_PROJECTION,
                                   with_groups=True)
                assert [r[0][0] for r in out] == ["dup-r13"], (
                    "the probed row was collapsed onto a survivor no probe "
                    "returned — the duplicate-id pre-pass missed a claimant "
                    f"outside `rows` (order {order[1][:40]!r}): {out}")
            finally:
                sdk.close()

    def test_an_exact_name_carrier_keeps_the_object_lane(self, sdk_factory):
        """The family defer asks exactness of the ARM, not of the representatives.

        Round 12 tested `str(_row[2]) == ref` over `_entity_rows` output — one row
        per ENTITY, and that row is the SURVIVOR's. An Object whose OWN name is the
        ref but which a merge collapsed onto a differently-named survivor then read
        as "not exact", the defer fired, and the record superseded an exactly-named
        Subject while the exact-name Object entity stayed live — inverting the
        Object-first precedence the branch exists to keep.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_subject("Acme Corp", "organization")
            sdk.create_subject("New Corp", "organization")
            sdk.create_object("New Corp", "document")
            g.query(
                "CREATE (s:Object {id:'obj-r13-surv', name:'Acme Holdings', "
                "objectKind:'document', status:'live', "
                "canonical_name:'acme holdings'}) "
                "CREATE (c:Object {id:'obj-r13-carrier', name:'Acme Corp', "
                "objectKind:'document', status:'live', "
                "canonical_name:'acme corp', resolved_from:'obj-r13-surv'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Acme Corp", "supersedes_by": "New Corp",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            subj = g.query(
                "MATCH (s:Subject {name:'Acme Corp'}) RETURN s.status").result_set
            assert subj and subj[0][0] != "superseded", (
                f"an exact-name Object carrier was read as non-exact and the "
                f"SUBJECT took the fold: {subj}")
            obj = g.query(
                "MATCH (o:Object {id:'obj-r13-surv'}) RETURN o.status").result_set
            assert obj and obj[0][0] == "superseded", (
                f"the exact-name Object entity did not take its own fold: {obj}")
        finally:
            sdk.close()

    def test_a_fold_that_matches_nothing_is_reported(
            self, sdk_factory, monkeypatch):
        """The last unapplied-record outcome must reach `skipped` (#5365).

        `apply_supersessions` promises one `{ref, successor, reason}` entry for
        every record it did not apply, and the sibling branches in this same loop
        all report. The `folded == 0` outcome (a fold-miss, and the concurrent-race
        loser) warned only — and it is the one outcome a caller can never
        re-derive, because the fold's own result is not in any other channel.
        """
        sdk = sdk_factory()
        try:
            proj = sdk._get_proj()
            sdk.create_object("Widget", "document")
            sdk.create_object("New Widget", "document")
            monkeypatch.setattr(proj.__class__, "_fold_object_superseded",
                                lambda self, ev, *, cas=False: (0, 0))
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert any("fold matched no object" in str(s.get("reason") or "")
                       for s in skips), (
                "a record the fold did not apply never reached the caller's "
                f"skipped list: {skips!r} (warnings: {msgs!r})")
        finally:
            sdk.close()


class TestThe12120ReviewRound14:
    """The two defects review round 14 found at the merged head.

    Both are holes in a guard that was written for the shape it was thinking of:
    the round-6 live narrowing assumed two rows under one name are two
    INCARNATIONS (they are corruption when they share an id), and the blank-fold
    zero-row answer was justified by a premise that measurement falsifies.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r14",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_duplicate_id_pair_refuses_before_the_live_narrowing(
            self, sdk_factory):
        """One terminal claimant must not hide the duplicate-id corruption.

        The round-6 live narrowing asks "is a terminal ex-incarnation competing
        with a live carrier?" — but when two rows share an `id` they are
        CORRUPTION, not two incarnations, and the narrowing collapsed them to one
        row, so the never-guess refusal never fired. Measured pre-fix: `applied=1,
        skipped=[]` for two nodes sharing an id (one live, one superseded), with
        the live/journal/replay paths then disagreeing; `main` refused the shape.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("New Widget", "document")
            g.query(
                "CREATE (a:Object {id:'dup-r14', name:'Twinned', "
                "objectKind:'document', status:'live'}) "
                "CREATE (b:Object {id:'dup-r14', name:'Twinned', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Older'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Twinned", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (
                f"a duplicate-id pair was folded instead of refused: {msgs}")
            assert any("ambiguous object id" in str(s.get("reason") or "")
                       for s in skips), skips
            row = g.query(
                "MATCH (o:Object {id:'dup-r14'}) "
                "WHERE o.supersededBy = 'Older' RETURN count(o)").result_set
            assert row and row[0][0] == 1, (
                "the terminal claimant's recorded successor was overwritten "
                f"by the fold: {row}")
        finally:
            sdk.close()

    def test_a_duplicate_id_pair_of_subjects_refuses(self, sdk_factory):
        """The Subject twin: the narrowing collapsed the pair, so both claimants
        were stamped (the Subject write is not CAS-gated) and the terminal
        claimant's recorded successor was silently overwritten."""
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_subject("New Team", "organization")
            g.query(
                "CREATE (a:Subject {id:'dup-subj-r14', name:'Old Team', "
                "subjectKind:'organization', status:'live'}) "
                "CREATE (b:Subject {id:'dup-subj-r14', name:'Old Team', "
                "subjectKind:'organization', status:'superseded', "
                "supersededBy:'Older Team'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Team", "supersedes_by": "New Team",
                 "evidence": "test"}])
            assert applied == 0, (
                f"a duplicate-id Subject pair was folded instead of refused: "
                f"{msgs}")
            assert any("ambiguous subject id" in str(s.get("reason") or "")
                       for s in skips), skips
            row = g.query(
                "MATCH (s:Subject {id:'dup-subj-r14'}) "
                "WHERE s.supersededBy = 'Older Team' RETURN count(s)").result_set
            assert row and row[0][0] == 1, (
                f"the terminal claimant's successor was overwritten: {row}")
        finally:
            sdk.close()


class TestThe12120ReviewRound15:
    """The duplicate-survivor FETCH must not discard the carriers it resolved.

    Round 15: `_entity_rows` reports `(dup, key, [dup])` when two nodes claim a
    survivor's id — replacing the carriers the widened probe matched with the two
    claimants. Both consumers read `members`, so when the claimants are TERMINAL and
    the only visible successor is one of those carriers, the record was refused
    "no visible successor" and the fold `main` applied was lost. `TestThe12120ReviewRound9`'s
    duplicate-id fixture cannot see this (its claimants are live); this one can.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r15",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_terminal_duplicate_pair_does_not_hide_the_live_carrier(
            self, sdk_factory):
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("Old Co", "document")
            # Two nodes claim one id and BOTH are terminal, so neither is a
            # visible successor; the only visible successor is `r15-carrier`,
            # which holds the successor name and points at that id.
            g.query(
                "CREATE (a:Object {id:'r15-dup', name:'Duplicated', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Elsewhere'}) "
                "CREATE (b:Object {id:'r15-dup', name:'Duplicated Two', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Elsewhere'}) "
                "CREATE (c:Object {id:'r15-carrier', name:'Widget', "
                "objectKind:'document', status:'live', "
                "canonical_name:'widget', resolved_from:'r15-dup'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "Widget",
                 "evidence": "test"}])
            assert applied == 1, (
                "the duplicate-survivor fetch hid the only LIVE carrier and the "
                f"fold was refused: {skips!r} {msgs!r}")
            row = g.query(
                "MATCH (o:Object {name:'Old Co'}) "
                "RETURN o.status, o.supersededBy").result_set
            assert row and tuple(row[0]) == ("superseded", "Widget"), (
                "the recorded successor must be the spelling the record "
                f"resolved (the carrier's), not a duplicate's: {row}")
        finally:
            sdk.close()


class TestThe12120ReviewRound16:
    """Round 16 (this lane): the duplicate-id guard must REFUSE, never disarm.

    Round 14 added ``_id_claimant_count`` to ``_entity_key_of`` and answered the
    corruption case with ``None``. But ``None`` is not a refusal at the two sites
    that consume the key — it is a value that no candidate key can equal
    (``all_candidates_are_target`` can never fire) and that every key differs
    from (so the merged-carrier exclusion stops excluding and the target's own
    entity group is offered as a successor). The two entity-level questions that
    exist to REFUSE silently passed, and the fold — which addresses the id, not
    the node — stamped every node claiming that id, including one that is not the
    record's target at all.

    ``TestThe12120ReviewRound15`` (above) is the peer lane's round at the same
    head and owns that round number; this class is the delta that closed the P1.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r16",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_duplicate_id_target_refuses_instead_of_stamping_every_claimant(
            self, sdk_factory):
        """The P1: `None` disarmed the self-supersession guard.

        `S` is the successor, `T`/`Target` is a merged carrier of `S`'s entity,
        and `D`/`Other` is an UNRELATED node that merely claims the same id (raw
        corruption). The ref `Target` resolves unambiguously by NAME to one node,
        so the ref-side duplicate-id probes never fire — and the entity question
        about its id cannot be answered. Measured pre-fix: `applied=1, skips=[]`
        with BOTH claimants stamped `superseded → Survivor`; the same graph
        refused at the previous head.
        """
        from tortoise.entity_identity import (
            clear_non_folded_entries,
            non_folded_entries,
        )

        order_survivor = (
            "CREATE (s:Object {id:'ent-r16-s', name:'Survivor', "
            "objectKind:'document', status:'live', canonical_name:'survivor'})")
        order_target = (
            "CREATE (t:Object {id:'ent-r16-t', name:'Target', "
            "objectKind:'document', status:'live', canonical_name:'target', "
            "resolved_from:'ent-r16-s'})")
        order_other = (
            "CREATE (d:Object {id:'ent-r16-t', name:'Other', "
            "objectKind:'document', status:'live', canonical_name:'other'})")
        for order in ((order_survivor, order_target, order_other),
                      (order_survivor, order_other, order_target)):
            sdk = sdk_factory()
            try:
                g = sdk._get_proj().g
                for stmt in order:
                    g.query(stmt)
                clear_non_folded_entries()
                applied, skips, msgs = self._apply(sdk, [
                    {"superseded": "Target", "supersedes_by": "Survivor",
                     "evidence": "test"}])
                assert applied == 0, (
                    "the fold stamped a duplicate-id target's every claimant "
                    f"instead of refusing ({order[1][:48]!r}): {msgs}")
                assert any("ambiguous object id" in str(s.get("reason") or "")
                           for s in skips), skips
                # The DURABLE record must name what the refusal COUNTED: both
                # claimants, not one carrier for a two-claimant refusal.
                entries = [e for e in non_folded_entries()
                           if e.shape == "duplicate-id"]
                assert entries, (
                    "the refusal recorded no non-folded entry — the durable "
                    "channel is the record (#9876), the warning is only "
                    "operator visibility")
                assert entries[0].candidate_ids == ("ent-r16-t", "ent-r16-t"), (
                    "the entry understates the claimant set it counted: "
                    f"{entries[0]!r}")
                rows = g.query(
                    "MATCH (o:Object) WHERE o.id = 'ent-r16-t' "
                    "RETURN o.name, o.status, o.supersededBy").result_set
                assert len(rows) == 2, rows
                for name, status, sb in rows:
                    assert (status or "") == "live", (
                        f"{name!r} was stamped {status!r} by a fold that "
                        f"should have refused: {rows}")
                    assert not sb, (
                        f"{name!r} was pointed at a successor it never had: "
                        f"{rows}")
                surv = g.query(
                    "MATCH (o:Object {id:'ent-r16-s'}) RETURN o.status"
                ).result_set
                assert surv and surv[0][0] == "live", surv
            finally:
                sdk.close()

    def test_an_idempotent_re_ingest_on_a_corrupt_graph_stays_silent(
            self, sdk_factory):
        """The refusal must not pre-empt keep-first (review round 16).

        A target that is ALREADY folded is an idempotent dedup: no write is
        being made, so the duplicate-id refusal has nothing to refuse. Placed
        above the terminal arm it would turn every retry of an unchanged
        conversation on a corrupt graph into a reported refusal (and burn the
        caller's warning budget), while the Subject twin stayed silent for the
        same shape — the two families disagreeing on a rule one of them states in
        prose. Both lanes now answer: `applied=0, skips=[]` — pinned here for BOTH
        (the Subject half is a second graph in the same test, because a claim
        about agreement that only one family pins reddens for neither).
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (s:Object {id:'ent-r16-s', name:'Survivor', "
                "objectKind:'document', status:'live'}) "
                "CREATE (t:Object {id:'ent-r16-t', name:'Target', "
                "objectKind:'document', status:'live'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Target", "supersedes_by": "Survivor",
                 "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            # A second node now claims the FOLDED target's id — the corruption
            # the round-15 refusal exists for, on a target that is already
            # terminal.
            g.query("CREATE (d:Object {id:'ent-r16-t', name:'Other', "
                    "objectKind:'document', status:'live'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Target", "supersedes_by": "Survivor",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips == [], (
                "an idempotent re-ingest of an already-folded target was "
                f"reported as a refusal: {skips} (warnings: {msgs})")
            assert msgs == [], (
                f"the dedup emitted warnings: {msgs}")
            folded = g.query(
                "MATCH (o:Object {id:'ent-r16-t'}) "
                "WHERE o.status = 'superseded' RETURN o.supersededBy"
            ).result_set
            assert folded and folded[0][0] == "Survivor", folded

            # ── the Subject half of the same claim ───────────────────────
            g.query(
                "CREATE (ss:Subject {id:'ent-r16-ss', name:'Survivor Team', "
                "subjectKind:'organization', status:'live'}) "
                "CREATE (st:Subject {id:'ent-r16-st', name:'Target Team', "
                "subjectKind:'organization', status:'live'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Target Team",
                 "supersedes_by": "Survivor Team", "evidence": "test"}])
            assert applied == 1, (applied, skips, msgs)
            g.query("CREATE (sd:Subject {id:'ent-r16-st', name:'Other Team', "
                    "subjectKind:'organization', status:'live'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Target Team",
                 "supersedes_by": "Survivor Team", "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips == [] and msgs == [], (
                "the Subject lane reported an idempotent re-ingest of an "
                f"already-folded target: {skips} / {msgs}")
        finally:
            sdk.close()

    def test_two_live_carriers_of_a_gone_survivor_are_not_duplicate_names(
            self, sdk_factory):
        """Round 17: the refusal's durable shape must name the TRUE condition.

        Two LIVE carriers of ONE merged entity (its survivor deleted) are two
        addressable nodes of a single entity — the never-guess count refuses them,
        which is right, but the durable `record_non_folded` shape for that arm
        must be the dangling `resolved_from` (`unaddressable-name`), not
        `ambiguous-name`: the graph's own grouping says these two are one entity,
        so a repair consumer reading `ambiguous-name` would be told to merge a pair
        the merge already made, and the true condition would be reported nowhere
        on this path.
        """
        from tortoise.entity_identity import (
            clear_non_folded_entries,
            non_folded_entries,
        )

        # Both families carry the SAME contract: each is exercised below on the
        # same shape, and both must report `ref carrier's survivor is gone`.
        for label, kind in (("Object", "document"), ("Subject", "organization")):
            sdk = sdk_factory()
            try:
                g = sdk._get_proj().g
                if label == "Object":
                    sdk.create_object("New Co", "document")
                    g.query(
                        "CREATE (a:Object {id:'gone-r17-a', name:'Old Co', "
                        "objectKind:'document', status:'live', "
                        "resolved_from:'gone-r17-surv'}) "
                        "CREATE (b:Object {id:'gone-r17-b', name:'Old Co', "
                        "objectKind:'document', status:'live', "
                        "resolved_from:'gone-r17-surv'})")
                    rec = {"superseded": "Old Co",
                           "supersedes_by": "New Co", "evidence": "test"}
                else:
                    sdk.create_subject("New Team", kind)
                    g.query(
                        "CREATE (a:Subject {id:'gone-r17-a', name:'Old Team', "
                        "subjectKind:'organization', status:'live', "
                        "resolved_from:'gone-r17-surv'}) "
                        "CREATE (b:Subject {id:'gone-r17-b', name:'Old Team', "
                        "subjectKind:'organization', status:'live', "
                        "resolved_from:'gone-r17-surv'})")
                    rec = {"superseded": "Old Team",
                           "supersedes_by": "New Team", "evidence": "test"}
                clear_non_folded_entries()
                applied, skips, msgs = self._apply(sdk, [rec])
                assert applied == 0, (applied, skips, msgs)
                reasons = [str(s.get("reason") or "") for s in skips]
                assert reasons and all("survivor is gone" in r
                                       for r in reasons), (
                    f"{label}: the refusal did not name the dangling "
                    f"`resolved_from`: {skips!r} (warnings: {msgs!r})")
                shapes = [e.shape for e in non_folded_entries()
                          if e.name == ("Old Co" if label == "Object"
                                        else "Old Team")]
                assert shapes == ["unaddressable-name"], (
                    f"{label}: the durable shape claims duplicate-name "
                    f"corruption this graph does not have: {shapes}")
            finally:
                sdk.close()

    def test_a_duplicate_id_subject_target_refuses(self, sdk_factory):
        """The Subject twin: same disarm, and the Subject write is not CAS-gated."""
        from tortoise.entity_identity import (
            clear_non_folded_entries,
            non_folded_entries,
        )

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (s:Subject {id:'ent-r16-ns', name:'Survivor Team', "
                "subjectKind:'organization', status:'live'}) "
                "CREATE (t:Subject {id:'ent-r16-nt', name:'Target Team', "
                "subjectKind:'organization', status:'live', "
                "resolved_from:'ent-r16-ns'}) "
                "CREATE (d:Subject {id:'ent-r16-nt', name:'Other Team', "
                "subjectKind:'organization', status:'live'})")
            clear_non_folded_entries()
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Target Team", "supersedes_by": "Survivor Team",
                 "evidence": "test"}])
            assert applied == 0, (
                "a duplicate-id Subject target was folded instead of refused: "
                f"{msgs}")
            assert any("ambiguous subject id" in str(s.get("reason") or "")
                       for s in skips), skips
            entries = [e for e in non_folded_entries()
                       if e.shape == "duplicate-id"]
            assert entries and entries[0].candidate_ids == (
                "ent-r16-nt", "ent-r16-nt"), (
                f"the durable entry does not name the two claimants: {entries}")
            rows = g.query(
                "MATCH (s:Subject) WHERE s.id = 'ent-r16-nt' "
                "RETURN s.name, s.status, s.supersededBy").result_set
            assert len(rows) == 2, rows
            for name, status, sb in rows:
                assert (status or "") == "live", (
                    f"{name!r} was stamped {status!r} by a fold that should "
                    f"have refused: {rows}")
                assert not sb, f"{name!r} was pointed at a successor: {rows}"
        finally:
            sdk.close()


class TestThe12120ReviewRound16FamilyGuard:
    """The gone-survivor guard must ask the FAMILY's key space, not exactness.

    `_subject_family_holds_ref` (this lane's round 16) replaces the defer's
    EXACTNESS test in the guard that decides whether a record whose Object carriers
    all collapsed away is dropped as `"ref carrier's survivor is gone"`. A Subject
    that holds the ref only by FOLD is invisible to the exact test, so the record
    was dropped — with a warning that no `:Subject` holds the ref, about a live one
    — and the target stayed live and search-visible.

    ⛔ The fixture's ref carrier is TERMINAL on purpose: `capstone`'s round-10
    `_ref_candidates` hands a gone-survivor group's LIVE carriers back as
    addressable, so a live carrier now folds in the Object lane and never reaches
    this guard. The terminal one does not, which is the shape the guard decides.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r16f",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_fold_only_subject_ref_is_not_dropped_as_a_gone_survivor(
            self, sdk_factory):
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (o:Object {id:'r16f-old', name:'Old Team', "
                "objectKind:'organization', status:'superseded', "
                "supersededBy:'Elsewhere', "
                "canonical_name:'old team', resolved_from:'r16f-deleted'})")
            sdk.create_subject("The Old Team", "organization")
            sdk.create_object("New Widget", "document")
            sdk.create_subject("New Widget", "organization")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Team", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 1, (
                "a fold-only Subject ref was dropped as a gone survivor: "
                f"{skips!r} {msgs!r}")
            row = g.query(
                "MATCH (s:Subject {name:'The Old Team'}) "
                "RETURN s.status, s.supersededBy").result_set
            assert row and tuple(row[0]) == ("superseded", "New Widget"), row
        finally:
            sdk.close()

    def test_a_subject_holding_the_ref_by_id_is_not_dropped_either(
            self, sdk_factory):
        """The lane resolves the ref by ID first, then by name — ask both.

        Round 16's second finding on this guard: asking only the name space missed a
        live `:Subject` whose `id` IS the ref, so the record was dropped as "ref
        carrier's survivor is gone" and the warning claimed no `:Subject` held the
        ref — about that very Subject. Measured pre-fix: applied=0; post-fix:
        applied=1 and the Subject is stamped.
        """
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (o:Object {id:'r16g-ghost', name:'Something Else', "
                "objectKind:'organization', status:'superseded', "
                "supersededBy:'Elsewhere', canonical_name:'ent-r16g-sub', "
                "resolved_from:'r16g-deleted'})")
            g.query("CREATE (s:Subject {id:'ent-r16g-sub', "
                    "name:'Subject By Id', subjectKind:'organization', "
                    "status:'live'})")
            sdk.create_object("New Widget", "document")
            sdk.create_subject("New Widget", "organization")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "ent-r16g-sub", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 1, (
                "a Subject holding the ref BY ID was dropped as a gone "
                f"survivor: {skips!r} {msgs!r}")
            row = g.query(
                "MATCH (s:Subject {id:'ent-r16g-sub'}) "
                "RETURN s.status, s.supersededBy").result_set
            assert row and tuple(row[0]) == ("superseded", "New Widget"), row
        finally:
            sdk.close()


class TestThe12120ReviewRound17:
    """Two GONE-survivor entities are not one repairable entity.

    `_ref_candidates` contributed the carriers of every group whose survivor is
    gone, and the refusal's "ONE entity" test could only ask whether each candidate
    came from SOME such group — so two distinct entities with two dangling
    `resolved_from` pointers were reported as one, and `main`'s `ambiguous-name`
    durable record was downgraded to `unaddressable-name`, hiding that there are two
    repairs. The group key is now carried per carrier.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r17",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_two_gone_survivors_keep_the_ambiguous_name_record(
            self, sdk_factory):
        from tortoise.entity_identity import (
            clear_non_folded_entries,
            non_folded_entries,
        )

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("New Co", "document")
            g.query(
                "CREATE (a1:Object {id:'p-a1', name:'Old Co', "
                "objectKind:'document', status:'live', "
                "resolved_from:'gone-1'}) "
                "CREATE (a2:Object {id:'p-a2', name:'Old Co', "
                "objectKind:'document', status:'live', "
                "resolved_from:'gone-1'}) "
                "CREATE (b1:Object {id:'p-b1', name:'Old Co', "
                "objectKind:'document', status:'live', "
                "resolved_from:'gone-2'})")
            clear_non_folded_entries()
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "New Co",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            shapes = [e.shape for e in non_folded_entries()
                      if e.name == "Old Co"]
            assert shapes == ["ambiguous-name"], (
                "two distinct entities with dangling survivors were reported as "
                f"one repairable entity: {shapes} (skips: {skips!r})")
        finally:
            sdk.close()


class TestThe12120ReviewRound18:
    """A live carrier of a RETIRED survivor is addressable; a re-ingest stays silent.

    `_ref_candidates` contributed a group's live members only when the survivor was
    GONE. A merge-then-supersede graph hands back the retired SURVIVOR as the
    representative (it is present, so the group is not a gone-survivor group), and
    the live carrier that actually holds the name was dropped — so the ref resolved
    to the retired row, keep-first refused `terminal object target`, and the target
    stayed live and search-visible where `main` applied the fold (measured, both
    families). The same drop made the Subject lane say "not found in the graph as a
    :Subject" about a Subject the graph holds, and turned an idempotent re-ingest
    into a fresh refusal with two warnings.
    """

    @staticmethod
    def _apply(sdk, records):
        from tortoise.commit_ops import apply_supersessions

        msgs: list[str] = []
        skips: list[dict] = []
        applied = apply_supersessions(
            sdk._get_proj(), sdk, records, session_id="s-12120-r18",
            skipped=skips,
            warn=lambda m, *a, **k: msgs.append(m % a if a else m))
        return applied, skips, msgs

    def test_a_live_carrier_beside_a_retired_survivor_folds(self, sdk_factory):
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("New Co", "document")
            g.query(
                "CREATE (s:Object {id:'r18-surv', name:'Acme Holdings', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Elsewhere'}) "
                "CREATE (c:Object {id:'r18-carrier', name:'The Team', "
                "objectKind:'document', status:'live', "
                "canonical_name:'team', resolved_from:'r18-surv'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "The Team", "supersedes_by": "New Co",
                 "evidence": "test"}])
            assert applied == 1, (
                "a live carrier beside a RETIRED survivor was refused as a "
                f"terminal target: {skips!r} {msgs!r}")
            row = g.query(
                "MATCH (o:Object {id:'r18-carrier'}) "
                "RETURN o.status, o.supersededBy").result_set
            assert row and tuple(row[0]) == ("superseded", "New Co"), row
        finally:
            sdk.close()

    def test_a_live_subject_carrier_beside_a_retired_survivor_folds(
            self, sdk_factory):
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_subject("New Team", "organization")
            g.query(
                "CREATE (s:Subject {id:'r18-ssurv', name:'Acme Holdings', "
                "subjectKind:'organization', status:'superseded', "
                "supersededBy:'Elsewhere'}) "
                "CREATE (c:Subject {id:'r18-scarrier', name:'The Team', "
                "subjectKind:'organization', status:'live', "
                "resolved_from:'r18-ssurv'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "The Team", "supersedes_by": "New Team",
                 "evidence": "test"}])
            assert applied == 1, (
                "the Subject twin refused a live carrier beside a retired "
                f"survivor: {skips!r} {msgs!r}")
            row = g.query(
                "MATCH (s:Subject {id:'r18-scarrier'}) "
                "RETURN s.status, s.supersededBy").result_set
            assert row and tuple(row[0]) == ("superseded", "New Team"), row
        finally:
            sdk.close()

    def test_an_idempotent_reingest_through_a_gone_survivor_stays_silent(
            self, sdk_factory):
        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("Even Older Team", "document")
            g.query(
                "CREATE (o:Object {id:'r18-old', name:'Old Team', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Even Older Team', "
                "resolved_from:'r18-deleted'})")
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Team", "supersedes_by": "Even Older Team",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            assert skips == [], (
                "an idempotent re-ingest was reported as a fresh refusal — "
                f"`main` is silent here: {skips!r}")
            assert msgs == [], f"the dedup emitted warnings: {msgs!r}"
        finally:
            sdk.close()

    def test_the_gone_survivor_refusal_is_recorded_durably(self, sdk_factory):
        from tortoise.entity_identity import (
            clear_non_folded_entries,
            non_folded_entries,
        )

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("New Co", "document")
            g.query(
                "CREATE (o:Object {id:'r18-old2', name:'Old Co', "
                "objectKind:'document', status:'superseded', "
                "supersededBy:'Different Older', "
                "resolved_from:'r18-deleted2'})")
            clear_non_folded_entries()
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Old Co", "supersedes_by": "New Co",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            shapes = [e.shape for e in non_folded_entries()
                      if e.name == "Old Co"]
            assert shapes == ["unaddressable-name"], (
                "the gone-survivor refusal wrote no durable record although its "
                f"twin arms do: {shapes} (skips: {skips!r})")
        finally:
            sdk.close()

    def test_an_id_less_holder_is_not_a_gone_entity(self, sdk_factory):
        """One gone-survivor carrier + one ID-LESS holder = TWO entities.

        `_ref_is_one_gone_entity` decides "one entity" from the ids that survive its
        own filter, so a holder with no `id` (or an id no group claims) is silently
        dropped and two holders read as one dangling survivor — which downgrades
        `main`'s `ambiguous-name` record to `unaddressable-name` and hides the
        duplicate name. Measured pre-fix: `unaddressable-name`; post-fix:
        `ambiguous-name` (the record a merge pass can act on).
        """
        from tortoise.entity_identity import (
            clear_non_folded_entries,
            non_folded_entries,
        )

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            sdk.create_object("New Widget", "document")
            # The legacy id-less holder `_upsert_object` never mints, plus a
            # carrier of an entity whose survivor is gone.
            g.query(
                "CREATE (o:Object {name:'Widget', canonical_name:'widget', "
                "objectKind:'document', status:'live'}) "
                "CREATE (c:Object {id:'r18-carrier', name:'Widget', "
                "canonical_name:'widget', objectKind:'document', "
                "status:'live', resolved_from:'r18-gone'})")
            clear_non_folded_entries()
            applied, skips, msgs = self._apply(sdk, [
                {"superseded": "Widget", "supersedes_by": "New Widget",
                 "evidence": "test"}])
            assert applied == 0, (applied, skips, msgs)
            shapes = [e.shape for e in non_folded_entries()
                      if e.name == "Widget"]
            assert shapes == ["ambiguous-name"], (
                "an id-less holder was dropped from the entity count, so two "
                f"entities were reported as one dangling survivor: {shapes} "
                f"(skips: {skips!r})")
        finally:
            sdk.close()

    def test_a_three_column_group_is_read_not_indexed(self):
        """`_ref_candidates` must READ a 3-column group, never index off its end.

        The REF probes pass the 5-column shape, whose columns 1 and 3 are id and
        status; the successor probe's `_SUCCESSOR_PROJECTION` is
        `(id, name, status)`, and indexing THAT with the defaults raises
        `IndexError` out of `apply_supersessions` — an exception where this module
        twice states a REFUSAL is required. The parameters exist for that caller
        (none in-tree yet), so they are pinned directly, both directions.
        """
        from tortoise.commit_ops import _ref_candidates

        group = (None, "gone-r18", [("three-a", "Same", "live"),
                                    ("three-b", "Same", "live")])
        with pytest.raises(IndexError):
            _ref_candidates([group], "Object")
        rows, widened = _ref_candidates([group], "Object", id_idx=0,
                                        status_idx=2)
        assert [r[0] for r in rows] == ["three-a", "three-b"], rows
        assert widened == {"three-a": "gone-r18",
                           "three-b": "gone-r18"}, widened
