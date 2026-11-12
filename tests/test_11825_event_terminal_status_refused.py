"""#11825 — an `:Event` carries no lifecycle state, so the TERMINAL half is refused.

Measured at #11825's head, on three doors:

```
update_entity(<event>, status='superseded')                     -> ACCEPTED
create_entity('event', …, status='superseded')                  -> ACCEPTED
create_event(…, status='superseded')                            -> ACCEPTED
EventAPI.add_event(id, kind, status='superseded')               -> ACCEPTED
EventAPI.add_event(id, kind, outdated=True)                     -> ACCEPTED
```

and the stored state was then **half-believed**: `ep.py` strips a terminal node from every factor
label-blindly (``status in TERMINAL_EXCLUDED_STATUSES or bool(outdated)``), while no retrieval leg
filters an ``:Event`` (``search_engine._STATUS_FILTERED_LABELS`` omits it). A node dead to belief and
live to every read.

The boundary this file pins: an Event may still carry a **non-terminal** ``status`` (the prop lane
#2788 declares, read by ``_promote_incident_operators`` as the live/draft gate, #7853/#10828), and the
**terminal** half of the vocabulary is refused before any write — the exact predicate the belief layer
applies, imported from the same module, so the two cannot drift.

⛔ Every refusal test here goes RED if the guard is removed: the write is ACCEPTED at the parent head.
⛔ T5 is the other half — the guard must not over-block a Point, a Subject, or a non-terminal Event
status (``status="scheduled"`` is pinned on main by
``tests/test_operator_dedup_parity.py::test_create_entity_event_journals_single_line``).

Runnable (URI-less embedded), or through the lane container:

  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_11825_event_terminal_status_refused.py -q
  uv run python tools/test_lane.py run -- tests/test_11825_event_terminal_status_refused.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise import live
from tortoise.api import EventAPI
from tortoise.live import (
    EPISTEMIC_LABELS,
    STATUS_CARRYING_EPISTEMIC_LABELS,
    TERMINAL_EXCLUDED_STATUSES,
    refuse_terminal_event_state,
    status_as_reported,
)
from tortoise.log import EventLog
from tortoise.sdk import POINT_STATUS_VALUES, TortoiseSDK

#: The terminal half, as a sorted tuple so a parametrized id list is stable.
_TERMINAL = tuple(sorted(TERMINAL_EXCLUDED_STATUSES))

#: Values that must KEEP working — the prop lane, not a lifecycle. `scheduled`
#: is an ingest marker and is pinned on main (#2061 parity test).
_NON_TERMINAL = ("draft", "live", "scheduled", "completed")


@pytest.fixture()
def sdk():
    root = tempfile.mkdtemp(prefix="tortoise_11825_")
    db = os.path.join(root, "t.db")
    log = os.path.join(root, "events.jsonl")
    client = TortoiseSDK(db, event_log_path=log)
    client._test_root = root  # for teardown
    client._test_log = log
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _event(sdk, name="DeployFailed", **props):
    node = sdk.create_entity("event", name, eventKind="deployment", **props)["node"]
    return node.get("eventId") or node.get("id")


def _journal_lines(path):
    """Every record written to ``path`` so far (empty when it does not exist)."""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [line for line in fh.read().splitlines() if line.strip()]


def _props(sdk, eid):
    rows = sdk._get_proj().g.query(
        "MATCH (e:Event {eventId:$e}) RETURN properties(e)",
        params={"e": eid},
    ).result_set
    assert rows, f"no :Event with eventId={eid!r}"
    return rows[0][0]


# ── The predicate itself ────────────────────────────────────────────────────


class TestTheRefusalPredicate:
    def test_a_novel_terminal_status_is_refused_without_touching_this_module(self, monkeypatch):
        """The decision is DELEGATED to `is_terminal_status`, not re-spelled.

        Adding a member to the vocabulary must refuse on the write door the
        moment the reader starts honouring it — so this patches the declaration
        the mirror reads and asserts the guard follows. (The earlier version of
        this test compared ``set(_TERMINAL)`` to the set it was derived from,
        which could never fail. Review cycle 1, P3.)
        """
        monkeypatch.setattr(
            live, "TERMINAL_EXCLUDED_STATUSES",
            live.TERMINAL_EXCLUDED_STATUSES | {"novel_terminal"})
        with pytest.raises(ValueError, match=":Event"):
            refuse_terminal_event_state({"status": "novel_terminal"}, door="test")
        # …and the pre-existing members are still refused (the vocabulary grew,
        # it did not move)
        for value in _TERMINAL:
            with pytest.raises(ValueError, match=":Event"):
                refuse_terminal_event_state({"status": value}, door="test")

    def test_it_does_not_invent_an_event_status_family(self):
        """A write-side refusal is not a status family.

        `:Event` stays OUT of `STATUS_CARRYING_EPISTEMIC_LABELS`, and the
        rendering contract is unchanged: an Event still REPORTS no status.
        Without this pin the guard's placement next to the constant reads as a
        claim that Event is status-carrying (found by PLAN-VERIFY round 1).
        """
        assert "Event" not in STATUS_CARRYING_EPISTEMIC_LABELS
        assert set(EPISTEMIC_LABELS) == set(STATUS_CARRYING_EPISTEMIC_LABELS) | {"Event"}
        # An ABSENT status on an Event reports "" — "this layer exposes no
        # status" — exactly as before; a stored value is echoed, unchanged.
        assert status_as_reported(None, label="Event") == ""
        assert status_as_reported("live", label="Event") == "live"
        assert status_as_reported(None) == "live"

    def test_non_terminal_values_are_allowed_by_the_predicate(self):
        for value in _NON_TERMINAL:
            refuse_terminal_event_state({"status": value}, door="test")
        # a clear (the legacy repair route) and an absent key are both fine
        refuse_terminal_event_state({"status": None}, door="test")
        refuse_terminal_event_state({}, door="test")
        # `outdated=False` is not a terminal marker; `ep.py` reads truthiness
        refuse_terminal_event_state({"outdated": False}, door="test")

    def test_the_outdated_flag_is_refused_by_the_same_predicate(self):
        """The vocabulary's OTHER terminal marker — `ep.py` honours it identically."""
        with pytest.raises(ValueError, match="not writable on an :Event"):
            refuse_terminal_event_state({"outdated": True}, door="test")

    @pytest.mark.parametrize("value", ["", 0, 0.0, 1, "true", "false"])
    def test_a_non_boolean_outdated_is_refused_not_half_read(self, value):
        """Review cycle 1, P1 — the two predicates disagree about these values.

        The Cypher terminal predicate negates ``coalesce(outdated,false) = false``
        (so ``''``/``0``/``0.0`` are TERMINAL) while `is_terminal_status` reads
        them by truthiness (LIVE). A truthiness-only gate therefore let a value
        through that one half of the system called terminal — through the one
        door that has no `_sanitize_props`. ``outdated`` is contractually a
        boolean, so a non-boolean is refused rather than half-read.
        """
        with pytest.raises(ValueError, match="not a legal value on an :Event"):
            refuse_terminal_event_state({"outdated": value}, door="test")


