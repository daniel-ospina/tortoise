"""#2788 — the Event layer carries dated facts, not current state.

`_EVENT_HANDLED` (`tortoise/projection/entities.py`) omits `name` /
`eventStatus` / `createdAt`, so `_persist_extra_props` carries them onto the
Event node — a PROP lane. Nothing folds them back into Event state, but the
omission reads like a state surface, so this file pins the distinction the
issue asks for:

  * the typed commit path cannot populate the lane at all — `CommitEvent` is
    `extra="forbid"`, so none of the three is a supported commit field; and
  * the lane is not a state carrier — the three keys sit outside the
    consistency gate's belief/status comparison, and the Object/Subject
    `status` state column has no Event analogue.

ONTOLOGY §4.5 states the invariant in prose ("an Event is a dated fact — it
carries no current state"); this file is its executable half.

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
        """``status`` is the Object/Subject state column; an Event has none —
        it is neither a projection-handled Event field nor a belief/state
        field the consistency gate compares."""
        assert "status" not in _EntityHandlers._EVENT_HANDLED
        assert "status" not in _ENTITY_BELIEF_FIELDS
        assert "eventStatus" not in _ENTITY_BELIEF_FIELDS

    def test_belief_surface_is_the_declared_pair(self):
        """Anchor the previous assertion to the real surface, so trimming the
        belief props cannot silently make it vacuous."""
        assert set(_ENTITY_BELIEF_FIELDS) == (
            set(BELIEF_PROPS) | set(BELIEF_BOOL_PROPS)
        )

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
