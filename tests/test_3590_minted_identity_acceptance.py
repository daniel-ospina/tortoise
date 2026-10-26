"""#3590 acceptance pins — minted identity is opaque, renameable, and replay-stable.

The #3590 model change re-keys ``Object``/``Subject`` from ``{name}`` to
``{id}``, so identity is an opaque id minted ONCE at creation and ``name`` is a
mutable natural key. Four things follow, and each is asserted here against a
real graph rather than by eye — the issue's own acceptance list says
*"asserted, not asserted-by-eye"*, and the 8-cycle bug classes in #3573 are
exactly the shapes a by-eye check misses:

1. **No id is derived from a name.** The pre-#3590 id was
   ``obj-<sha26(name)>`` — a *function* of the mutable label, which is why a
   rename minted a second entity.
2. **A re-mention of a live name is idempotent.** Two creates of one name are
   one entity (the id does not churn).
3. **A rename is journaled on the stable id and survives replay**, and the old
   name does not come back (acceptance: *"Renaming an entity is journaled and
   survives replay; the old name does not come back"*).
4. **Delete then re-create the same name yields two distinct entities** —
   acceptance: *"no resurrection, no burial"*.

Every assertion in items 1, 3 and 4 is RED on the pre-#3590 tree: the id was
the name's hash, a rename was withheld rather than journaled, and a re-create
resurrected the buried id. That is the point of those pins — each one names the
behaviour that regresses if the MERGE key ever moves back to `{name}`.

⚠️ **Item 2 is NOT a pre-fix regression pin, and must not be sold as one.**
`test_two_creates_of_one_live_name_are_one_entity` PASSES on the pre-#3590 tree,
because the derived id made both creates land on the same id by construction;
it is the #452 idempotency contract — a property the change must PRESERVE, so
it is a load-bearing anti-regression pin, just of the other kind (and it is the
one test the base run reports as passing: "4 failed, 1 passed"). The measured
base result for this file is exactly that split, and the earlier blanket claim
("every assertion here is RED") was wrong about this one (#3590 review P3).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK

pytestmark = pytest.mark.timeout(300)


def _name_ids(proj, label: str, name: str) -> list[str]:
    """Every id carrying ``label.name == name`` — the graph's own answer."""
    rows = proj.g.query(
        f"MATCH (n:{label} {{name:$n}}) RETURN n.id", params={"n": name}
    ).result_set
    return [r[0] for r in rows]


def _derived(label: str, name: str) -> str:
    """The RETIRED derivation, computed the way ``ids.entity_name_id`` did."""
    return f"{label[:3].lower()}-" + hashlib.sha256(
        f"{label}:{name}".encode()).hexdigest()[:26]


@pytest.fixture()
def sdk(tmp_path: Path):
    events = tmp_path / "events"
    events.mkdir()
    client = TortoiseSDK(str(tmp_path / "t.db"),
                         event_log_path=str(events / "events.jsonl"))
    try:
        yield client, events
    finally:
        client.close()


def _journal(events: Path) -> list[dict]:
    return [json.loads(line) for line in
            (events / "events.jsonl").read_text().splitlines() if line.strip()]


def test_an_object_id_is_not_derived_from_its_name(sdk):
    """Acceptance 1: the create mints an opaque id, never ``obj-<sha26(name)>``.

    #3590's root cause, stated in the issue: ``obj-<sha26(name)>`` is a function
    of the name, so the id cannot tell two same-named incarnations apart and it
    changes meaning whenever the name changes.
    """
    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Acme", objectKind="core:strategy",
                         is_episodic=False)
    holders = _name_ids(proj, "Object", "Acme")
    assert len(holders) == 1, holders
    assert holders[0] != _derived("Object", "Acme"), (
        "the id is still a function of the name — the pre-#3590 derivation "
        "survived (#3590 R1)")
    # …and it is not the name itself either: `MERGE (o {name}) ON CREATE SET
    # o.id = $name` was the third spelling of name-as-identity the change
    # retires (`projection/edges.py::_mint_subject_stub` did exactly that).
    assert holders[0] != "Acme", holders


def test_a_subject_id_is_not_derived_from_its_name(sdk):
    """The ``:Subject`` twin of the same pin — both kinds were name-keyed."""
    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("subject", "Team Alpha", is_episodic=False)
    holders = _name_ids(proj, "Subject", "Team Alpha")
    assert len(holders) == 1, holders
    assert holders[0] != _derived("Subject", "Team Alpha"), holders
    assert holders[0] != "Team Alpha", holders


