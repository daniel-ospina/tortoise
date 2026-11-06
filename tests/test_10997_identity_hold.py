"""#10997 — S3's HOLD: an unresolved identity is HELD, never minted, never dropped.

THE DEFECT THIS PINS
--------------------
§4.4's invariant **I1** (*"An unresolved identity is NEVER asserted `new`"*) and
its failure policy for S3 RESOLVE (*"a candidate whose identity is unresolved is
`HOLD`-ed"*) were, until this change, a SPEC with **no** enforcing code: `#5095`
made the run *honest* (the lookup's state, the `link_before_create` polarity, the
`identity` block) and deliberately suppressed nothing.

The one place the run still **asserted `new`** was `execute_embed`'s
mint-before-wire pre-pass (`_mint_endpoint`, `#2552`): an operator endpoint the
model did not also emit was materialised as a `statement` Point **from the
model's ref**. When the identity lookup had not resolved, nothing had checked
whether that endpoint already existed — so the fabrication was the natural
fail-open §4.4 names as *"the exact failure S3 exists to prevent"*: the duplicate
manufacturer.

WHAT THIS PINS
--------------
1. **Held, not minted.** On an `unresolved`/`partial` lookup no Point and no
   operator are fabricated for the ref.
2. **Not dropped (I2).** The candidate is in the run's `held` list, with its
   EDGE (#2552: the operator travels with the endpoint), and the run warns.
3. **Observable and drainable.** With a `HoldStore` the held set persists; a
   later run against the same graph whose lookup RESOLVED commits it.
4. **Exactly once, both directions.** A held identity is not re-held after it
   resolves (`hold` never reopens a RESOLVED key), and two drains mint ONE item
   (`claim` is the guard, `confirm` binds the id).
5. **Non-vacuity.** The red-first test is re-run with the predicate FLIPPED (a
   resolved lookup) and the mint comes back — so the assertion is reading the
   gate, not a coincidence of the fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100
from tortoise import failure_policy as fp  # noqa: E402, RUF100
from tortoise import identity_hold as hold  # noqa: E402, RUF100

#: One emitted point (`dst`) and one ref the model never emitted (`src`) — the
#: exact shape the mint-before-wire pre-pass exists for.
_EMBED = {
    "entities": [],
    "events": [],
    "points": [{"content": "the budget is 40k", "pointKind": "statement"}],
    "operators": [{"src": "the launch slips to Q3",
                   "dst": "the budget is 40k", "op_type": "IMPL"}],
    "chain_notes": [],
    "link_before_create": [],
}

_REF = "the launch slips to Q3"


def _search(resolved: bool) -> dict:
    """A COMPLETE lookup (`resolved`) or a lookup that did not run (`unresolved`).

    The only difference is the coverage report `search_graph` emits — which is
    what `failure_policy.lookup_state` reads (never `degraded` alone).
    """
    if not resolved:
        return {}
    return {
        "mode": "real", "degraded": False, "reason": None,
        "entities": [], "points": [], "events": [], "queries_run": 4,
        "query_coverage": {"derived": {"object": 1, "event": 1, "point": 2},
                           "ran": {"object": 1, "event": 1, "point": 2},
                           "failed": {"object": 0, "event": 0, "point": 0}},
    }


def _minted_contents(result: dict) -> list[str]:
    return [p["content"] for p in result["payload"]["points"]]


class TestTheGate:
    def test_an_unresolved_lookup_holds_the_endpoint_and_its_edge(self):
        """The red-first assertion: nothing is minted, and nothing is lost."""
        result = v2.execute_embed(_EMBED, {}, session_id="s1")
        assert _REF not in _minted_contents(result), (
            "a Point was fabricated from a ref whose identity was never checked "
            "— the duplicate manufacturer §4.4 I1 exists to prevent")
        assert result["payload"]["operators"] == [], (
            "an operator wired an endpoint that does not exist")
        # …and it is HELD, with the edge that referenced it (#2552).
        assert len(result["held"]) == 1, result["held"]
        rec = result["held"][0]
        assert rec["content"] == _REF
        assert rec["operator"]["src"] == _REF, (
            "the hold must carry the EDGE, not the endpoint alone — dropping it "
            "trades a duplicate for a lost operator edge (#2552, I2)")
        assert rec["persisted"] is False, (
            "no store was supplied, so the record must SAY it was not persisted")
        assert any("S3 HOLD" in w and "not minted" in w for w in result["warnings"]), \
            result["warnings"]
        assert result["stats"]["held"] == 1
        assert result["stats"]["operator_endpoints_minted"] == 0

    def test_a_partial_lookup_holds_too(self):
        """`partial` is the measured common shape (a bounded lookup), and a miss in
        an unrun leg is not evidence of absence — the same rule `#5095` applies to
        the RECORD applies to the MINT."""
        partial = _search(True)
        partial["query_coverage"]["ran"]["point"] = 0
        assert fp.lookup_state(partial) == fp.LOOKUP_PARTIAL
        result = v2.execute_embed(_EMBED, partial, session_id="s1")
        assert _REF not in _minted_contents(result), result["held"]
        assert len(result["held"]) == 1, result["held"]

    def test_the_predicate_flipped_mints_again(self):
        """NON-VACUITY (mutation): flip the predicate — a RESOLVED lookup — and the
        SAME fixture mints. Without this the test above could be passing for a
        reason unrelated to the gate."""
        result = v2.execute_embed(_EMBED, _search(True), session_id="s1")
        assert _REF in _minted_contents(result), (
            "a resolved lookup must still mint (#2552's mint-before-wire is NOT "
            "replaced by the hold — only licensed by a check that ran)")
        assert result["held"] == []
        assert result["stats"]["operator_endpoints_minted"] == 1
        assert len(result["payload"]["operators"]) == 1

    def test_the_gate_reads_the_lookup_state_and_nothing_else(self, monkeypatch):
        """The gate must consult `failure_policy.lookup_state` — the ONE predicate
        `#5095` built — rather than growing a second classifier of identity."""
        monkeypatch.setattr(fp, "lookup_state",
                            lambda search: fp.LOOKUP_UNRESOLVED)
        result = v2.execute_embed(_EMBED, _search(True), session_id="s1")
        assert len(result["held"]) == 1, (
            "a resolved `search` still minted because something other than "
            "`lookup_state` decided")

    def test_the_mutation_is_observable_from_the_other_side(self, monkeypatch):
        """MUTATION, the other direction: force the predicate to `resolved` on an
        UNRESOLVED search and the red-first test above goes red.

        That is what proves the hold is produced by the GATE and not by the
        fixture: the same embed list, the same `{}` search, one flipped predicate.
        """
        monkeypatch.setattr(fp, "lookup_state",
                            lambda search: fp.LOOKUP_RESOLVED)
        result = v2.execute_embed(_EMBED, {}, session_id="s1")
        assert _REF in _minted_contents(result), (
            "flipping the predicate did not change the outcome — the assertion "
            "this mutant exists to falsify is not reading the gate")
        assert result["held"] == []


class TestTheHoldIsDrainable:
    def test_run_1_holds_and_run_2_commits_it_exactly_once(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        first = v2.execute_embed(_EMBED, {}, session_id="s1", hold_store=store)
        assert first["held_drained"] == []
        assert len(store.pending()) == 1, store.summary()
        key = first["held"][0]["key"]

        # run 2: the lookup RESOLVES against the same graph.
        second = v2.execute_embed(_EMBED, _search(True), session_id="s2",
                                  hold_store=store)
        assert len(second["held_drained"]) == 1, second["held_drained"]
        assert second["held_drained"][0]["key"] == key
        assert second["held_drained"][0]["committed"] is True, (
            "the drained item is only COMMITTED if its Point survived the "
            "#2552 prune — a confirmed-but-pruned item is memory the hold "
            "thinks it resolved")
        assert store.get(key)["state"] == hold.HOLD_RESOLVED
        minted = [p for p in second["payload"]["points"] if p["content"] == _REF]
        assert len(minted) == 1, "the held endpoint was not committed exactly once"
        assert minted[0]["id"] == store.get(key)["minted_id"]
        # the EDGE came back with it (#2552)
        assert len(second["payload"]["operators"]) == 1, second["payload"]["operators"]
        assert second["payload"]["operators"][0]["src"] == minted[0]["id"]
        assert second["held"] == [], "run 2 re-held what it had just drained"

        # run 3: same store, same resolved lookup — NOTHING is minted twice.
        third = v2.execute_embed(_EMBED, _search(True), session_id="s3",
                                 hold_store=store)
        assert third["held_drained"] == [], (
            "a resolved held identity was drained a second time")
        # The model re-emits the operator, so the pre-pass mints the endpoint again
        # — and it MUST land on the same content-addressed id it had when the drain
        # committed it. That is what "exactly once" means at the graph: one
        # identity, one id, an upsert rather than a second node.
        third_pts = [p for p in third["payload"]["points"] if p["content"] == _REF]
        assert len(third_pts) == 1, third_pts
        assert third_pts[0]["id"] == minted[0]["id"], (
            "one identity produced two different ids — two nodes, which is the "
            "duplicate this whole mechanism exists to prevent")
        assert store.summary()["by_state"][hold.HOLD_RESOLVED] == 1

    def test_a_drained_item_that_folds_onto_an_existing_point_still_commits(
            self, tmp_path):
        """Survival is "still WIRED", not "in `payload_points`" (review 2, P1).

        A drain can legally mint nothing into the payload: the `#4716` NOOP fold
        resolves a ref against a PRIOR point, so the id is already in the graph
        and is never appended. Asking payload membership alone released the
        claim and printed *"no surviving operator references it, so the Point was
        pruned"* about an item whose edge is right there in the payload — so the
        store re-drained it on every resolved run, forever, and the held count
        could never return to zero. The mutation this pins: with the shipped
        predicate the run below reports `committed=False` and the key stays
        `pending`; with the fix it reports `committed=True`.
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        first = v2.execute_embed(_EMBED, {}, session_id="s1", hold_store=store)
        key = first["held"][0]["key"]

        # The fold: the lookup returns a PRIOR point with the held ref's own
        # content, so the mint resolves to an id that is already in the graph and
        # appends nothing to the payload (exactly what `prior_id` does).
        folded = _search(True)
        folded["points"] = [{"id": "pt_existing", "content": _REF,
                             "pointKind": "statement", "session_ref": "s0"}]
        second = v2.execute_embed(_EMBED, folded, session_id="s2",
                                  hold_store=store)
        assert [e["point_id"] for e in second["held_drained"]] == ["pt_existing"]
        assert second["held_drained"][0]["committed"] is True, (
            "a fold onto an existing point was reported as PRUNED and the item "
            "was released back to pending — it can never resolve")
        assert store.get(key)["state"] == hold.HOLD_RESOLVED
        assert store.pending() == [], store.summary()
        # …and its EDGE is in the payload, which is what makes it committed.
        assert [o["src"] for o in second["payload"]["operators"]] == ["pt_existing"]

    def test_the_held_count_is_identities_not_occurrences_without_a_store(self):
        """`stats.held` is a CENSUS, and it must mean the same thing with and
        without a store (review 2, P2).

        `hold_store` is `None` on every production call, so a key derived only
        when a store is supplied made the shipping path count OCCURRENCES: two
        operators naming one ref reported `held == 2`, contradicting the run's
        own comment and the epic's \"the held count returns to zero\" reading.
        """
        embed = {**_EMBED, "operators": [
            {"src": _REF, "dst": "the budget is 40k", "op_type": "IMPL"},
            {"src": _REF, "dst": "the budget is 40k", "op_type": "MITIGATES"},
        ]}
        result = v2.execute_embed(embed, {}, session_id="s1")
        assert len(result["held"]) == 2, "one record per held OCCURRENCE"
        assert result["stats"]["held"] == 1, (
            "two operators naming one ref are ONE held identity — an occurrence "
            "count makes the census unreadable without a store")
        assert len({h["key"] for h in result["held"]}) == 1

    def test_a_resolved_identity_is_never_re_held(self, tmp_path):
        """⛔ Review round 4 P2: the refusal to reopen is not enough — the CALLER
        must read it, or an identity committed turns ago is re-reported as held.

        The old arm asserted only `reopened is False` and the store state, so the
        false census it produced was pinned as acceptable by omission: the run
        counted the candidate in `held`/`stats["held"]`, said "carried in the hold
        store", and then dropped the operator on the ordinary "did not resolve"
        arm — three false signals at once, and the epic's acceptance ("the held
        count returns to zero") unreachable, because every ambiguous run after a
        resolution re-inflated it by one.
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        v2.execute_embed(_EMBED, {}, session_id="s1", hold_store=store)
        key = store.pending()[0]["key"]
        store.claim(key)
        store.confirm(key, "pt_canonical")
        again = v2.execute_embed(_EMBED, {}, session_id="s2", hold_store=store)
        assert store.get(key)["state"] == hold.HOLD_RESOLVED
        assert store.pending() == [], (
            "a resolved identity came back into the drainable population")
        assert again["held"] == [], (
            "an identity resolved on an earlier run was reported as newly held: "
            f"{again['held']}")
        assert again["stats"]["held"] == 0, again["stats"]
        # …and the EDGE is not dropped: the ref is wired to the id the store
        # already recorded for this graph (idempotent consumer: return the
        # recorded outcome for a completed key).
        assert [o["src"] for o in again["payload"]["operators"]] == ["pt_canonical"], \
            again["payload"]["operators"]
        assert any("already resolved" in w for w in again["warnings"]), \
            again["warnings"]


class TestADrainedRefResolvesToWhatTheRunEmitted:
    """⛔ Review round 6, P1 (measured): the drain minted without `_resolve`.

    The ordinary mint-before-wire pre-pass gates EVERY mint on
    `not _resolve(ref)`, and `_resolve` reads BOTH maps (`point_ids` AND
    `event_ids`). The drain called `_mint_endpoint` directly, and that helper
    dedups on `point_ids` only — so a held ref the SAME run emitted as an EVENT
    was materialised a second time as a content-identical Point (one identity,
    `ev_…` and `pt_…` with the same content hash), and because `_resolve`
    consults `point_ids` first, the restored edge wired the DUPLICATE. The Event —
    the thing the model actually asserted — lost, and `confirm` bound the store's
    key to the fabricated copy.

    This is the duplicate manufacturer #10997 exists to remove, produced by the
    drain half of the same mechanism.
    """

    def test_the_drain_binds_the_id_the_run_already_emitted(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        first = v2.execute_embed(_EMBED, {}, session_id="s1", hold_store=store)
        assert len(first["held"]) == 1, first["held"]
        assert len(store.pending()) == 1, store.summary()

        # run 2: the lookup RESOLVES, and the model emits the held ref as an EVENT
        # — an identity this run already HAS, which the drain must bind, not mint.
        embed = {**_EMBED,
                 "events": [{"content": _REF, "eventKind": "core:occurrence"}]}
        second = v2.execute_embed(embed, _search(True), session_id="s2",
                                  hold_store=store)

        events = [e for e in second["payload"]["events"] if e["content"] == _REF]
        assert len(events) == 1, second["payload"]["events"]
        event_id = events[0]["id"]

        assert [p for p in second["payload"]["points"] if p["content"] == _REF] == [], (
            "the drain fabricated a second node for an identity this run already "
            f"emitted as an Event ({event_id}) — one identity, two ids")
        assert [o["src"] for o in second["payload"]["operators"]] == [event_id], (
            "the restored edge wired the drain's duplicate instead of the Event")
        assert [e["point_id"] for e in second["held_drained"]] == [event_id]
        assert second["held_drained"][0]["committed"] is True, second["held_drained"]
        assert not any("minted as" in w for w in second["warnings"]
                       if "#10997" in w), (
            "the COMMITTED arm reported a MINT for an identity this run minted "
            f"nothing for (it bound the run's own Event): {second['warnings']}")
        assert store.get(second["held_drained"][0]["key"])["minted_id"] == event_id, (
            "the store bound the key to an id no node in this payload carries")
        assert store.pending() == [], store.summary()
        assert second["held"] == [], second["held"]

    def test_a_held_ref_longer_than_the_content_cap_resolves_to_its_event(
            self, tmp_path):
        """⛔ Review round 7, P1 (measured): `event_ids` is keyed on the CLAMPED
        content (`_write_span`, `_MAX_CONTENT` = 1000), while `point_ids` also
        carries the untruncated key. A `_resolve` that asked one form missed the
        other, so a >cap ref emitted as an Event was re-minted as a duplicate
        Point — and the run reported it committed.

        The pre-pass had the same asymmetry (a >cap ref+Event duplicated with NO
        store at all), which is why the fix is in `_resolve` itself: one resolver,
        both key forms, and every caller inherits it.
        """
        long_ref = "the release train slips to the third quarter because " + "x" * 1400
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        first = v2.execute_embed(
            {**_EMBED, "operators": [{"src": long_ref, "dst": "the budget is 40k",
                                      "op_type": "IMPL"}]},
            {}, session_id="s1", hold_store=store)
        assert len(first["held"]) == 1, first["held"]

        embed = {**_EMBED,
                 "events": [{"content": long_ref, "eventKind": "core:occurrence"}],
                 "operators": [{"src": long_ref, "dst": "the budget is 40k",
                                "op_type": "IMPL"}]}
        second = v2.execute_embed(embed, _search(True), session_id="s2",
                                  hold_store=store)
        events = [e for e in second["payload"]["events"]
                  if e["content"].startswith("the release train")]
        assert len(events) == 1, second["payload"]["events"]
        event_id = events[0]["id"]
        assert [p for p in second["payload"]["points"]
                if p["content"].startswith("the release train")] == [], (
            "a ref longer than the content cap was re-minted as a duplicate Point "
            f"beside the Event {event_id}")
        assert [o["src"] for o in second["payload"]["operators"]] == [event_id]
        assert second["held_drained"][0]["committed"] is True, second["held_drained"]
        assert store.pending() == [], store.summary()

    def test_an_identity_that_survived_is_not_reported_as_a_pruned_point(
            self, tmp_path):
        """⛔ Review round 7, P2 (measured): once `_pid` can be an EVENT id, the
        settle's "did it survive?" set (`payload_points` + `_wired`) no longer
        covers it, and the not-committed arm asserted *"the Point was pruned"*
        about an identity whose node is right there in the payload.

        The DISPOSITION is unchanged and correct (no surviving edge ⇒ hold again,
        I2): only the reported cause was false.
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        # The held ref's edge names an ENTITY as its `dst` — a shape the mint refuses
        # — so the hold carries an edge that can NEVER be re-emitted. Run 1 holds the
        # ref; run 2 resolves the ref (an emitted Event) but the restored edge drops.
        entity_embed = {**_EMBED,
                        "entities": [{"name": "Acme Corp", "kind": "core:organization"}],
                        "operators": [{"src": _REF, "dst": "Acme Corp",
                                       "op_type": "IMPL"}]}
        v2.execute_embed(entity_embed, {}, session_id="s1", hold_store=store)
        embed = {**entity_embed,
                 "events": [{"content": _REF, "eventKind": "core:occurrence"}]}
        second = v2.execute_embed(embed, _search(True), session_id="s2",
                                  hold_store=store)
        assert [e["content"] for e in second["payload"]["events"]] == [_REF]
        assert second["payload"]["operators"] == [], second["payload"]["operators"]
        assert second["held_drained"][0]["committed"] is False
        assert len(store.pending()) == 1, store.summary()
        assert not any("pruned" in w or "endpoint keeps" in w
                       for w in second["warnings"] if "#10997" in w), (
            "the run asserted a fate for the ENDPOINT (pruned / kept) that this "
            f"arm cannot know: {[w for w in second['warnings'] if '#10997' in w]}")
        assert any("EDGE did not survive" in w for w in second["warnings"]), (
            "the not-committed arm must name the loss it actually suffered — the "
            f"restored EDGE, never the endpoint: {second['warnings']}")


