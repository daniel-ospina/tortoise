"""#11653 — S3's FAILED disambiguation holds the candidate; it never becomes an ADD.

THE DEFECT THIS PINS
--------------------
S3 (RESOLVE) is the single authority on whether a candidate is new or existing
(§4.2). `resolve_entities` — its D3 implementation — ran phase 1 (mechanical
`_find_existing_entity`) and one bounded phase-2 LLM call for the remainder, and
then returned **only its resolution `map`**. Everything outside that map was read
by the caller as *"create it"*, and the docstring said so out loud:

    Failure/timeout → warning + phase-1-only map (degrade to ADD semantics at
    the caller — unresolved entities keep their names).

So BOTH failure shapes minted the thing they could not identify:

* a **phase-2 failure/timeout**, and
* an **ambiguous verdict**, which the phase-2 prompt answered by instructing
  *"ambiguous → omit"* — an omission indistinguishable from *"this is a
  confidently new entity"*, because the contract had no word for either.

§4.4's **I1** — *"An unresolved identity is NEVER asserted `new`"* — and its S3
disambiguation row — *"FAIL-CLOSED on `new`. A failed disambiguation `HOLD`s the
candidate — unless the lookup returned exactly ONE exact-key match, which
attaches mechanically with no model"* — were therefore unenforced on this seam.
The natural fail-open **manufactures the duplicate S3 exists to prevent**.

WHAT THIS PINS
--------------
1. **Held, not minted.** A failed/undecided/ambiguous disambiguation puts the
   candidate in `resolve_entities`' `held` population, with its reason, and the
   payload — which IS the commit's create list — does not carry it. The GRAPH
   node count is asserted, not only the payload.
2. **The two outcomes are DISTINGUISHABLE.** `new` (licensed ADD) and `held`
   (unresolved) are disjoint and jointly cover every unmatched name; the phase-2
   contract carries an explicit `new` verdict, so an omission means *undecided*.
3. **The add path is not frozen.** The design's own exception (one exact-key
   match, mechanical, no model) still attaches, a mechanical `none` with no
   resolver still creates, and an explicit `new` verdict still creates.
4. **Recorded loudly (I2).** The hold reaches `result["resolution_held"]`,
   `stats["held_reasons"]` and the `entity_identity_held` census class — never
   only `warnings` — and it is NOT written to the `hold_store`, whose drain mints
   Points (an entity record there would be re-minted as a claim).
5. **Non-vacuity** (the mutation arm): the SAME fixture with a resolver that
   answers `new` mints again, so the assertions read the gate and not the fixture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100

_CANDIDATE = {"id": "obj_acme", "name": "Acme Corporation",
              "kind": "core:organisation"}

_EMBED = {"entities": [{"name": "Acme", "kind": "core:organisation"}],
          "events": [], "points": [], "operators": []}


def _search(*entities: dict) -> dict:
    """A COMPLETE (`resolved`) lookup that returned the given candidates."""
    return {
        "mode": "real", "degraded": False, "reason": None,
        "entities": list(entities), "points": [], "events": [],
        "queries_run": 4,
        "query_coverage": {"derived": {"object": 1, "event": 1, "point": 2},
                           "ran": {"object": 1, "event": 1, "point": 2},
                           "failed": {"object": 0, "event": 0, "point": 0}},
    }


class _Boom:
    """Phase 2 times out — the failure arm the old code degraded to ADD."""

    def complete(self, **kw):
        raise TimeoutError("wedged resolver call")


class _Says:
    """A phase-2 answer, verbatim."""

    def __init__(self, body: dict):
        self.body = body

    def complete(self, **kw):
        return json.dumps(self.body)


class _Spy:
    """A model that must never be called (a mechanical decision)."""

    def complete(self, **kw):
        raise AssertionError("the mechanical path called the model")


def _ref(name: str = "Acme", kind: str = "core:organisation") -> list[dict]:
    return [{"name": name, "kind": kind}]


def _held_names(res: dict) -> dict[str, str]:
    return {h["name"]: h["reason"] for h in res["held"]}


class TestTheDefectIsGone:
    def test_a_failed_disambiguation_holds_the_candidate(self):
        """RED-FIRST: the phase-2 failure must not reach the payload."""
        res = v2.resolve_entities(_ref(), _search(_CANDIDATE), model=_Boom())
        assert res["map"] == {}, res["map"]
        assert res["new"] == [], (
            "a candidate whose disambiguation FAILED was licensed as new — the "
            "duplicate S3 exists to prevent (§4.4 I1)")
        assert _held_names(res) == {"Acme": v2.HOLD_DISAMBIGUATION_FAILED}, res["held"]

        out = v2.execute_embed(_EMBED, _search(_CANDIDATE), session_id="s1",
                               held_entities=_held_names(res))
        assert out["payload"]["entities"] == [], (
            "the payload IS the commit's create list, so an entry here is a node")
        assert out["stats"]["entities"] == 0
        assert out["stats"]["held_entities"] == 1
        assert out["stats"]["held_reasons"] == {v2.HOLD_DISAMBIGUATION_FAILED: 1}
        assert out["held"][0]["kind"] == "entity"
        assert out["held"][0]["reason"] == v2.HOLD_DISAMBIGUATION_FAILED
        assert "link_before_create" not in out or all(
            "Acme" not in str(r.get("searched_for")) for r in out["link_before_create"]), (
            "a create that did not happen must not publish a searched_for record")
        assert any("S3 HOLD" in w and "disambiguation_failed" in w
                   for w in out["warnings"]), out["warnings"]

    def test_the_graph_node_count_does_not_increase(self, sdk_factory):
        """The claim the issue makes, measured on a real graph: no second node."""
        sdk = sdk_factory()
        sdk.create_entity("object", "Acme Corporation",
                          objectKind="core:organisation", is_episodic=False)
        proj = sdk._get_proj()

        def objects() -> dict[str, str]:
            return {r[0]: r[1] for r in proj.g.query(
                "MATCH (o:Object) RETURN o.id, o.name").result_set}

        before = objects()
        assert list(before.values()) == ["Acme Corporation"]

        res = v2.resolve_entities(_ref(), _search(_CANDIDATE), model=_Boom())
        out = v2.execute_embed(_EMBED, _search(_CANDIDATE), session_id="s1",
                               held_entities=_held_names(res))
        # Exactly what the commit does with the payload's entity list.
        for e in out["payload"]["entities"]:
            sdk.create_entity("object", e["name"],
                              objectKind=e.get("kind") or None,
                              is_episodic=False)

        after = objects()
        assert after == before, (
            f"the failed lookup minted a duplicate: {before} → {after}")
        assert len(after) == 1
        # …and the existing node is what a later resolved run would attach to.
        assert res["records"] == [] and "Acme" in _held_names(res)

    def test_the_mutation_arm_mints_when_the_resolver_answers(self):
        """NON-VACUITY: an explicit `new` verdict is the ADD licence."""
        res = v2.resolve_entities(
            _ref(), _search(_CANDIDATE),
            model=_Says({"resolutions": [], "new": ["Acme"]}))
        assert res["held"] == []
        assert res["new"] == ["Acme"]
        out = v2.execute_embed(_EMBED, _search(_CANDIDATE), session_id="s1",
                               held_entities=_held_names(res))
        assert [e["name"] for e in out["payload"]["entities"]] == ["Acme"], (
            "the add path was frozen: a confidently-new entity must still be "
            "created, or the fix trades a duplicate for a lost entity")
        assert out["held"] == []

    def test_the_docstring_states_the_hold_not_the_degrade(self):
        """The issue's third indicator: the next reader must not be told the
        opposite of the ruling."""
        doc = v2.resolve_entities.__doc__ or ""
        assert "degrade to ADD" not in doc
        assert "NEVER raises" in doc
        assert "held" in doc and "new" in doc


class TestTheOtherArms:
    def test_an_ambiguous_collision_is_held_without_a_model(self):
        """Two namespaces collide on the bare form: phase 1 refuses to guess, and
        with no resolver there is no decision — so the candidate waits."""
        colliding = [{"id": "a1", "name": "Acme", "kind": "dev:person"},
                     {"id": "a2", "name": "Acme", "kind": "ops:person"}]
        res = v2.resolve_entities(_ref("Acme", "core:person"),
                                  _search(*colliding), model=None)
        assert res["map"] == {}
        assert _held_names(res) == {"Acme": v2.HOLD_AMBIGUOUS}, res["held"]
        out = v2.execute_embed(
            {"entities": [{"name": "Acme", "kind": "core:person"}],
             "events": [], "points": [], "operators": []},
            _search(*colliding), session_id="s1",
            held_entities=_held_names(res))
        assert out["payload"]["entities"] == []

    def test_the_resolver_may_still_pick_one_of_the_collision(self):
        """A phase-2 resolution of a genuinely AMBIGUOUS name attaches — and the
        resolved name is in NEITHER population (#11653 review).

        ⛔ Both colliding candidates must be present, or phase 1 answers `bare`
        and phase 2 never runs — the arm then turns on nothing (the vacuous
        shape this test was caught carrying).
        """
        colliding = [{"id": "a1", "name": "Acme", "kind": "dev:person"},
                     {"id": "a2", "name": "Acme", "kind": "ops:person"}]
        res = v2.resolve_entities(
            _ref("Acme", "core:person"), _search(*colliding),
            model=_Says({"resolutions": [{"name": "Acme",
                                          "resolves_to": "a2"}], "new": []}))
        assert res["map"]["Acme"]["id"] == "a2", res["map"]
        assert res["held"] == [] and res["new"] == []

    def test_a_resolved_name_is_never_reported_held(self):
        """The alias case phase 2 EXISTS for: `Joe → Joseph` (phase 1 says `none`,
        the resolver maps it). The resolved name must be in `map` ONLY.

        Review finding (#11653): the disposition loop walked every unmatched
        name, so `Joe` was published as `held (disambiguation_undecided)` — and
        `extract_session_v2` bumped `entity_identity_held` — on a run that had
        just created `Joseph`. Fail-closed behaviour, FALSE report: I2's census
        claimed a hold that did not happen, on the success path.
        """
        res = v2.resolve_entities(
            _ref("Joe", "core:person"),
            _search({"id": "obj_j", "name": "Joseph",
                     "kind": "core:person"}),
            model=_Says({"resolutions": [{"name": "Joe",
                                          "resolves_to": "Joseph"}]}))
        assert res["map"]["Joe"]["id"] == "obj_j"
        assert res["held"] == [], (
            "a RESOLVED name was reported as held — a false hold on the "
            f"success path: {res['held']}")
        assert res["new"] == []
        assert not any("UNRESOLVED" in w for w in res["warnings"]), res["warnings"]

    def test_an_undecided_answer_holds(self):
        """An omission is now a HOLD signal, not an implicit ADD."""
        res = v2.resolve_entities(
            _ref(), _search(_CANDIDATE),
            model=_Says({"resolutions": []}))
        assert _held_names(res) == {"Acme": v2.HOLD_DISAMBIGUATION_UNDECIDED}
        assert res["new"] == []

    def test_an_invalid_resolution_holds_rather_than_minting(self):
        """A resolution the candidate table cannot validate is not evidence of
        absence — the old code warned and let the caller create the entity."""
        res = v2.resolve_entities(
            _ref(), _search(_CANDIDATE),
            model=_Says({"resolutions": [{"name": "Acme",
                                          "resolves_to": "NotAnEntity"}],
                         "new": []}))
        assert res["map"] == {}
        assert any("does not match an existing entity" in w
                   for w in res["warnings"]), res["warnings"]
        assert _held_names(res) == {"Acme": v2.HOLD_DISAMBIGUATION_UNDECIDED}

    def test_a_phase1_exact_match_attaches_with_no_model_call(self):
        """§4.4's stated exception, unchanged by this fix."""
        res = v2.resolve_entities(_ref("Acme Corporation"),
                                  _search(_CANDIDATE), model=_Spy())
        assert res["map"]["Acme Corporation"]["id"] == "obj_acme"
        assert res["records"][0]["mode"] == "exact"
        assert res["held"] == [] and res["new"] == []

    def test_a_mechanical_absence_with_no_resolver_is_still_new(self):
        """The ADD path for a genuinely new entity: candidates exist, no resolver
        is configured, and phase 1 finds no candidate of that name."""
        res = v2.resolve_entities(_ref(), _search(_CANDIDATE), model=None)
        assert res["new"] == ["Acme"]
        assert res["held"] == []

    def test_a_run_with_no_candidates_still_creates(self):
        """Nothing to disambiguate against: phase 2 cannot fire, so the
        mechanical answer stands."""
        res = v2.resolve_entities(_ref(), _search(), model=_Spy())
        assert res["new"] == ["Acme"]
        assert res["held"] == []

    def test_the_two_populations_are_disjoint_and_total(self):
        """Every unmatched name lands in EXACTLY one population — the property
        that makes \"failed\" and \"absent\" distinguishable at all."""
        res = v2.resolve_entities(
            [{"name": "Joseph", "kind": "core:person"},
             {"name": "Acme", "kind": "core:organisation"},
             {"name": "Zeta", "kind": "core:project"}],
            _search({"id": "obj_j", "name": "Joseph", "kind": "core:person"},
                    _CANDIDATE,
                    {"id": "a1", "name": "Acme", "kind": "dev:org"},
                    {"id": "a2", "name": "Acme", "kind": "ops:org"}),
            model=_Says({"resolutions": [], "new": ["Zeta"]}))
        assert res["map"]["Joseph"]["id"] == "obj_j"
        unmatched = {"Acme", "Zeta"}
        assert set(res["new"]) == {"Zeta"}, res["new"]
        assert set(_held_names(res)) == {"Acme"}, res["held"]
        assert set(res["new"]) & set(_held_names(res)) == set()
        assert set(res["new"]) | set(_held_names(res)) == unmatched


class TestTheRunReports:
    """The orchestrator arm — `extract_session_v2`, the real caller."""

    def _model(self, resolution_response):
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
                return resolution_response(system, user)
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        class _M:
            def complete(self, *, system: str, user: str):
                return responder(system, user)

        return _M()

    def _run(self, monkeypatch, responder):
        def fake_search(sdk, embed_list, story, **kw):
            return _search(_CANDIDATE)

        monkeypatch.setattr(v2, "search_graph", fake_search)
        conv = [{"role": "user", "content": "Acme signed the deal."}]
        return v2.extract_session_v2(self._model(responder), conv,
                                     session_id="s1")

    def test_the_orchestrator_holds_the_entity_and_censors_it(self, monkeypatch):
        def boom(system, user):
            raise RuntimeError("resolver backend down")

        out = self._run(monkeypatch, boom)
        assert out["payload"] is not None, "capture must never be blocked"
        assert [e["name"] for e in out["payload"]["entities"]] == [], (
            "a failed disambiguation minted the entity it could not identify")
        assert out["payload"]["points"], "the batch's own claims still commit"
        assert [h["name"] for h in out["resolution_held"]] == ["Acme"]
        assert out["resolution_held"][0]["reason"] == v2.HOLD_DISAMBIGUATION_FAILED
        assert out["stats"]["resolution_held"] == 1
        assert out["error_census"].get("entity_identity_held") == 1, (
            "I2: the hold is reported on the CENSUS, not only in warnings")
        assert any("HELD, not created" in w for w in out["warnings"])

    def test_an_entity_hold_is_never_written_to_the_hold_store(self, tmp_path):
        """The trap this fix must not walk into: the hold store's DRAIN mints
        Points, so an entity written there would be re-minted as a claim."""
        from tortoise import identity_hold as hold

        store = hold.HoldStore(tmp_path / "hold.jsonl")
        res = v2.resolve_entities(_ref(), _search(_CANDIDATE), model=_Boom())
        out = v2.execute_embed(_EMBED, _search(_CANDIDATE), session_id="s1",
                               held_entities=_held_names(res), hold_store=store)
        assert out["stats"]["held_entities"] == 1
        assert store.pending() == [], (
            "an ENTITY was written to a store whose drain mints Points — a later "
            "run would mint the entity's name as a claim Point")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