def test_two_creates_of_one_live_name_are_one_entity(sdk):
    """A re-mention resolves to the live holder — the id does not churn.

    The natural-key half of the model: the name still dedupes creates, so
    retiring the derived id must not also retire idempotency (#452).
    """
    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Acme", objectKind="core:strategy",
                         is_episodic=False)
    first = _name_ids(proj, "Object", "Acme")
    # ⛔ The length pin is what makes this test non-vacuous. Without it BOTH
    # lists are `[]` when the create writes nothing, and `[] == []` passes —
    # measured: stubbing `create_entity` to a no-op left this test GREEN.
    assert len(first) == 1, first
    client.create_entity("object", "Acme", objectKind="core:strategy",
                         is_episodic=False)
    second = _name_ids(proj, "Object", "Acme")
    assert len(second) == 1, second
    assert first == second, (first, second)


def test_a_rename_journals_on_the_stable_id_and_survives_replay(sdk):
    """Acceptance 3: rename → ``EntityMutated op=rename``, stable id, old name gone.

    Pre-#3590 the rename was WITHHELD from the journal (a deferred-rename
    block), because the journal could not express "same identity, new name" —
    the identity WAS the name. The probe is deliberately replay-based: a rename
    that lands live but is dropped on ``rebuild_all`` is the divergence class
    this epic exists to end.
    """
    client, events = sdk
    proj = client._get_proj()
    client.create_entity("object", "Acme", objectKind="core:strategy",
                         is_episodic=False)
    (oid,) = _name_ids(proj, "Object", "Acme")

    client.update_entity(oid, name="Acme Renamed")

    assert _name_ids(proj, "Object", "Acme") == [], "the old name survived the rename"
    assert _name_ids(proj, "Object", "Acme Renamed") == [oid], (
        "the rename minted a second entity instead of renaming the stable id")

    renames = [rec for rec in _journal(events)
               if rec.get("type") == "EntityMutated" and rec.get("op") == "rename"]
    assert renames, (
        "the rename was not journaled as `EntityMutated op=rename` — replay "
        "cannot reproduce it (#3377)")
    assert [rec["id"] for rec in renames] == [oid], (
        "the rename record must carry the STABLE id, not the new name")

    # ⛔ MUTATE LIVE STATE FIRST, so the assertions below can only pass if the
    # rebuild actually replayed. Measured: with a no-op `rebuild_all` the
    # previous "rebuild then assert the graph" form left all five tests GREEN,
    # because after a clean live write the live graph already IS the expected
    # post-replay state. `live-drift` exists nowhere in the journal, so only a
    # real rebuild removes it and restores the journaled name.
    proj.g.query("MATCH (o:Object {id:$id}) SET o.name='live-drift'",
                 params={"id": oid})
    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _name_ids(proj, "Object", "live-drift") == [], (
        "the rebuild did not replay — the unjournaled live drift survived")
    assert _name_ids(proj, "Object", "Acme Renamed") == [oid], (
        "the rename did not survive replay — `rebuild != live`")
    assert _name_ids(proj, "Object", "Acme") == [], (
        "replay resurrected the old name")