# ── T3: both create doors ───────────────────────────────────────────────────


class TestTheCreateDoors:
    @pytest.mark.parametrize("status", _TERMINAL)
    def test_create_entity_refuses_a_terminal_status(self, sdk, status):
        with pytest.raises(ValueError, match=":Event"):
            sdk.create_entity("event", f"E-{status}", eventKind="deployment",
                              status=status)
        # T6: refused BEFORE any write — nothing was minted, nothing journalled
        assert not sdk._get_proj().g.query(
            "MATCH (e:Event {name:$n}) RETURN count(e)",
            params={"n": f"E-{status}"},
        ).result_set[0][0]
        assert _journal_lines(sdk._test_log) == []

    @pytest.mark.parametrize("status", _TERMINAL)
    def test_create_event_alias_refuses_too(self, sdk, status):
        """`create_event` relays through `create_entity(type='event')` — one guard."""
        with pytest.raises(ValueError, match=":Event"):
            sdk.create_event(f"Alias-{status}", "deployment", status=status)

    def test_create_refuses_the_outdated_flag(self, sdk):
        with pytest.raises(ValueError, match=":Event"):
            sdk.create_entity("event", "Flagged", eventKind="deployment",
                              outdated=True)

    @pytest.mark.parametrize("status", _NON_TERMINAL)
    def test_a_non_terminal_status_still_lands(self, sdk, status):
        """#2788's prop lane, and main's #2061 parity pin (`status="scheduled"`)."""
        eid = _event(sdk, name=f"Ok-{status}", status=status)
        assert _props(sdk, eid).get("status") == status


