"""#7936 under #3590 — a fold-time mention mints NOTHING, so nothing can move.

#7936's requirement was: the `:Object`/`:Subject` "name stub" an `EventRecorded`
implies must not carry a **random** `ulid()`, because a digest over the entity
view (`consistency._graph_entities` → `_graph_fingerprint` → `db_hash`) would
then be a function of WHEN the graph was built rather than of the JOURNAL — so
every legitimate `rebuild_all` would report a false `unrecorded-mutation`.
#7936 answered it by making the stub id a pure function of the name
(`ids.entity_name_id`).

**#3590 retires the stub itself.** A fold-time mint cannot be journaled, so
under an OPAQUE minted identity it cannot be replay-deterministic at all; and a
name-derived identity is precisely what made a rename mint a second entity and a
delete+re-create resurrect the first one. So the fold mints no carrier for a
bare name: it RESOLVES the name against LIVE holders
(`entity_identity.resolve_fold_name`) and otherwise leaves the link unwired,
recording the EXEMPT `unfolded-mention` shape (a mention moves no entity's
state, and the live pass and the replay pass run the same resolver). The
registration duty moved to the PRODUCER (D9) — `sdk._register_referenced_entity`
/ the SDK's entity doors.

So #7936's stability property now holds *structurally*: there is no mint to
move. The pins below assert exactly that, and they are non-vacuous in the
direction that matters — each one reds if a fold-time mint comes back.

The #1155-P1 half is preserved: whatever a mention resolves to, it never
rewrites that carrier's `id` (that is why the fold's writes are `ON CREATE`-only
where they write at all), so a connector's own id scheme is never clobbered.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tortoise.consistency import _graph_entities
from tortoise.entity_identity import (
    resolve_fold_name,
    resolve_fold_name_ids,
)
from tortoise.projection.nonfolded import (
    SHAPE_AMBIGUOUS_ENTITY_NAME,
    SHAPE_UNFOLDED_MENTION,
    collect_non_folded,
)
from tortoise.sdk import TortoiseSDK

#: A journal whose ONLY record is the event — no `ObjectRegistered` /
#: `SubjectAdded` ever registers the names, so a fold that minted would be
#: caught here and nothing else would.
_MENTION_EVENT = {
    "type": "EventRecorded",
    "id": "evt-7936-stub",
    "name": "stub matrix event",
    "eventKind": "test",
    "startedAt": "2026-01-01T00:00:00Z",
    "subject": "stub-subject",
    "object": "stub-object",
    "uses": [{"name": "stub-use", "kind": "tool"}],
}

#: The three mention sites in `_event_plain_merge`: the Subject of
#: `EventRecorded.subject`, the Object of `EventRecorded.object`, and one
#: Object per `EventRecorded.uses[]`.
_MENTION_TARGETS = (
    ("Subject", "stub-subject"),
    ("Object", "stub-object"),
    ("Object", "stub-use"),
)


def _journal(tmp_path: Path, *records: dict) -> tuple[TortoiseSDK, Path]:
    """An SDK plus a hand-written journal dir holding exactly ``records``."""
    events = tmp_path / "events"
    events.mkdir(parents=True, exist_ok=True)
    log_path = events / "events.jsonl"
    log_path.write_text("".join(json.dumps(r) + "\n" for r in records))
    sdk = TortoiseSDK(str(tmp_path / "t.db"), event_log_path=str(log_path))
    return sdk, events


def _carrier_ids(sdk: TortoiseSDK) -> dict:
    """``{(label, name): id}`` for every Object/Subject node in the graph."""
    rows = sdk._get_proj().g.query(
        "MATCH (n) WHERE n:Object OR n:Subject "
        "RETURN labels(n)[0], n.name, n.id"
    ).result_set or []
    return {(r[0], r[1]): r[2] for r in rows}


def _rebuild(sdk: TortoiseSDK, events: Path) -> None:
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)


@pytest.mark.parametrize("label,name", _MENTION_TARGETS)
def test_a_mention_alone_mints_no_carrier(tmp_path: Path, label: str, name: str):
    """The fold writes the Event and NO `:Object`/`:Subject` for its mentions.

    This is the shape that reds the moment a fold-time mint comes back — and it
    is the property that makes #7936's digest stable without a derived id.
    """
    sdk, events = _journal(tmp_path, _MENTION_EVENT)
    try:
        _rebuild(sdk, events)
        ids = _carrier_ids(sdk)
        assert (label, name) not in ids, (
            f"the fold minted a {label} carrier {ids.get((label, name))!r} for "
            f"the bare mention {name!r} — a fold-time mint cannot be "
            "journaled, so replay would mint a different id and "
            "rebuild != live by construction (#3590 D9)")
    finally:
        sdk.close()


def test_an_unresolved_mention_is_the_exempt_shape(tmp_path: Path):
    """It is RECORDED, not silent — and as the EXEMPT `unfolded-mention`.

    A mention that cannot resolve drops one link and moves no entity's state,
    and both passes drop the same one, so it must not fail the run; but a
    journal that references an unregistered name must still be OBSERVABLE, or
    the producer's omission (D9) is invisible. The IDENTITY form of the same
    lookup stays NON-exempt — that contrast is what makes the exemption a
    classification rather than a blanket.
    """
    sdk, events = _journal(tmp_path, _MENTION_EVENT)
    try:
        g = sdk._get_proj().g
        with collect_non_folded() as mention_entries:
            assert resolve_fold_name(g, "Subject", "nobody-registered",
                                     mention=True) is None
        assert [e.shape for e in mention_entries] == [SHAPE_UNFOLDED_MENTION], (
            mention_entries)
        assert mention_entries[0].exempt, (
            "a mention miss is loose ATTRIBUTION: the live and replay passes "
            "drop the same link, so it is a producer omission, not a "
            "live/replay divergence")

        with collect_non_folded() as identity_entries:
            assert resolve_fold_name(g, "Subject", "nobody-registered") is None
        assert [e.shape for e in identity_entries] == [
            "unregistered-reference"], identity_entries
        assert not identity_entries[0].exempt, (
            "a fold that must MOVE an entity's state and cannot resolve its "
            "target must fail the run — a blanket exemption would hide it")
    finally:
        sdk.close()


def test_an_ambiguous_name_is_recorded_by_the_singular_resolver(tmp_path: Path):
    """A name TWO live entities hold: the PLURAL resolver wires both, the
    SINGULAR one cannot pick, and it must RECORD that rather than returning a
    silent ``None`` (a silent ``None`` is a fold that neither moves the entity
    nor fails the run — the fail-open the shapes exist to prevent)."""
    sdk = TortoiseSDK(str(tmp_path / "ambiguous.db"))
    try:
        g = sdk._get_proj().g
        g.query("CREATE (o:Object {id:'amb-1', name:'Amb', status:'live'})")
        g.query("CREATE (o:Object {id:'amb-2', name:'Amb', status:'live'})")
        with collect_non_folded() as plural_entries:
            assert sorted(resolve_fold_name_ids(g, "Object", "Amb")) == [
                "amb-1", "amb-2"]
        assert plural_entries == [], (
            "an ATTRIBUTION wires every live carrier — it is not a miss")
        with collect_non_folded() as singular_entries:
            assert resolve_fold_name(g, "Object", "Amb") is None
        assert [e.shape for e in singular_entries] == [
            SHAPE_AMBIGUOUS_ENTITY_NAME], singular_entries
        assert not singular_entries[0].exempt, singular_entries
    finally:
        sdk.close()


def test_the_graph_entity_view_is_a_pure_function_of_the_journal(
        tmp_path: Path):
    """The exact index a widened digest would consume, over a NON-empty graph.

    Non-vacuous on purpose: a registration is journaled too, so the entity view
    has a member and `[] == []` cannot carry the assertion.
    """
    registered = {"type": "ObjectRegistered", "id": "obj-registered",
                  "name": "registered-thing", "objectKind": "core:other",
                  "status": "live"}
    sdk, events = _journal(tmp_path, registered, _MENTION_EVENT)
    try:
        views: list = []
        for _ in range(2):
            _rebuild(sdk, events)
            views.append(_graph_entities(sdk._get_proj())[0])
        assert views[0], "the entity view must not be empty — else this is vacuous"
        assert views[0] == views[1], (
            "the graph's entity view is not a pure function of the journal: "
            f"{views[0]} != {views[1]} (#7936)")
    finally:
        sdk.close()


# ── #1155-P1: a mention never re-addresses a registered carrier ────────────
#
# The registration comes FIRST (with a foreign, connector-style id); a later
# `EventRecorded` mentioning the same name must resolve ONTO that carrier and
# leave its `id` alone. This is the half of #1155-P1 that survives the change,
# and it is the reason every write on this path is id-keyed and
# `ON CREATE`-only where it writes at all.

_FOREIGN_ID = "github-issue-test/repo-42"


def _event_mentioning(name: str, *, label: str) -> dict:
    return {
        "type": "EventRecorded",
        "id": f"evt-1155-{label.lower()}",
        "eventId": f"evt-1155-{label.lower()}",
        "name": "clobber matrix event",
        "eventKind": "test",
        "startedAt": "2026-01-01T00:00:00Z",
        ("subject" if label == "Subject" else "object"): name,
    }


@pytest.mark.parametrize("label,creation", (
    ("Object", "ObjectRegistered"), ("Subject", "SubjectAdded")))
def test_a_late_mention_does_not_re_address_a_registered_carrier(
        tmp_path: Path, label: str, creation: str):
    """#1155-P1: the mention resolves to the carrier and leaves its id alone."""
    name = "clobber-target"
    record = {"type": creation, "id": _FOREIGN_ID, "name": name}
    if creation == "ObjectRegistered":
        record["objectKind"] = "core:other"
    else:
        record["subject_kind"] = "core:other"
    sdk, events = _journal(
        tmp_path, record, _event_mentioning(name, label=label))
    try:
        _rebuild(sdk, events)
        ids = _carrier_ids(sdk)
        assert ids[(label, name)] == _FOREIGN_ID, (
            "a later mention REWROTE the registered carrier's id: "
            f"{ids[(label, name)]!r} != {_FOREIGN_ID!r} (#1155-P1)")
        assert len([k for k in ids if k == (label, name)]) == 1, ids
    finally:
        sdk.close()


def test_a_registered_then_replayed_mention_keeps_one_carrier(tmp_path: Path):
    """And the replay reproduces it — no second incarnation from a mention."""
    sdk, events = _journal(
        tmp_path,
        {"type": "ObjectRegistered", "id": _FOREIGN_ID, "name": "stub-object",
         "objectKind": "core:other", "status": "live"},
        _MENTION_EVENT,
    )
    try:
        _rebuild(sdk, events)
        live_ids = _carrier_ids(sdk)
        _rebuild(sdk, events)
        assert _carrier_ids(sdk) == live_ids, (
            "replay moved the carrier set: "
            f"{_carrier_ids(sdk)} != {live_ids}")
        assert live_ids[("Object", "stub-object")] == _FOREIGN_ID, live_ids
    finally:
        sdk.close()