def test_delete_then_recreate_the_same_name_yields_two_entities(sdk):
    """Acceptance 4: two incarnations, distinct ids — no resurrection, no burial.

    This is the shape ``obj-<sha26(name)>`` could not express: the re-create
    recomputed the SAME id, so the graph either resurrected the deleted entity
    or buried the new one. The incumbents must stay distinct ids.
    """
    client, events = sdk
    proj = client._get_proj()
    client.create_entity("object", "Acme", objectKind="core:strategy",
                         is_episodic=False)
    (first,) = _name_ids(proj, "Object", "Acme")

    client.delete_entity(first)
    client.create_entity("object", "Acme", objectKind="core:strategy",
                         is_episodic=False)
    holders = _name_ids(proj, "Object", "Acme")
    assert len(holders) == 1, holders
    assert holders[0] != first, (
        "the re-create reused the deleted entity's id — that is resurrection "
        "(#3590 R5: no id reuse)")

    # …and replay agrees with live. The DELETE is an `EntityMutated op=delete`,
    # so exactly one Object survives — the second incarnation, holding the name.
    #
    # ⛔ As in test 3, LIVE STATE IS MUTATED FIRST: `live-drift` is in no journal
    # record, so only a real rebuild can remove it. Without this the assertions
    # are satisfied by the live graph itself and a no-op `rebuild_all` passes
    # them (measured). The Object COUNT is asserted too — a resurrected phantom
    # third node would otherwise satisfy every membership check.
    proj.g.query("MATCH (o:Object {id:$id}) SET o.name='live-drift'",
                 params={"id": holders[0]})
    proj.rebuild_all(str(events), confirm_destructive=True)

    all_objects = {r[0] for r in proj.g.query(
        "MATCH (o:Object) RETURN o.id").result_set}
    assert len(all_objects) == 1, (
        f"replay left {len(all_objects)} Objects — resurrection or a phantom: "
        f"{all_objects}")
    assert _name_ids(proj, "Object", "live-drift") == [], (
        "the rebuild did not replay — the unjournaled live drift survived")
    assert holders[0] in all_objects, (
        "replay lost the surviving incarnation — burial")
    assert first not in all_objects, (
        "replay resurrected the deleted incarnation — resurrection")
    assert set(_name_ids(proj, "Object", "Acme")) == {holders[0]}, (
        "replay changed which incarnation holds the name")


# ── Review findings from the #3590 checkpoint review ───────────────────────
# Four behaviours the D9 "register what you reference" rule decides, each one
# RED on the pre-fix head at this branch (`b7f2b4d40`). They live here — beside
# the acceptance promises — because each is a consequence of the same model
# change: the fold no longer mints, so SOME door has to.