# ── T1/T2/T6: the update door ───────────────────────────────────────────────


class TestTheUpdateDoor:
    @pytest.mark.parametrize("status", _TERMINAL)
    def test_update_entity_refuses_a_terminal_status(self, sdk, status):
        eid = _event(sdk, name=f"Upd-{status}")
        before = _props(sdk, eid)
        records_before = len(_journal_lines(sdk._test_log))
        with pytest.raises(ValueError, match=":Event"):
            sdk.update_entity(eid, status=status)
        # T6: the refusal precedes the write — the node is byte-identical, and
        # no EntityMutated record was emitted (the journal half of R5).
        assert _props(sdk, eid) == before
        assert "status" not in _props(sdk, eid)
        assert len(_journal_lines(sdk._test_log)) == records_before

    def test_the_refusal_also_covers_update(self, sdk):
        """`update()` is the documented alias of `update_entity()`."""
        eid = _event(sdk, name="Aliased")
        with pytest.raises(ValueError, match=":Event"):
            sdk.update(eid, status="superseded")

    def test_nested_props_cannot_slip_past_the_guard(self, sdk):
        """T1 — the MCP `props={…}` convention flattens before the guard runs."""
        eid = _event(sdk, name="Nested")
        with pytest.raises(ValueError, match=":Event"):
            sdk.update_entity(eid, props={"status": "retracted"})
        assert "status" not in _props(sdk, eid)

    def test_the_guard_resolves_the_write_s_own_key(self, sdk):
        """T2 — an `:Event` is reached by `eventId`; a `{id:$id}`-only guard is inert.

        The node below carries ONLY `eventId` (the shape `_upsert_event`'s plain
        path and the capture mint both produce), so a guard predicated on `id`
        would let the write through and every assertion here would pass vacuously.
        """
        g = sdk._get_proj().g
        g.query(
            "MERGE (e:Event {eventId:'ev-key-only'}) "
            "SET e.eventKind='deployment', e.name='KeyOnly'")
        with pytest.raises(ValueError, match=":Event"):
            sdk.update_entity("ev-key-only", status="superseded")
        assert "status" not in g.query(
            "MATCH (e:Event {eventId:'ev-key-only'}) RETURN properties(e)"
        ).result_set[0][0]

    def test_a_non_terminal_update_still_lands(self, sdk):
        eid = _event(sdk, name="Restatus")
        sdk.update_entity(eid, status="draft")
        assert _props(sdk, eid).get("status") == "draft"
        sdk.update_entity(eid, status="scheduled")
        assert _props(sdk, eid).get("status") == "scheduled"


# ── The third door: EventAPI.add_event ──────────────────────────────────────