class TestTheResolverAsksBothKeyForms:
    """⛔ Review round 8, P1 (measured on the MERGED tree): the asymmetry is not
    only the drain's — it is `_resolve`'s, so it duplicates with **no store at
    all**, on the ordinary production call.

    `event_ids` is keyed on the CLAMPED content (`_write_span`, `_MAX_CONTENT`
    = 1000) while `point_ids` ALSO carries the untruncated key. A pre-pass
    `_resolve(full_ref)` therefore missed an Event emitted from a >cap ref and
    materialised a content-identical Point beside it — then wired the operator
    to the duplicate, and warned that *"no emitted point/event carried it"*
    about an Event that is right there in the payload.

    Pinned with `hold_store` ABSENT on purpose: this path needs no opt-in, so
    the defect was reachable on every shipped call, not only on the drain.
    """

    def test_a_long_ref_emitted_as_an_event_is_not_duplicated_without_a_store(
            self):
        long_ref = ("the release train slips to the third quarter because "
                    + "x" * 1400)
        embed = {**_EMBED,
                 "events": [{"content": long_ref,
                             "eventKind": "core:occurrence"}],
                 "operators": [{"src": long_ref, "dst": "the budget is 40k",
                                "op_type": "IMPL"}]}
        # The production shape: no `hold_store`, a RESOLVED lookup.
        result = v2.execute_embed(embed, _search(True), session_id="s1")

        events = [e for e in result["payload"]["events"]
                  if e["content"].startswith("the release train")]
        assert len(events) == 1, result["payload"]["events"]
        event_id = events[0]["id"]
        assert [p for p in result["payload"]["points"]
                if p["content"].startswith("the release train")] == [], (
            "a ref longer than the content cap was re-minted as a duplicate "
            f"Point beside the Event {event_id} — with no store supplied")
        assert [o["src"] for o in result["payload"]["operators"]] == [event_id]
        assert not any("no emitted point/event carried it" in w
                       for w in result["warnings"]), (
            "the mint warned that nothing carried a ref the payload's own "
            f"Event carries: {result['warnings']}")

    def test_a_ref_whose_span_clamp_differs_from_the_hard_cut_also_resolves(self):
        """⛔ Review round 8, P2 (both reviewers, measured): the two maps do not
        key the SAME >cap form either.

        `event_ids` is keyed on the HARD cut (``content[:_MAX_CONTENT]``), while
        `point_ids` is keyed on ``_write_span`` — which SHORTENS the span when the
        cut would split a numeral-bearing token (990 chars for the ref below). A
        resolver that probes only the span form therefore misses the Event and
        re-mints the duplicate, which is why the resolver asks the FULL ref, the
        HARD cut and the span.
        """
        # 989 + a numeral-bearing tail: `_write_span` stops at 990, the Event's
        # content is the 1000-char hard cut, so the two clamped keys differ.
        long_ref = "x" * 989 + " p95hit4" + "0" * 100 + " end"
        embed = {**_EMBED,
                 "events": [{"content": long_ref,
                             "eventKind": "core:occurrence"}],
                 "operators": [{"src": long_ref, "dst": "the budget is 40k",
                                "op_type": "IMPL"}]}
        result = v2.execute_embed(embed, _search(True), session_id="s1")
        events = [e for e in result["payload"]["events"]
                  if e["content"].startswith("xxxx")]
        assert len(events) == 1, result["payload"]["events"]
        event_id = events[0]["id"]
        assert [p for p in result["payload"]["points"]
                if p["content"].startswith("xxxx")] == [], (
            "the span clamp and the hard cut keyed different forms, so the Event "
            f"{event_id} was duplicated as a Point")
        assert [o["src"] for o in result["payload"]["operators"]] == [event_id]


