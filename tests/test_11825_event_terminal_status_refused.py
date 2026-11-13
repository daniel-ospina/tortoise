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

#: Either refusal answers "can this key smuggle a terminal status?".
#:
#: `EventAPI.add_event` grew main's #12106/#12133 KEY-TYPE guard, which fires
#: BEFORE the `:Event` arm and is deliberately STRICTER (it refuses a key that is
#: not an exact `str`, whatever it renders as). Both refusals are correct answers
#: to the question these tests ask, and neither may be weakened: the assertion is
#: the STATE (nothing stored, nothing journalled), so the pattern accepts either
#: and the ordering is main's to decide.
_EITHER_REFUSAL = r"(not writable on an :Event|not an exact `str`)"

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
    """The update door, including the MULTI-LABEL shape review cycle 10 measured.

    A node can carry `:Point` and `:Event` at once (`#3311`'s `retype`, migrated
    graphs). The `:Event` guard used to sit AFTER the Point status arm, whose
    `SET n += $s` writes and then drops `status` from `props` — so on such a node
    the guard saw no declaration and the terminal status landed. It also probed
    reachability by `eventId` alone, so the label without that key was invisible.
    Both halves are pinned here, because either one alone leaves the state the
    change exists to prevent reachable.
    """

    def _multi_label(self, sdk, name, *, event_id: bool, status="live"):
        node = sdk.create_point("statement", name, status=status)
        pid = (node.get("node") or {}).get("id") or node.get("id")
        g = sdk._get_proj().g
        g.query("MATCH (p:Point {id:$p}) SET p:Event", params={"p": pid})
        if event_id:
            g.query("MATCH (n {id:$p}) SET n.eventId = $p", params={"p": pid})
        return pid

    def _state(self, sdk, pid):
        rows = sdk._get_proj().g.query(
            "MATCH (n {id:$p}) RETURN [n.status, labels(n)]",
            params={"p": pid}).result_set
        assert rows, f"no node for {pid!r}"
        return rows[0][0]

    @pytest.mark.parametrize("event_id", [False, True])
    def test_a_terminal_update_is_refused_on_a_multi_label_event(
            self, sdk, event_id):
        pid = self._multi_label(sdk, f"Multi-{event_id}", event_id=event_id)
        before = self._state(sdk, pid)
        records_before = len(_journal_lines(sdk._test_log))
        with pytest.raises(ValueError, match=":Event"):
            sdk.update_entity(pid, status="superseded")
        assert self._state(sdk, pid) == before, (
            "the Point arm wrote before the :Event guard could refuse")
        assert len(_journal_lines(sdk._test_log)) == records_before, (
            "a refused update must not journal a restatus the fold would replay")

    def test_a_non_terminal_update_still_lands_on_a_multi_label_event(self, sdk):
        """No over-block: `live` is a legal `:Event` prop-lane write (#2788).

        ⛔ The node is born `draft`, not `live`: created `live`, the assertion below
        held with the write deleted entirely, so it pinned nothing (measured,
        review cycle 11 P2). The pin is the MOVE.
        """
        pid = self._multi_label(sdk, "Multi-live", event_id=False,
                                status="draft")
        assert self._state(sdk, pid)[0] == "draft"
        sdk.update_entity(pid, status="live")
        assert self._state(sdk, pid)[0] == "live"

    def test_a_refused_rename_costs_no_status_write(self, sdk):
        """#12222: every refusal on this surface precedes every write.

        Measured before the fix, on a `:Point:Object` in `draft` whose target
        name a live `:Object` already holds: `update_entity(<id>,
        status="live", name=<held>)` raised `NameAlreadyHeld` and STILL left
        `status='live'` on the node with a journaled `restatus` — the fold
        would replay a status the caller was told had not been applied.
        """
        holder = sdk.create_entity("object", "taken-name")["node"]["id"]
        assert holder
        pid = sdk.create_point("statement", "rename-target", status="draft")["id"]
        g = sdk._get_proj().g
        g.query("MATCH (p:Point {id:$p}) SET p:Object", params={"p": pid})
        before = self._state(sdk, pid)
        records_before = len(_journal_lines(sdk._test_log))
        with pytest.raises(ValueError, match=r"taken-name|NameAlreadyHeld"):
            sdk.update_entity(pid, status="live", name="taken-name")
        assert self._state(sdk, pid) == before, (
            "the status arm wrote before the rename guard refused")
        assert len(_journal_lines(sdk._test_log)) == records_before, (
            "a refused rename must not journal the restatus it refused to pair")

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


# ── The lifecycle doors: retract / supersede / invalidate ────────────────────


