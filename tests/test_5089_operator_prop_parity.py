"""#5089 (epic, durability) — the journal must not invent an operator's content.

Root: ``derived = replay(journal)``. The epic's DoD bullet 2 asks that a replay
reproduce the graph identically, and ``#5048`` is its implementation. The
capstone run on ``origin/main`` found a divergence ``#7882`` (the
``mitigated_by`` edge) did not cover — rebuilding an SDK-created operator went

    live     {direction, id, is_operator, op_type}
    rebuilt  {… the same …, content: 'IMPL(src, claim)', status: 'live'}

The two halves are NOT the same defect, and this file pins both facts — one
fixed, one a door question:

**``content`` — FIXED, in the EMITTER.** ``sdk._emit_event`` SYNTHESIZED the
``IMPL(inputs)`` label into the record's payload under a comment asserting
"``_upsert_point_props`` requires it". No writer had ever stored that label on
the node (operators store no ``content``; the display label is derived at the
point of display, ``api._point``, and nothing reads it back off the node), so
the record asserted a value the graph never held and the replay persisted it.
Removing the synthesis is the fix; the record now says what the live SDK door
actually produced — an omitted key.

The RESIDUAL asymmetry is deliberate and NOT fixed here: the replay writer sets
``content`` unconditionally (``n.content=$content`` with ``p.get("content", "")``),
so a rebuilt operator carries ``''`` while a live one has no property at all.
That one-sided case is a modelled, REPORTED, fingerprint-excluded asymmetry
(``consistency._OPERATOR_ABSENT_PROPS``, with ``_WRITER_DEFAULTS["content"]``),
documented before #5089. Making the writer *preserve* absence instead was tried
and REVERTED: ``_upsert_point_props`` is also the promotion writer, and there an
omitted key means RESET, not "unset" — the change resurrected a stale content on
a promotion snapshot and made ``check_consistency`` report a divergence on a
correctly-replayed graph
(``test_consistency_divergence_5011.py::test_a_promotion_snapshot_without_the_operator_key_resets_operator_ness``,
measured). The two intents need a per-record distinction, not one clause.

**``status`` — A DOOR QUESTION, not fixed here.** Its
``coalesce($st, n.status, 'live')`` default is LOAD-BEARING, not cosmetic:
reads select on it (``projection/grounding.py`` matches ``{status: 'live'}``),
so it decides whether a point participates in belief propagation. Dropping it
for operator records removed the point from the grounding result
(``test_projection.py::test_compute_grounding_basic`` reds — measured, and the
reason that half was reverted). The divergence is real in the other direction
too: NEITHER live door writes ``'live'`` (the SDK's ``create_operator`` writes no
status at all; ``api._point`` writes ``'draft'``), so no LIVE door's operator is
a grounding participant while every REBUILT one is. Which side is canonical is a
decision reported on #5089/#5091; the last test pins the measured state so the
ruling has a red-to-green target.

MUTATIONS THAT MUST RED:
- restore the ``sdk._emit_event`` content synthesis → the record carries the
  label again and the rebuilt node does too
  (``test_the_record_carries_no_synthesized_label``);
- make the emitter synthesize only when the nested operator key has inputs →
  ``test_the_record_carries_no_synthesized_label`` reds on the single-input
  operator too (the synthesis keyed on ``"content" not in clean``, not on
  arity);
- set ``_WRITER_DEFAULTS["content"]`` to the synthesized label → the rebuilt
  content assertion reds (it pins the writer's real default, ``''``).
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from tortoise.api import EventAPI, provenance
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK


def _sdk(tmp_path):
    ns = f"test_5089_{uuid.uuid4().hex[:8]}"
    events = tmp_path / "events"
    events.mkdir()
    if os.environ.get("TORTOISE_DB_URI"):
        sdk = TortoiseSDK(db_path=None, namespace=ns,
                          event_log_path=str(events / "events.jsonl"))
    else:
        sdk = TortoiseSDK(db_path=str(tmp_path / "t5089.db"), namespace=ns,
                          event_log_path=str(events / "events.jsonl"))
    return sdk, events


def _props(sdk, pid) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    return dict(rows[0][0]) if rows else {}


def _operator_records(events) -> list[dict]:
    out = []
    with open(events / "events.jsonl") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "OperatorAdded":
                out.append(rec["point"])
    return out


def _rebuild(sdk, events):
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)


@pytest.fixture()
def sdk_and_events(tmp_path):
    sdk, events = _sdk(tmp_path)
    try:
        yield sdk, events
    finally:
        sdk.close()


def _sdk_operator(sdk):
    """An operator created by the SDK door: no content, no status, live."""
    src = sdk.create_point("statement", "T0 source", status="live",
                           dedup=False)["id"]
    claim = sdk.create_point("statement", "downstream claim", status="live",
                             dedup=False)["id"]
    return sdk.create_operator("IMPL", src, [claim])["id"]


def _eventapi_operator(sdk, events):
    proj = sdk._get_proj()
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=proj)
    prov = provenance("d.txt", [0, 5], "quote", extracted_by="e@0")
    a = api.add_point("ea", prov)
    b = api.add_point("eb", prov)
    return api.add_operator("IMPL", [a, b], prov), a, b


def test_the_record_carries_no_synthesized_label(sdk_and_events):
    """The journal must state what a writer stored — not a display label."""
    sdk, events = sdk_and_events
    op = _sdk_operator(sdk)
    assert "content" not in _props(sdk, op), (
        "the SDK operator door stores no content; this pins the shape the "
        "record is supposed to describe")

    records = _operator_records(events)
    assert records, "the operator must be journaled"
    assert "content" not in records[-1], (
        "the record carries a SYNTHESIZED label that no writer stored — the "
        f"replay then persists it, got {records[-1].get('content')!r}")


def test_the_rebuilt_operator_has_no_fabricated_label(sdk_and_events):
    sdk, events = sdk_and_events
    op = _sdk_operator(sdk)

    _rebuild(sdk, events)
    rebuilt = _props(sdk, op)

    assert rebuilt.get("content", "") == "", (
        "a rebuilt operator carries a label live never had: "
        f"content={rebuilt.get('content')!r}")
    assert "IMPL(" not in str(rebuilt.get("content", "")), (
        "the removed synthesis is what produced an `IMPL(...)` string here")


def test_the_replay_writer_default_is_the_empty_string_not_a_label(
        sdk_and_events):
    """The residual one-sided asymmetry is the documented `_WRITER_DEFAULTS`."""
    from tortoise.consistency import _WRITER_DEFAULTS

    assert _WRITER_DEFAULTS.get("content") == "", (
        "the consistency model pins the writer's content default; if the "
        "writer's semantics change, this model must change with it")


def test_the_other_live_door_still_keeps_its_label(sdk_and_events):
    """Non-vacuity: the EventAPI door DOES store a label and a draft status."""
    sdk, events = sdk_and_events
    op, a, b = _eventapi_operator(sdk, events)

    live = _props(sdk, op)
    assert live.get("content") == f"IMPL({a}, {b})"

    _rebuild(sdk, events)
    rebuilt = _props(sdk, op)
    assert rebuilt.get("content") == live.get("content"), (
        "a label a writer really stored must survive the replay")
    assert rebuilt.get("status") == live.get("status") == "draft"


def test_non_operator_points_keep_the_status_chain(sdk_and_events):
    sdk, events = sdk_and_events
    # #1088 (owner ruling): live is the default for anything added to the graph;
    # draft is explicit. Pin the default, then pin the case with real protective
    # power — a no-status point lands 'live', which is ALSO the replay writer's
    # own `coalesce($st, n.status, 'live')` fallback, so only an EXPLICIT draft
    # can catch that fallback silently promoting a point on replay.
    pid = sdk.create_point("statement", "no status arg", dedup=False)["id"]
    assert _props(sdk, pid).get("status") == "live", (
        "#1088: a point created with no explicit status lands live")

    draft = sdk.create_point("statement", "explicit draft", status="draft",
                             dedup=False)["id"]
    assert _props(sdk, draft).get("status") == "draft"

    _rebuild(sdk, events)
    assert _props(sdk, pid).get("status") == "live"
    assert _props(sdk, draft).get("status") == "draft", (
        "a 'live' fallback was reintroduced: the replay promoted an explicit "
        "draft instead of preserving the status the writer stored")


def test_check_consistency_agrees_after_rebuilding_an_operator(sdk_and_events):
    """The checker and the writer must still agree — the #5011 machinery."""
    from tortoise.consistency import check_consistency

    sdk, events = sdk_and_events
    _sdk_operator(sdk)
    _rebuild(sdk, events)

    r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert r["ok"] is True, r.get("divergent_points")