class TestEveryHeldEdgeIsCarried:
    """⛔ Review round 4, P1/P2: `hold()` kept ONE operator per key, and the key is
    the REF — so a second operator naming the same unresolved ref had its edge
    discarded while the run reported it as carried.

    Both arms below are the two shapes that collapse: two DISTINCT operators over
    one ref (one record, two edges) and ONE operator over two unresolved refs (two
    records, one edge each) — the second was emitted twice.
    """

    _TWO_OPERATORS: ClassVar[dict] = {
        "entities": [], "events": [], "chain_notes": [],
        "points": [{"content": "the budget is 40k", "pointKind": "statement"},
                   {"content": "margin holds at 12%", "pointKind": "statement"}],
        "link_before_create": [],
        "operators": [
            {"src": _REF, "dst": "the budget is 40k", "op_type": "IMPL"},
            {"src": _REF, "dst": "margin holds at 12%", "op_type": "IMPL"},
        ],
    }

    def test_two_edges_over_one_ref_are_both_carried_and_both_drained(
            self, tmp_path):
        """End to end: one record, two edges in; two edges out.

        Both operators name the SAME unresolved ref, so both land on ONE store
        record (the key is the ref). Run 2 does not re-emit either operator, so
        every edge it wires is the one the hold carried — the shape the old
        singular `operator` field lost half of while reporting both as carried.
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        first = v2.execute_embed(self._TWO_OPERATORS, {}, session_id="s1",
                                 hold_store=store)
        assert len(first["held"]) == 2, (
            f"one record per held OCCURRENCE: {first['held']}")
        assert first["stats"]["held"] == 1, (
            f"one ref named twice is ONE held identity: {first['stats']}")
        assert len(store.pending()) == 1, store.summary()
        rec = store.pending()[0]
        assert len(rec["operators"]) == 2, (
            "the store kept only the first operator naming this ref — the "
            f"second edge is already lost: {rec['operators']}")

        second = v2.execute_embed(
            {"entities": [], "events": [], "chain_notes": [],
             "points": [{"content": "the budget is 40k",
                         "pointKind": "statement"},
                        {"content": "margin holds at 12%",
                         "pointKind": "statement"}],
             "link_before_create": [], "operators": []},
            _search(True), session_id="s2", hold_store=store)
        ops = second["payload"]["operators"]
        assert len(ops) == 2, (
            "only one of the two held edges came back from the drain: "
            f"{ops}")
        by_content = {p["content"]: p["id"] for p in second["payload"]["points"]}
        assert {o["dst"] for o in ops} == {by_content["the budget is 40k"],
                                           by_content["margin holds at 12%"]}, \
            ops
        assert len({o["src"] for o in ops}) == 1, (
            f"both edges must name the ONE drained endpoint: {ops}")
        assert second["held_drained"][0]["committed"] is True, second["held_drained"]
        assert store.pending() == []

    def test_two_operators_over_one_ref_carry_two_edges(self, tmp_path):
        """The load-bearing arm: `hold()` ACCUMULATES, so the second operator
        naming one unresolved ref is carried instead of silently discarded."""
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        embed = {"entities": [], "events": [], "chain_notes": [],
                 "points": [{"content": "the budget is 40k",
                             "pointKind": "statement"}],
                 "link_before_create": [],
                 "operators": [
                     {"src": _REF, "dst": "the budget is 40k", "op_type": "IMPL"},
                 ]}
        v2.execute_embed(embed, {}, session_id="s1", hold_store=store)
        key = hold.hold_key(hold.HOLD_ENDPOINT, _REF)
        store.hold(hold.HOLD_ENDPOINT, _REF, where="IMPL src",
                   operator={"src": _REF, "dst": "margin holds at 12%",
                             "op_type": "IMPL"})
        rec = store.get(key)
        assert len(rec["operators"]) == 2, (
            "a second operator over one unresolved ref was DISCARDED — only the "
            f"first edge survives: {rec['operators']}")
        assert rec["operators"][0]["dst"] == "the budget is 40k"
        assert rec["operators"][1]["dst"] == "margin holds at 12%"
        # the same edge twice is ONE operator
        store.hold(hold.HOLD_ENDPOINT, _REF, where="IMPL src",
                   operator={"src": _REF, "dst": "margin holds at 12%",
                             "op_type": "IMPL"})
        assert len(store.get(key)["operators"]) == 2, (
            "a re-asserted identical edge was appended as a second operator")

    def test_one_operator_with_two_unresolved_endpoints_drains_once(self, tmp_path):
        """P2: the dedupe must also run AMONG the held operators.

        `_mint_endpoint` is asked about each side of one operator, so an operator
        whose src AND dst are both unresolved is held twice — two records, each
        carrying the same `_op`. Deduping only against the run's own operators let
        both copies into `payload_operators`: the same edge twice, `stats["operators"]`
        reporting 2 for one edge, and `reconcile_payload` marking each copy `new`
        (budget/census inflation).
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        both = {"entities": [], "events": [], "points": [], "chain_notes": [],
                "link_before_create": [],
                "operators": [{"src": "claim alpha", "dst": "claim beta",
                               "op_type": "IMPL"}]}
        first = v2.execute_embed(both, {}, session_id="s1", hold_store=store)
        assert len(store.pending()) == 2, store.summary()
        assert first["stats"]["held"] == 2, first["stats"]

        second = v2.execute_embed(
            {"entities": [], "events": [], "points": [], "chain_notes": [],
             "link_before_create": [], "operators": []},
            _search(True), session_id="s2", hold_store=store)
        assert len(second["payload"]["operators"]) == 1, (
            "one operator with two unresolved endpoints was emitted twice — the "
            f"held copies were never deduped: {second['payload']['operators']}")
        assert second["stats"]["operators"] == 1, second["stats"]
        assert second["stats"]["held_drained"] == 2, second["stats"]