class TestTheEventApiDoor:
    def _api(self, sdk):
        root = sdk._test_root
        log = EventLog(os.path.join(root, "events.jsonl"))
        return EventAPI(log, initiated_by="user", projection=sdk._get_proj())

    @pytest.mark.parametrize("status", _TERMINAL)
    def test_add_event_refuses_a_terminal_status(self, sdk, status):
        api = self._api(sdk)
        eid = f"ev-api-{status}"
        with pytest.raises(ValueError, match=":Event"):
            api.add_event(eid, "deployment", status=status)
        assert not sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e}) RETURN count(e)",
            params={"e": eid},
        ).result_set[0][0]
        # R5's journal half: a refused call appends NOTHING (the emit is after
        # the guard). Review cycle 1, P3.
        assert _journal_lines(sdk._test_log) == []

    @pytest.mark.parametrize("value", ["", 0, 0.0])
    def test_add_event_refuses_a_non_boolean_outdated(self, sdk, value):
        """Review cycle 1, P1 — measured reachable at the previous head: this
        door stored `outdated=''` / `0` / `0.0`, which the Cypher terminal
        predicate reads as TERMINAL and the Python mirror reads as LIVE."""
        api = self._api(sdk)
        with pytest.raises(ValueError, match="not a legal value on an :Event"):
            api.add_event("ev-api-nonbool", "deployment", outdated=value)
        assert not sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:'ev-api-nonbool'}) RETURN count(e)"
        ).result_set[0][0]
        assert _journal_lines(sdk._test_log) == []


    def test_add_event_refuses_the_outdated_flag(self, sdk):
        """Measured reachable at #11825's head: this door had NO refusal for it
        (the SDK doors get one from `_sanitize_props`; this one writes the
        envelope straight onto the node)."""
        api = self._api(sdk)
        with pytest.raises(ValueError, match=":Event"):
            api.add_event("ev-api-flag", "deployment", outdated=True)
        assert not sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:'ev-api-flag'}) RETURN count(e)"
        ).result_set[0][0]

    def test_add_event_still_accepts_a_non_terminal_status(self, sdk):
        api = self._api(sdk)
        api.add_event("ev-api-ok", "deployment", status="scheduled")
        assert _props(sdk, "ev-api-ok").get("status") == "scheduled"

    def test_a_nested_props_map_is_inert_at_this_door(self, sdk):
        """`add_event` never flattens, so `props={…}` is not a status lane here.

        Pinned so nobody "fixes" the guard against a shape that cannot land —
        the lane that CAN is `event={…}`, below.
        """
        api = self._api(sdk)
        api.add_event("ev-api-props", "deployment",
                      props={"status": "superseded"})
        assert "status" not in _props(sdk, "ev-api-props")

    def test_the_nested_record_key_is_unwrapped_and_refused(self, sdk):
        """Review cycle 2, P0 — the fold UNWRAPS a caller key named `event`.

        `_upsert_event` reads `inner = event.get("event", event)`, so
        `add_event(id, kind, event={"status": "superseded"})` put the status on
        the node while a top-level-only guard saw nothing. Measured at the
        previous head; refused now by the `_emit` chokepoint.
        """
        api = self._api(sdk)
        with pytest.raises(ValueError, match=":Event"):
            api.add_event("ev-api-nested", "deployment",
                          event={"id": "ev-api-nested", "status": "superseded"})
        assert not sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:'ev-api-nested'}) RETURN count(e)"
        ).result_set[0][0]
        assert _journal_lines(sdk._test_log) == []

    def test_the_nested_record_key_carries_the_flag_too(self, sdk):
        api = self._api(sdk)
        with pytest.raises(ValueError, match=":Event"):
            api.add_event("ev-api-nested-flag", "deployment",
                          event={"id": "ev-api-nested-flag", "outdated": True})
        assert _journal_lines(sdk._test_log) == []

    @pytest.mark.parametrize("value", [123, ["a"]])
    def test_a_non_string_status_is_a_named_refusal_not_a_type_error(self, sdk, value):
        """Review cycle 2, P2 — an unhashable status raised a bare `TypeError`
        (no door name, no remedy) and a hashable one stored an unnameable value.

        ⛔ Scoped to values the writer would PERSIST: `123` (a scalar) and
        `["a"]` (FalkorDB stores arrays) both land on the node. A
        NON-persistable value is the neighbouring test's case, and it is
        ACCEPTED — see `test_a_non_persistable_status_is_dropped_not_refused`.
        """
        api = self._api(sdk)
        with pytest.raises(ValueError, match="not a legal value on an :Event"):
            api.add_event("ev-api-typed", "deployment", status=value)
        assert _journal_lines(sdk._test_log) == []

    @pytest.mark.parametrize("value", [{"a": 1}, {"a": {"b": []}}])
    def test_a_non_persistable_status_is_dropped_not_refused(self, sdk, value):
        """#7925 (recorded decision) — a value the WRITER DROPS is not refused.

        `tests/test_ingest_event_endpoint.py::
        test_direct_route_object_and_event_status_values_not_over_rejected` pins
        this for the direct route: a map/set/bytes `status` is filtered out
        before the `SET`, so the node never carries it and no predicate can read
        it as terminal. Refusing it rejects a write the writers accept — the
        over-rejection class #7925 round 4 already fixed once, which the first
        revision of this guard re-introduced by asking only
        `isinstance(status, str)` (measured RED there, and it is why this arm
        asks `live._event_state_would_be_stored` instead).

        Pinned as a REGRESSION guard, not a new-guard assertion: it passes on
        the pre-fix code too, by design.

        ⛔ SCOPE: a MAP, and only a map. `bytes`/`set` are non-persistable too,
        but at THIS door they die earlier — `add_event` journals `**extra` and
        the value is not JSON-serializable, which is the pre-existing
        `EventAPI` defect filed as #12114, not this guard's question. Widening
        this param list to them would pin THAT bug as if it were intended.
        """
        api = self._api(sdk)
        api.add_event("ev-api-dropped", "deployment", status=value)
        # Accepted, and the value is genuinely DROPPED rather than stored: the
        # node carries no `status` at all, which is what makes the terminal
        # predicates unable to see it.
        assert "status" not in _props(sdk, "ev-api-dropped")


