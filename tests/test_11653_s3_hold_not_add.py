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
6. **One node, never the session** (#11693 review, P1). A held entity is
   stripped from every reference set Layer-1 resolves — the points' and events'
   `about_entities`, their typed `subject`/`object` slots — and operators naming
   it are dropped, so the payload the hold produces still PASSES `commit_schema`.
   ⛔ Without that strip the payload is a 422: the entity is absent from
   `entities[]` while the claims still name it, so `commit_session` returns
   `ok=False` without POSTing and the duplicate is traded for memory loss — the
   one trade I2 forbids, and the hazard `route_mentions` (the pre-existing hold)
   was built to avoid.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import commit_schema  # noqa: E402, RUF100
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


class TestTheHoldRemovesOneNodeNotTheSession:
    """⛔ The held entity's name must not be left dangling in the payload.

    Review finding 1 (#11693), the blocking one: `execute_embed` withheld the
    entity from `payload["entities"]` — which IS the commit's create list — but
    nothing removed it from the claims and events that NAME it. Layer-1 resolves
    both of those sets against the emitted entities, so the pre-fix payload was
    refused as a whole: `tortoise/commit_schema.py` reports
    `about_entities reference unknown entities: ['Acme']`,
    `tortoise/sdk.py::commit_session` folds `not l1.ok` into `errors` and returns
    `ok=False` WITHOUT POSTing, and the hosted door answers 422 before the first
    write. "One duplicate node" was traded for "no memory at all".

    The remedy is the RECORDED one — the same move `route_mentions` already makes
    for its own hold (`extractor_v2.py::_strip_reference_sets`, whose docstring
    says *"a hold that 422s the whole session … is worse than the phantom node it
    removed"*): strip the held name from every claim's/event's `about_entities`
    and from the typed `subject`/`object` slots, and DROP operators naming it
    (the mint-before-wire pass would otherwise re-materialise it as a fabricated
    statement Point, #2552).

    ⛔ These assertions run the PRODUCED payload through the real Layer-1 gate.
    Asserting the payload's shape without asking the gate is how the invalid
    shape shipped: the previous arm asserted `points` non-empty while
    `entities == []` and never looked at the referential rule that reads BOTH.
    """

    def _search_body(self) -> dict:
        return {
            "mode": "real", "degraded": False, "reason": None,
            "entities": [dict(_CANDIDATE)], "points": [], "events": [],
            "queries_run": 4,
            "query_coverage": {"derived": {"object": 1, "event": 1, "point": 2},
                               "ran": {"object": 1, "event": 1, "point": 2},
                               "failed": {"object": 0, "event": 0, "point": 0}},
        }

    def _embed(self) -> dict:
        """One claim and one event that NAME the held entity, plus an operator
        whose endpoint is it — every reference set Layer-1 resolves."""
        return {
            "entities": [{"name": "Acme", "kind": "core:organisation"}],
            "events": [{
                "content": "Acme shipped the release",
                "eventKind": "core:occurrence",
                "about_entities": ["Acme"],
                "startedAt": None,
                "slots": {"subject": [{"name": "Acme",
                                        "kind": "core:organisation"}],
                          "object": [], "event": []},
            }],
            "points": [{
                "content": "Acme signed the deal",
                "pointKind": "statement",
                "about_entities": ["Acme"],
                "slots": {"subject": [{"name": "Acme",
                                        "kind": "core:organisation",
                                        "confidence": 0.9}],
                          "object": [], "event": []},
            }],
            "operators": [{"src": "Acme",
                           "dst": "Acme shipped the release",
                           "op_type": "IMPL"}],
            "chain_notes": [], "link_before_create": [],
        }

    def test_the_payload_the_hold_produces_passes_layer1(self):
        """The assertion that would have caught finding 1."""
        out = v2.execute_embed(self._embed(), self._search_body(), session_id="s1",
                               held_entities={"Acme": v2.HOLD_DISAMBIGUATION_FAILED})
        payload = out["payload"]
        assert payload is not None, "capture must never be blocked"
        # The hold itself still holds: no create, one node fewer.
        assert payload["entities"] == []
        assert [p["content"] for p in payload["points"]] == ["Acme signed the deal"]
        # …and every reference that named it is gone, in the payload the door reads.
        assert payload["points"][0]["about_entities"] == [], (
            f"the claim still names the held entity: "
            f"{payload['points'][0]['about_entities']!r} — Layer-1 refuses the "
            "whole session on it")
        assert "slots" not in payload["points"][0], (
            "the typed slot still resolves to a held entity — Layer-1 accepts "
            "`about_entities` and refuses the SLOT, so both halves must go")
        assert payload["events"][0]["about_entities"] == [], payload["events"][0]
        assert "slots" not in payload["events"][0], payload["events"][0]
        # Dropped, not minted: an endpoint naming the held entity would be
        # fabricated into a statement Point by the mint-before-wire pass (#2552).
        assert payload["operators"] == [], payload["operators"]
        assert [p["content"] for p in payload["points"]] == ["Acme signed the deal"]
        # Measured, not inferred: 1 claim + 1 event carrier changed, 1 operator
        # dropped, and the run says so in words.
        assert out["stats"]["held_entity_refs_stripped"] == 2, out["stats"]
        assert out["stats"]["held_entity_operators_dropped"] == 1, out["stats"]
        assert any("the hold removes ONE node, never the session" in w
                   for w in out["warnings"]), out["warnings"]
        # ⛔ THE GATE ITSELF, over the payload this run produced.
        l1, _ = commit_schema.validate_payload_dict(payload)
        assert l1.ok, (
            "the payload the S3 hold produces is REFUSED by Layer-1, so the "
            f"session is never committed (the 422 the fix must not create): "
            f"{l1.errors}")

    def test_the_unstripped_shape_is_the_rejection_this_fix_removes(self):
        """NON-VACUITY: put the references back and the SAME gate refuses it.

        Without this arm the test above could pass on a gate that never read the
        referential rule at all — the shape the pre-fix code produced is exactly
        this payload with the names left in, so the pair pins the differential
        and not the fixture.
        """
        out = v2.execute_embed(self._embed(), self._search_body(), session_id="s1",
                               held_entities={"Acme": v2.HOLD_DISAMBIGUATION_FAILED})
        prefix = copy.deepcopy(out["payload"])
        assert commit_schema.validate_payload_dict(prefix)[0].ok is True
        prefix["points"][0]["about_entities"] = ["Acme"]
        prefix["events"][0]["about_entities"] = ["Acme"]
        # Recompute the canonical id over the mutated body, so the ONLY thing
        # this arm measures is the referential rule (not the commit-id check).
        prefix["client_commit_id"] = commit_schema.compute_client_commit_id(
            prefix["session_id"], prefix["points"], prefix["entities"],
            prefix["operators"], prefix["summary"], prefix["story_arc"],
            prefix.get("events", []), prefix.get("supersessions", []))
        l1, _ = commit_schema.validate_payload_dict(prefix)
        assert l1.ok is False, (
            "Layer-1 ACCEPTED a claim naming an entity the session does not "
            "emit — then the strip this fix adds is unnecessary, and finding 1 "
            "was wrong")
        reasons = " ".join("".join(v) for v in l1.errors.values())
        assert "about_entities reference unknown entities" in reasons, l1.errors

    def test_a_hold_with_nothing_to_strip_still_creates_nothing(self):
        """The strip must not become a second gate: an entity held on its own,
        named by no claim, is still simply absent from the create list."""
        embed = {"entities": [{"name": "Acme", "kind": "core:organisation"}],
                 "events": [], "points": [], "operators": [],
                 "chain_notes": [], "link_before_create": []}
        out = v2.execute_embed(embed, self._search_body(), session_id="s1",
                               held_entities={"Acme": v2.HOLD_DISAMBIGUATION_FAILED})
        assert out["payload"]["entities"] == []
        assert out["stats"]["held_entity_refs_stripped"] == 0
        assert out["stats"]["held_entity_operators_dropped"] == 0
        assert commit_schema.validate_payload_dict(out["payload"])[0].ok is True


class TestTheVocabularyIsEnforced:
    """`HOLD_REASONS` is a DECLARED set, so a reason arriving through the
    `held_entities` PARAMETER must not widen it silently (#11693 review, P3).

    The reason is not chosen inside `execute_embed` — `extract_session_v2`
    forwards whatever `resolve_entities` produced, and a future caller can pass
    its own `{name: reason}`. An unknown spelling would reach
    `stats["held_reasons"]` and `result["resolution_held"]`, the census an
    operator reads, naming a state the design never decided.
    """

    def test_an_unknown_reason_is_normalised_not_published(self):
        out = v2.execute_embed(_EMBED, _search(_CANDIDATE), session_id="s1",
                               held_entities={"Acme": "some_future_reason"})
        # Still HELD — the fail-closed direction is unchanged…
        assert out["payload"]["entities"] == []
        assert out["stats"]["held_entities"] == 1
        # …and the census carries a class the design declares, with the arrival
        # disclosed rather than absorbed.
        assert out["stats"]["held_reasons"] == {
            v2.HOLD_DISAMBIGUATION_UNDECIDED: 1}, out["stats"]["held_reasons"]
        assert out["held"][0]["reason"] == v2.HOLD_DISAMBIGUATION_UNDECIDED
        assert any("some_future_reason" in w and "HOLD_REASONS" in w
                   for w in out["warnings"]), out["warnings"]

    def test_an_empty_reason_is_the_undecided_class_not_the_run_lookup(self):
        """The bare-iterable shape carries NO reason — and must not borrow one.

        `execute_embed(held_entities=["Acme"])` is the documented iterable
        shape, and it arrives with reason `""`. The census fell back to the
        run-level lookup state (`_lookup`), so a COMPLETE lookup published
        `held_reasons: {"resolved": 1}` — *an entity held with reason
        "resolved"*, a state the design never decided, on the very census that
        exists to tell an operator WHY (P3, #11693 review).
        """
        for shape in (["Acme"], {"Acme": ""}):
            out = v2.execute_embed(_EMBED, _search(_CANDIDATE),
                                   session_id="s1", held_entities=shape)
            assert out["stats"]["held_entities"] == 1
            assert out["stats"]["held_reasons"] == {
                v2.HOLD_DISAMBIGUATION_UNDECIDED: 1}, (shape, out["stats"]["held_reasons"])
            assert "resolved" not in out["stats"]["held_reasons"], (
                "an entity hold published the run-level lookup state as its "
                f"reason: {out['stats']['held_reasons']!r}")
            assert out["held"][0]["reason"] == v2.HOLD_DISAMBIGUATION_UNDECIDED

    def test_a_declared_reason_passes_through_untouched(self):
        """Non-vacuity: the three declared reasons are NOT rewritten."""
        for reason in v2.HOLD_REASONS:
            out = v2.execute_embed(
                _EMBED, _search(_CANDIDATE), session_id="s1",
                held_entities={"Acme": reason})
            assert out["stats"]["held_reasons"] == {reason: 1}, reason
            assert not any("HOLD_REASONS" in w for w in out["warnings"]), reason


class TestAMalformedAnswerIsNamed:
    def test_a_string_new_verdict_is_ignored_as_a_whole_not_by_character(self):
        """`"new": "Acme"` is iterable, so the old loop walked it character by
        character: every character was dropped as "not among the unmatched
        names" and the candidate was held (fail-closed, correct) while one
        bogus warning per character reported it (#11693 review, P3)."""
        res = v2.resolve_entities(
            _ref(), _search(_CANDIDATE),
            model=_Says({"resolutions": [], "new": "Acme"}))
        assert res["new"] == [], (
            "a string was read as a list of names — it must license nothing")
        assert _held_names(res) == {"Acme": v2.HOLD_DISAMBIGUATION_UNDECIDED}
        dropped = [w for w in res["warnings"] if "not among the unmatched" in w]
        assert dropped == [], (
            f"one malformed answer produced {len(dropped)} per-character "
            "warnings instead of one sentence saying what arrived")
        assert any("'new' verdict is a str" in w for w in res["warnings"]), (
            res["warnings"])

    def test_a_list_new_verdict_still_licenses_the_add(self):
        """Non-vacuity: the guard does not freeze the ADD path."""
        res = v2.resolve_entities(
            _ref(), _search(_CANDIDATE),
            model=_Says({"resolutions": [], "new": ["Acme"]}))
        assert res["new"] == ["Acme"] and res["held"] == []


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
        # ⛔ The orchestrator's payload is the one the commit door reads, so the
        # assertion this test was missing is the GATE's verdict on it (#11693
        # review, finding 1): the claim names the held entity, and a payload that
        # leaves that name in is refused as a whole — `commit_session` returns
        # `ok=False` without POSTing, the hosted door answers 422.
        assert out["payload"]["points"][0]["about_entities"] == [], (
            f"the claim still names the held entity: "
            f"{out['payload']['points'][0]['about_entities']!r}")
        l1, _ = commit_schema.validate_payload_dict(out["payload"])
        assert l1.ok, (
            "the run's own payload is refused by Layer-1, so this capture "
            f"commits NOTHING (the duplicate traded for memory loss): {l1.errors}")

    def test_an_apply_failure_does_not_mint_the_held_entity(self, monkeypatch):
        """P1-A (#11693 review): a raise from the APPLY must not lose the hold.

        `_apply_entity_resolution` iterates two channels of RAW model output
        (`about_entities`, typed slot values) with no type guard, downstream of
        the only sanitizer (`_clean_slots` runs inside `execute_embed`), so a
        non-iterable slot value raises `TypeError` out of it. The three
        `res.get(...)` captures used to sit BELOW that call, so the `except`
        path saw `resolution_held == []`: `held_entities={}` reached
        `execute_embed`, and the entity S3 could not identify was **MINTED** —
        the duplicate this issue exists to remove.

        ⛔ The test above (`_Boom`) raises from the RESOLVER, a different arm:
        it stays GREEN with this ordering bug restored. This one raises from
        the APPLY with a NON-EMPTY `map`, so the arm actually executes.
        """
        embed = {"entities": [{"name": "Acme", "kind": "core:organisation"},
                              {"name": "Beta", "kind": "core:organisation"}],
                 "events": [],
                 "points": [{"content": "Beta signed the deal",
                             "pointKind": "statement",
                             "about_entities": ["Beta"]}],
                 "operators": [], "chain_notes": [],
                 "link_before_create": []}
        empty_gaps = {"entities": [], "events": [], "points": [],
                      "operators": [], "chain_notes": [],
                      "link_before_create": []}

        def responder(system, user):
            if "STORY SUMMARIZER" in system:
                return "Beta signed the deal."
            if "GRAPH MAPPER" in system:
                return json.dumps(embed)
            if "GAP REVIEWER" in system:
                return json.dumps(empty_gaps)
            if "ENTITY RESOLUTION" in system:
                # ONE name resolved (so `map` is non-empty and the apply RUNS),
                # ONE left undecided — the HOLD this arm must not lose.
                return json.dumps({"resolutions": [
                    {"name": "Acme", "resolves_to": "obj_acme"}],
                    "new": []})
            raise AssertionError(f"unexpected system prompt: {system[:50]}")

        class _M:
            def complete(self, *, system: str, user: str):
                return responder(system, user)

        def two_candidates(sdk, embed_list, story, **kw):
            return _search(
                {"id": "obj_acme", "name": "Acme Corporation",
                 "kind": "core:organisation"},
                {"id": "obj_beta", "name": "Beta Holdings",
                 "kind": "core:organisation"})

        def raising_apply(embed_list, resolution_map):
            raise TypeError("'int' object is not iterable")

        monkeypatch.setattr(v2, "search_graph", two_candidates)
        monkeypatch.setattr(v2, "_apply_entity_resolution", raising_apply)
        out = v2.extract_session_v2(
            _M(), [{"role": "user", "content": "Beta signed the deal."}],
            session_id="s1")
        assert out["payload"] is not None, "capture must never be blocked"
        assert "Beta" not in [e["name"] for e in out["payload"]["entities"]], (
            "the apply raised, the hold was lost with it, and the entity S3 "
            "could not identify was MINTED — the duplicate #11653 removes")
        assert [h["name"] for h in out["resolution_held"]] == ["Beta"], (
            f"I2: the hold must survive the apply's failure, got {out['resolution_held']!r}")
        assert out["resolution_held"][0]["reason"] == v2.HOLD_DISAMBIGUATION_UNDECIDED
        assert out["stats"]["resolution_held"] == 1
        assert out["error_census"].get("entity_identity_held") == 1, (
            "the hold is reported on the CENSUS, not only in warnings")
        assert out["error_census"].get("entity_resolution_failed") == 1, (
            "the apply's failure is still censused — both facts are true")
        assert out["payload"]["points"][0]["about_entities"] == [], (
            f"the claim still names the held entity: "
            f"{out['payload']['points'][0]['about_entities']!r}")
        l1, _ = commit_schema.validate_payload_dict(out["payload"])
        assert l1.ok, (
            "the run's own payload is refused by Layer-1, so this capture "
            f"commits NOTHING: {l1.errors}")

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