class TestAMitigatesTargetIsPartOfTheEdge:
    """⛔ Review round 5, P2: the drain's OWN edge identity omitted the target.

    The store deduped and replayed on `operator_identity` (src, dst, op_type,
    target), but the drain then re-identified the restored edges on
    `(src, dst, op_type)` alone — so two MITIGATES naming the SAME pair and
    attacking DIFFERENT edges collapsed: the second was dropped with no warning,
    its materialised target IMPL went with it, and the record was confirmed
    `resolved`. The same duplicate-for-loss trade, one function later.
    """

    def test_two_mitigates_on_one_ref_with_different_targets_both_drain(
            self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        points = [{"content": c, "pointKind": "statement"} for c in
                  ("alpha claim", "beta claim", "gamma claim", "delta claim")]
        embed = {"entities": [], "events": [], "chain_notes": [],
                 "link_before_create": [], "points": points, "operators": [
                     {"src": _REF, "dst": "alpha claim", "op_type": "MITIGATES",
                      "target": {"src": "alpha claim", "dst": "beta claim",
                                 "op_type": "IMPL"}},
                     {"src": _REF, "dst": "alpha claim", "op_type": "MITIGATES",
                      "target": {"src": "gamma claim", "dst": "delta claim",
                                 "op_type": "IMPL"}},
                 ]}
        first = v2.execute_embed(embed, {}, session_id="s1", hold_store=store)
        assert first["stats"]["held"] == 1
        assert len(hold.operators_of(store.pending()[0])) == 2, (
            "the store must keep BOTH edges for this leg to mean anything")

        second = v2.execute_embed(
            {"entities": [], "events": [], "chain_notes": [],
             "link_before_create": [], "points": points, "operators": []},
            _search(True), session_id="s2", hold_store=store)
        mitigates = [o for o in second["payload"]["operators"]
                     if o["op_type"] == "MITIGATES"]
        assert len(mitigates) == 2, (
            "two MITIGATES differing only by TARGET collapsed into one — the "
            f"second edge (and its target IMPL) is lost: {mitigates}")
        assert store.pending() == []

    def test_the_drain_identity_folds_what_pass_2_emits(self, tmp_path):
        """⛔ Review round 5 (P2): the identity must fold what pass 2 FOLDS.

        Two ways the drain's twin goes unrecognised, both measured: a re-emitted
        edge spelled in a different CASE resolves to the same point ids, and two
        MITIGATES whose targets differ only by the DECLARED `op_type` are one edge
        once pass 2 coerces every target to `IMPL`. Either one emitted the same
        edge twice, and `reconcile_payload` counts both as `new`.
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        points = [{"content": c, "pointKind": "statement"} for c in
                  ("the budget is 40k", "gamma claim", "delta claim")]
        held = {"entities": [], "events": [], "chain_notes": [],
                "link_before_create": [], "points": points, "operators": [
                    {"src": _REF.lower(), "dst": "the budget is 40k",
                     "op_type": "IMPL"}]}
        v2.execute_embed(held, {}, session_id="s1", hold_store=store)
        assert len(store.pending()) == 1, store.summary()

        # run 2 re-emits the SAME edge in another case, and the lookup resolves.
        again = {**held, "operators": [
            {"src": _REF.upper(), "dst": "the budget is 40k", "op_type": "impl"}]}
        second = v2.execute_embed(again, _search(True), session_id="s2",
                                  hold_store=store)
        assert len(second["payload"]["operators"]) == 1, (
            "the drained edge and the run's own re-emission were emitted as TWO "
            f"operators: {second['payload']['operators']}")

        # ...and two MITIGATES whose targets differ only by declared op_type are
        # ONE edge once pass 2 coerces every target to IMPL.
        store2 = hold.HoldStore(tmp_path / "hold2.jsonl")
        both = {"entities": [], "events": [], "chain_notes": [],
                "link_before_create": [], "points": points, "operators": [
                    {"src": _REF, "dst": "gamma claim", "op_type": "MITIGATES",
                     "target": {"src": "gamma claim", "dst": "delta claim",
                                "op_type": "IMPL"}},
                    {"src": _REF, "dst": "gamma claim", "op_type": "MITIGATES",
                     "target": {"src": "gamma claim", "dst": "delta claim",
                                "op_type": "NAND"}},
                ]}
        v2.execute_embed(both, {}, session_id="s3", hold_store=store2)
        third = v2.execute_embed(
            {"entities": [], "events": [], "chain_notes": [],
             "link_before_create": [], "points": points, "operators": []},
            _search(True), session_id="s4", hold_store=store2)
        deduped = [o for o in third["payload"]["operators"]
                   if o["op_type"] == "MITIGATES"]
        assert len(deduped) == 1, (
            "one MITIGATES was emitted twice because the declared target "
            f"op_type was part of the drain identity: {deduped}")


class TestTheAggregateHoldSentenceIsTrue:
    def test_a_store_that_cannot_write_is_not_reported_as_carried(self):
        """⛔ Review round 4 P3: the run-level line keyed on `hold_store is not
        None`, so with a store that RAISES on write (read-only volume, full disk,
        bad permissions) it said "carried in the hold store" while the
        per-occurrence warnings in the same list said the write FAILED. Two
        contradicting warnings, and the aggregate is the one a census reads."""

        class _FailingStore:
            def hold(self, *a, **k):
                raise OSError("Read-only file system")

            def summary(self):
                return {"path": "<unwritable>", "total": 0,
                        "by_state": {}, "by_kind": {}}

        result = v2.execute_embed(_EMBED, {}, session_id="s1",
                                  hold_store=_FailingStore())
        aggregate = [w for w in result["warnings"] if "S3 HOLD (#10997)" in w]
        assert len(aggregate) == 1, result["warnings"]
        assert "0 of 1 occurrence(s) carried" in aggregate[0], aggregate[0]
        assert "NOT persisted" in aggregate[0], aggregate[0]
        assert "carried in the hold store for the next run" not in aggregate[0], (
            "the aggregate sentence contradicts the per-occurrence warning it "
            f"sits beside: {aggregate[0]}")
        assert any("hold store write FAILED" in w for w in result["warnings"]), \
            result["warnings"]
        assert result["held"][0]["persisted"] is False
        assert "OSError" in result["held"][0]["persist_error"]


class TestTheDrainCensus:
    """Review round 3 (MINOR): the run-level number must mean COMMITTED.

    `stats["held_drained"]` was `len(held_drained)` — the ATTEMPT count — while
    its own comment and the docstring said "committed". A run that drains, mints
    and then PRUNES every endpoint therefore reported `held_drained=N` with
    `committed: False` on all N: a census that reads as success on the epic's own
    acceptance ("the held count can return to zero") while nothing committed. The
    truth was already on each entry; these two arms pin WHICH number is which and
    stop the fix from being satisfied by reporting zero always.
    """

    #: An embed the drain will attempt in full and commit NONE of: a MITIGATES
    #: with no target edge drops its own guard, so both endpoint refs are held,
    #: and on the resolved run both are minted, pruned and released again.
    _ORPHANED: ClassVar[dict] = {
        "entities": [], "events": [], "points": [], "chain_notes": [],
        "link_before_create": [],
        "operators": [{"src": "orphan claim alpha", "dst": "orphan claim beta",
                       "op_type": "MITIGATES", "strength": 0.3}],
    }

    def test_the_run_level_count_is_committed_not_attempted(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        v2.execute_embed(self._ORPHANED, {}, session_id="s1", hold_store=store)
        assert len(store.pending()) == 2, store.summary()

        pruned = v2.execute_embed(
            {"entities": [], "events": [], "points": [], "chain_notes": [],
             "link_before_create": [], "operators": []},
            _search(True), session_id="s2", hold_store=store)
        assert len(pruned["held_drained"]) == 2, pruned["held_drained"]
        assert all(e["committed"] is False for e in pruned["held_drained"])
        assert pruned["stats"]["held_drained"] == 0, (
            "the run reported a COMMITTED drain count over entries that all say "
            f"committed=False: {pruned['stats']}")
        assert pruned["stats"]["held_drain_attempted"] == 2, (
            "the attempt count was dropped instead of separated — the run drained "
            f"two items and committed none: {pruned['stats']}")

    def test_a_committed_drain_counts_in_both_readings(self, tmp_path):
        """The positive control: commit ONE item and both numbers must say one.

        Without this arm a `stats["held_drained"] = 0` constant would satisfy the
        test above, which is the vacuity the census exists to avoid.
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        v2.execute_embed(_EMBED, {}, session_id="s1", hold_store=store)
        second = v2.execute_embed(_EMBED, _search(True), session_id="s2",
                                  hold_store=store)
        assert [e["committed"] for e in second["held_drained"]] == [True]
        assert second["stats"]["held_drained"] == 1, second["stats"]
        assert second["stats"]["held_drain_attempted"] == 1, second["stats"]


class TestTheDrainCarriesTheEdge:
    """⛔ Review P0/P1: the drain must carry the EDGE, and must not resolve an
    item the #2552 prune then discards.

    Both arms were MISSING before the review and both were real: run 2 in the
    round-trip test re-emits the operator, so the run's OWN operator satisfied
    every assertion and the held edge could be deleted without a red; and a
    drained item whose operator did not survive was confirmed into a resolution
    the payload did not contain — the hold store leaking the memory it exists to
    protect.
    """

    #: run 2's embed: the same point, and NO operator — the held edge is the only
    #: thing that can reconstruct the IMPL.
    _EMBED_NO_OPERATOR: ClassVar[dict] = {**_EMBED, "operators": []}

    def test_a_resolved_run_that_does_not_re_emit_the_operator_still_wires_it(
            self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        v2.execute_embed(_EMBED, {}, session_id="s1", hold_store=store)
        assert len(store.pending()) == 1

        second = v2.execute_embed(self._EMBED_NO_OPERATOR, _search(True),
                                  session_id="s2", hold_store=store)
        assert len(second["held_drained"]) == 1, second["held_drained"]
        assert second["held_drained"][0]["committed"] is True
        # the endpoint AND the edge came back from the hold
        minted = [p for p in second["payload"]["points"] if p["content"] == _REF]
        assert len(minted) == 1, second["payload"]["points"]
        ops = second["payload"]["operators"]
        assert len(ops) == 1, (
            "the held EDGE was not reconstructed — carrying only the endpoint is "
            f"the loss #2552 forbids: {ops}")
        assert ops[0]["src"] == minted[0]["id"]
        assert ops[0]["op_type"] == "IMPL"

    def test_a_drained_item_whose_edge_dies_is_HELD_again_not_resolved(self, tmp_path):
        """A MITIGATES with no target edge still drops (its own guard); the
        pre-pass mint, the operator, and now the DRAIN's claim must not turn that
        into a 'resolved' identity the payload does not hold."""
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        held_embed = {
            "entities": [], "events": [], "points": [], "chain_notes": [],
            "link_before_create": [],
            "operators": [{"src": "orphan claim alpha", "dst": "orphan claim beta",
                           "op_type": "MITIGATES", "strength": 0.3}],
        }
        first = v2.execute_embed(held_embed, {}, session_id="s1", hold_store=store)
        assert first["held"], "the unresolved endpoints were not held"
        pending_before = {r["key"] for r in store.pending()}
        assert len(pending_before) == 2, store.summary()

        second = v2.execute_embed(
            {"entities": [], "events": [], "points": [], "chain_notes": [],
             "link_before_create": [], "operators": []},
            _search(True), session_id="s2", hold_store=store)
        assert all(e["committed"] is False for e in second["held_drained"]), \
            second["held_drained"]
        assert {r["key"] for r in store.pending()} == pending_before, (
            "a drained identity was marked RESOLVED while the payload discarded "
            "it — the item is now unrecoverable")
        assert any("NOT committed" in w for w in second["warnings"]), \
            second["warnings"]

    def test_a_held_ref_longer_than_the_content_cap_still_drains(self, tmp_path):
        """⛔ Review P1: the record stores the CLAMPED content, but the operator
        names the FULL ref — draining from the clamped text mints a key the
        restored operator cannot resolve, so the item is minted and pruned."""
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        long_ref = "long endpoint claim " + ("x" * 1500)
        embed = {"entities": [], "events": [], "chain_notes": [],
                 "points": [{"content": "the budget is 40k",
                             "pointKind": "statement"}],
                 "link_before_create": [],
                 "operators": [{"src": long_ref, "dst": "the budget is 40k",
                                "op_type": "IMPL"}]}
        v2.execute_embed(embed, {}, session_id="s1", hold_store=store)
        assert len(store.pending()) == 1, store.summary()
        assert len(store.pending()[0]["content"]) == v2._MAX_CONTENT, (
            "the fixture needs a CLAMPED content for this to test anything")

        second = v2.execute_embed(
            {"entities": [], "events": [], "chain_notes": [],
             "points": [{"content": "the budget is 40k",
                         "pointKind": "statement"}],
             "link_before_create": [], "operators": []},
            _search(True), session_id="s2", hold_store=store)
        assert second["held_drained"][0]["committed"] is True, second["held_drained"]
        assert len(second["payload"]["operators"]) == 1, (
            "the restored operator could not resolve the drained endpoint — the "
            "record kept the clamped content instead of the ref")
        assert store.pending() == []


class TestTheStoreExactlyOnce:
    def test_claim_is_the_only_way_in_and_it_is_once(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        store.hold(hold.HOLD_ENDPOINT, "a ref")
        key = hold.hold_key(hold.HOLD_ENDPOINT, "a ref")
        assert store.claim(key) is True
        assert store.claim(key) is False, "two drains could mint one identity twice"
        store.confirm(key, "pt_1")
        assert store.claim(key) is False

    def test_a_second_different_id_for_one_identity_raises(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        store.hold(hold.HOLD_ENDPOINT, "a ref")
        key = hold.hold_key(hold.HOLD_ENDPOINT, "a ref")
        store.claim(key)
        store.confirm(key, "pt_1")
        with pytest.raises(ValueError):
            store.confirm(key, "pt_2")

    def test_confirming_the_same_id_twice_is_a_noop(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        store.hold(hold.HOLD_ENDPOINT, "a ref")
        key = hold.hold_key(hold.HOLD_ENDPOINT, "a ref")
        store.claim(key)
        store.confirm(key, "pt_1")
        assert store.confirm(key, "pt_1")["minted_id"] == "pt_1"

    def test_hold_is_idempotent_and_counts_the_runs(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        store.hold(hold.HOLD_ENDPOINT, "a ref", where="IMPL src")
        store.hold(hold.HOLD_ENDPOINT, "a  ref", where="IMPL src")  # same identity
        assert len(store.pending()) == 1
        assert store.pending()[0]["runs_held"] == 2

    def test_release_returns_a_claim_that_minted_nothing(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        store.hold(hold.HOLD_ENDPOINT, "a ref")
        key = hold.hold_key(hold.HOLD_ENDPOINT, "a ref")
        assert store.claim(key) is True
        assert store.release(key) is True
        assert [r["key"] for r in store.pending()] == [key], (
            "a claim whose mint produced nothing leaked the item out of the "
            "drainable population — a hold with no way out is a leak")

    def test_the_log_survives_a_reload(self, tmp_path):
        path = tmp_path / "hold.jsonl"
        store = hold.HoldStore(path)
        store.hold(hold.HOLD_ENDPOINT, "a ref", where="IMPL src")
        key = hold.hold_key(hold.HOLD_ENDPOINT, "a ref")
        store.claim(key)
        store.confirm(key, "pt_1")
        # ⛔ `HoldStore.__new__` hands back the REGISTRY instance for a path it
        # has already seen (the two-in-memory-views guard), so without this the
        # "reload" below is the SAME object and `_load` never runs — measured:
        # `HoldStore(path) is store` was True and truncating the file left
        # `pending()` at 1. The test then asserted nothing about durability and
        # would not have redded if `_load` were deleted (#10997 review 2, P2).
        hold._STORES.clear()
        reloaded = hold.HoldStore(path)
        assert reloaded is not store
        assert reloaded.get(key)["state"] == hold.HOLD_RESOLVED
        assert reloaded.get(key)["minted_id"] == "pt_1"
        assert reloaded.summary()["by_state"][hold.HOLD_RESOLVED] == 1

    def test_a_torn_line_does_not_lose_the_rest(self, tmp_path):
        path = tmp_path / "hold.jsonl"
        store = hold.HoldStore(path)
        store.hold(hold.HOLD_ENDPOINT, "a ref")
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"key": "torn", "kind"')  # a crash mid-append
        hold._STORES.clear()  # force a real `_load` (see the reload test above)
        assert len(hold.HoldStore(path).pending()) == 1

    def test_a_record_missing_its_state_cannot_sink_the_census(self, tmp_path):
        """A torn/hand-edited line is COUNTED by `summary()`, never raised over.

        `_load` accepts any JSON object carrying a `key`, so `rec["state"]` could
        raise `KeyError` out of a reporting method — and inside `execute_embed`
        that replaced the whole S5 payload with the caller's error dict, i.e. a
        corrupt hold file could lose a session's points and operators (#10997
        review 2, P2).
        """
        path = tmp_path / "hold.jsonl"
        path.write_text('{"key": "k1", "content": "x"}\n', encoding="utf-8")
        summary = hold.HoldStore(path).summary()
        assert summary["total"] == 1
        assert summary["by_state"][hold.HOLD_UNKNOWN] == 1

    def test_open_store_is_per_graph_and_env_overridable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TORTOISE_IDENTITY_HOLD_DIR", str(tmp_path))
        a = hold.open_store("graph-a")
        b = hold.open_store("graph-b")
        assert a.path != b.path
        a.hold(hold.HOLD_ENDPOINT, "a ref")
        assert hold.open_store("graph-a").pending()[0]["content"] == "a ref"
        assert b.pending() == [], "two graphs must not share one hold set"


class TestAClaimedItemIsReclaimable:
    """#10997 review round 3 (MINOR): `claimed` was recoverable in name only.

    The module promised a crashed drain's item was *"recoverable (`release`)
    instead of stuck forever"* — but every `release` call site lived in the SAME
    in-process drain, and `pending()` lists only PENDING. A kill/OOM between
    `claim` and the settle therefore removed the item from the drain's entire
    input POPULATION with nothing left to act on it, forever: the I2 loss this
    store exists to prevent, one state further along. These arms pin the reclaim
    scan (SQS visibility timeout / Redis `XAUTOCLAIM`'s shape) and the timestamp
    that makes it possible.
    """

    def test_a_claim_is_timestamped_and_its_age_is_readable(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        store.hold(hold.HOLD_ENDPOINT, "a ref")
        key = hold.hold_key(hold.HOLD_ENDPOINT, "a ref")
        assert store.claim(key) is True
        rec = store.get(key)
        assert isinstance(rec.get("claimed_at"), float), rec
        assert rec.get("attempts") == 1, rec
        # A LIVE claim is not stale under the shipped idle bound, and IS stale
        # under a zero bound — so the scan reads the bound, not a constant.
        assert [r["key"] for r in store.stale_claims(60.0)] == []
        assert [r["key"] for r in store.stale_claims(0.0)] == [key]

    def test_a_claim_left_by_a_dead_drain_goes_back_to_pending(self, tmp_path):
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        store.hold(hold.HOLD_ENDPOINT, "a ref")
        key = hold.hold_key(hold.HOLD_ENDPOINT, "a ref")
        store.claim(key)  # …and then the process dies: nothing releases it.
        assert store.pending() == []
        assert store.reclaim_stale(0.0) == [key]
        assert [r["key"] for r in store.pending()] == [key], (
            "a claim left by a dead drain was never returned to the drainable "
            "population — the item is invisible to every later run, forever")
        assert store.get(key)["state"] == hold.HOLD_PENDING
        assert store.get(key)["attempts"] == 1, (
            "the attempt counter was lost across the reclaim — a key that keeps "
            "being claimed and dying must stay legible")

    def test_an_undatable_claim_is_reclaimed_rather_than_hidden_forever(self, tmp_path):
        """A record claimed BEFORE claims were timestamped carries no `claimed_at`.

        It cannot be shown to be live, so treating it as live is the permanent
        leak the scan exists to end — the reclaim population includes it.
        """
        path = tmp_path / "hold.jsonl"
        path.write_text(
            '{"key": "k_old", "kind": "operator_endpoint", "content": "a ref",'
            ' "state": "claimed"}\n', encoding="utf-8")
        store = hold.HoldStore(path)
        assert [r["key"] for r in store.stale_claims()] == ["k_old"]
        assert store.reclaim_stale() == ["k_old"]
        assert store.get("k_old")["state"] == hold.HOLD_PENDING

    def test_a_run_reclaims_and_commits_what_a_crashed_drain_claimed(
            self, tmp_path, monkeypatch):
        """The end-to-end consequence: the item is not lost to the crash.

        Run 1 holds it; a drain claims it and is killed (nothing settles the
        claim); run 2 resolves and — because it reclaims first — drains and
        COMMITS the item instead of never seeing it.
        """
        store = hold.HoldStore(tmp_path / "hold.jsonl")
        first = v2.execute_embed(_EMBED, {}, session_id="s1", hold_store=store)
        key = first["held"][0]["key"]
        assert store.claim(key) is True  # the crashed drain — and nothing else
        assert store.pending() == []

        monkeypatch.setattr(hold, "HOLD_CLAIM_IDLE_SECONDS", 0.0)
        second = v2.execute_embed(_EMBED, _search(True), session_id="s2",
                                  hold_store=store)
        assert any("RECLAIMED" in w for w in second["warnings"]), second["warnings"]
        assert [e["key"] for e in second["held_drained"]] == [key], (
            "a claim left by a dead drain was never reclaimed, so the item was "
            f"silently lost: {second['held_drained']}")
        assert second["held_drained"][0]["committed"] is True
        assert store.get(key)["state"] == hold.HOLD_RESOLVED
        assert store.get(key)["minted_id"] == second["held_drained"][0]["point_id"]