class TestTheLifecycleDoors:
    """An `:Event` that is also a `:Point` must not be TERMINALIZED (#11825).

    All three doors reach `_assert_lifecycle_guard` before any write, so that is
    where the refusal belongs — and it must be there, because each of them writes a
    TERMINAL status of its own, none of which passes through `update_entity`.
    Measured at review cycle 11 on a `:Point:Event`:
    `retract_point` → `['retracted', ['Point','Event']]`, a node the belief layer
    reads as dead and every retrieval leg serves as live.
    """

    def _multi_label(self, sdk, name):
        pid = sdk.create_point("statement", name)["id"]
        sdk._get_proj().g.query(
            "MATCH (p:Point {id:$p}) SET p:Event", params={"p": pid})
        return pid

    def _state(self, sdk, pid):
        return sdk._get_proj().g.query(
            "MATCH (n {id:$p}) RETURN n.status", params={"p": pid},
        ).result_set[0][0]

    def test_retract_refuses_a_multi_label_event(self, sdk):
        pid = self._multi_label(sdk, "RetractEvent")
        with pytest.raises(ValueError, match=":Event"):
            sdk.retract_point(pid)
        assert self._state(sdk, pid) != "retracted"

    def test_invalidate_refuses_a_multi_label_event(self, sdk):
        pid = self._multi_label(sdk, "InvalidateEvent")
        replacement = sdk.create_point("statement", "Replacement")["id"]
        with pytest.raises(ValueError, match=":Event"):
            sdk.invalidate_point(pid, replacement)
        assert self._state(sdk, pid) != "outdated"

    def test_supersede_refuses_a_multi_label_event(self, sdk):
        pid = self._multi_label(sdk, "SupersedeEvent")
        replacement = sdk.create_point("statement", "Successor")["id"]
        with pytest.raises(ValueError, match=":Event"):
            sdk.supersede_point(pid, replacement)
        assert self._state(sdk, pid) != "superseded"

    def test_an_ordinary_point_still_terminalizes(self, sdk):
        """No over-block: the guard is label-scoped, not a blanket refusal."""
        pid = sdk.create_point("statement", "PlainPoint")["id"]
        sdk.retract_point(pid)
        assert self._state(sdk, pid) == "retracted"

    def test_an_event_that_only_shares_the_id_does_not_block_the_point(
            self, sdk):
        """The probe is `:Point:Event`, i.e. the nodes THIS door writes.

        `add_event(<a point's id>)` mints a SEPARATE `:Event` keyed by that id
        (review cycle 12, measured). Probing with the update door's OR-form
        refused the point's retraction with "carries the :Event label" while the
        node the door writes was a plain `:Point` and stayed `live` — a refusal
        justified by a node it could not reach.
        """
        from tortoise.api import EventAPI
        from tortoise.log import EventLog

        pid = sdk.create_point("statement", "SharedId")["id"]
        api = EventAPI(EventLog(os.path.join(sdk._test_root, "ev.jsonl")),
                       initiated_by="user", projection=sdk._get_proj())
        api.add_event(pid, "deployment")
        sdk.retract_point(pid)
        assert self._state(sdk, pid) == "retracted"


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


    @pytest.mark.parametrize("bad", ["a string", 42, None, ["a"]],
                             ids=["str", "int", "none", "list"])
    def test_a_non_mapping_record_gets_the_named_refusal_not_a_bare_typeerror(
            self, sdk, bad):
        """Conflict-sweep lane F, P3 — the SPLIT-shape splice was built eagerly.

        The state-op box sweeps `(payload, *record, {**payload, **record[0]})`, and
        that third element was built INLINE in the tuple — so it was evaluated
        before the loop's `isinstance(_map, dict)` test and before the
        `record[0] is not dict` refusal further down. A non-mapping record
        therefore raised a bare ``TypeError: 'str' object is not a mapping`` out
        of the splat, and the guard whose own comment explains why such a
        container is refused became UNREACHABLE. That is a regression against
        `main`, which validates the record before reading it and answers these
        same inputs with its named ``ValueError`` — the un-named 500-class this
        box exists to replace with a diagnosis.
        """
        api = self._api(sdk)
        # NO label/op/state: those trip the OTHER early refusal, and this pin is
        # about the SPLICE being evaluated before the loop can skip a non-mapping.
        with pytest.raises(ValueError, match="must be an exact `dict`"):
            api._emit("EntityMutated", bad)

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
    def test_a_stored_non_string_status_is_a_named_refusal(self, sdk, value):
        """Review cycle 2, P2 — an unhashable status raised a bare `TypeError`
        (no door name, no remedy) and a hashable one stored an unnameable value.

        Both are PERSISTABLE, so both would land on the node — the test is
        scoped to those, see the drop pin below.
        """
        api = self._api(sdk)
        with pytest.raises(ValueError, match="not a legal value on an :Event"):
            api.add_event("ev-api-typed", "deployment", status=value)
        assert _journal_lines(sdk._test_log) == []

    @pytest.mark.parametrize("value", [b"x", {1, 2}, bytearray(b"x")])
    def test_a_value_the_journal_cannot_encode_is_refused_not_left_to_crash(self, sdk, value):
        """Review cycle 3, P2 — THREE writers, not one.

        The first revision of the scope asked only the FOLD's allowlist, so these
        were accepted: they are not persistable, but the JSON journal encoder
        raises on them — a bare `TypeError` at `add_event`/`_emit`, and at
        `emit_belief` a raise inside the best-effort `except` arm that DROPS the
        record silently. Refused by name, and nothing is journalled (proved by
        the fact that this door journals the whole bundle, so a swallowed write
        would have appended a record).
        """
        api = self._api(sdk)
        with pytest.raises(ValueError, match="not a legal value on an :Event"):
            api.add_event("ev-api-unencodable", "deployment", status=value)
        assert _journal_lines(sdk._test_log) == []

    @pytest.mark.parametrize("factory", ["np.int64", "np.bool_", "Decimal"])
    def test_a_value_the_transport_stores_is_refused_even_if_the_fold_would_drop_it(self, sdk, factory):
        """Review cycle 3, P2 — the fold's allowlist is NOT the writer's test.

        ``_is_persistable_prop_value`` refuses these LEAVES while the engine
        really does STORE them (``projection._value_ok``, the predicate the
        ``#7369`` gate nulls on), so scoping on the fold alone let
        ``update_entity(status=np.int64(7))`` write ``7`` onto the node — a
        status no vocabulary can name, which is exactly what the type refusal is
        for. Refused now, and the stored status is untouched.
        """
        np = pytest.importorskip("numpy")
        from decimal import Decimal

        value = {"np.int64": lambda: np.int64(7),
                 "np.bool_": lambda: np.bool_(True),
                 "Decimal": lambda: Decimal("0.25")}[factory]()
        eid = _event(sdk, name=f"Transport-{factory}")
        sdk.update_entity(eid, status="draft")
        with pytest.raises(ValueError, match="not a legal value on an :Event"):
            sdk.update_entity(eid, status=value)
        assert _props(sdk, eid)["status"] == "draft"

    def test_a_key_that_RENDERS_as_status_cannot_smuggle_a_terminal_status(self, sdk):
        """Review cycle 5, P0 — the key is rendered, not normalised by content.

        The writer inlines a key as a backticked string with ``str(key)`` (bytes:
        ``key.decode()``) — `projection._map_key_ok` says so verbatim — so a
        ``str`` SUBCLASS whose CONTENT is ``"x"`` but whose ``__str__`` returns
        ``"status"`` is STORED under ``status``. Normalising the key with
        ``str.__str__`` (by content) therefore let it through while the writer
        filed it under the guarded name: MEASURED at review cycle 5, the terminal
        value was stored at ``update_entity``, ``add_event`` and
        ``create_entity('event')``, and the flag variant hid the same way. The
        KEY is now rendered exactly as the writer renders it; the VALUE keeps its
        base content (``str.__str__``), because that is what the writer keeps.
        """

        class _KeyLiar(str):
            def __str__(self):
                return "status"

        eid = _event(sdk, name="KeyRenders")
        sdk.update_entity(eid, status="draft")
        with pytest.raises(ValueError, match="not writable on an :Event"):
            sdk.update_entity(eid, **{_KeyLiar("x"): "superseded"})
        assert _props(sdk, eid)["status"] == "draft"

        api = self._api(sdk)
        _before = len(_journal_lines(sdk._test_log))
        with pytest.raises(ValueError, match=_EITHER_REFUSAL):
            api.add_event("ev-kliar", "deployment", **{_KeyLiar("x"): "superseded"})
        assert len(_journal_lines(sdk._test_log)) == _before, (
            "a refused call must append nothing to the journal")

    def test_a_terminal_declaration_cannot_be_MASKED_by_a_later_key(self, sdk):
        """Review cycle 8, P0 — the guard judged only the LAST declaration.

        The writer applies EVERY key of the map, so two keys that both render to
        `status` are BOTH written. A last-one-wins guard let a terminal value be
        masked by a later non-terminal one: measured, `{status: superseded,
        <alias whose content is status>: live}` was ACCEPTED and the node landed
        `status='superseded'` — live, and again after `rebuild_all` — at six of
        the seven doors. The guard now refuses if ANY declaration is terminal,
        and the same rule covers `outdated` (`{outdated: True, <alias
        outdated>: False}`), symmetric with the Phase-1 arm.
        """

        class _AliasHarmless(str):
            def __str__(self):
                return "harmless"

            def __hash__(self):
                return 424242

            def __eq__(self, other):
                return isinstance(other, _AliasHarmless) and str.__eq__(self, other)

        class _AliasOutdated(str):
            def __str__(self):
                return "harmless"

            def __hash__(self):
                return 919191

            def __eq__(self, other):
                return isinstance(other, _AliasOutdated) and str.__eq__(self, other)

        eid = _event(sdk, name="Masked")
        with pytest.raises(ValueError, match="not writable on an :Event"):
            sdk.update_entity(eid, **{"status": "superseded",
                                      _AliasHarmless("status"): "live"})
        assert "status" not in _props(sdk, eid)

        api = self._api(sdk)
        before = len(_journal_lines(sdk._test_log))
        with pytest.raises(ValueError, match=_EITHER_REFUSAL):
            api.add_event("ev-masked-flag", "deployment",
                          **{"outdated": True, _AliasOutdated("outdated"): False})
        assert len(_journal_lines(sdk._test_log)) == before

    def test_a_key_the_JOURNAL_would_file_as_status_is_guarded_too(self, sdk):
        """Review cycle 7, P1 — TWO key renderers, and the journal is the second.

        The live transport inlines ``str(key)``; the journal serialises the map
        with ``json.dumps``, which writes a ``str`` SUBCLASS key by its CONTENT.
        A key with content ``"status"`` and ``__str__`` returning ``"harmless"``
        was therefore ACCEPTED at every door: the live node kept
        ``harmless='superseded'``, the journalled record carried
        ``"status": "superseded"``, and ``rebuild_all`` from that journal
        produced a TERMINAL ``:Event`` — a bypass that exists only on the replay
        path, which is the path this whole half-state class is about. The guard
        now asks the UNION of both renderings.
        """

        class _ReplayKey(str):
            def __str__(self):
                return "harmless"

        eid = _event(sdk, name="ReplaySplit")
        with pytest.raises(ValueError, match="not writable on an :Event"):
            sdk.update_entity(eid, **{_ReplayKey("status"): "superseded"})
        stored = _props(sdk, eid)
        assert "harmless" not in stored and "status" not in stored, stored

    def test_the_outdated_half_is_gated_by_the_same_rendered_key_rule(self, sdk):
        """Review cycle 6, P1 — the SDK door gated on `status` ALONE.

        `declares_status_key` (a helper since removed at PR #12108's review)
        gated `update_entity`, so `outdated` was dead code
        there for exactly the key class this change fixed for `status`: a
        rendered-alias `outdated` key slipped past `_sanitize_props`' literal
        `"outdated" in props`, the guard was skipped, and the flag was STORED —
        the terminal half-state #11825 records, through a declared door. Measured
        on the previous head: `update_entity(<event>, **{KOut("x"): True})` was
        ACCEPTED and the node read `status=None, outdated=True`, which `ep.py`
        reads as terminal while every retrieval leg serves it.
        """

        class _KeyOutdated(str):
            def __str__(self):
                return "outdated"

        eid = _event(sdk, name="AliasOutdated")
        with pytest.raises(ValueError, match="not writable on an :Event"):
            sdk.update_entity(eid, **{_KeyOutdated("x"): True})
        assert "outdated" not in _props(sdk, eid)

    def test_a_BYTES_key_naming_status_is_refused_before_the_write(self, sdk):
        """Review cycle 5, P1 — the driver DECODES a bytes key.

        ``_map_key_ok`` renders ``bytes`` keys with ``key.decode()``, so
        ``b"status"`` really does name the property; a guard that skipped non-
        ``str`` keys missed it. Measured: ``create_entity('event', props={b'status':
        …})`` stored the terminal status and left NO journal record (the live
        write committed before the ``**`` splat raised), i.e. a replay-invisible
        terminal Event. Refused now, before any write, and nothing is stored.
        """
        with pytest.raises(ValueError, match="not writable on an :Event"):
            sdk.create_entity("event", "BytesKey", eventKind="deployment",
                              props={b"status": "superseded"})
        assert sdk._get_proj().g.query(
            "MATCH (e:Event {name:'BytesKey'}) RETURN count(e)").result_set[0][0] == 0

    def test_the_positional_record_channel_is_swept_for_state_ops(self, sdk):
        """Review cycle 5, P1 — `_emit` takes a keyword payload OR a record.

        The state-op arm read only the keyword payload, while the positional
        record is spliced into ``payload["point"]`` and the fold applies it — so
        ``_emit("EntityMutated", {… op: restatus, state: {status: superseded}})``
        was ACCEPTED, stored the terminal status and journalled a record that
        reproduces it on replay. Both channels are swept now, exactly as the
        `EventRecorded` arm above already does.
        """
        eid = _event(sdk, name="PosRecord")
        sdk.update_entity(eid, status="draft")
        api = self._api(sdk)
        with pytest.raises(ValueError, match="not writable on an :Event"):
            api._emit("EntityMutated", {"id": eid, "label": "Event",
                                        "op": "restatus",
                                        "state": {"status": "superseded"}})
        assert _props(sdk, eid)["status"] == "draft"

    def test_the_two_doors_handle_a_dropped_status_differently_and_both_are_legal(self, sdk):
        """Review cycle 3, P2 — "dropped" is FOLD behaviour, not a universal rule.

        The fold skips a non-persistable property (``_persist_extra_props``), so
        ``add_event`` leaves an EXISTING status untouched. The live write door
        hands the same value to the ``#7369`` transport gate, which nulls it —
        and ``SET n += {status: null}`` REMOVES the property, so
        ``update_entity`` CLEARS it. Two different outcomes from one accepted
        value; neither can PRODUCE a terminal state, which is the only property
        this gate is about (and clearing is permitted by design: ``status=None``
        clears). Pinned because the docstring's first revision claimed the fold's
        outcome was universal, and nothing measured the other door.
        """
        api = self._api(sdk)
        api.add_event("ev-drop-fold", "deployment", status="draft")
        assert _props(sdk, "ev-drop-fold")["status"] == "draft"
        api.add_event("ev-drop-fold", "deployment", status={"a": 1})
        assert _props(sdk, "ev-drop-fold")["status"] == "draft"

        eid = _event(sdk, name="DropLive")
        sdk.update_entity(eid, status="draft")
        assert _props(sdk, eid)["status"] == "draft"
        sdk.update_entity(eid, status={"a": 1})
        assert "status" not in _props(sdk, eid)

    def test_an_unhashable_string_subclass_is_refused_by_name(self, sdk):
        """Review cycle 3, P3 — a ``str`` SUBCLASS can be unhashable.

        ``class S(str): __hash__ = None`` passes ``isinstance(status, str)``, so
        the ``frozenset`` membership inside ``is_terminal_status`` raised a bare
        ``TypeError`` at every door. Pre-existing — the original predicate had
        the same hole — but it falsifies the claim that this gate never leaks an
        un-named 500-class, so the value is now normalised with
        ``str.__str__`` and a TERMINAL one is still refused, by name. A
        ``TypeError`` escaping here fails this test rather than passing it.
        """

        class _UnhashableStr(str):
            __hash__ = None

        api = self._api(sdk)
        with pytest.raises(ValueError, match="not writable on an :Event"):
            api.add_event("ev-api-subclass", "deployment",
                          status=_UnhashableStr("superseded"))
        assert _journal_lines(sdk._test_log) == []

    def test_a_str_subclass_KEY_cannot_smuggle_a_terminal_status(self, sdk):
        """Review cycle 4, P2 — the KEY is caller input too, and ``props.get`` is
        not a boundary.

        ``props.get("status")`` hashes the literal name, so a ``str`` SUBCLASS key
        that overrides ``__hash__`` (or ``__eq__``) is invisible to it while
        ``_persist_extra_props`` writes it anyway (it iterates ``items()`` and the
        driver renders the key with ``str()``) — MEASURED at review cycle 4:
        ``add_event(**{K("status"): "superseded"})``, the same through
        ``update_entity`` and ``create_entity('event')``, all ACCEPTED and STORED
        as ``superseded``. The value axis was already closed; this pins the key
        axis, so the guard's promise — an answer independent of the kwargs SHAPE —
        holds on both.
        """

        class _ConstHashKey(str):
            def __hash__(self):
                return 0

        class _LyingEqKey(str):
            # ``__eq__`` alone would make the class UNHASHABLE (Python nulls
            # ``__hash__``), and an unhashable key cannot be passed through ``**``
            # at all — so the reachable shape keeps the base hash and lies only
            # about equality. ``props.get("status")`` then finds the bucket and
            # the stored KEY's ``__eq__`` answers False, which is the miss.
            __hash__ = str.__hash__

            def __eq__(self, other):
                return False

            __ne__ = __eq__

        for key in (_ConstHashKey("status"), _LyingEqKey("status")):
            api = self._api(sdk)
            with pytest.raises(ValueError, match=_EITHER_REFUSAL):
                api.add_event("ev-api-keyed", "deployment", **{key: "superseded"})
            assert _journal_lines(sdk._test_log) == []

        eid = _event(sdk, name="KeyedUpdate")
        sdk.update_entity(eid, status="draft")
        with pytest.raises(ValueError, match="not writable on an :Event"):
            sdk.update_entity(eid, **{_ConstHashKey("status"): "superseded"})
        assert _props(sdk, eid)["status"] == "draft"

    def test_a_str_subclass_that_lies_about_hash_and_eq_is_still_refused(self, sdk):
        """Review cycle 3, P2 — the normalisation is the whole fix.

        A subclass can defeat the terminal test three ways: a constant
        ``__hash__`` lands in the wrong bucket so a terminal word tests False, an
        ``__eq__`` that answers False is caught by the same membership call, and
        ``__hash__ = None`` raises. With the first two MEASURED at review cycle 3
        the value was not refused — it was STORED as ``'superseded'``, i.e. the
        guard became the bypass instead of the refusal. ``str.__str__`` returns
        a plain ``str`` without consulting any override, so the test runs on the
        value the store would hold.
        """

        class _LyingStr(str):
            def __hash__(self):
                return 0

            def __str__(self):
                return "draft"

            def __eq__(self, other):
                return False

            __ne__ = __eq__

        api = self._api(sdk)
        with pytest.raises(ValueError, match="not writable on an :Event"):
            api.add_event("ev-api-lying", "deployment",
                          status=_LyingStr("superseded"))
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
        asks `live._status_is_discarded_by_every_writer` instead).

        Pinned as a REGRESSION guard, not a new-guard assertion: it passes on
        the pre-fix code too, by design.

        ⛔ SCOPE: a MAP, and only a map. `bytes`/`set`/`bytearray` are
        non-persistable too, but they are NOT accepted: no journal can encode
        them, so accepting one made the record append raise a bare `TypeError`
        (and, at `emit_belief`, drop the record silently). They are refused by
        name in
        `test_a_value_the_journal_cannot_encode_is_refused_not_left_to_crash`,
        which is the neighbouring arm of this same three-predicate rule.
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

    @pytest.mark.parametrize("status", _TERMINAL)
    def test_an_event_record_carrying_the_state_op_shape_is_refused_too(
            self, sdk, status):
        """Conflict-sweep lane F, P1 — the two guarded shapes OVERLAP.

        The state-op check sat on an `elif` under the `EventRecorded` arm, so a
        record that is BOTH — `EventRecorded` plus `label="Event"`, `op="restatus"`,
        `state={"status": …}` — matched the first arm and skipped the second. The
        first arm reads a TOP-LEVEL `status`, which this payload does not carry; the
        second would have seen it, and did not run. The record then reached `_emit`,
        whose chokepoint sweeps the state-op shape UNCONDITIONALLY, raised THERE —
        inside this door's `except Exception`, which re-raises only
        `UnapprovedKindNamespace` — and was logged and DROPPED. Measured before the
        fix: the call returned `None` with the node and the journal unchanged, i.e.
        the F2 divergence the door's own comment says it exists to prevent, reached
        through a public method. The check is a separate `if` now, so the refusal is
        eager and NAMES this door.
        """
        eid = _event(sdk, name=f"BeliefBoth-{status}")
        before = _props(sdk, eid)
        journal_before = len(_journal_lines(sdk._test_log))
        with pytest.raises(ValueError, match=":Event"):
            self._api(sdk).emit_belief(
                "EventRecorded", id=eid, eventKind="deployment",
                label="Event", op="restatus", state={"status": status})
        assert _props(sdk, eid) == before
        assert len(_journal_lines(sdk._test_log)) == journal_before, (
            "a refused record must not reach the journal either")

    def test_a_label_that_renders_two_ways_is_still_an_event(self, sdk):
        """Review cycle 8, P1 — `str(label)` is not the only renderer.

        The live writers interpolate `str(label)`; the JOURNAL keeps a `str`
        subclass by its CONTENT. So a label whose content is `Event` and whose
        `__str__` says `harmless` passed a `str(label)` test, landed on
        `n:harmless` live, and REPLAYED as a terminal `:Event` — a bypass that
        exists only on the record path, which is the path the whole half-class is
        about. Both doors ask `live.label_names_an_event` now.
        """
        class Lie(str):
            def __str__(self):  # the live rendering
                return "harmless"

        for door in ("_emit", "emit_belief"):
            eid = _event(sdk, name=f"LabelLie-{door.strip('_')}")
            before = _props(sdk, eid)
            with pytest.raises(ValueError, match=":Event"):
                getattr(self._api(sdk), door)(
                    "EntityMutated", id=eid, label=Lie("Event"), op="restatus",
                    state={"status": "superseded"})
            assert _props(sdk, eid) == before, door

    def test_the_chokepoint_refuses_the_same_state_op(self, sdk):
        """Review cycle 6/7, P1 — the seam, on EVERY channel `_emit` accepts.

        The `EventRecorded` half is guarded at both this door and the `_emit`
        chokepoint, and the chokepoint sweeps the kwargs payload AND the
        positional record — because `_norm` splices the record OVER the envelope
        (`{**ev, **ev["point"]}`), so the map the fold reads is neither channel
        alone. The state-op half was kwargs-only in a first revision, and a
        positional record stored a terminal `:Event` status (measured cycle 7),
        so each channel and the split variants are pinned here.
        """
        payload = {"label": "Event", "op": "restatus",
                   "state": {"status": "superseded"}}
        shapes = {
            "kwargs": ((), dict(payload, id=None)),
            "positional": ((dict(payload),), {}),
            "label-in-record": (({"label": "Event", "op": "restatus"},),
                                {"state": {"status": "superseded"}}),
            "state-in-record": (({"state": {"status": "superseded"}},),
                                {"label": "Event", "op": "restatus"}),
        }
        for name, (args, kwargs) in shapes.items():
            eid = _event(sdk, name=f"BeliefChokepoint-{name}")
            before = _props(sdk, eid)
            kwargs = dict(kwargs)
            if "id" in kwargs:
                kwargs["id"] = eid
            else:
                args = tuple((dict(args[0], id=eid) if args else None,)) + args[1:] \
                    if args else args
            with pytest.raises(ValueError, match=":Event"):
                self._api(sdk)._emit("EntityMutated", *args, **kwargs)
            assert _props(sdk, eid) == before, name

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

    def test_the_type_is_resolved_the_way_its_writer_resolves_it(self, sdk):
        """Review cycle 8/9 — CONTENT, in both directions, since that is the rule.

        `ingest` and `create_entity` route with `(type or "").strip().lower()`, and
        `str.strip` builds a PLAIN `str` from a subclass's CONTENT — no `__str__`
        is consulted anywhere on that path. So this arm must read the content too,
        and asking the UNION of renderers here would refuse an item the writer
        files as an `:Object` (an over-block with a wrong diagnosis: measured,
        review cycle 9 P2, where `content="object", __str__="event"` got a
        violation and the same item landed via `create_entity`). The LABEL is the
        two-renderer name — the API arms are right to ask the union there — but
        the TYPE is a content string, and one rule per side is what keeps this arm
        from disagreeing with the door it mirrors.
        """
        class Lie(str):
            def __str__(self):
                return "harmless"

        class Reverse(str):
            def __str__(self):
                return "event"

        terminal_event = {
            "points": [{"content": "must-not-commit"}],
            "entities": [{"type": Lie("event"), "name": "ContentEvent",
                          "eventKind": "deployment", "status": "superseded"}],
        }
        assert sdk._validate_bundle(terminal_event), (
            "content='event' passed Phase 1 — step 3 routes it to the :Event door")

        object_by_content = {
            "points": [{"content": "must-not-commit"}],
            "entities": [{"type": Lie("object"), "name": "ContentObject",
                          "objectKind": "policy", "status": "superseded"}],
        }
        assert sdk._validate_bundle(object_by_content) == [], (
            "Phase 1 refused an :Object the writer files by content")

        renders_as_event = {
            "points": [{"content": "must-not-commit"}],
            "entities": [{"type": Reverse("object"), "name": "RenderOnly",
                          "objectKind": "policy", "status": "superseded"}],
        }
        assert sdk._validate_bundle(renders_as_event) == [], (
            "Phase 1 refused an :Object because of a __str__ the writer never calls")

    def test_the_writer_resolves_the_type_by_content_too(self, sdk):
        """Review cycle 12, P1 — the WRITER's own read called the subclass's `strip`.

        `_item_type_text` fixed the arms; `ingest` step 3 still ran
        `(item.pop("type") or "").strip().lower()`, a virtual dispatch. So
        `class LieStrip(str): def strip(self): return "event"` with content
        `object` was an `:Object` to Phase 1 and an `:Event` to the writer:
        measured, `_validate_bundle` returned `[]` and `ingest` raised with one
        point ALREADY committed — the half-commit this issue closes, reached
        through the one read the round did not convert. Both directions.
        """
        class LieStrip(str):
            def strip(self, *a, **k):
                return "event"

        class LieStripReverse(str):
            def strip(self, *a, **k):
                return "object"

        before = sdk._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0]
        bundle = {"points": [{"content": "writer-strip"}],
                  "entities": [{"type": LieStrip("object"), "name": "StripObj",
                                "objectKind": "policy",
                                "status": "superseded"}]}
        assert sdk._validate_bundle(bundle) == [], (
            "Phase 1 and the writer must read the same content")
        sdk.ingest(bundle)
        after = sdk._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert after == before + 1, "the :Object ingest did not land"

        refused = {"points": [{"content": "must-not-commit"}],
                   "entities": [{"type": LieStripReverse("event"),
                                 "name": "StripEvt",
                                 "eventKind": "deployment",
                                 "status": "superseded"}]}
        assert sdk._validate_bundle(refused), (
            "content 'event' with a `strip()` that says 'object' passed Phase 1 "
            "- T5 says a terminal status on an :Event is not writable")

    def test_every_phase_1_arm_resolves_the_type_by_content(self, sdk):
        """Review cycle 10, P1 — the rule is per-ARM, and four arms still used `str()`.

        The Event arm above was fixed first and the other arms kept comparing
        `str(item.get("type") or "")`, i.e. the `__str__` RENDERING. Measured: a
        `:Subject` item whose content is `subject` and whose `__str__` is
        `harmless` passed Phase 1, and `ingest` then raised in step 3 with one
        point ALREADY committed — the #2422/#7925 partial-mutation class this
        arm exists to prevent, reached through a different label. Each tuple is
        (item, why the writer treats it as an item of that label).
        """
        class Lie(str):
            def __str__(self):
                return "harmless"

        cases = (
            ({"type": Lie("subject"), "name": "LieSubject",
              "subjectKind": "organization", "status": "dead"},
             "the Subject vocabulary arm: content 'subject', renders 'harmless'"),
            ({"type": Lie("object"), "name": "LieObject",
              "objectKind": "policy", "status": "superseded"},
             "the Object/Event mint arm: content 'object', renders 'harmless'"),
        )
        # ⛔ The DEPTH the arm reads the status at is decided by the SAME
        # resolution (`_item_flatten_depth`), so a rendering there re-opened the
        # defect one level down: measured, `props.props.status='dead'` under a
        # `Lie("subject")` returned ZERO violations while the writer folded two
        # levels and stored the value — the same half-commit, one key deeper.
        deep = {"points": [{"content": "must-not-commit"}],
                "entities": [{"type": Lie("subject"), "name": "DeepLie",
                              "subjectKind": "organization",
                              "props": {"props": {"status": "dead"}}}]}
        assert sdk._validate_bundle(deep), (
            "a nested `props.props.status` was read at the wrong DEPTH — "
            "Phase 1 passed and step 3 would raise with the point committed")

        for item, why in cases:
            bundle = {"points": [{"content": "must-not-commit"}],
                      "entities": [item]}
            violations = sdk._validate_bundle(bundle)
            # `:Object` has a lifecycle (T5) so a terminal status is legal for it;
            # what must hold for BOTH is that the arm SAW the item at all — a
            # `__str__`-only read skips it silently, and that silence is the bug.
            if str.__str__(item["type"]) == "subject":
                assert violations, (
                    f"{why}: the item passed Phase 1 and step 3 would then raise "
                    "with the points already committed")
            else:
                assert violations == [], (
                    f"{why}: Phase 1 refused an :Object the writer files by content")

    def test_create_entity_resolves_the_type_by_content_too(self, sdk):
        """The DOOR argument, not just the bundle item (`create_entity(type=…)`).

        Review cycle 13 measured this read unpinned: reverting `t =
        _type_text(type)` to the bound `(type or "").strip().lower()` left 290
        tests green while `create_entity(LieStrip("object"), …, status=
        "superseded")` flipped from an accepted `:Object` (T5: it HAS a
        lifecycle) to a refusal, because the subclass's `strip()` said `event`.
        Both directions are asserted, and the node's label is, so a pass cannot
        come from the write merely being refused.
        """
        class LieStrip(str):
            def strip(self, *a, **k):
                return "event"

        node = sdk.create_entity(LieStrip("object"), "StripCreated",
                                 objectKind="policy",
                                 status="superseded")["node"]
        labels = sdk._get_proj().g.query(
            "MATCH (n {id:$id}) RETURN labels(n)",
            params={"id": node["id"]}).result_set[0][0]
        assert "Object" in labels and "Event" not in labels, (
            f"the door argument was routed by a `strip()` override: {labels}")

    def test_the_endpoint_arms_resolve_the_type_by_content(self, sdk):
        """Review cycle 12, P2 — the arm with NO pin was the endpoint arm.

        Reverting all three `_check_endpoints` reads to `str(item.get("type")
        or "").strip().lower()` left the suite green (measured), because nothing
        exercised an endpoint whose item type RENDERS differently from its
        content. One direction is pinned here — a lie RENDERER must not cause a
        false "must be a ... endpoint" refusal, which is the failure this arm
        produced. The `strip()` direction is NOT observable at this arm (measured:
        identical violations with and without the override), and the writer-side
        rule is pinned by `test_the_writer_resolves_the_type_by_content_too`.
        """
        class Lie(str):
            def __str__(self):
                return "harmless"

        class LieStrip(str):
            def strip(self, *a, **k):
                return "event"

        bundle = {
            "points": [{"ref": "p1", "content": "endpoint claim"}],
            "entities": [{"ref": "s1", "type": Lie("subject"),
                          "name": "EndpointSubject",
                          "subjectKind": "organization"}],
            "connections": [{"from": "s1", "to": "p1",
                             "operator": "IMPL"}],
        }
        assert sdk._validate_bundle(bundle) == [], (
            "the endpoint arm refused a Subject endpoint over a __str__ the "
            "writer never calls")

        # ⛔ No second half here on purpose: a `strip()`-override endpoint case was
        # tried and MEASURED inert — the violation it produced is the `:Subject`
        # vocabulary arm's and a plain-`str` control produced the identical one, so
        # it pinned a different arm (review cycle 13). The writer-side `strip()` is
        # pinned by `test_the_writer_resolves_the_type_by_content_too`.

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

    @pytest.mark.parametrize("status", [{"a": 1}, {"a": {"b": []}}])
    def test_a_non_persistable_status_is_accepted_and_dropped(self, sdk, status):
        """The other half of the door's rule (#7925): it is never stored.

        ⛔ SCOPE — a MAP, and this is the cross-lane merge's decision, not a
        narrowing for convenience. `{1, 2}` and `b"raw"` are non-persistable
        too, so under the FOLD's filter alone they belonged in this list; but the
        journal cannot JSON-encode them, and accepting them made the record
        append raise a bare `TypeError` (and, at `emit_belief`, drop the record
        silently). `_status_is_discarded_by_every_writer` therefore asks all
        THREE writers, and those two values are violations — pinned next.
        """
        assert sdk._validate_bundle({
            "entities": [{"ref": "e1", "type": "event", "name": "Drop",
                          "eventKind": "deployment", "status": status}],
        }) == []

    @pytest.mark.parametrize("status", [{1, 2}, b"raw", bytearray(b"raw")])
    def test_a_status_no_journal_can_encode_is_a_phase_1_violation(
            self, sdk, status):
        """Non-persistable is not enough — the value must survive ALL writers.

        Measured (review cycle 3, P2): with only the fold's filter asked, these
        passed validation and then raised a bare `TypeError: Object of type
        bytes is not JSON serializable` inside the record append, with ingest's
        earlier steps already committed. Phase 1 mirrors the refusal instead, so
        the caller gets a diagnosis before anything is written.
        """
        violations = sdk._validate_bundle({
            "entities": [{"ref": "e1", "type": "event", "name": "Unencodable",
                          "eventKind": "deployment", "status": status}],
        })
        assert violations, f"status={status!r} passed Phase 1"
        assert any("STRING" in v["message"] for v in violations)

    def test_phase_1_refuses_an_aliased_key_the_door_would_refuse(self, sdk):
        """Review cycle 6, P1 — the Phase-1 readers hashed the literal name.

        `_item_prop_at_depth` read `if key in cur`, so a rendered-alias
        `status`/`outdated` key passed Phase 1 while the write door refused it:
        Phase 1 said clean, `ingest` committed the earlier items, then step 3
        raised — the partial-mutation class the arm exists to close. Both the
        alias-vs-nested-prop precedence and the `outdated` pair are pinned here.
        """

        class _KeyStat(str):
            def __str__(self):
                return "status"

        class _KeyOutdated(str):
            def __str__(self):
                return "outdated"

        base = {"ref": "e1", "type": "event", "name": "A",
                "eventKind": "deployment"}
        # (0) the SAME key at both levels: `_coerce_props` fills absent keys from
        # the nested map, so the TOP-LEVEL value wins — a top terminal is a
        # violation, and a top non-terminal shadows the nested terminal exactly as
        # the writer shadows it.
        assert sdk._validate_bundle({"entities": [
            {**base, "status": "superseded", "props": {"status": "live"}},
        ]}), "the door refuses the top-level terminal status; Phase 1 must too"
        assert sdk._validate_bundle({"entities": [
            {**base, "status": "live", "props": {"status": "superseded"}},
        ]}) == [], "the writer keeps the top-level `live`; Phase 1 must not over-reject"
        # (a) an aliased `status: live` plus a nested terminal status: the door
        # merges both and refuses, so Phase 1 must too.
        assert sdk._validate_bundle({"entities": [
            {**base, _KeyStat("x"): "live", "props": {"status": "superseded"}},
        ]}), "the door refuses this bundle; Phase 1 must not call it clean"
        # (b) the aliased server-managed flag.
        assert sdk._validate_bundle({"entities": [
            {**base, _KeyOutdated("x"): True},
        ]}), "the entity write refuses the flag; Phase 1 must too"

    def test_an_unhashable_state_op_does_not_escape_as_a_type_error(self, sdk):
        """Review cycle 6, P3 — `op` was tested for set membership untyped.

        An unhashable `op` raised a bare `TypeError: unhashable type` out of
        `emit_belief` — the un-named 500-class the eager refusal exists to
        replace with a diagnosis. The value is outside this PR's surface (it is
        the `op` field, not `status`), so the pin is only that it no longer
        escapes as a `TypeError`.
        """
        eid = _event(sdk, name="BadOp")
        _log = EventLog(os.path.join(sdk._test_root, "badop.jsonl"))
        api = EventAPI(_log, initiated_by="user", projection=sdk._get_proj())
        try:
            api.emit_belief("EntityMutated", id=eid, label="Event",
                            op=["restatus"], state={"status": "superseded"})
        except TypeError as exc:  # pragma: no cover - the regression
            pytest.fail(f"bare TypeError escaped the state-op gate: {exc}")
        except ValueError:
            pass  # a named refusal is fine
        assert "status" not in _props(sdk, eid)

    def test_phase_1_reads_the_value_and_the_key_the_way_the_door_does(self, sdk):
        """Review cycle 5, P1/P2 — the arm must give the DOOR's answer.

        It read the literal key and tested the raw value, so a ``str`` SUBCLASS
        value (or a subclass/bytes key naming ``status``) produced ZERO
        violations while the door refused it: Phase 1 said clean, ``ingest``
        committed the point, and step 3 raised — the half-commit this arm exists
        to prevent (measured: `phase1: []` then ``ValueError``, points 0 -> 1).
        And ``status: None`` is the DOCUMENTED clear route the door ACCEPTS, so
        flagging it was this file's own over-rejection class (#7925).
        """

        class _HashLiar(str):
            def __hash__(self):
                return 0

        base = {"ref": "e1", "type": "event", "name": "A",
                "eventKind": "deployment"}
        for label, item in (
                ("subclass value", {**base, "status": _HashLiar("superseded")}),
                ("subclass key", {**base, _HashLiar("status"): "superseded"}),
                ("bytes key", {**base, b"status": "superseded"}),
        ):
            violations = sdk._validate_bundle({"entities": [item]})
            assert violations, f"{label} passed Phase 1 but the door refuses it"
        assert sdk._validate_bundle({
            "entities": [{**base, "status": None}],
        }) == [], "`status: None` is the documented clear route; the door accepts it"

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

    def test_the_violation_names_the_TERMINAL_declaration_when_a_legal_one_masks_it(
            self, sdk):
        """Conflict-sweep lane F, P3 — the message rendered `_status_values[-1]`.

        The arm tests EVERY declaration for terminality (an `any`), because the
        writer applies every key of the map and two keys can both render to
        `status` — but it rendered the LAST value. When the last declaration is a
        LEGAL one, the message indicted that legal value and then advised the
        caller to pass it, while the offending terminal declaration never appeared
        — the one datum the repair needs. Measured before the fix, with an item
        declaring `superseded` and, via the liar key below, `live`: "declares
        status 'live' for an :Event … use a non-terminal value".
        """
        class SaysStatus(str):
            """Content is distinct; only `str()` renders to `status`.

            Two DISTINCT dict keys, both filed under `status` by one renderer or
            the other — the shape the eager `_event_state_key_names` union exists
            to catch.
            """

            def __str__(self):
                return "status"

        item = {"ref": "evt", "type": "event", "name": "Launch",
                "eventKind": "sessionCaptured", "status": "superseded"}
        item[SaysStatus("harmless")] = "live"    # the MASKING, legal value
        bundle = {"points": [{"ref": "pX", "kind": "statement",
                               "content": "must-not-commit"}],
                  "entities": [item]}
        msgs = [v["message"] for v in sdk._validate_bundle(bundle)]
        assert msgs, "the masked-terminal item passed Phase 1 entirely"
        event_msg = [m for m in msgs if "for an :Event" in m]
        assert event_msg, msgs
        assert any("'superseded'" in m for m in event_msg), (
            "the message must NAME the terminal declaration that failed",
            event_msg)


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
        """`#1423`'s `:Subject` vocabulary is its own arm and is untouched."""
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
