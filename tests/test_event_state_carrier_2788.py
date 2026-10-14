"""#2788 — the Event layer carries dated facts, not current state.

`_EVENT_HANDLED` (`tortoise/projection/entities.py`) omits `name` /
`eventStatus` / `createdAt`, so `_persist_extra_props` carries them onto the
Event node — a PROP lane. Nothing folds them back into Event state, but the
omission reads like a state surface, so this file pins the distinction the
issue asks for:

  * the typed commit path cannot populate the lane at all — `CommitEvent` is
    `extra="forbid"`, so none of the three is a supported commit field; and
  * the lane is not a projection-owned state surface — the three keys sit
    outside the consistency gate's comparison, and the projection declares no
    Event `status` column (the gate compares `status` only for the
    Object/Subject families). A writer-supplied `status` prop is **not inert**:
    `_promote_incident_operators` (`tortoise/sdk.py`, #7853) reads an Event
    endpoint's `status` as a live/draft gate, so the declared absence is not a
    claim that an Event's `status` is unread (pinned by
    `tests/test_7853_confidence_label_propagation.py`).

ONTOLOGY §4.5 states the invariant in prose ("an Event is a dated fact — it
carries no projection-owned state"); this file is its executable half.

Runnable (URI-less embedded):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_event_state_carrier_2788.py -q
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pydantic import ValidationError

from tortoise.commit_schema import CommitEvent
from tortoise.consistency import _ENTITY_BELIEF_FIELDS
from tortoise.projection.entities import (
    BELIEF_BOOL_PROPS,
    BELIEF_PROPS,
    _EntityHandlers,
)
from tortoise.sdk import TortoiseSDK

#: the three keys the projection's extras lane carries onto an Event node.
_PROP_LANE_KEYS = ("name", "eventStatus", "createdAt")


def _valid_commit_event(**overrides) -> CommitEvent:
    base = dict(
        id="ev_" + "a" * 32,
        eventKind="meeting",
        content="a dated occurrence",
        source_ref="src_1",
    )
    base.update(overrides)
    return CommitEvent(**base)


class TestCommitPathForbidsTheLane:
    """The typed commit path is the canonical Layer-1 contract. It cannot set
    any of the three (``extra="forbid"``), so the lane is reachable only from
    the untyped in-tree SDK/indexer writers — never from a commit client."""

    @pytest.mark.parametrize("key", _PROP_LANE_KEYS)
    def test_extra_forbid_rejects_the_key(self, key):
        with pytest.raises(ValidationError) as exc:
            _valid_commit_event(**{key: "completed"})
        assert exc.value.errors()[0]["type"] == "extra_forbidden"

    def test_the_declared_fields_still_validate(self):
        """Control: the base payload validates, so the rejects above are the
        ``extra="forbid"`` rule and not a malformed fixture."""
        assert _valid_commit_event().eventKind == "meeting"


class TestTheLaneIsNotAStateCarrier:
    def test_event_handled_omits_the_prop_lane_keys(self):
        """The omission IS the mechanism: ``_persist_extra_props`` skips
        ``_META_KEYS | _EVENT_HANDLED``, so a key absent here is carried onto
        the node."""
        for key in _PROP_LANE_KEYS:
            assert key not in _EntityHandlers._EVENT_HANDLED

    def test_no_event_state_column_exists(self):
        """The projection declares no Event ``status`` column: ``status`` is
        not projection-handled (``_EVENT_HANDLED``) and is not a belief field
        the consistency gate compares. It is absent from ``_EVENT_HANDLED``
        for the same reason the three ``_PROP_LANE_KEYS`` are — the extras
        lane carries it as a prop — so this is a statement about the DECLARED
        surface, never a claim that an Event's ``status`` is inert. It is not:
        ``_promote_incident_operators`` (``tortoise/sdk.py``, #7853) reads an
        Event endpoint's ``status`` as a live/draft gate, pinned by
        ``tests/test_7853_confidence_label_propagation.py::
        test_promote_incident_operators_gates_on_non_point_endpoint_status``.
        """
        assert "status" not in _EntityHandlers._EVENT_HANDLED
        assert "status" not in _ENTITY_BELIEF_FIELDS
        assert "eventStatus" not in _ENTITY_BELIEF_FIELDS

    def test_belief_surface_is_the_declared_pair(self):
        """Pin the fold's belief surface to its literal membership and to the
        two registries it is built from, so adding or trimming a belief prop
        moves an expectation instead of both sides of a set identity.

        (``set(_ENTITY_BELIEF_FIELDS) == set(BELIEF_PROPS) |
        set(BELIEF_BOOL_PROPS)`` is deliberately NOT asserted: the fold is
        DEFINED as that concatenation, so it holds for every input and can
        never fail. Pinning the literal names is the non-vacuous check.)
        """
        assert set(_ENTITY_BELIEF_FIELDS) == {
            "confidence",
            "posterior_alpha",
            "posterior_beta",
            "lastDreamedAt",
            "outdated",
        }
        # The surface must still be the two declared registries: disjoint (a
        # key in both would be compared twice) and duplicate-free.
        assert set(BELIEF_PROPS).isdisjoint(BELIEF_BOOL_PROPS)
        assert len(_ENTITY_BELIEF_FIELDS) == len(set(_ENTITY_BELIEF_FIELDS))

    def test_event_status_lands_as_a_prop_and_no_state_is_folded(self):
        """Behavioural half: the lane IS reachable (an SDK-created Event node
        carries ``eventStatus``) and still yields no Event state — the node
        gets no ``status`` column, so ``create_event``'s
        ``eventStatus="scheduled"`` is an ingest marker, not a state read."""
        db = os.path.join(tempfile.mkdtemp(prefix="tortoise_2788_"), "t.db")
        sdk = TortoiseSDK(db)
        try:
            ev = sdk.create_event("Board review", "meeting")
            eid = ev.get("eventId") or ev.get("id")
            props = sdk._get_proj().g.query(
                "MATCH (e:Event {eventId:$e}) RETURN properties(e)",
                params={"e": eid},
            ).result_set[0][0]
            assert props.get("eventStatus") == "scheduled"
            assert "status" not in props
        finally:
            sdk.close()
            shutil.rmtree(os.path.dirname(db), ignore_errors=True)

    def test_a_writer_supplied_status_lands_as_a_prop(self):
        """The declared surface has no Event ``status`` column, yet the extras
        lane still carries a writer-supplied ``status`` onto the node — which
        is why the projection-declared absence must not be read as "an Event's
        ``status`` is inert". ``_promote_incident_operators`` reads exactly
        this prop as a live/draft gate (#7853).
        """
        db = os.path.join(tempfile.mkdtemp(prefix="tortoise_2788_"), "t.db")
        sdk = TortoiseSDK(db)
        try:
            created = sdk.create_entity(
                "event", "StatusCarrier", eventKind="meeting", status="draft")
            node = created["node"]
            eid = node.get("eventId") or node.get("id")
            props = sdk._get_proj().g.query(
                "MATCH (e:Event {eventId:$e}) RETURN properties(e)",
                params={"e": eid},
            ).result_set[0][0]
            assert props.get("status") == "draft"
        finally:
            sdk.close()
            shutil.rmtree(os.path.dirname(db), ignore_errors=True)