class TestTheBeliefSeam:
    """Review cycle 2, P0 — `emit_belief` is a FOURTH door onto an `:Event`.

    `type_` is a free string and `**payload` is splatted onto the record, so
    `emit_belief("EventRecorded", …)` reached the fold with no guard at all —
    on the same public class this change declares in scope. The refusal must be
    LOUD here: this seam's `except` arm swallows I/O failures, so a refusal that
    only fired inside `_emit` would be logged and the record silently dropped.
    """

    def _api(self, sdk):
        log = EventLog(os.path.join(sdk._test_root, "belief.jsonl"))
        return EventAPI(log, initiated_by="user", projection=sdk._get_proj())

    @pytest.mark.parametrize("status", _TERMINAL)
    def test_emit_belief_refuses_a_terminal_status_loudly(self, sdk, status):
        eid = _event(sdk, name=f"Belief-{status}")
        before = _props(sdk, eid)
        with pytest.raises(ValueError, match=":Event"):
            self._api(sdk).emit_belief(
                "EventRecorded", id=eid, eventKind="deployment", status=status)
        assert _props(sdk, eid) == before

    def test_emit_belief_refuses_the_flag(self, sdk):
        eid = _event(sdk, name="BeliefFlag")
        with pytest.raises(ValueError, match=":Event"):
            self._api(sdk).emit_belief(
                "EventRecorded", id=eid, eventKind="deployment", outdated=True)
        assert "outdated" not in _props(sdk, eid)

    def test_emit_belief_unwraps_the_nested_record_key(self, sdk):
        eid = _event(sdk, name="BeliefNested")
        with pytest.raises(ValueError, match=":Event"):
            self._api(sdk).emit_belief(
                "EventRecorded",
                event={"id": eid, "eventKind": "deployment",
                       "status": "superseded"})
        assert "status" not in _props(sdk, eid)

    def test_emit_belief_is_not_gated_for_other_record_types(self, sdk):
        """The chokepoint is RECORD-gated: a non-Event record is untouched."""
        self._api(sdk).emit_belief(
            "ConfidenceChanged", id="pt_x", status="superseded")

    # ── review cycle 3, P1: the STATE-OP shape, not just `EventRecorded` ──
    # Gating on the record type alone left the seam open one shape over. A caller
    # can hand this door the canonical state-op record for any label —
    # `EntityMutated` nested under `state` is exactly what
    # `projection._journal_entity_mutation` emits, and the fold reads it through
    # `state["status"]` → `restatus` — so these pins hold the fifth door shut.

    @pytest.mark.parametrize("status", _TERMINAL)
    def test_emit_belief_refuses_a_terminal_state_op(self, sdk, status):
        eid = _event(sdk, name=f"BeliefState-{status}")
        before = _props(sdk, eid)
        with pytest.raises(ValueError, match=":Event"):
            self._api(sdk).emit_belief(
                "EntityMutated", id=eid, label="Event", op="restatus",
                state={"status": status})
        assert _props(sdk, eid) == before

    def test_the_chokepoint_refuses_the_same_state_op(self, sdk):
        """Review cycle 6, P1 — the door-only placement left `_emit` open.

        The `EventRecorded` half is guarded at BOTH this door and the `_emit`
        chokepoint, precisely because "a door list is not a boundary". The
        state-op half was guarded only at the door, and a measurement showed the
        identical record reaching the fold with `_emit(…, label="Event",
        op="restatus", state={"status": "superseded"})` and storing it.
        """
        eid = _event(sdk, name="BeliefChokepoint")
        before = _props(sdk, eid)
        with pytest.raises(ValueError, match=":Event"):
            self._api(sdk)._emit(
                "EntityMutated", id=eid, label="Event", op="restatus",
                state={"status": "superseded"})
        assert _props(sdk, eid) == before

    def test_a_non_state_op_is_left_to_the_folds_own_verdict(self, sdk):
        """Review cycle 6, P2 — `op="delete"` writes no status, so no refusal.

        The fold reads `state` only for `_ENTITY_MUTATION_STATE_OPS`, so refusing
        a `delete` record that happens to carry a `state` map is an over-block
        with a WRONG diagnosis: the message would say a status is "not writable"
        when no status was ever going to be written.
        """
        eid = _event(sdk, name="BeliefDeleteOp")
        self._api(sdk).emit_belief(
            "EntityMutated", id=eid, label="Event", op="delete",
            state={"status": "superseded"})
        # The fold's delete arm ran (the node is gone) and no refusal was raised
        # on the way — the record was left to the arm that owns it.
        rows = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e}) RETURN count(e)",
            params={"e": eid}).result_set
        assert rows[0][0] == 0, "the delete op should have removed the node"

    def test_emit_belief_refuses_a_terminal_flag_in_a_state_op(self, sdk):
        eid = _event(sdk, name="BeliefStateFlag")
        with pytest.raises(ValueError, match=":Event"):
            self._api(sdk).emit_belief(
                "EntityMutated", id=eid, label="Event", op="restatus",
                state={"outdated": True})
        assert "outdated" not in _props(sdk, eid)

    def test_emit_belief_state_op_with_a_non_terminal_status_still_lands(self, sdk):
        """No over-block: `draft`/`live` are the prop lane, and #2788 pins it."""
        eid = _event(sdk, name="BeliefStateLive")
        self._api(sdk).emit_belief(
            "EntityMutated", id=eid, label="Event", op="restatus",
            state={"status": "live"})
        assert _props(sdk, eid)["status"] == "live"


