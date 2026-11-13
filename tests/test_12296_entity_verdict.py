"""#12296 — the verdict step over noun candidates: three bands, hard vetoes, and
the closure guard.

The defect this file exists to keep closed
------------------------------------------
**D-1** (`#12186` comment `6839898289`, same root as `#12244`). `apply_entity_merge`
is non-destructive: the retired carrier keeps its `name` and gains `resolved_from`,
and it writes **no** `status` (`entity_resolver.py:797-805`). `:Object` is
full-text indexed on `name` alone and the FTS leg filters `status` only
(`search_engine.py:2973-2997`) — so a name search returns the survivor AND the
carrier, and the capture path's candidate set never consulted the resolver. A
mention could therefore bind to the node the merge already retired. **The first
half of this file is the guard that closes it**, tested the way it fails: on a
graph that really holds a merged pair.

The second half is the verdict's own vocabulary — the bands, the hard vetoes and
the closure re-verification (M-4) — pinned as pure functions so a rule change is
visible without a graph.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise import entity_verdict as verdict
from tortoise import extractor_v2 as v2
from tortoise import failure_policy as _failure_policy
from tortoise.entity_resolver import apply_entity_merge

# ── helpers ───────────────────────────────────────────────────────────────


def _create_object(sdk, node_id: str, name: str, kind: str = "core:plan",
                   **extra) -> None:
    props = {"id": node_id, "name": name, "objectKind": kind,
             "status": "live", "createdAt": "2026-01-01", **extra}
    assigns = ", ".join(f"{k}: ${k}" for k in props)
    sdk._get_proj().g.query(f"CREATE (n:Object {{{assigns}}})", params=props)


def _merged_pair(sdk, carrier_id: str = "obj-carrier",
                 survivor_id: str = "obj-survivor",
                 carrier_name: str = "the team",
                 survivor_name: str = "Acme Holdings") -> None:
    _create_object(sdk, carrier_id, carrier_name)
    _create_object(sdk, survivor_id, survivor_name)
    apply_entity_merge(sdk, carrier_id, survivor_id,
                       method="exact", similarity=1.0)


class _RowStub:
    def __init__(self, rows):
        self.result_set = rows


# ── D-1: the candidate set excludes what a merge retired ─────────────────


class TestThePriorSetNeverCarriesARetiredNode:
    def test_the_guard_tests_resolved_from_and_replaces_the_carrier(self, sdk_factory):
        """The one thing that is non-negotiable (#12296).

        After a real `apply_entity_merge`, a prior row naming the CARRIER must
        come out naming the SURVIVOR — same id, survivor's name, survivor's kind —
        and the result must hold ONE row for the pair, not two.
        """
        sdk = sdk_factory()
        _merged_pair(sdk)
        rows = [
            {"id": "obj-carrier", "name": "the team", "kind": "core:plan"},
            {"id": "obj-survivor", "name": "Acme Holdings", "kind": "core:plan"},
        ]
        out = v2._current_entity_priors(
            sdk, rows, {"obj-carrier": "Object", "obj-survivor": "Object"})
        assert [r["id"] for r in out] == ["obj-survivor"], out
        assert out[0]["name"] == "Acme Holdings"
        # The pair is ONE entity, so one row — the carrier must not consume a
        # slot in the verdict's candidate set and be re-offered as a duplicate.
        assert len(out) == 1

    def test_a_carrier_alone_still_yields_the_survivor(self, sdk_factory):
        """Replacement, not deletion.

        The FTS query only matched the carrier's spelling (`the team` vs
        `Acme Holdings`), so the carrier is the only row S3 returns. Dropping it
        would report the mention as a measured ABSENCE and the run would mint a
        duplicate — the failure this guard must not trade the defect for.
        """
        sdk = sdk_factory()
        _merged_pair(sdk)
        out = v2._current_entity_priors(
            sdk, [{"id": "obj-carrier", "name": "the team", "kind": "core:plan"}],
            {"obj-carrier": "Object"})
        assert [(r["id"], r["name"]) for r in out] == [
            ("obj-survivor", "Acme Holdings")], out

    def test_an_unmerged_graph_is_untouched(self, sdk_factory):
        sdk = sdk_factory()
        _create_object(sdk, "obj-a", "the plan")
        _create_object(sdk, "obj-b", "the ledger")
        rows = [{"id": "obj-a", "name": "the plan", "kind": "core:plan"},
                {"id": "obj-b", "name": "the ledger", "kind": "core:plan"}]
        out = v2._current_entity_priors(
            sdk, rows, {"obj-a": "Object", "obj-b": "Object"})
        assert [r["id"] for r in out] == ["obj-a", "obj-b"]

    def test_the_survivor_is_kept_even_when_it_is_terminal(self, sdk_factory):
        """The terminal-inclusive prior is load-bearing (#3301) and NOT this guard's
        business: this leg deliberately opts back INTO terminal entities so
        `apply_supersessions` can reach its idempotency branch. Retiring the
        carrier is a different question from terminal status, and a guard that
        conflated them would silently drop the prior set it exists to protect.
        """
        sdk = sdk_factory()
        _create_object(sdk, "obj-carrier", "the team")
        _create_object(sdk, "obj-survivor", "Acme Holdings",
                       status="superseded")
        apply_entity_merge(sdk, "obj-carrier", "obj-survivor",
                           method="exact", similarity=1.0)
        out = v2._current_entity_priors(
            sdk, [{"id": "obj-carrier", "name": "the team", "kind": "core:plan"}],
            {"obj-carrier": "Object"})
        assert [r["id"] for r in out] == ["obj-survivor"]

    def test_a_search_that_cannot_read_raises_rather_than_returning_the_carrier(
            self, sdk_factory):
        """Cannot-answer is not an answer (`entity_resolver.py:562-572`).

        Returning the PRE-hop rows on a failed read is the one outcome this guard
        exists to prevent, and it would look like a successful resolve.
        """
        sdk = sdk_factory()

        class _Boom:
            def query(self, *_a, **_kw):
                raise RuntimeError("graph gone")

        class _Proj:
            g = _Boom()

        sdk._proj = _Proj()
        sdk._get_proj = lambda: _Proj()
        with pytest.raises(RuntimeError):
            v2._current_entity_priors(
                sdk, [{"id": "obj-carrier", "name": "the team", "kind": ""}],
                {"obj-carrier": "Object"})

    def test_a_client_with_no_projection_is_skipped(self):
        """A lightweight test double holds no `resolved_from` state to test."""
        class _NoProj:
            pass

        rows = [{"id": "obj-a", "name": "the plan", "kind": ""}]
        assert v2._current_entity_priors(_NoProj(), rows, {"obj-a": "Object"}) == rows

    def test_a_fold_failure_is_PARTIAL_and_keeps_the_rows_it_did_resolve(
            self, sdk_factory, monkeypatch):
        """The ENTRY POINT's contract, not the helper's — and the right GRADE.

        Not `degraded`: `degraded` means the search never got through its FIRST
        query and therefore carries ZERO priors, and `_render_search_results`
        acts on it by withholding every prior AND telling the gap reviewer the
        lookup did not run. Here the queries RAN and their point/event priors are
        real, so the honest grade is PARTIAL (#5095 §4.4 G3): keep what the fold
        resolved, record the entity legs as failed, and never emit a pre-hop row.
        """
        sdk = sdk_factory()
        _create_object(sdk, "obj-good", "the ledger")
        depth = v2._PRIOR_HOP_BOUND + 2
        graph = sdk._get_proj().g
        for i in range(depth):
            _create_object(sdk, f"obj-d{i}", f"deep {i}")
        for i in range(depth - 1):
            graph.query("MATCH (n:Object {id:$id}) SET n.resolved_from = $r",
                        params={"id": f"obj-d{i}", "r": f"obj-d{i + 1}"})
        monkeypatch.setattr(v2, "_is_searchable_backend",
                            lambda mode, sdk=None: True)
        # The good row comes FIRST, so the fold has already resolved it when the
        # unresolvable row is met — the per-row failure must not discard it.
        monkeypatch.setattr(
            v2, "_fts_rows",
            lambda *a, **kw: [{"id": "obj-good", "name": "the ledger",
                               "kind": "core:plan"},
                              {"id": "obj-d0", "name": "deep 0",
                               "kind": "core:plan"}])
        search = v2.search_graph(sdk, {"entities": [{"name": "the ledger"}]},
                                 "the story", max_queries=1, limit=3)
        assert search["degraded"] is False, search
        assert [r["id"] for r in search["entities"]] == ["obj-good"], search
        assert "prior-set fold" in (search["reason"] or "")
        assert _failure_policy.lookup_state(search) == _failure_policy.LOOKUP_PARTIAL
        # An entity candidate's fixpoint was NOT established, so its own query
        # must not read as "ran and matched nothing": found None, never false.
        assert v2._candidate_covered(search, "object", "the ledger") is False

    def test_a_chain_longer_than_the_hop_bound_is_cannot_answer(self, sdk_factory):
        """A chain the bound cannot resolve must NOT be emitted as a carrier.

        `apply_entity_merge` hops the survivor first, so a chain longer than 1
        cannot be CREATED — a longer one is a corrupt/legacy record, i.e. exactly
        when a silent misreport is worst. The row's fixpoint is unknown, so the
        honest answer is cannot-answer — carrying the rows already folded, so one
        bad row cannot void the whole prior set.
        """
        sdk = sdk_factory()
        _create_object(sdk, "obj-good", "the ledger")
        depth = v2._PRIOR_HOP_BOUND + 2
        graph = sdk._get_proj().g
        for i in range(depth):
            _create_object(sdk, f"obj-c{i}", f"link {i}")
        for i in range(depth - 1):
            graph.query("MATCH (n:Object {id:$id}) SET n.resolved_from = $r",
                        params={"id": f"obj-c{i}", "r": f"obj-c{i + 1}"})
        rows = [{"id": "obj-good", "name": "the ledger", "kind": ""},
                {"id": "obj-c0", "name": "link 0", "kind": ""}]
        with pytest.raises(v2._PriorFoldUnresolvable,
                           match="resolved_from hops") as exc:
            v2._current_entity_priors(
                sdk, rows, {"obj-good": "Object", "obj-c0": "Object"})
        assert [r["id"] for r in exc.value.folded] == ["obj-good"], exc.value
        assert exc.value.unresolved == ["obj-c0"]

    def test_search_graph_folds_the_set_it_returns(self, sdk_factory, monkeypatch):
        """The wiring, not only the helper: S3's OWN return carries the survivor."""
        sdk = sdk_factory()
        _merged_pair(sdk)
        monkeypatch.setattr(v2, "_is_searchable_backend", lambda mode, sdk=None: True)
        monkeypatch.setattr(
            v2, "_fts_rows",
            lambda *a, **kw: [{"id": "obj-carrier", "name": "the team",
                               "kind": "core:plan"}])
        search = v2.search_graph(sdk, {"entities": [{"name": "the team"}]},
                                 "the story", max_queries=1, limit=3)
        assert [r["id"] for r in search["entities"]] == ["obj-survivor"], search
        assert search["entities"][0]["name"] == "Acme Holdings"


# ── the vocabulary ────────────────────────────────────────────────────────


class TestTheBandVocabulary:
    def test_three_bands_and_link_is_not_one_of_them(self):
        assert verdict.BANDS == ("merge", "review", "new")
        assert "link" not in verdict.BANDS
        # The pointer exists (`aliases[]` on the survivor) but a pointer is not a
        # verdict, and writing `dedup_reviewed='link'` dequeues the row from
        # content review (`sdk.py:24256-24257`) — so it is deliberately absent.
        assert not any(b == "link" for b in verdict.BANDS)

    def test_a_clean_pair_is_a_merge(self):
        out = verdict.judge({"name": "Acme Holdings", "kind": "core:org"},
                            {"name": "Acme Holdings Inc", "kind": "core:org"})
        assert out["band"] == verdict.BAND_MERGE
        assert out["vetoes"] == []

    def test_a_veto_never_licenses_new(self):
        """A contradiction says the two are DIFFERENT; it says nothing about
        absence, and conflating the two is how a precision fix manufactures
        duplicates (§4.4 I1)."""
        out = verdict.judge({"name": "billing v2 ledger", "kind": "core:plan"},
                            {"name": "billing v3 ledger", "kind": "core:plan"})
        assert out["band"] == verdict.BAND_REVIEW
        assert out["band"] != verdict.BAND_NEW


class TestHardVetoes:
    def test_disjoint_identifiers_veto_and_the_fold_does_not(self):
        vetoed = verdict.judge({"name": "billing v2 ledger", "kind": "k"},
                               {"name": "billing v3 ledger", "kind": "k"})
        assert vetoed["vetoes"] == [verdict.VETO_IDENTIFIER_CONFLICT]
        # #8933's class: separator/case variants are the SAME identifier, so the
        # veto must not fire across them — a fold that makes the class measurable
        # must not refuse the merges it enables.
        same = verdict.judge({"name": "ERR-4032 handler", "kind": "k"},
                             {"name": "err4032 handler", "kind": "k"})
        assert same["vetoes"] == []

    def test_an_identifier_on_only_one_side_is_not_a_conflict(self):
        out = verdict.judge({"name": "the ERR-4032 handler", "kind": "k"},
                            {"name": "the error handler", "kind": "k"})
        assert out["vetoes"] == []

    def test_disjoint_dates_veto_and_are_compared_like_with_like(self):
        assert verdict.judge({"name": "the Q3 plan", "kind": "k"},
                             {"name": "the Q4 plan", "kind": "k"})[
                                 "vetoes"] == [verdict.VETO_DATE_CONFLICT]
        assert verdict.judge({"name": "the 2026 plan", "kind": "k"},
                             {"name": "the 2027 plan", "kind": "k"})[
                                 "vetoes"] == [verdict.VETO_DATE_CONFLICT]
        # One side states a year, the other a quarter: nothing is contradicted.
        assert verdict.judge({"name": "the Q3 plan", "kind": "k"},
                             {"name": "the 2026 plan", "kind": "k"})["vetoes"] == []

    def test_a_declared_kind_conflict_vetoes_and_a_default_does_not(self):
        assert verdict.judge({"name": "acme", "kind": "core:org"},
                             {"name": "acme", "kind": "core:person"})[
                                 "vetoes"] == [verdict.VETO_KIND_CONFLICT]
        # `other` is the coalesce default (`entities.py:5086`) — a default must
        # never become evidence.
        assert verdict.judge({"name": "acme", "kind": "core:org"},
                             {"name": "acme", "kind": "other"})["vetoes"] == []

    def test_graph_facts_are_their_own_vetoes(self):
        mention = {"name": "the team", "kind": "k"}
        cand = {"name": "Acme Holdings", "kind": "k"}
        assert verdict.judge(mention, cand, {"retired": True})["vetoes"] == [
            verdict.VETO_TARGET_RETIRED]
        assert verdict.judge(mention, cand, {"dependents": 1})["vetoes"] == [
            verdict.VETO_GROUP_JOIN]
        assert verdict.judge(mention, cand, {"suppressed": True})["vetoes"] == [
            verdict.VETO_TARGET_SUPPRESSED]
        assert verdict.judge(mention, cand, {"dependents": 0})["band"] == "merge"

    def test_a_group_join_stands_down_when_the_alias_proves_the_spelling(self):
        """M-4 RE-VERIFIES a group join; it must not refuse the case the tree has
        already decided.

        `apply_entity_merge` writes the candidate's folded spelling into the
        survivor's `aliases[]`, and the by-name resolver reads `$k IN n.aliases`
        AS a resolution (`entity_resolver.py:611`) — so this spelling's membership
        is RECORDED, not inferred, and no group-level comparison is being skipped.
        Refusing it is not merely conservative: an ENTITY hold is not carried
        forward (`extractor_v2.execute_embed`, the `"ENTITY hold(s) are NOT carried forward"` branch (~`:12156`)), so the mention is dropped on
        every run for the lifetime of the merge.
        """
        mention = {"name": "the team", "kind": "k"}
        cand = {"name": "Acme Holdings", "kind": "k"}
        assert verdict.judge(mention, cand, {"dependents": 2})["vetoes"] == [
            verdict.VETO_GROUP_JOIN]
        assert verdict.judge(mention, cand, {"dependents": 2,
                                            "mention_is_alias": True})[
            "band"] == verdict.BAND_MERGE

    def test_the_veto_record_is_ordered_and_carries_a_sentence_each(self):
        mention = {"name": "billing v2 ledger Q3", "kind": "core:plan"}
        cand = {"name": "billing v3 ledger Q4", "kind": "core:person"}
        out = verdict.judge(mention, cand, {"retired": True, "dependents": 2})
        assert out["vetoes"] == [v for v in verdict.VETO_CODES
                                 if v in out["vetoes"]]
        assert len(out["vetoes"]) == len(out["reasons"])
        assert all(isinstance(s, str) and s for s in out["reasons"])


class TestClosureReverification:
    """M-4: `A matches B` + `B matches C` is not a verdict on `A`/`C`."""

    def test_a_retired_survivor_is_refused(self):
        assert verdict.closure_vetoes("a", "b", [("b", "c")]) == [
            verdict.VETO_TARGET_RETIRED]

    def test_a_candidate_carrying_dependents_is_refused(self):
        assert verdict.closure_vetoes("a", "b", [("d", "a")]) == [
            verdict.VETO_GROUP_JOIN]

    def test_a_pair_with_no_history_is_allowed(self):
        assert verdict.closure_vetoes("a", "b", [("x", "y")]) == []

    def test_chain_length_is_cycle_guarded(self):
        assert verdict.chain_length("a", [("a", "b"), ("b", "c")]) == 2
        assert verdict.chain_length("a", [("a", "b"), ("b", "a")]) == 1
        assert verdict.chain_length("a", []) == 0

    def test_the_merge_verb_cannot_create_a_chain(self, sdk_factory):
        """The closure guard's positive half, on a real graph.

        Merging onto a survivor that is ITSELF retired would write
        `candidate -> survivor -> fixpoint`, a second hop the reviewer never saw
        that `follow_resolved_from` then collapses silently. The verb hops the
        survivor first, so no chain can be created — the invariant the verdict's
        `dependents >= 1` reading depends on.
        """
        sdk = sdk_factory()
        _merged_pair(sdk)                      # carrier -> obj-survivor
        _create_object(sdk, "obj-third", "Acme Holdings Ltd")
        result = apply_entity_merge(sdk, "obj-third", "obj-carrier",
                                    method="exact", similarity=1.0)
        assert result["survivor_id"] == "obj-survivor", result
        assert result.get("survivor_hopped") is True
        rows = sdk._get_proj().g.query(
            "MATCH (n:Object) WHERE n.id IN ['obj-carrier','obj-third'] "
            "RETURN n.id, n.resolved_from").result_set
        # Both point STRAIGHT at the fixpoint — a chain of length 1, never 2.
        assert {str(r[1]) for r in rows} == {"obj-survivor"}, rows


# ── the verdict as the resolver's gate ───────────────────────────────────


def _search(names, **extra):
    """S3's shape: `{"entities": [{id, name, kind}], "degraded": False}`."""
    return {"entities": [{"id": f"obj-{i}", "name": n, "kind": k}
                         for i, (n, k) in enumerate(names)],
            "degraded": False, **extra}


class _Model:
    """A phase-2 model double: returns one canned answer, never calls a provider."""

    def __init__(self, payload):
        self.payload = payload

    def __call__(self, *_a, **_kw):
        return self.payload


class TestVetoesAreSilentOnTheGenuineVariantCorpus:
    """The available half of #12296's falsifier, run against `#11901`'s own generator.

    The issue's falsifier asks whether the bands are materially better than
    "always merge the top candidate" — which needs a hand-labelled gold set and a
    model, neither available here. What IS available and is the direction that
    matters first: the vetoes must not refuse the variant classes `#11901`
    measured, because those are the MERGES we want. A veto that fires on
    `tortoise/sdk.py` → `sdk.py` buys precision by re-opening the recall defect
    the epic exists to close.

    The variant list is the instrument's (`tools/candidate_recall_11901.py::
    variants`), not a fixture written to pass: same classes, same shapes.
    """

    def _variants(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "candidate_recall_11901",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tools", "candidate_recall_11901.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.variants

    @pytest.mark.parametrize("name,cls", [
        ("the owner", "definite"),
        ("the play doc", "definite"),
        ("release-notes", "definite"),
        ("reconcile-ledger", "definite"),
        ("the Q3 plan", "definite"),
        ("#11901", "ident"),
        ("hosts.yml", "path"),
        ("tortoise/sdk.py", "path"),
        ("test_12047_entity_resolution.py", "path"),
        ("docs/research/2026-11-12-12186-search-inventory/research-brief.md",
         "path"),
    ])
    def test_no_veto_fires_on_a_measured_variant_class(self, name, cls):
        variants = self._variants()
        for variant_cls, surface in variants(name, cls):
            out = verdict.judge({"name": surface, "kind": "core:plan"},
                                {"name": name, "kind": "core:plan"})
            assert out["vetoes"] == [], (variant_cls, surface, name, out)

    def test_the_dated_path_class_is_the_case_that_needs_the_skeleton(self):
        """`basename-only` on a path carrying a DATE would otherwise read as a
        conflict: the stem `2026-11-12-12186-...` versus the basename. Pinned as its
        own case because it is the reason `skeleton` exists."""
        long = "docs/research/2026-11-12-12186-search-inventory/research-brief.md"
        assert verdict.judge({"name": "research-brief.md", "kind": "k"},
                             {"name": long, "kind": "k"})["vetoes"] == []


class TestTheResolverBands:
    def test_a_vetoed_proposal_is_held_neither_resolved_nor_new(self, monkeypatch):
        monkeypatch.setattr(
            v2, "_complete",
            lambda *a, **kw: '{"resolutions": [{"name": "billing v2 ledger", '
                             '"resolves_to": "obj-0"}], "new": []}')
        res = v2.resolve_entities(
            [{"name": "billing v2 ledger", "kind": "core:plan"}],
            _search([("billing v3 ledger", "core:plan")]),
            model=object())
        assert res["map"] == {}
        assert res["new"] == []
        assert [h["name"] for h in res["held"]] == ["billing v2 ledger"]
        assert res["held"][0]["reason"] == v2.HOLD_VETOED
        assert res["held"][0]["vetoes"] == [verdict.VETO_IDENTIFIER_CONFLICT]

    def test_a_clean_proposal_records_the_band_it_reached(self, monkeypatch):
        monkeypatch.setattr(
            v2, "_complete",
            lambda *a, **kw: '{"resolutions": [{"name": "Acme", '
                             '"resolves_to": "obj-0"}], "new": []}')
        res = v2.resolve_entities(
            [{"name": "Acme", "kind": "core:org"}],
            _search([("Acme Holdings", "core:org")]),
            model=object())
        assert res["map"]["Acme"]["id"] == "obj-0"
        assert res["records"][0]["band"] == verdict.BAND_MERGE
        assert res["records"][0]["mode"] == "llm"

    def test_an_exact_match_on_a_RETIRED_candidate_is_held(self):
        """D-1's second door: a `search` dict built by another caller.

        The S3 guard folds the carrier upstream, but a replayed run, a test or
        `_index_search` can hand the resolver a table with the carrier in it. An
        exactly-equal spelling is the strongest evidence the tree has — and it is
        still wrong when the target is a node the merge retired.
        """
        res = v2.resolve_entities(
            [{"name": "the team", "kind": "core:plan"}],
            _search([("the team", "core:plan")]),
            facts={"obj-0": {"retired": True}})
        assert res["map"] == {}
        assert [h["reason"] for h in res["held"]] == [v2.HOLD_VETOED]
        assert res["held"][0]["vetoes"] == [verdict.VETO_TARGET_RETIRED]

    def test_a_group_join_does_not_block_an_exact_re_mention(self):
        """`dependents >= 1` refuses an INFERRED membership, never an exact
        spelling: the merge queue's own rule lets an exact re-mention bind
        (`_pair_reviewed`), and refusing it would hold every mention of every
        merged entity."""
        res = v2.resolve_entities(
            [{"name": "Acme Holdings", "kind": "core:org"}],
            _search([("Acme Holdings", "core:org")]),
            facts={"obj-0": {"retired": False, "dependents": 3}})
        assert res["map"]["Acme Holdings"]["id"] == "obj-0"
        assert res["held"] == []

    def test_no_facts_leaves_the_name_and_kind_vetoes_running(self, monkeypatch):
        """An offline caller keeps the deterministic rules — the fact-dependent
        vetoes are simply not reachable, and no default is invented for them."""
        monkeypatch.setattr(
            v2, "_complete",
            lambda *a, **kw: '{"resolutions": [{"name": "the Q3 plan", '
                             '"resolves_to": "obj-0"}], "new": []}')
        res = v2.resolve_entities(
            [{"name": "the Q3 plan", "kind": "core:plan"}],
            _search([("the Q4 plan", "core:plan")]),
            model=object())
        assert res["held"][0]["reason"] == v2.HOLD_VETOED

    def test_the_held_vocabulary_is_published(self):
        assert v2.HOLD_VETOED in v2.HOLD_REASONS
        assert len(set(v2.HOLD_REASONS)) == len(v2.HOLD_REASONS)

    def test_a_repeat_mention_of_a_folded_alias_merges_instead_of_being_lost(
            self, sdk_factory, monkeypatch):
        """The case `group_join` must NOT refuse, end to end on a real graph.

        After `the team → Acme Holdings` the survivor's `aliases[]` holds the
        folded `team`, so phase 2's proposal to bind to it carries the tree's own
        record of the decision. Before the alias escape this was HELD — and an
        entity hold is not carried forward, so the mention was dropped on every
        run, for the lifetime of the merge.
        """
        sdk = sdk_factory()
        _merged_pair(sdk)
        facts = v2._entity_verdict_facts(sdk, [{"id": "obj-survivor"}])
        assert facts["obj-survivor"]["dependents"] == 1, facts
        monkeypatch.setattr(
            v2, "_complete",
            lambda *a, **kw: '{"resolutions": [{"name": "the team", '
                             '"resolves_to": "obj-survivor"}], "new": []}')
        res = v2.resolve_entities(
            [{"name": "the team", "kind": "core:plan"}],
            {"entities": [{"id": "obj-survivor", "name": "Acme Holdings",
                           "kind": "core:plan"}], "degraded": False},
            model=object(), facts=facts)
        assert res["map"].get("the team", {}).get("id") == "obj-survivor", res
        assert res["held"] == [], res
        assert res["records"][0]["band"] == verdict.BAND_MERGE

    def test_the_facts_reader_and_the_resolver_are_wired_together(
            self, sdk_factory):
        """The two FUNCTIONS compose: a foreign `search` table + the real reader.

        A `search` table built by another caller (a replay, `_index_search`, a
        test) can still carry a merge-retired carrier; feeding the REAL facts
        reader into the resolver is what holds it. This covers the pair — the
        argument at the `extract_session_v2` call site is pinned separately by
        `test_the_orchestrator_passes_the_facts_reader_output_to_the_resolver`.
        """
        sdk = sdk_factory()
        _merged_pair(sdk)
        rows = [{"id": "obj-carrier", "name": "the team",
                 "kind": "core:plan"}]
        res = v2.resolve_entities(
            [{"name": "the team", "kind": "core:plan"}],
            {"entities": rows, "degraded": False},
            facts=v2._entity_verdict_facts(sdk, rows))
        assert res["map"] == {}, res
        assert [h["reason"] for h in res["held"]] == [v2.HOLD_VETOED]
        assert res["held"][0]["vetoes"] == [verdict.VETO_TARGET_RETIRED]

    def test_the_orchestrator_passes_the_facts_reader_output_to_the_resolver(
            self, monkeypatch):
        """The PRODUCTION call site's `facts=` argument (P2 round 1, unfixed then).

        Nothing else pins it: delete `facts=_entity_verdict_facts(...)` from
        `extract_session_v2` and every other test still passes, because the
        orchestrator suites stub `search_graph` with `sdk=None` (so the reader
        returns `{}` and no veto is reachable). Here the argument's PRESENCE is
        the assertion — its value is covered by the facts-reader tests.

        The extraction harness is `tests/test_11653_s3_hold_not_add.py`'s: a
        model double keyed on the stage prompt, and `search_graph` stubbed.
        """
        embed = {"entities": [{"name": "Acme", "kind": "core:organisation"}],
                 "events": [],
                 "points": [{"content": "Acme signed the deal",
                             "pointKind": "statement",
                             "about_entities": ["Acme"]}],
                 "operators": [], "chain_notes": [],
                 "link_before_create": []}
        empty_gaps = {"entities": [], "events": [], "points": [],
                      "operators": [], "chain_notes": [],
                      "link_before_create": []}

        def responder(system, user):
            if "STORY SUMMARIZER" in system:
                return "Acme signed the deal."
            if "GRAPH MAPPER" in system:
                return json.dumps(embed)
            if "GAP REVIEWER" in system:
                return json.dumps(empty_gaps)
            if "ENTITY RESOLUTION" in system:
                return json.dumps({"resolutions": [], "new": ["Acme"]})
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        class _M:
            def complete(self, *, system: str, user: str):
                return responder(system, user)

        seen: dict = {}
        real = v2.resolve_entities

        def recorder(*a, **kw):
            seen.update(kw)
            return real(*a, **kw)

        monkeypatch.setattr(v2, "resolve_entities", recorder)
        monkeypatch.setattr(
            v2, "search_graph",
            lambda sdk, embed_list, story, **kw: {
                "entities": [{"id": "obj-acme", "name": "Acme",
                              "kind": "core:organisation"}],
                "points": [], "events": [], "degraded": False})
        out = v2.extract_session_v2(
            _M(), [{"role": "user", "content": "Acme signed the deal."}],
            session_id="s1")
        assert out["payload"] is not None, out
        assert "facts" in seen, (
            "extract_session_v2 did not pass the facts reader's output to "
            "resolve_entities — the D-1 second door is open for every search "
            "table built by another caller")
        assert isinstance(seen["facts"], dict), seen


class TestTheFactsReader:
    def test_a_merged_carrier_reads_as_retired_and_the_survivor_as_a_group(
            self, sdk_factory):
        sdk = sdk_factory()
        _merged_pair(sdk)
        facts = v2._entity_verdict_facts(
            sdk, [{"id": "obj-carrier"}, {"id": "obj-survivor"}])
        assert facts["obj-carrier"]["retired"] is True
        assert facts["obj-carrier"]["dependents"] == 0
        assert facts["obj-survivor"]["retired"] is False
        assert facts["obj-survivor"]["dependents"] == 1

    def test_a_target_less_rejection_reads_as_suppressed(self, sdk_factory):
        sdk = sdk_factory()
        _create_object(sdk, "obj-rejected", "the plan",
                       dedup_reviewed="reject", reviewed=True)
        facts = v2._entity_verdict_facts(sdk, [{"id": "obj-rejected"}])
        assert facts["obj-rejected"]["suppressed"] is True

    def test_a_targeted_rejection_is_not_suppressed(self, sdk_factory):
        """It is PAIR-scoped; the verdict layer must not turn one pair's verdict
        into a blanket refusal."""
        sdk = sdk_factory()
        _create_object(sdk, "obj-rejected", "the plan",
                       dedup_reviewed="reject", reviewed=True,
                       dedup_target_id="obj-other")
        facts = v2._entity_verdict_facts(sdk, [{"id": "obj-rejected"}])
        assert facts["obj-rejected"]["suppressed"] is False

    def test_the_survivor_facts_carry_its_aliases(self, sdk_factory):
        """The alias escape's input, read from a real merge.

        `normalize_entity_name` drops a leading article, so `the team` is stored as
        `team` — and the veto compares with the SAME fold, so the two cannot
        disagree about a spelling.
        """
        sdk = sdk_factory()
        _merged_pair(sdk)
        facts = v2._entity_verdict_facts(sdk, [{"id": "obj-survivor"}])
        assert "team" in facts["obj-survivor"]["aliases"], facts

    def test_no_candidates_means_no_read(self):
        class _Sdk:
            def _get_proj(self):        # pragma: no cover - must not be called
                raise AssertionError("no candidate ids, no read")

        assert v2._entity_verdict_facts(_Sdk(), []) == {}