def test_a_mention_no_door_registered_does_not_make_the_journal_unrebuildable(
        sdk):
    """#3590 review P1: a mention with no registration must not abort replay.

    ``EventAPI.add_event`` is this repo's main journal producer (the module
    docstring names the v1 extractor, ``ingest``, ``docs_miner``, ``mining``
    and the CLI as its callers), and it registers NONE of the names it writes
    into ``subject`` / ``object`` / ``uses[]``. Pre-#3590 the fold minted a
    stub for each, so those journals replayed; once the fold stopped minting,
    every one of them raised ``NonFoldedEventsError`` — *the durable copy of
    the data could no longer be rebuilt from its journal*.

    A mention is loose ATTRIBUTION, not the folded record's identity: the live
    pass and the replay pass run the same resolver, so both drop the SAME link.
    That is a producer omission (real, and recorded), not a live/replay
    divergence — so the shape is exempt, and the run completes.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.projection.nonfolded import EXEMPT_SHAPES, SHAPE_UNFOLDED_MENTION

    client, events = sdk
    proj = client._get_proj()
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=proj)
    # No `add_object`/`add_subject` for ANY of these names — exactly the
    # unregistered-mention journal a producer writes.
    api.add_event("ev-mention", "meeting",
                  subject="Nobody Registered",
                  object_name="Nothing Registered",
                  uses=[{"name": "No Tool Registered", "kind": "core:tool"}])

    # The exemption is a CLASSIFICATION, not a silent pass: an unclassified
    # shape is fail-closed by default (`nonfolded.py`), so this pins that the
    # mention shape was decided rather than forgotten.
    assert SHAPE_UNFOLDED_MENTION in EXEMPT_SHAPES, (
        "the mention shape is not exempt — replay of this journal refuses")

    # The P1: pre-fix this raised NonFoldedEventsError (2 unregistered
    # references, one per mention) and the graph was left WIPED.
    proj.rebuild_all(str(events), confirm_destructive=True)

    rows = proj.g.query(
        "MATCH (e:Event {eventId:'ev-mention'}) RETURN e.eventKind"
    ).result_set
    assert rows and rows[0][0] == "meeting", (
        "the rebuild did not replay the journal it refused to refuse")
    # …and replay agrees with live: neither side fabricated the mention.
    assert proj.g.query(
        "MATCH (o:Object {name:'Nothing Registered'}) RETURN count(o)"
    ).result_set[0][0] == 0, (
        "replay MINTED an entity for a mention — the fold is not the minter")


def test_an_unregistered_mention_is_exempt_but_an_identity_miss_is_not(sdk):
    """The exemption's BOUND — asserted by RUNNING the fold, not by reading a flag.

    ``identity-decision.md`` cites this test BY NAME as the proof that the
    ``unfolded-mention`` exemption cannot leak into the identity class, so it
    must fail if the leak happens. An earlier version asserted two membership
    tests on module CONSTANTS (``SHAPE_UNFOLDED_MENTION in EXEMPT_SHAPES`` and
    its negation) — which stays GREEN if ``_fold_object_superseded``'s id-less
    branch is changed to record ``unfolded-mention``, i.e. under exactly the
    mutation it claims to guard (#3590 review P1-2).

    So the bound is now asserted against a real replay of a real journal, in
    ONE run so the two sides cannot drift apart:

    * a mention of a name nobody registered is EXEMPT — the run completes (the
      live and replay passes drop the same link);
    * an id-less ``ObjectSuperseded`` over that same journal is REFUSED — the
      run raises ``NonFoldedEventsError`` carrying ``unregistered-reference``,
      because a supersede must MOVE an entity's state and cannot resolve its
      target.

    Non-vacuous by construction: re-point the fold site's shape at
    ``SHAPE_UNFOLDED_MENTION`` and the ``pytest.raises`` below does not fire.
    """
    from tortoise.projection import _replay_write_scope
    from tortoise.projection.nonfolded import (
        SHAPE_UNFOLDED_MENTION,
        SHAPE_UNREGISTERED_REFERENCE,
        NonFoldedEventsError,
    )

    client, events = sdk
    proj = client._get_proj()
    journal = events / "events.jsonl"

    # ── the MENTION half: unregistered names only, and the run completes ──
    journal.write_text(json.dumps({
        "type": "EventRecorded", "id": "ev-bound-1",
        "eventId": "ev-bound-1", "name": "bound matrix event",
        "eventKind": "meeting", "subject": "Nobody Registered",
        "object": "Nothing Registered",
        "startedAt": "2026-01-01T00:00:00Z",
    }) + "\n")
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert proj.g.query(
        "MATCH (o:Object {name:'Nothing Registered'}) RETURN count(o)"
    ).result_set[0][0] == 0, "the fold minted a mention carrier"

    # ── the IDENTITY half: the same journal plus an id-less supersede ──
    with journal.open("a") as fh:
        fh.write(json.dumps({
            "type": "ObjectSuperseded", "name": "Nobody Registered",
            "supersedes_by": "Successor",
            "ts": "2026-01-01T00:00:01+00:00",
        }) + "\n")
    with pytest.raises(NonFoldedEventsError) as refused:
        proj.rebuild_all(str(events), confirm_destructive=True)

    shapes = [e.shape for e in refused.value.refused]
    assert SHAPE_UNREGISTERED_REFERENCE in shapes, (
        "an id-less ObjectSuperseded must keep the NON-exempt identity shape — "
        f"the run refused {shapes} instead")
    assert SHAPE_UNFOLDED_MENTION not in shapes, (
        "the mention exemption LEAKED into the identity class: the supersede "
        f"was refused under the exempt shape too ({shapes})")
    assert not any(e.exempt for e in refused.value.refused), (
        f"an identity miss was marked exempt: {shapes}")
    # …and the mention in the SAME journal was still classified as the exempt
    # shape rather than silently dropped: the two dispositions coexist in one
    # fold, which is what "a classification, not a blanket" means.
    assert SHAPE_UNFOLDED_MENTION in [e.shape for e in refused.value.events]


def test_a_fold_that_moves_state_cannot_ride_the_mention_exemption(sdk):
    """#3590 review P1 (round 5): the explicit-``object`` status fold is NOT attribution.

    ``_event_plain_merge`` resolves the event's explicit ``object`` through the
    MENTION resolver (``mention=True``), which records the EXEMPT
    ``unfolded-mention`` when no single live holder carries the name. The
    ``#1350`` work-item fold then took that same ``None`` as its target:
    ``_targets()`` appends a target only when it has an id, and the built-in
    half is guarded on the id too — so an unresolvable ``object`` applied NO
    status write, reported NO ``status_fold_unapplied``, and the run PASSED with
    an exempt warning. That is the exemption's own written bound crossed:
    ``identity-decision.md`` and ``nonfolded.EXEMPT_SHAPES`` both say a fold that
    must MOVE an entity's state can never take it — an unrested entity silently
    keeps its old status behind a green rebuild.

    Non-vacuous by construction: the journal below is the SAME shape the
    exemption test above replays green (an unregistered name mentioned by an
    event). Only the fold's classification separates them, so restoring
    ``mention=True`` for the fold's own resolution reds the ``pytest.raises``.
    """
    from tortoise.projection.nonfolded import (
        SHAPE_UNREGISTERED_REFERENCE,
        NonFoldedEventsError,
    )

    client, events = sdk
    proj = client._get_proj()
    journal = events / "events.jsonl"
    journal.write_text(json.dumps({
        "type": "EventRecorded", "id": "ev-fold-miss",
        "eventId": "ev-fold-miss", "name": "unregistered lifecycle target",
        # a #1350 lifecycle kind: this event's ONLY projection is Entity state
        "eventKind": "github.issue.closed",
        "object": "Never Registered",
        "startedAt": "2026-01-01T00:00:00Z",
    }) + "\n")

    with pytest.raises(NonFoldedEventsError) as refused:
        proj.rebuild_all(str(events), confirm_destructive=True)

    shapes = [e.shape for e in refused.value.refused]
    assert SHAPE_UNREGISTERED_REFERENCE in shapes, (
        "a lifecycle event whose `object` resolved to nothing applied no status "
        f"write and passed the run; refused {shapes} instead")
    assert not any(e.exempt for e in refused.value.refused), (
        f"the state-moving fold's miss was marked EXEMPT: {shapes}")


def test_a_declared_objecttype_object_still_folds_the_work_item_status(sdk):
    """#3590 review P2: ``objectType='Object'`` must not drop the status fold.

    ``_event_plain_merge`` resolves the event's explicit ``object`` to an id and
    hands it to ``_fold_object_status``. Keying that hand-off on TRUTHINESS of
    ``objectType`` discarded the id for a DECLARED ``'Object'`` (only
    ``'Document'`` has no entity id to give — its object is a :Source url), so
    both halves of the fold went inert: the ``#1350`` work-item status was
    silently NOT applied and nothing was reported. Fail-open, in the opposite
    direction to every other fold in the change.

    Reachable from a public door: ``EventAPI.add_event(object_type=…)``.
    """
    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Widget", is_episodic=False)
    (oid,) = _name_ids(proj, "Object", "Widget")

    client.create_event("widget closed", "github.issue.closed",
                        object="Widget", objectType="Object",
                        is_episodic=False)

    status = proj.g.query(
        "MATCH (o:Object {id:$id}) RETURN o.status", params={"id": oid}
    ).result_set
    assert status and status[0][0] == "completed", (
        "objectType='Object' suppressed the built-in lifecycle fold — status "
        f"is {status[0][0] if status else None!r} (#1350)")
    # …and the produces edge still lands on the resolved id, not a stub.
    assert proj.g.query(
        "MATCH (e:Event)-[:produces]->(o:Object {id:$id}) "
        "RETURN count(e)", params={"id": oid}).result_set[0][0] == 1


def test_an_event_may_mention_a_name_two_live_entities_hold(sdk):
    """#3590 review P2: a doubly-held name is legal — it must not fail the door.

    Same-name coexistence is a state the model explicitly legalises (#8864 /
    D2), and the about-link cascade journals and writes EVERY carrier. The
    registration this door performs must therefore not turn a legal graph into
    a hard ``AmbiguousEntityName`` for the event door only — the refusal
    belongs to the CREATE door, not to a reference.
    """
    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Dup", _server_id="id-dup-a",
                         is_episodic=False)
    client.create_entity("object", "Dup", _server_id="id-dup-b",
                         is_episodic=False)
    assert sorted(_name_ids(proj, "Object", "Dup")) == ["id-dup-a", "id-dup-b"], (
        "fixture did not produce the legal two-carrier state")

    client.create_event("mentions a doubly-held name", "note",
                        aboutObject="Dup", _server_id="ev-dup",
                        is_episodic=False)
    eid = "ev-dup"

    wired = {r[0] for r in proj.g.query(
        "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o:Object) "
        "RETURN o.id", params={"eid": eid}).result_set}
    assert wired == {"id-dup-a", "id-dup-b"}, (
        f"the event wired {sorted(wired)} instead of BOTH legal carriers")


def test_the_event_door_registers_every_name_its_record_mentions(sdk):
    """#3590 D9 at the SDK door: mentions are REGISTERED, so edges survive replay.

    ``EventRecorded`` carries three kinds of entity reference — the ``about*``
    links, and the loose mentions ``subject`` / ``object`` / ``uses[]``. The
    ``about*`` half already registered its targets; the mention half registered
    nothing, so the fold resolved them against no holder and silently dropped
    ``performs`` / ``produces`` / ``uses`` — live AND on replay (this is the
    producer omission the ``unfolded-mention`` exemption tolerates for legacy
    journals, not something a current door may keep doing).

    The pin is replay-based on purpose: a mention edge that lands live but
    vanishes on ``rebuild_all`` is `rebuild != live`, which is the invariant the
    whole change exists for.
    """
    client, events = sdk
    proj = client._get_proj()
    client.create_event("meeting with a tool", "meeting",
                        subject="Agent Pi",
                        object="Docs Thing",
                        uses=[{"name": "Tool X", "kind": "core:tool"}],
                        _server_id="ev-mentions",
                        is_episodic=False)

    def _edges() -> dict:
        return {
            "performs": {r[0] for r in proj.g.query(
                "MATCH (s:Subject)-[:performs]->(e:Event {eventId:'ev-mentions'}) "
                "RETURN s.name").result_set},
            "produces": {r[0] for r in proj.g.query(
                "MATCH (e:Event {eventId:'ev-mentions'})-[:produces]->(o:Object) "
                "RETURN o.name").result_set},
            "uses": {r[0] for r in proj.g.query(
                "MATCH (e:Event {eventId:'ev-mentions'})-[:uses]->(o:Object) "
                "RETURN o.name").result_set},
        }

    live = _edges()
    assert live == {"performs": {"Agent Pi"},
                    "produces": {"Docs Thing"},
                    "uses": {"Tool X"}}, live

    # An id-shaped name would be the pre-#3590 derivation sneaking back in; the
    # registration must MINT (and the mention must resolve to that minted id).
    for name in ("Docs Thing", "Tool X"):
        ids = _name_ids(proj, "Object", name)
        assert len(ids) == 1 and ids[0] != name, (name, ids)

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _edges() == live, (
        "the mention edges did not survive replay — the door referenced names "
        "its journal never registered (#3590 D9)")


def test_a_create_holds_the_per_name_mint_lock_across_its_write(sdk, monkeypatch):
    """#3590 review P2-1: the lock's SCOPE is the write, not just the resolve.

    `entity_identity`'s contract says the per-name lock closes the create race
    *within a process*. It did not: `resolve_or_mint`'s ``with`` block ENDED when
    the resolver returned, and the caller writes the node afterwards, so two
    threads creating one name interleave as

    1. T1 sees ZERO live holders, mints ``id-a``, releases the lock;
    2. T2 — before T1 has written — sees ZERO, mints ``id-b``, releases;
    3. both MERGE by their own id ⇒ **two live carriers of one name**.

    That state is create-poisoning: every later by-name create and every
    `resolve_entity_id` refuses with `AmbiguousEntityName`, and there is no
    by-name repair path. The fix is that a create door holds
    `entity_identity.mint_lock` across the resolve AND the write.

    The pin is taken AT the write: a probe thread tries to acquire the very
    lock the door must be holding, and must FAIL. Non-vacuous by construction —
    drop the ``with mint_lock(...)`` from `create_entity` (or from
    `_connect_issue_objects`) and the probe acquires it, so this reds.
    """
    import threading

    from tortoise.entity_identity import _holder_lock

    client, _ = sdk
    proj = client._get_proj()
    observed: dict = {}
    real = TortoiseSDK._create_entity

    def _spy(self, label, id_val, props, event_type, **kw):
        if label == "Object" and props.get("name") == "Serialised":
            lock = _holder_lock("Object", "Serialised")
            probe: dict = {}

            def _try_acquire():
                got = lock.acquire(blocking=False)
                probe["free"] = got
                if got:
                    lock.release()

            t = threading.Thread(target=_try_acquire, daemon=True)
            t.start()
            t.join(10)
            observed["free"] = probe.get("free")
        return real(self, label, id_val, props, event_type, **kw)

    monkeypatch.setattr(TortoiseSDK, "_create_entity", _spy)
    client.create_entity("object", "Serialised", is_episodic=False)

    assert observed.get("free") is False, (
        "the per-name mint lock was FREE at the moment of the write "
        f"(observed={observed}) — it is released when `resolve_or_mint` "
        "returns, so two concurrent creates of one name both mint and the "
        "name is create-poisoned (#3590 review P2-1)")
    assert len(_name_ids(proj, "Object", "Serialised")) == 1, (
        "one name, one carrier")