# ── the bundle door: Phase 1 must refuse what step 3 would refuse mid-write ──


class TestTheBundleValidationDoor:
    """Review cycle 3, P1 — `_validate_bundle` passed a terminal Event item.

    The guard sat only at the DOOR, so a bundle item carrying a terminal status
    passed Phase 1 and raised in step 3 AFTER the point items had committed —
    the #2422/#7925 partial-mutation class, which is why the `:Subject`
    vocabulary arm next to this one was put in Phase 1. Measured before the
    fix: `_validate_bundle` returned `[]` and `ingest` left `Point 1` written.
    """

    @pytest.mark.parametrize("status", _TERMINAL)
    def test_a_terminal_event_item_is_a_phase_1_violation(self, sdk, status):
        violations = sdk._validate_bundle({
            "points": [{"content": "first"}],
            "entities": [{"ref": "e1", "type": "event",
                          "name": f"Bundle-{status}",
                          "eventKind": "deployment", "status": status}],
        })
        assert violations, (
            f"a bundle item with status={status!r} passed Phase 1 — ingest would "
            "then raise mid-write with the point already committed")
        assert any("event" in v["message"].lower() for v in violations)

    def test_a_non_terminal_event_item_is_not_a_violation(self, sdk):
        """No over-block: the door accepts `scheduled`, so Phase 1 must too."""
        assert sdk._validate_bundle({
            "entities": [{"ref": "e1", "type": "event", "name": "Sched",
                          "eventKind": "deployment", "status": "scheduled"}],
        }) == []

    def test_an_object_item_with_a_terminal_status_is_not_a_violation(self, sdk):
        """`:Object` has a lifecycle — its writer stores the value (T5)."""
        assert sdk._validate_bundle({
            "entities": [{"ref": "o1", "type": "object", "name": "PolicyV2",
                          "objectKind": "policy", "status": "superseded"}],
        }) == []

    @pytest.mark.parametrize("status", [42, 3.5, True])
    def test_a_persistable_non_string_status_is_a_phase_1_violation(
            self, sdk, status):
        """Review cycle 6, P1 — the TYPE dimension of the same door.

        `refuse_terminal_event_state` refuses a PERSISTABLE non-string status,
        because an int glides through the vocabulary test and stores a status no
        reader can name. Phase 1 did not mirror that dimension, so this bundle
        passed validation and raised in step 3 with the point already committed
        (`phase1: []` then `ValueError`, point count 0 → 1).
        """
        bundle = {
            "points": [{"content": "must-not-commit"}],
            "entities": [{"ref": "e1", "type": "event", "name": "BadType",
                          "eventKind": "deployment", "status": status}],
        }
        violations = sdk._validate_bundle(bundle)
        assert violations, (
            f"status={status!r} passed Phase 1 — ingest would then raise "
            "mid-write with the point already committed")
        assert any("STRING" in v["message"] for v in violations)

    @pytest.mark.parametrize("status", [{"a": 1}, {1, 2}, b"raw"])
    def test_a_non_persistable_status_is_accepted_and_dropped(self, sdk, status):
        """The other half of the door's rule (#7925): it is never stored."""
        assert sdk._validate_bundle({
            "entities": [{"ref": "e1", "type": "event", "name": "Drop",
                          "eventKind": "deployment", "status": status}],
        }) == []

    def test_every_connection_shape_still_gets_a_diagnosis(self, sdk):
        """COVERAGE is pinned, not the message count (review cycle 5 P1).

        The arm must fire for EVERY shape that ends up writing a terminal
        `:Event` in step 3 — and one shape it must not miss is a connection the
        OTHER Event arm never inspects: that one runs only for a DIRECT-route
        connection carrying an `operator`, so `reify: true` and
        `relation: …` connections reach step 3 unguarded if this arm defers to
        them (measured: a deferring revision produced ZERO violations for both
        shapes, so `ingest` committed the point and then raised mid-write — the
        #2422/#7925 class this arm exists to prevent). A repeated message on the
        direct-operator shape is the accepted cost, exactly as the `:Subject`
        vocabulary arm next door accepts it.
        """
        item = {"ref": "evt", "type": "event", "name": "Launch",
                "eventKind": "sessionCaptured", "status": "superseded"}
        point = {"ref": "pX", "kind": "statement", "content": "must-not-commit"}
        object_item = {"ref": "o", "type": "object", "name": "Delta"}
        shapes = {
            "operator": {"from": "evt", "to": "o", "operator": "IMPL"},
            "reify": {"from": "evt", "to": "o", "operator": "IMPL",
                      "reify": True},
            "relation": {"from": "evt", "to": "o",
                         "relation": "extractedFrom"},
            "to-list": {"from": "evt", "to": ["o"], "operator": "IMPL"},
        }
        for name, conn in shapes.items():
            bundle = {"points": [dict(point)],
                      "entities": [dict(item), dict(object_item)],
                      "connections": [conn]}
            msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
            assert msgs, f"the {name!r} shape produced NO violation — step 3 would half-commit"
            assert any("declares status 'superseded'" in m for m in msgs), (name, msgs)
        # …and the unreferenced shape, which no connection arm can ever see.
        alone = {"points": [dict(point)], "entities": [dict(item)]}
        assert any("declares status 'superseded'" in m
                   for m in (v["message"] for v in sdk._validate_bundle(alone))), (
            "an unreferenced terminal Event item passed Phase 1")


