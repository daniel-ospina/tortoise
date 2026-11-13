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

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise import entity_verdict as verdict
from tortoise import extractor_v2 as v2
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

    def test_no_candidates_means_no_read(self):
        class _Sdk:
            def _get_proj(self):        # pragma: no cover - must not be called
                raise AssertionError("no candidate ids, no read")

        assert v2._entity_verdict_facts(_Sdk(), []) == {}