def test_operator_status_divergence_is_a_door_question(sdk_and_events):
    """PINNED DIVERGENCE (#5089/#5091) — not fixed here, deliberately.

    Reads select on ``status`` (``grounding.py`` matches ``{status: 'live'}``),
    so this is a PARTICIPATION difference, not a prop difference: whichever side
    is canonical changes whether operators feed the belief state. Both live
    doors are measured below; if this test starts failing, a ruling landed and
    this assertion (plus the epic's DoD) should be updated with it.
    """
    sdk, events = sdk_and_events
    sdk_op = _sdk_operator(sdk)
    api_op, _, _ = _eventapi_operator(sdk, events)

    assert "status" not in _props(sdk, sdk_op), "SDK door writes no status"
    assert _props(sdk, api_op).get("status") == "draft", "EventAPI door drafts"

    def _live_participants() -> set:
        return {r[0] for r in sdk._get_proj().g.query(
            "MATCH (n:Point {status: 'live'}) RETURN n.id").result_set}

    assert sdk_op not in _live_participants(), (
        "the SDK door's operator is not a grounding participant live")

    _rebuild(sdk, events)
    assert _props(sdk, sdk_op).get("status") == "live", (
        "the replay promotes a status-less operator record to 'live'")
    assert _props(sdk, api_op).get("status") == "draft", (
        "a recorded status is not overridden by the default")
    assert sdk_op in _live_participants(), (
        "the rebuild alone made an operator a grounding participant that live "
        "excluded")
