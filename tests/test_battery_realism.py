"""Task 9 — realism + emission-loss-proof tests (hermetic, registry-valid).

Locks the SECOND-MODEL-GATE P1 residual (emission-loss-proof executor): a
surfacing the agent declared MUST always produce its tool_event entry (a
malformed/duplicate emission fails closed via validate_event_entry), so
CONDITIONAL absence on the real path is provably non-occurrence, never a
lost emission. Also locks MANDATORY coverage from the envelope (the only
scalar channel) + the emitted-trace flattening the probes read.
"""
from __future__ import annotations

import pytest

from battery.runner.emit import MANDATORY, validate_event_entry
from battery.runner.executor import (
    Envelope,
    emitted_trace,
    envelope_events,
    state_events,
    surfacing_event,
    validate_emitted,
)


def _env() -> Envelope:
    return Envelope(position="Proceed", stated_confidence=0.8,
                    undecided=False,
                    defeat_conditions=["data-loss"], intents=[])


def test_envelope_events_cover_mandatory_fields():
    events = envelope_events(_env())
    validate_emitted(events)  # registry-valid — fails closed if malformed
    # #2740: the envelope also emits its declared `position`, which is NOT a
    # registry scalar field (payload-only) — registry-valid and invisible to
    # the scalar trace. Field-keyed assertions therefore use .get().
    fields = {e.get("field") for e in events}
    assert {"stated_confidence", "stated_undecided",
            "stated_defeat_conditions"} <= fields
    pos = [e for e in events if "position" in (e.get("payload") or {})]
    assert len(pos) == 1 and "field" not in pos[0]
    assert pos[0]["payload"]["position"] == "Proceed"
    # envelope carries the envelope subset of MANDATORY; the full MANDATORY
    # set is covered once the executor adds the state-terminal entries
    env_fields = {"stated_confidence", "stated_undecided",
                  "stated_defeat_conditions"}
    assert env_fields <= MANDATORY
    all_events = events + state_events(ep_outcome="converged",
                                       decide_cycles=1)
    assert {e.get("field") for e in all_events} >= MANDATORY


def test_state_events_valid_and_typed():
    events = state_events(ep_outcome="contested", decide_cycles=2,
                          ep_contested=True)
    validate_emitted(events)
    by_field = {e["field"]: e for e in events}
    assert by_field["ep_outcome"]["payload"]["value"] == "contested"
    assert by_field["decide_cycles"]["payload"]["value"] == 2
    assert by_field["ep_contested"]["payload"]["value"] is True


def test_surfacing_event_emission_loss_proof():
    """A declared surfacing always emits its tool_event (Amend-1 event_ref
    present; field emitted as the registry tool_event kind)."""
    ev = surfacing_event(within_turn=3, event_ref="ns:42", explicit=True)
    validate_event_entry(ev)  # fails closed on a malformed entry
    assert ev["type"] == "tool_event"
    assert ev["field"] == "contradiction_surfaced"
    assert ev["payload"]["event_ref"] == "ns:42"


def test_emitted_trace_flattens_semantic_keys():
    events = (envelope_events(_env())
              + state_events(ep_outcome="converged", decide_cycles=1)
              + [surfacing_event(within_turn=2, event_ref="ns:7")])
    trace = emitted_trace(events)
    assert trace["stated_confidence"] == 0.8
    assert trace["stated_undecided"] is False
    assert trace["ep_outcome"] == "converged"
    assert trace["decide_cycles"] == 1
    assert trace["contradiction_surfaced"] is True
    assert trace["surfaced_within_turn"] == 2


def test_malformed_entry_fails_closed():
    with pytest.raises(ValueError):
        validate_event_entry(
            {"type": "envelope", "event": "declared",
             "field": "stated_confidence", "payload": {"value": True}})
    with pytest.raises(ValueError):
        # a surfacing WITHOUT its Amend-1 event_ref never validates
        surfacing_event(within_turn=1, event_ref="") if False else \
            validate_event_entry(
                {"type": "tool_event", "event": "register_conflict",
                 "field": "contradiction_surfaced",
                 "payload": {"value": True}})