# ── T5: no over-block on the other labels ───────────────────────────────────


# ── T5: no over-block on the other labels ───────────────────────────────────

class TestTheGuardDoesNotOverBlock:
    def test_a_point_still_takes_a_terminal_status(self, sdk):
        pid = sdk.create_point("statement", "a claim", status="live")["id"]
        sdk.update_entity(pid, status="superseded")
        row = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status", params={"id": pid}
        ).result_set
        assert row[0][0] == "superseded"

    def test_a_point_still_refuses_an_undeclared_status(self, sdk):
        """The Point door's own vocabulary is untouched by this change."""
        pid = sdk.create_point("statement", "a claim", status="live")["id"]
        with pytest.raises(ValueError, match="Invalid status"):
            sdk.update_entity(pid, status="dead")

    def test_a_subject_still_takes_a_terminal_status(self, sdk):
        """The `:Subject` arm sits immediately above the new `:Event` arm."""
        sid = sdk.create_entity("subject", "Acme Corp", subjectKind="organization")["node"]["id"]
        sdk.update_entity(sid, status="superseded")
        row = sdk._get_proj().g.query(
            "MATCH (n:Subject {id:$id}) RETURN n.status", params={"id": sid}
        ).result_set
        assert row[0][0] == "superseded"

    def test_an_object_still_takes_a_terminal_status(self, sdk):
        oid = sdk.create_entity("object", "PolicyV1", objectKind="policy")["node"]["id"]
        sdk.update_entity(oid, status="superseded")
        row = sdk._get_proj().g.query(
            "MATCH (n:Object {id:$id}) RETURN n.status", params={"id": oid}
        ).result_set
        assert row[0][0] == "superseded"

    def test_a_legacy_terminal_event_can_still_be_repaired(self, sdk):
        """The repair route a denylist (unlike an allowlist) preserves.

        A pre-fix head — or raw Cypher — can leave a terminal status on an
        Event. It cannot be written again, but it can be CLEARED, which is the
        only route a tenant has; `status="live"` and `status=None` are both
        non-terminal and therefore still accepted. Pinned because an allowlist
        of `{live, draft}` would have kept `None` refused and made the state
        permanent for existing data (PLAN-VERIFY round 1, finding 7).
        """
        eid = _event(sdk, name="Legacy")
        sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e}) SET e.status='superseded'",
            params={"e": eid})
        assert _props(sdk, eid).get("status") == "superseded"
        # the terminal value can no longer be written…
        with pytest.raises(ValueError, match=":Event"):
            sdk.update_entity(eid, status="superseded")
        # …and the legacy mark can be lifted
        sdk.update_entity(eid, status="live")
        assert _props(sdk, eid).get("status") == "live"
        sdk.update_entity(eid, status=None)
        assert "status" not in _props(sdk, eid)

    def test_the_whole_terminal_vocabulary_is_a_subset_of_the_point_vocabulary(self):
        """Documents the boundary: the Point door validates, the Event door denies.

        `TERMINAL_EXCLUDED_STATUSES` is the Point vocabulary's terminal half plus
        the legacy `deprecated`; every member except `deprecated` is a legal
        Point status. Nothing here changes that — this is a WRITE denial on the
        one label that has no lifecycle.
        """
        extra = TERMINAL_EXCLUDED_STATUSES - POINT_STATUS_VALUES
        assert extra == {"deprecated"}
