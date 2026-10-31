"""#3590 acceptance pins — minted identity is opaque, renameable, and replay-stable.

The #3590 model change re-keys ``Object``/``Subject`` from ``{name}`` to
``{id}``, so identity is an opaque id minted ONCE at creation and ``name`` is a
mutable natural key. Four things follow, and each is asserted here against a
real graph rather than by eye — the issue's own acceptance list says
*"asserted, not asserted-by-eye"*, and the 8-cycle bug classes in #3573 are
exactly the shapes a by-eye check misses:

1. **No id is derived from a name** — at the ``create_entity`` door. The
   pre-#3590 id was ``obj-<sha26(name)>`` — a *function* of the mutable label,
   which is why a rename minted a second entity. ⛔ ONE bounded exception is
   recorded at ``identity-decision.md`` (D1's home): the fold-side ``:Subject``
   about-edge stub still mints ``id = name`` (``edges._mint_subject_stub``,
   kept for journals already written, ``#8914``), so a ``create_entity`` that
   resolves to such a stub adopts it — the pins below exercise the
   create-first path, where no stub exists.
2. **A re-mention of a live name is idempotent.** Two creates of one name are
   one entity (the id does not churn).
3. **A rename is journaled on the stable id and survives replay**, and the old
   name does not come back (acceptance: *"Renaming an entity is journaled and
   survives replay; the old name does not come back"*).
4. **Delete then re-create the same name yields two distinct entities** —
   acceptance: *"no resurrection, no burial"*.

Every assertion here is RED on the pre-#3590 tree: the id was the name's hash,
a rename was withheld rather than journaled, and a re-create resurrected the
buried id. That is the point of the pins — each one names the behaviour that
regresses if the MERGE key ever moves back to `{name}`.
"""

from __future__ import annotations

import hashlib
import json
import re
import tokenize
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


def _raw(events: Path, **rec) -> None:
    """Append one hand-written journal line (the only way to reach a shape no
    public writer emits — an id-less legacy ``ObjectSuperseded``)."""
    rec.setdefault("event_id", "evt-" + hashlib.sha256(
        repr(sorted(rec.items())).encode()).hexdigest()[:8])
    rec.setdefault("ts", "2026-01-01T00:00:00+00:00")
    with open(events / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


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
    # o.id = $name` was a third spelling of name-as-identity. It survives in
    # ONE place this door never reaches from here — the fold-side `:Subject`
    # about-edge stub (`projection/edges.py::_mint_subject_stub`), kept for
    # journals already written and bounded at `identity-decision.md` (#8914).
    # This pin is the create-first path: no about-edge, so no stub.
    assert holders[0] != "Acme", holders


def test_a_subject_id_is_not_derived_from_its_name(sdk):
    """The ``:Subject`` twin of the same pin — both kinds were name-keyed.

    ⛔ The create-first path ONLY. The fold-side ``:Subject`` about-edge stub
    (``edges._mint_subject_stub``) still mints ``id = name`` for journals
    already written, and a create that resolves to such a stub ADOPTS it — so
    this pin must not be read as "a Subject id can never be its name". That
    bounded exception is recorded at ``identity-decision.md`` (D1's home) and
    ends with ``#8914``.
    """
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


def test_a_create_adopts_the_single_id_less_live_holder(sdk):
    """#3590 review F2: a create ADOPTS an id-less holder — never mints beside it.

    ``MERGE (o:Object {name:$name})`` (a legacy / raw / foreign writer's
    construction) leaves a LIVE holder carrying NO id. Minting a fresh id beside
    it makes TWO live carriers of one name, and from then on every resolve of
    that name raises ``AmbiguousEntityName`` — the name is create-poisoned, with
    no by-name repair path, silently and permanently. The write must land on ONE
    carrier, and the name must stay create-able.

    Mutation that must red this: restoring the mint-on-id-less arm in
    ``entity_identity.resolve_or_mint`` (i.e. deleting ``_adopt_idless_holder``).
    """
    client, _ = sdk
    proj = client._get_proj()
    proj.g.query(
        "MERGE (o:Object {name:$n}) SET o.objectKind='core:strategy'",
        params={"n": "Legacy"})
    assert _name_ids(proj, "Object", "Legacy") == [None], (
        "fixture did not build the id-less live holder")

    created = client.create_entity("object", "Legacy",
                                   objectKind="core:strategy",
                                   is_episodic=False)
    ids = _name_ids(proj, "Object", "Legacy")
    assert len(ids) == 1, (
        "the create minted a SECOND live carrier beside the id-less holder — "
        f"the name is now create-poisoned: {ids}")
    adopted = ids[0]
    assert adopted, ids
    assert created["node"]["id"] == adopted, (created, adopted)

    # …and the name is still create-able: a second create RESOLVES to the one
    # adopted carrier instead of refusing `AmbiguousEntityName` forever.
    again = client.create_entity("object", "Legacy",
                                 objectKind="core:strategy",
                                 is_episodic=False)
    assert again["node"]["id"] == adopted, (again, adopted)
    assert _name_ids(proj, "Object", "Legacy") == [adopted]


def _statuses(proj, label: str, name: str) -> list:
    """Every stored ``status`` on the ``label.name`` carriers, as the store has it."""
    return [r[0] for r in proj.g.query(
        f"MATCH (n:{label} {{name:$n}}) RETURN n.status",
        params={"n": name}).result_set]


def test_the_adopted_id_less_holder_is_not_a_mint_to_the_phase1_arms(sdk):
    """#9876 review F2: the ADOPTED shape is NOT a mint (``merges`` is TRUE).

    ``_entity_name_holders`` feeds the ``#7925`` Phase-1 arms, and both read
    ``merges`` as *"does this write MINTS the node, so its declared status is
    stored?"*. Adoption is neither a resolve (the id does not exist yet) nor a
    mint (the write lands on an EXISTING carrier, and the projection's status
    write is ``ON CREATE``-only, so a matched ``MERGE`` never applies it) —
    answering ``merges=False`` made both arms refuse a bundle the writer
    commits.
    """
    client, _ = sdk
    proj = client._get_proj()
    proj.g.query(
        "MERGE (o:Object {name:$n}) SET o.objectKind='core:strategy'",
        params={"n": "Legacy"})
    assert _name_ids(proj, "Object", "Legacy") == [None], (
        "fixture did not build the id-less live holder")

    exists, hit, _op, bad, carriers, merges, target = \
        client._entity_name_holders("Object", "Legacy")
    assert exists is True and carriers == 1, (exists, carriers)
    assert merges is True, (
        "the guard reads the ADOPTED carrier as a MINT, so the #7925 arms "
        "believe a declared status is stored and refuse a bundle the writer "
        "commits (the writer MERGEs onto the adopted row; ON CREATE never "
        "fires)")
    # …and it is NOT a resolve either: the landing id is minted mid-write, so no
    # id-valued axis can be reported at Phase-1 time (the recorded residual).
    assert target is None and hit is None and bad is None, (target, hit, bad)


def test_a_bundle_naming_the_adopted_holder_is_not_a_born_terminal_mint(sdk):
    """#9876 review F2, consumer B: the born-terminal arm over-rejected an ADOPT.

    The arm's premise is *"not merging ⇒ this write CREATES the node ⇒ its
    declared terminal status is stored"*, and it refuses a direct-route edge to
    a born-terminal endpoint. For the adopted shape the premise is false twice
    over: the write lands on the existing carrier and the status is dropped —
    the node stays LIVE, so ``create_direct_edge`` would have written the edge
    the guard refused.
    """
    client, _ = sdk
    proj = client._get_proj()
    proj.g.query(
        "MERGE (o:Object {name:$n}) SET o.objectKind='core:strategy'",
        params={"n": "Legacy"})
    assert _name_ids(proj, "Object", "Legacy") == [None], (
        "fixture did not build the id-less live holder")

    bundle = {
        "entities": [{"ref": "a", "type": "object", "name": "Legacy",
                      "status": "superseded"},
                     {"ref": "o", "type": "object", "name": "Other",
                      "objectKind": "core:strategy"}],
        # IMPL without `reify`/`mitigation` over two non-Point-bearing endpoints
        # ⇒ the EFFECTIVE route is 'direct', the route `create_direct_edge`
        # serves (an Object↔Object pair keeps the direct shape, #8499).
        "connections": [{"from": "a", "to": "o", "operator": "IMPL"}],
    }
    msgs = [v["message"] for v in client._validate_bundle(bundle)]
    assert not any("resolves into a terminal status" in m for m in msgs), msgs
    assert not any("born terminal" in m for m in msgs), msgs

    client.ingest(bundle)
    ids = _name_ids(proj, "Object", "Legacy")
    assert len(ids) == 1 and ids[0], (
        f"the bundle did not adopt the id-less carrier: {ids}")
    # The declared status was NOT stored — the fact that falsifies the arm's
    # premise, and the reason a direct edge to this node is legal.
    assert _statuses(proj, "Object", "Legacy") == [None], (
        "the ingest stored the declared status on the adopted carrier")
    assert _name_ids(proj, "Object", "Other"), "the bundle wrote nothing"


def test_a_bundle_naming_the_adopted_holder_may_declare_an_array_status(sdk):
    """#9876 review F2, consumer A: the array-status arm over-rejected an ADOPT.

    That arm exists because an ARRAY status the writer really stores makes
    ``create_direct_edge``'s frozenset test raise ``TypeError`` after steps 2-3
    committed. For the adopted shape the writer stores nothing — the ``MERGE``
    matches the row the adopt just stamped — so there is no array to evaluate
    and no mid-write raise to predict.
    """
    client, _ = sdk
    proj = client._get_proj()
    proj.g.query(
        "MERGE (o:Object {name:$n}) SET o.objectKind='core:strategy'",
        params={"n": "Legacy"})
    assert _name_ids(proj, "Object", "Legacy") == [None], (
        "fixture did not build the id-less live holder")

    bundle = {"entities": [{"ref": "a", "type": "object", "name": "Legacy",
                             "status": ["live", "superseded"]}]}
    msgs = [v["message"] for v in client._validate_bundle(bundle)]
    assert not any("would store status" in m for m in msgs), msgs

    client.ingest(bundle)
    ids = _name_ids(proj, "Object", "Legacy")
    assert len(ids) == 1 and ids[0], (
        f"the bundle did not adopt the id-less carrier: {ids}")
    assert _statuses(proj, "Object", "Legacy") == [None], (
        "the ingest stored the declared array status on the adopted carrier")


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


def test_an_unregistered_mention_is_exempt_but_an_identity_miss_is_not(
        sdk, tmp_path: Path):
    """The exemption's BOUND — exercised through the FOLD, on both sides.

    The degraded guarantee recorded in ``EXEMPT_SHAPES`` is worth nothing unless
    the neighbouring shape still fails the run. A mention is loose ATTRIBUTION (a
    dropped link, no entity's state moves); an ``ObjectSuperseded`` addresses an
    ENTITY the fold must MOVE, so its miss must never take that exemption.

    ⛔ Both arms run through ``rebuild_all``, i.e. through the JOURNAL-aware
    classifier — NOT through ``_fold_object_superseded`` recording a shape
    itself. A record-only verdict at the fold refuses the D2-exempt journal in
    arm 3 (one journal, two engines, opposite verdicts), which is why the fold
    records nothing for an unaddressable target and the classification is made
    one level up.

    Mutations that must red this: exempting the established-name miss
    (``classify_terminalizer_miss`` → ``SHAPE_OBJECT_SUPERSEDED_UNREGISTERED``
    for a name the journal DID establish) turns arm 2's refusal green; deleting
    the D2 exemption turns arm 3's green run into a refusal.
    """
    from tortoise.projection import _replay_write_scope
    from tortoise.projection.nonfolded import (
        EXEMPT_SHAPES,
        SHAPE_OBJECT_SUPERSEDED_MISS,
        SHAPE_OBJECT_SUPERSEDED_UNREGISTERED,
        SHAPE_UNFOLDED_MENTION,
        NonFoldedEventsError,
        collect_non_folded,
    )

    client, events = sdk
    proj = client._get_proj()

    # 1. A mention that resolves to nothing: the EXEMPT attribution shape.
    with _replay_write_scope(), collect_non_folded() as entries:
        proj.apply({"type": "EventRecorded", "eventId": "ev-bound",
                    "event": {"eventId": "ev-bound",
                              "eventKind": "meeting",
                              "subject": "Nobody At All"}})
    mention_shapes = [e.shape for e in entries]
    assert SHAPE_UNFOLDED_MENTION in mention_shapes, mention_shapes

    # 2. An ID-LESS ObjectSuperseded whose name the journal DID establish: an
    #    identity the fold must move, so the miss is NON-exempt and fails the
    #    run.
    client.create_entity("object", "Established", objectKind="core:strategy",
                         _server_id="id-established", is_episodic=False)
    _raw(events, type="ObjectSuperseded", name="Established",
         supersedes_by="Successor")
    with pytest.raises(NonFoldedEventsError) as exc:
        proj.rebuild_all(str(events), confirm_destructive=True)
    refused = [e.shape for e in exc.value.refused]
    assert SHAPE_OBJECT_SUPERSEDED_MISS in refused, (
        f"an id-less supersede of a name the journal established was NOT "
        f"refused — the identity miss took the attribution exemption: {refused}")
    assert SHAPE_OBJECT_SUPERSEDED_MISS not in EXEMPT_SHAPES, (
        "the identity shape became exempt — a supersede that could land can be "
        "skipped with a green run")

    # 3. The OTHER side of the bound: the same id-less shape on a name NOTHING
    #    established is the adopted D2 exemption — the journal replays (nothing
    #    could have produced the address, so live and replay agree by
    #    construction) — and the exemption is the CLASSIFIED one, not a silent
    #    pass.
    events2 = tmp_path / "events2"
    events2.mkdir()
    client2 = TortoiseSDK(str(tmp_path / "t2.db"),
                          event_log_path=str(events2 / "events.jsonl"))
    try:
        _raw(events2, type="ObjectSuperseded", name="Never Registered",
             supersedes_by="Successor")
        client2._get_proj().rebuild_all(str(events2), confirm_destructive=True)
        assert SHAPE_OBJECT_SUPERSEDED_UNREGISTERED in EXEMPT_SHAPES, (
            "the D2 exemption is not in EXEMPT_SHAPES — a legacy journal that "
            "replays on `main` can no longer be rebuilt")
    finally:
        client2.close()


def test_a_legacy_journal_with_a_synthesized_id_aborts_the_rebuild(
        sdk, tmp_path: Path):
    """A journal a PREVIOUS release wrote is no longer rebuildable — pinned.

    The retired ``#2164 ISSUE B`` id→name fallback let ``main`` fold an
    ``ObjectSuperseded`` that carried the **synthesised** ``obj-<sha26(name)>``
    id plus a ``name`` an earlier ``ObjectRegistered`` established. That is
    exactly what ``main``'s own ``apply_supersessions`` journaled (the pin it
    wrote here, ``test_rebuild_all_legacy_idless_object_fold_survives``, is
    rewritten in ``tests/test_status_projection.py`` to pin the refusal).

    ⛔ This is the class the retirement is EASY to get wrong in prose: the
    record carries TWO addresses, and only the **name** is established. The
    name limb answers ``OBJECT_ADDRESS_ESTABLISHED``, so the record is an
    IDENTITY miss (``SHAPE_OBJECT_SUPERSEDED_MISS``, non-exempt) and the run
    REFUSES — it is NOT the D2 ``…_UNREGISTERED`` exemption, which requires the
    journal to have established **none** of the addresses it carries.

    The decision this pins, and its unreconciled sibling, are recorded at
    ``docs/epics/2026-09-10-2835-capability-registry/identity-decision.md``
    ("A retirement that is NOT an exemption"); D2 case (i) — *"a journal that
    replays on ``main`` must keep replaying"* — is a live obligation tracked on
    ``#10345``, NOT discharged here.

    RED-capability: on ``origin/main`` this journal rebuilds CLEAN (the name
    fallback folds it), so ``pytest.raises`` reds there. Exempting the
    established-name miss, or re-growing the name branch, also reds it.
    """
    from tortoise.projection.nonfolded import (
        EXEMPT_SHAPES,
        SHAPE_OBJECT_SUPERSEDED_MISS,
        NonFoldedEventsError,
    )

    client, events = sdk
    name = "legacy-R"
    # The registration establishes the NAME. Its id is the pre-canonical
    # github-style shape, so it is NOT the id the retired fold synthesised —
    # the record below carries an id nothing ever created.
    _raw(events, type="ObjectRegistered", id=f"github-issue-{name}-7",
         name=name, objectKind="core:strategy", status="live",
         event_id="e-legacy-reg")
    # The retired release's own journal line: the SYNTHESISED canonical id.
    _raw(events, type="ObjectSuperseded", id=_derived("Object", name),
         name=name, supersedes_by="successor-S", event_id="e-legacy-sup")

    with pytest.raises(NonFoldedEventsError) as exc:
        client._get_proj().rebuild_all(str(events), confirm_destructive=True)
    refused = [e.shape for e in exc.value.refused]
    assert SHAPE_OBJECT_SUPERSEDED_MISS in refused, (
        "a supersede whose NAME the journal established was not refused — the "
        f"identity miss took the attribution exemption: {refused}")
    assert SHAPE_OBJECT_SUPERSEDED_MISS not in EXEMPT_SHAPES, (
        "the identity shape became exempt — a legacy supersede that must MOVE "
        "an entity can be skipped behind a green run")


def test_an_unresolvable_about_target_keeps_the_mention_exemption_and_replays(
        sdk, tmp_path: Path):
    """#3590 review F2: ``about_entities`` is the LOOSE list — a miss is EXEMPT.

    ⛔ This test's predecessor asserted the OPPOSITE (a non-exempt record for an
    unresolved ``about_entities`` target) and was the round-3 fix. That choice
    made ``rebuild_all`` REFUSE journals that replay on ``main``: the extraction
    pipeline emits ``about_entities`` with no ``object`` as a matter of course
    (the capture door passes the payload list straight through —
    ``sdk.py::create_event(..., about_entities=…)``), so a single extracted
    event naming an unregistered subject raised ``NonFoldedEventsError`` after
    the graph had already been wiped. That is the durable-copy regression the
    ``unfolded-mention`` exemption exists to prevent, and this fold's own
    contract says a non-match on that list is NOT a violation (*"reporting
    per-non-match would spam every unrelated entity"*).

    So the two targets are classified in opposite directions: this one keeps the
    exemption (the miss is still recorded and named at the run boundary), while
    the event's EXPLICIT ``object`` — the fold's declared target — is
    re-resolved non-exemptly and fails the run
    (``test_a_lifecycle_event_on_an_unresolvable_object_is_not_a_green_run``).

    Mutation that must red this: dropping ``mention=True`` from the
    ``resolve_fold_name`` call in ``_fold_object_status._targets`` — the record
    becomes a NON-exempt ``unregistered-reference`` and the replay below raises
    for a journal that replays on ``main``.
    """
    from tortoise.projection import _replay_write_scope
    from tortoise.projection.nonfolded import (
        SHAPE_UNFOLDED_MENTION,
        collect_non_folded,
        refused_events,
    )

    client, _events = sdk
    proj = client._get_proj()
    event = {"eventId": "ev-status-fold",
             "eventKind": "venture:disbursement",
             # a DECLARED lifecycle kind whose target is named only here, and
             # is registered nowhere
             "aboutEntities": ["Nothing Registered"]}

    # 1. The miss is RECORDED (not silently swallowed) and it is EXEMPT.
    with _replay_write_scope(), collect_non_folded() as entries:
        proj.apply({"type": "EventRecorded", "eventId": "ev-status-fold",
                    "event": event})
    shapes = [e.shape for e in entries]
    assert SHAPE_UNFOLDED_MENTION in shapes, (
        "the declared fold resolved an unregistered about_entities target and "
        f"did not record it as the mention shape: {shapes}")
    assert not refused_events(entries), (
        "an unresolved about_entities target FAILED THE RUN — a journal that "
        "replays on `main` is now un-rebuildable (the durable-copy "
        f"regression): {[str(e) for e in refused_events(entries)]}")

    # 2. …and the journal really is rebuildable (this is the assertion that
    #    fails on the pre-fix code: `rebuild_all` raised NonFoldedEventsError).
    journal = tmp_path / "about"
    journal.mkdir()
    (journal / "events.jsonl").write_text(
        json.dumps({"type": "EventRecorded", "id": "ev-status-fold",
                    "name": "about-only event",
                    "startedAt": "2026-01-01T00:00:00Z", **event}) + "\n")
    proj.rebuild_all(str(journal), confirm_destructive=True)
    rows = proj.g.query(
        "MATCH (e:Event {eventId:'ev-status-fold'}) RETURN e.eventKind"
    ).result_set
    assert rows and rows[0][0] == "venture:disbursement", (
        "the rebuild did not replay the journal it refused to refuse")



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


def test_a_bare_mention_mints_nothing_and_drops_its_link(sdk):
    """#3590 D1 (RULED by the owner, 2026-10-26): a mention does not create.

    ``create_event(subject=…, object=…, uses=[…])`` names three things in
    prose. D1's ruling sentence: a name written inside a statement is a
    *mention*, the ``aboutObject``/``aboutSubject`` **link** this graph already
    defines a mention to be, and never a creation — 'so the entity set grows
    only from an explicit registration (``ObjectRegistered`` /
    ``SubjectAdded``) that mints the id; an unresolvable mention drops its
    link, records the miss, and mints nothing.'

    So the door must journal NO registration, grow NO node, and the dropped
    link must be RECORDED (the exempt ``unfolded-mention``), not silent.

    ⛔ This replaces the pin this file carried one commit earlier,
    ``test_the_event_door_registers_every_name_its_record_mentions``, which
    required the door to MINT and register every mention — the exact arm the
    ruling forbids, i.e. the entity set growing out of prose again. Do not
    restore it: the way to get the attribution edge is an explicit
    ``create_entity`` (the second half below), never a mention that creates.

    Mutations that must red this: restoring the door's mention create arm →
    the no-node / no-registration assertions; dropping the exemption → the
    ``unfolded-mention`` records disappear and the rebuild refuses.
    """
    from tortoise.projection.nonfolded import (
        SHAPE_UNFOLDED_MENTION,
        collect_non_folded,
        refused_events,
    )

    client, events = sdk
    proj = client._get_proj()

    with collect_non_folded() as entries:
        client.create_event("meeting with a tool", "meeting",
                            subject="Agent Pi",
                            object="Docs Thing",
                            uses=[{"name": "Tool X", "kind": "core:tool"}],
                            _server_id="ev-mentions",
                            is_episodic=False)

    # 1. NOTHING was created — the mention is not a creation.
    for label, name in (("Subject", "Agent Pi"), ("Object", "Docs Thing"),
                        ("Object", "Tool X")):
        assert _name_ids(proj, label, name) == [], (
            f"the door minted a {label} out of a bare mention (D1)")

    # 2. NOTHING was journaled: a registration record per mention is unbounded
    #    journal growth out of prose. (Nothing was created above either, so the
    #    whole journal must carry no registration at all yet.)
    assert [r for r in _journal(events)
            if r.get("type") in ("ObjectRegistered", "SubjectAdded")] == [], (
        "the mention JOURNALED an entity registration it must not have created")

    # 3. …and the LOSS is recorded, not silent: three mentions, three exempt
    #    non-folds, none of them a refusal.
    shapes = [e.shape for e in entries]
    assert shapes.count(SHAPE_UNFOLDED_MENTION) == 3, shapes
    assert not refused_events(entries), (
        "a mention miss was classified as a producer omission that fails the "
        f"run: {[str(e) for e in entries]}")

    # 4. No attribution edge — there is no node to wire it to.
    for rel in ("performs", "produces", "uses"):
        n = proj.g.query(
            f"MATCH (e:Event {{eventId:'ev-mentions'}})-[:{rel}]->() "
            "RETURN count(*)").result_set
        assert n[0][0] == 0, f"an edge `{rel}` appeared from a bare mention"

    # 5. The journal still replays (attribution is not identity), and replay
    #    agrees with live: neither pass fabricates the mention.
    proj.rebuild_all(str(events), confirm_destructive=True)
    for label, name in (("Subject", "Agent Pi"), ("Object", "Docs Thing"),
                        ("Object", "Tool X")):
        assert _name_ids(proj, label, name) == [], (
            f"replay MINTED a {label} for a mention — rebuild != live")

    # ── The SUPPORTED way to get the edge: register the target explicitly ──
    # This is D1's other half (and D9's producer duty): the record resolves
    # against entities that EXIST, so the edges land live and survive replay.
    client.create_entity("subject", "Agent Pi", _server_id="id-agent-pi",
                         is_episodic=False)
    client.create_entity("object", "Docs Thing", objectKind="core:doc",
                         _server_id="id-docs-thing", is_episodic=False)
    client.create_entity("object", "Tool X", objectKind="core:tool",
                         _server_id="id-tool-x", is_episodic=False)
    client.create_event("meeting with a tool", "meeting",
                        subject="Agent Pi",
                        object="Docs Thing",
                        uses=[{"name": "Tool X", "kind": "core:tool"}],
                        _server_id="ev-registered",
                        is_episodic=False)

    def _edges() -> dict:
        return {
            "performs": {r[0] for r in proj.g.query(
                "MATCH (s:Subject)-[:performs]->(e:Event {eventId:'ev-registered'}) "
                "RETURN s.id").result_set},
            "produces": {r[0] for r in proj.g.query(
                "MATCH (e:Event {eventId:'ev-registered'})-[:produces]->(o:Object) "
                "RETURN o.id").result_set},
            "uses": {r[0] for r in proj.g.query(
                "MATCH (e:Event {eventId:'ev-registered'})-[:uses]->(o:Object) "
                "RETURN o.id").result_set},
        }

    live = _edges()
    # IDs, not names: comparing names alone would let a rebuild that re-minted
    # a DIFFERENT id for the same name satisfy the assertion.
    assert live == {"performs": {"id-agent-pi"},
                    "produces": {"id-docs-thing"},
                    "uses": {"id-tool-x"}}, live

    # ⛔ MUTATE LIVE STATE the journal cannot reproduce, BEFORE the rebuild: a
    # `rebuild_all` stubbed to a no-op leaves the live graph untouched, so
    # without this the replay half passes on a no-op (the round-2 P1 class).
    proj.g.query(
        "MATCH (o:Object {name:'Docs Thing'}) SET o.name='live-drift'")

    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _edges() == live, (
        "the edges of a REGISTERED mention did not survive replay")
    assert not _name_ids(proj, "Object", "live-drift"), (
        "the live-only mutation survived the rebuild — `rebuild_all` did not "
        "re-derive the graph from the journal, so `_edges()` equality proves "
        "nothing about replay")


# ══════════════════════════════════════════════════════════════════════
# Round-5 review pins — each is RED on the tree the review reported against
# ══════════════════════════════════════════════════════════════════════

def test_the_create_resolves_and_writes_under_one_holder_lock(sdk, monkeypatch):
    """#3590 review (finding 1): the lock must span the WRITE, not only the read.

    ``resolve_or_mint`` is check-then-act: it looks for live holders and, when
    there are none, mints an id. The holder becomes visible only at the caller's
    ``MERGE``, so releasing the lock before that write leaves the exact window
    the lock claims to close — two threads both see zero live holders, both
    mint, and one name ends up with two live carriers (after which every later
    create of it raises ``AmbiguousEntityName`` and there is no by-name repair
    path).

    ASSERTED as the invariant itself — the write executes INSIDE the
    ``(label, name)`` critical section — rather than by racing two threads, so
    the pin is deterministic. On the pre-fix tree the lock is already released
    when ``_create_entity`` runs, so ``locked()`` is False and this fails.
    """
    from tortoise.entity_identity import _holder_lock

    client, _ = sdk
    seen: dict = {}
    original = TortoiseSDK._create_entity

    def _spy(self, label, entity_id, *args, **kwargs):
        lock = _holder_lock(label, "Locked Name")
        # `RLock.locked()` only exists from 3.14; `_is_owned()` is the same-thread
        # question this pin asks (the spike runs on the creating thread).
        seen["locked"] = (lock._is_owned() if hasattr(lock, "_is_owned")
                          else lock.locked())
        seen["entity_id"] = entity_id
        return original(self, label, entity_id, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, "_create_entity", _spy)
    client.create_entity("object", "Locked Name", objectKind="core:strategy",
                         is_episodic=False)
    assert seen.get("locked") is True, (
        "the mint and the write that makes it visible are not ONE critical "
        "section — a concurrent create can pass the same empty-holder check "
        "(#3590 review, finding 1)")


def test_a_rename_onto_a_held_name_is_refused(sdk):
    """#3590 review (finding 2): the new public refusal had no pin at all.

    ``NameAlreadyHeld`` is a NEW publicly-observable refusal on
    ``update_entity``: a rename onto a name another live entity holds would make
    the natural key ambiguous with no by-name repair path, so it is refused
    before anything is written. Nothing pinned it (``git grep
    NameAlreadyHeld`` found no test), and an unpinned refusal is one refactor
    away from silently disappearing.
    """
    from tortoise.exceptions import NameAlreadyHeld

    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Held", objectKind="core:strategy",
                         _server_id="id-held", is_episodic=False)
    mover = client.create_entity("object", "Mover", objectKind="core:strategy",
                                 _server_id="id-mover",
                                 is_episodic=False)["node"]["id"]

    with pytest.raises(NameAlreadyHeld):
        client.update_entity(mover, name="Held")

    # The refusal wrote NOTHING: the mover keeps its own name.
    assert _name_ids(proj, "Object", "Mover") == [mover], (
        "the refused rename still moved the name")
    assert _name_ids(proj, "Object", "Held") == ["id-held"]

    # …and the guard is not a blanket block: a rename onto a FREE name works.
    client.update_entity(mover, name="Freed")
    assert _name_ids(proj, "Object", "Freed") == [mover]


def test_a_rename_onto_an_id_less_live_holder_is_refused(sdk):
    """#9876 review F1: the rename guard counts holders, not truthy ids.

    ``live_name_holder_ids`` is row-per-holder and yields ``None`` for an
    id-less LIVE row ON PURPOSE — its docstring says filtering the ``None`` out
    "would silently pick one". The guard filtered it, so a rename onto a name
    held by an id-less carrier LANDED and left two live carriers of one name:
    every later by-name resolve then reports ``ambiguous-entity-name``, which is
    NON-exempt, so ``rebuild_all`` stops over the name this door made ambiguous
    — with no by-name repair path.

    Mutation that must red this: restoring ``if h and str(h) != …`` in
    ``TortoiseSDK._refuse_ambiguous_rename``.
    """
    from tortoise.exceptions import NameAlreadyHeld

    client, _ = sdk
    proj = client._get_proj()
    proj.g.query(
        "MERGE (o:Object {name:$n}) SET o.objectKind='core:strategy'",
        params={"n": "Legacy"})
    assert _name_ids(proj, "Object", "Legacy") == [None], (
        "fixture did not build the id-less live holder")
    mover = client.create_entity("object", "Mover", objectKind="core:strategy",
                                 _server_id="id-mover",
                                 is_episodic=False)["node"]["id"]

    with pytest.raises(NameAlreadyHeld) as refused:
        client.update_entity(mover, name="Legacy")
    # The refusal COUNTS the holder it cannot address by id — a message that
    # silently dropped it would report the graph as less ambiguous than it is.
    assert "1 other live Object" in str(refused.value), str(refused.value)

    # Nothing moved: ONE id-less carrier of Legacy (not two) and the mover kept
    # its own name.
    assert _name_ids(proj, "Object", "Legacy") == [None], (
        "the refused rename still landed — the name is now held by two live "
        "carriers, which is the state this guard exists to prevent")
    assert _name_ids(proj, "Object", "Mover") == [mover]


def test_a_create_refusal_records_the_id_less_live_holder_it_counted(sdk):
    """#9876 review (this head): the create-side refusal must COUNT and REPORT
    the same holders — and it must RECORD the refusal at all.

    ``resolve_or_mint`` counted with ``len(holders)`` (correct: an id-less LIVE
    row is a holder) and then built the evidence with
    ``[h for h in holders if h]`` — the exact filter
    ``live_name_holder_ids``'s own docstring forbids ("filtering the ``None``
    out would silently pick one"). Two consequences, both pinned here:

    1. the shipped ``409`` evidence said *"resolves to 1 live carriers"* for a
       graph holding TWO — a report false for the record it describes, one
       module away from the ``NameAlreadyHeld`` fix that LABELS such a holder;
    2. the site raised directly instead of going through ``_refuse``, so it
       recorded NOTHING: R8 requires a refusal to BE a record, and
       ``sdk._connect_issue_objects`` catches this exception and continues, so
       a skipped reference left no structured evidence anywhere.

    Mutation that must red this: restoring ``raise AmbiguousEntityName(…,
    [h for h in holders if h])`` at ``entity_identity.resolve_or_mint``.
    """
    from tortoise.entity_identity import (
        AmbiguousEntityName,
        clear_non_folded_entries,
        non_folded_entries,
    )

    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Shared", objectKind="core:strategy",
                         _server_id="obj-carrier", is_episodic=False)
    # A foreign/raw writer (a bare name-keyed write that mints NO id) opens a
    # second, id-less LIVE carrier of the same name. CREATE, not MERGE: a
    # name-keyed MERGE would MATCH the carrier above and open nothing.
    proj.g.query("CREATE (o:Object {name:$n})", params={"n": "Shared"})
    assert sorted(_name_ids(proj, "Object", "Shared"),
                  key=str) == [None, "obj-carrier"], (
        "fixture did not build the mixed holder set (one id-carrying, one "
        "id-less, both LIVE)")

    clear_non_folded_entries()
    with pytest.raises(AmbiguousEntityName) as refused:
        client.create_entity("object", "Shared", objectKind="core:strategy",
                             is_episodic=False)

    # (1) The evidence names BOTH carriers — the id-less one is labelled, never
    # dropped, so the count in the message is truthful.
    assert len(refused.value.candidate_ids) == 2, refused.value.candidate_ids
    assert set(refused.value.candidate_ids) == {"obj-carrier", "<id-less>"}, (
        "the create refusal counted two live carriers and reported only the "
        f"addressable one: {refused.value.candidate_ids}")
    assert "resolves to 2 live carriers" in str(refused.value), str(refused.value)

    # (2) And the refusal is RECORDED (R8), not merely raised.
    entries = non_folded_entries()
    assert len(entries) == 1, entries
    assert entries[0].label == "Object" and entries[0].name == "Shared"
    assert entries[0].shape == "ambiguous-name"
    assert set(entries[0].candidate_ids) == {"obj-carrier", "<id-less>"}, (
        "the recorded refusal names a holder set the refusal did not count: "
        f"{entries[0].candidate_ids}")


def test_a_fold_refusal_records_the_id_less_holder_it_counted(sdk):
    """#9876 review (this head), fold side: ``resolve_fold_name`` counted the
    id-less LIVE holder and recorded ``candidates`` WITHOUT it.

    ``candidates`` is precisely R8's *"the candidates"*, and the id-less
    carrier is the shape this PR deliberately classifies: a sole live-but-id-less
    holder routes to the NON-exempt ``unregistered-reference`` (it fails the
    run). Recorded as ``candidates=()``, that record said *"refused with no
    candidates"* for a refusal that happened because there was one; for the
    mixed shape it named only the addressable holder, sending the operator to
    repair the wrong thing — and still refusing.

    Mutation that must red this: restoring ``candidates=tuple(ids)`` (the
    truthy-id list) in ``entity_identity.resolve_fold_name``.

    Read through ``collect_non_folded`` — that is the collector
    ``resolve_fold_name`` writes to (it imports ``nonfolded.record_non_folded``,
    which appends to the run-scoped contextvar, NOT to this module's
    ``non_folded_entries()``).
    """
    from tortoise.entity_identity import resolve_fold_name
    from tortoise.projection.nonfolded import (
        SHAPE_AMBIGUOUS_ENTITY_NAME,
        SHAPE_UNREGISTERED_REFERENCE,
        collect_non_folded,
    )

    client, _ = sdk
    proj = client._get_proj()

    # Shape 1: ONE live holder, carrying no id — non-exempt, and the record
    # must NAME it rather than report an empty candidate set.
    proj.g.query("MERGE (o:Object {name:$n})", params={"n": "SoleLegacy"})
    with collect_non_folded() as entries:
        assert resolve_fold_name(proj.g, "Object", "SoleLegacy") is None
    assert [e.shape for e in entries] == [SHAPE_UNREGISTERED_REFERENCE], entries
    assert entries[0].candidates == ("<id-less>",), (
        "the fold recorded a refusal with NO candidates for a name held by one "
        f"live-but-id-less carrier: {entries[0].candidates}")

    # Shape 2: mixed — one id-carrying, one id-less — names BOTH.
    client.create_entity("object", "Mixed", objectKind="core:strategy",
                         _server_id="obj-mixed", is_episodic=False)
    proj.g.query("CREATE (o:Object {name:$n})", params={"n": "Mixed"})
    with collect_non_folded() as entries:
        assert resolve_fold_name(proj.g, "Object", "Mixed") is None
    assert [e.shape for e in entries] == [SHAPE_AMBIGUOUS_ENTITY_NAME], entries
    assert set(entries[0].candidates) == {"obj-mixed", "<id-less>"}, (
        "the fold recorded only the addressable holder of a two-carrier name: "
        f"{entries[0].candidates}")


def test_a_participants_fallback_wires_only_the_resolved_performer(sdk):
    """#3590 review (F2): one mention may not be refused AND broadcast.

    The ``performs`` arm resolves the event's ``subject`` to ONE live holder and
    refuses to guess between carriers; the ``participatesIn`` fallback wired
    EVERY same-name carrier from the same reference. Two live carriers therefore
    produced two links from a reference the fold had just declined to resolve —
    inside the fold this change rewrote.
    """
    client, _ = sdk
    proj = client._get_proj()
    # Two live carriers of one name: the reference is unresolvable by design.
    client.create_entity("subject", "Twinned", _server_id="sub-a",
                         is_episodic=False)
    client.create_entity("subject", "Twinned", _server_id="sub-b",
                         is_episodic=False)
    client.create_event("standup", "meeting", subject="Twinned",
                        _server_id="ev-twinned", is_episodic=False)
    assert {r[0] for r in proj.g.query(
        "MATCH (s:Subject)-[:participatesIn]->(e:Event {eventId:'ev-twinned'})"
        " RETURN s.id").result_set} == set(), (
        "the participants fallback guessed between carriers — the same "
        "reference is refused by `performs` and broadcast here")

    # One live carrier: the edge is wired on THAT id, never re-derived by name.
    client.create_entity("subject", "Solo", _server_id="sub-solo",
                         is_episodic=False)
    client.create_event("solo standup", "meeting", subject="Solo",
                        _server_id="ev-solo", is_episodic=False)
    assert {r[0] for r in proj.g.query(
        "MATCH (s:Subject)-[:participatesIn]->(e:Event {eventId:'ev-solo'})"
        " RETURN s.id").result_set} == {"sub-solo"}


def test_the_bundle_guard_reads_the_live_holder_not_a_terminal_one(sdk):
    """#3590 review (F1): the guard must read the node the WRITE lands on.

    ``_entity_name_holders`` decides the ``#7925`` Phase-1 axes. The create now
    resolves over LIVE carriers only, so a name whose sole carrier is TERMINAL
    mints a fresh live node — reading the axes off the terminal carrier skipped
    arms that exist to catch a write which DOES store its status (fail-open) and
    refused legal direct edges ("resolves to terminal") in the other direction.
    """
    client, _ = sdk
    obj = client.create_entity("object", "Ghost", objectKind="core:strategy",
                               _server_id="id-ghost",
                               is_episodic=False)["node"]["id"]
    client.update_entity(obj, status="superseded")

    _exists, hit, _op, _bad, _carriers, merges, target = \
        client._entity_name_holders("Object", "Ghost")
    assert merges is False, (
        "the guard thinks the write MERGEs onto the terminal carrier, but the "
        "create mints a fresh live node (#3590 review F1)")
    assert target is None and hit is None, (target, hit)


def test_a_buried_id_with_a_same_name_impostor_is_reported(sdk):
    """#3590 review: the presence leg may not resolve a journal id by NAME.

    The retired fallback resolved a journal id the graph does not carry onto a
    SAME-NAME node under a DIFFERENT id, because the graph used to MERGE these
    labels by name. Under the id-keyed model that node is a different
    incarnation, so resolving it reported a faithful graph while the faithful
    node was buried — a false GREEN, the one failure mode ``consistency`` must
    never produce.
    """
    from tortoise.consistency import check_consistency

    client, events = sdk
    proj = client._get_proj()
    journaled = client.create_entity("object", "Twin",
                                     objectKind="core:strategy",
                                     is_episodic=False)["node"]["id"]
    # Bury the journaled id and leave a same-name impostor (a different id) —
    # the exact shape the name fallback used to call CLEAN.
    proj.g.query("MATCH (o:Object {id:$id}) SET o.id='impostor-id'",
                 params={"id": journaled})

    r = check_consistency(str(events / "events.jsonl"), proj)
    presence = {(d["label"], d["id"]) for d in r["divergent_entities"]
                if d["field"] == "presence"}
    assert ("Object", journaled) in presence, (
        "the buried id was resolved by NAME onto the impostor and reported "
        f"CLEAN — presence must be an identity question: {r}")


# ══════════════════════════════════════════════════════════════════════
# Round-5 review, second batch — the MENTION may not mint (F2)
# ══════════════════════════════════════════════════════════════════════

def _status_of(proj, label: str, entity_id: str):
    rows = proj.g.query(
        f"MATCH (n:{label} {{id:$id}}) RETURN n.status",
        params={"id": entity_id}).result_set
    return rows[0][0] if rows else None


def test_a_mention_of_a_superseded_name_does_not_mint_an_incarnation(sdk):
    """#3590 round-5 review F2: attribution may not CREATE an entity.

    ``_register_referenced_entity`` probed LIVE holders only, so an event that
    merely mentioned a name whose only carrier is TERMINAL ran a user-level
    create: it minted a new LIVE incarnation, JOURNALED the registration, and
    folded the attribution onto the ghost. Pre-#3590 the fold's
    ``MERGE (o:Object {name:$name})`` matched the terminal carrier (MERGE matches
    on the pattern's properties only) and created nothing, so this is a
    behaviour CHANGE, not a refactor — and it resurrects a superseded name,
    grows the journal by one record per mention, and makes the next
    ``create_entity`` resolve to the ghost instead of the real incarnation.

    Whether a bare mention should ever create an entity is **D1 on #3590 —
    RULED NO (owner, 2026-10-26)**, so this door mints nothing for any mention.
    The attribution is then dropped as the EXEMPT ``unfolded-mention``, so the
    journal still replays.

    Mutation that must red this: giving the door a create arm for mentions
    again (``create_entity`` per ``subject`` / ``object`` / ``uses[]``) — the
    mutant resurrects the buried name as a NEW live incarnation, journaling a
    registration for it.
    """""
    client, events = sdk
    proj = client._get_proj()
    buried = client.create_entity("object", "Retired",
                                  objectKind="core:strategy",
                                  _server_id="id-retired",
                                  is_episodic=False)["node"]["id"]
    client.update_entity(buried, status="superseded")
    registrations_before = [r for r in _journal(events)
                            if r.get("type") in ("ObjectRegistered",
                                                 "SubjectAdded")]

    client.create_event("ev-after-retire", "meeting", uses=["Retired"],
                        _server_id="ev-after-retire", is_episodic=False)

    # 1. NO second incarnation — live or otherwise.
    assert _name_ids(proj, "Object", "Retired") == [buried], (
        "a MENTION minted a new incarnation of a superseded name — the door "
        "created an entity from attribution (D1: a mention never creates)")
    assert _status_of(proj, "Object", buried) == "superseded", (
        "the mention moved the buried incarnation's state")

    # 2. Nothing was journaled for the mention: a registration record per
    #    mention is unbounded journal growth over a capture loop.
    registrations_after = [r for r in _journal(events)
                           if r.get("type") in ("ObjectRegistered",
                                                "SubjectAdded")]
    assert registrations_after == registrations_before, (
        "the mention JOURNALED an entity registration it should not have "
        f"created: {registrations_after[len(registrations_before):]}")

    # 3. The link was DROPPED (recorded), never fabricated onto a ghost.
    assert proj.g.query(
        "MATCH (e:Event {eventId:'ev-after-retire'})-[:uses]->(o) "
        "RETURN o.id").result_set == [], (
        "the attribution edge was wired to an entity the record never named")

    # 4. …and the journal still replays: the drop is the EXEMPT mention shape.
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _name_ids(proj, "Object", "Retired") == [buried], (
        "replay disagreed with the live pass on the mention")
    assert _status_of(proj, "Object", buried) == "superseded"


def test_a_lifecycle_event_on_an_unresolvable_object_is_not_a_green_run(sdk):
    """#3590: a STATE-MOVING fold miss with no carrier at all fails the run.

    The ``unfolded-mention`` exemption's recorded bound is that a fold which must
    MOVE an entity's state can never take it. ``about_entities`` is the loose
    list (see the test above); the event's EXPLICIT ``object`` is the fold's
    declared target, so when nothing at all holds that name the miss is the
    NON-exempt ``unregistered-reference``: the producer violated D9 (the doors
    register before appending) and a green pass would report success over a fold
    that wrote nothing.

    Mutation that must red this: passing ``mention=True`` unconditionally at the
    status fold's explicit-target re-resolve (``_fold_object_status``).
    """
    from tortoise.projection import _replay_write_scope
    from tortoise.projection.nonfolded import (
        SHAPE_UNREGISTERED_REFERENCE,
        collect_non_folded,
        refused_events,
    )

    client, _ = sdk
    proj = client._get_proj()
    with _replay_write_scope(), collect_non_folded() as entries:
        proj.apply({"type": "EventRecorded", "eventId": "ev-life-miss",
                    "event": {"eventId": "ev-life-miss",
                              "eventKind": "venture:disbursement",
                              "object": "Nothing Registered Anywhere"}})
    shapes = [e.shape for e in entries]
    assert SHAPE_UNREGISTERED_REFERENCE in shapes, (
        "a lifecycle event whose declared target no entity holds was not "
        f"recorded as the non-exempt identity miss: {shapes}")
    assert refused_events(entries), (
        "a state-moving fold miss FAILED to fail the run — a lifecycle event "
        "that moved nothing passed: "
        f"{[str(e) for e in entries]}")


def test_a_lifecycle_event_on_a_terminal_only_object_is_a_recorded_no_op(
        sdk, tmp_path: Path):
    """#3590 round-5 review F2 (the other half): a BURIED target is not a miss.

    The door may not register a mention of a name an existing carrier holds
    (above), and the fold may not resurrect a superseded incarnation (R5) — so
    the two together must NOT make the journal un-rebuildable. They would if the
    fold classified this as the non-exempt ``unregistered-reference``: the entity
    IS registered, the incarnation is merely buried, which is a deliberate no-op
    rather than a missing creation event. Measured before the split: a journal
    containing ``ObjectRegistered → EntityMutated(status=superseded) →
    EventRecorded(lifecycle, object=<that name>)`` raised
    ``NonFoldedEventsError`` on ``rebuild_all`` — the durable-copy regression
    this framework exists to prevent — while replaying cleanly on the tree that
    minted the ghost instead. It is recorded (``cause=no-live-holder``) and named
    by the run boundary, so the no-op is not silent.

    Mutation that must red this: dropping the ``all_name_holder_ids`` probe at
    the status fold's explicit-target re-resolve (making every miss non-exempt).
    """
    from tortoise.projection.nonfolded import (
        SHAPE_UNFOLDED_MENTION,
        collect_non_folded,
        refused_events,
    )

    client, events = sdk
    proj = client._get_proj()
    buried = client.create_entity("object", "Buried",
                                  objectKind="core:strategy",
                                  _server_id="id-buried",
                                  is_episodic=False)["node"]["id"]
    client.update_entity(buried, status="superseded")

    # The DOOR writes the event (no ghost minted, nothing registered) and the
    # miss it records is the bounded, reported one.
    with collect_non_folded() as entries:
        client.create_event("ev-life-buried", "venture:disbursement",
                            object="Buried", _server_id="ev-life-buried",
                            is_episodic=False)
    shapes = [e.shape for e in entries]
    assert SHAPE_UNFOLDED_MENTION in shapes, (
        f"the buried target was not recorded as the bounded no-op: {shapes}")
    assert not refused_events(entries), (
        "a buried (not missing) target was classified as a producer omission "
        f"and failed the run: {[str(e) for e in entries]}")
    assert any("no-live-holder" in str(e.detail) for e in entries), (
        "the record does not name WHY it missed — a terminal-only holder reads "
        f"the same as an absent one: {[str(e) for e in entries]}")
    assert _name_ids(proj, "Object", "Buried") == [buried], (
        "the lifecycle target's mention minted a ghost incarnation")
    assert _status_of(proj, "Object", buried) == "superseded", (
        "the fold MOVED a superseded incarnation's state — that is a "
        "resurrection, which the identity model forbids (R5)")

    # The property that matters: the journal REPLAYS.
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _name_ids(proj, "Object", "Buried") == [buried], (
        "replay disagreed with the live pass on the buried target")
    assert _status_of(proj, "Object", buried) == "superseded"


def test_a_lifecycle_event_on_a_doubly_held_name_is_not_a_green_run(sdk):
    """#3590 review, finding 1 (fail-OPEN): the THIRD arm of the exemption.

    The two tests above pin the ends — no carrier at all (non-exempt refusal)
    and terminal-only carriers (bounded no-op). The middle case was pinned
    NOWHERE and it is the dangerous one: same-name coexistence is LEGAL (D2 /
    #8864), so a lifecycle event whose declared ``object`` is held by TWO OR
    MORE *live* entities can be written through the ordinary door
    (``_register_referenced_entity`` returns early for a name any carrier
    holds).

    FAILS IF: the exemption is granted from the ANY-status probe
    (``all_name_holder_ids``) instead of the LIVE count — measured at the head
    this test was written for: ``mention=True`` for two live holders, so the
    re-resolve recorded the EXEMPT ``unfolded-mention``, ``object_id`` stayed
    ``None``, no status write happened, ``refused_events()`` was empty, and the
    run was GREEN over a state-moving fold that moved nothing. The recorded
    bound is explicit: "a fold that MUST MOVE an entity's state can never take
    this exemption" (``identity-decision.md``).
    """
    from tortoise.projection import _replay_write_scope
    from tortoise.projection.nonfolded import (
        SHAPE_AMBIGUOUS_ENTITY_NAME,
        collect_non_folded,
        refused_events,
    )

    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Dup", _server_id="id-dup-live-a",
                         is_episodic=False)
    client.create_entity("object", "Dup", _server_id="id-dup-live-b",
                         is_episodic=False)
    assert sorted(_name_ids(proj, "Object", "Dup")) == [
        "id-dup-live-a", "id-dup-live-b"], (
        "fixture did not produce the legal two-LIVE-carrier state")

    with _replay_write_scope(), collect_non_folded() as entries:
        proj.apply({"type": "EventRecorded", "eventId": "ev-life-amb",
                    "event": {"eventId": "ev-life-amb",
                              "eventKind": "venture:disbursement",
                              "object": "Dup"}})
    shapes = [e.shape for e in entries]
    assert SHAPE_AMBIGUOUS_ENTITY_NAME in shapes, (
        "a lifecycle event whose declared target is held by 2+ LIVE entities "
        "took the EXEMPT mention shape instead of the non-exempt identity "
        f"miss: {shapes}")
    assert refused_events(entries), (
        "a state-moving fold miss over an ambiguous target FAILED to fail "
        f"the run: {[str(e) for e in entries]}")
    # Nothing was moved: the ambiguity is not silently resolved by picking one.
    for oid in ("id-dup-live-a", "id-dup-live-b"):
        assert _status_of(proj, "Object", oid) != "in_progress", (
            "the fold MOVED one carrier's state out of an ambiguous target: "
            f"{oid}")


def test_an_about_target_that_folds_cannot_mask_the_explicit_targets_miss(sdk):
    """#3590 review, finding 1 (fail-OPEN): a folded ATTRIBUTION target must
    not silence the identity miss of the record's OWN declared target.

    ``_fold_object_status`` re-resolves the event's explicit ``object``
    non-exemptly when it has no carrier — *"a fold that must MOVE an entity's
    state can never take this exemption"* (``identity-decision.md``). That
    re-resolve was also gated on ``not matched_any``, borrowed from the #2729
    *report* rule ("a target missing while ANOTHER target folded is not a real
    signal"). The borrowing inverts the meaning: when the branch is reached
    ``object_id is None``, so ``_targets()`` never added the EXPLICIT target —
    every row that can set ``matched_any`` comes from an ``about_entities``
    target. So one registered attribution target folding was enough to skip
    the miss, and the run went GREEN over a declared lifecycle fold that wrote
    nothing for its own target.

    FAILS IF: ``not matched_any`` is restored to the re-resolve gate
    (measured at ``6365149a4``: the only record was the EXEMPT
    ``unfolded-mention`` from ``_event_plain_merge`` and
    ``refused_events(entries)`` was empty). The middle assertion is what keeps
    this test's premise honest — if the attribution target ever stops folding,
    ``matched_any`` would be False and the pre-fix code would refuse too, so
    the test would pass for the wrong reason.
    """
    from tortoise.projection import _replay_write_scope
    from tortoise.projection.nonfolded import (
        SHAPE_UNREGISTERED_REFERENCE,
        collect_non_folded,
        refused_events,
    )

    client, _ = sdk
    proj = client._get_proj()
    # A registered Object of a kind `venture:disbursement` DECLARES — this is
    # the attribution target that folds (`matched_any`).
    tranche = client.create_entity("object", "Folded Tranche",
                                   objectKind="venture:tranche",
                                   _server_id="id-folded-tranche",
                                   is_episodic=False)["node"]["id"]

    with _replay_write_scope(), collect_non_folded() as entries:
        proj.apply({"type": "EventRecorded", "eventId": "ev-life-masked",
                    "event": {"eventId": "ev-life-masked",
                              "eventKind": "venture:disbursement",
                              # the fold's DECLARED target — nothing holds it
                              "object": "Nothing Registered Anywhere",
                              # the ATTRIBUTION target — registered, folds
                              "aboutEntities": ["Folded Tranche"]}})

    assert _status_of(proj, "Object", tranche) == "released", (
        "the attribution target did not fold, so this fixture cannot exercise "
        "the mask — `matched_any` stayed False and the assertion below would "
        "pass on the pre-fix code for the wrong reason")

    shapes = [e.shape for e in entries]
    assert SHAPE_UNREGISTERED_REFERENCE in shapes, (
        "a lifecycle event whose DECLARED target no entity holds was masked by "
        "an attribution target that folded — the explicit target's non-exempt "
        f"identity miss was never recorded: {shapes}")
    assert refused_events(entries), (
        "a state-moving fold miss was silenced by an unrelated folded target "
        f"and the run stayed GREEN: {[str(e) for e in entries]}")


def test_the_event_api_door_is_the_recorded_producer_that_loses_its_mentions(
        sdk, tmp_path: Path):
    """#3590 round-5 review P1-3: the legacy door's mention loss is recorded.

    Both event doors register NOTHING themselves — ``EventAPI.add_event``
    has always done so, and since **D1 was RULED (2026-10-26: a bare mention
    does not create an entity)** the SDK's ``create_event`` does the same
    (``test_a_bare_mention_mints_nothing_and_drops_its_link``). ``add_event``
    has a LIVE in-repo caller: ``tortoise.ingest --capture-metadata`` writes
    ``sessionCaptured`` through it with ``subject="pi-agent"`` and
    ``uses=[{"name": "tortoise-capture", "kind": "skill"}]``. Neither name is
    registered by the DOOR, so the fold resolves them against no live holder and
    the ``performs`` / ``uses`` edges are never wired — pre-#3590 they existed,
    because the retired fold MERGEed a stub whose id WAS the name.

    ⛔ **The PRODUCER is a different surface, and there D1's remedy applies.**
    A caller that WANTS an attribution edge registers the target explicitly
    (``ObjectRegistered`` / ``SubjectAdded`` — D1's own words), and that is what
    ``tortoise/ingest.py`` now does for its ``uses[]`` skill before emitting
    (``api.add_object("tortoise-capture", "skill")``): #125 pins that edge, and
    while the producer left it to the fold the pin was red (CI run 40917794096).
    What the producer still loses, and this file records, is its
    ``subject="pi-agent"`` mention.

    This is the acceptance pin for D1's SECOND OPTION, now the ruling:
    registering at ``add_event`` would take the minting arm at the door every
    legacy pipeline writes through, which D1 forbids. What is bought instead is
    that the loss is **recorded, bounded and identical on both passes** — an
    EXEMPT ``unfolded-mention`` naming the cause, not a silent drop behind a
    green run (which is the class ``SHAPE_UNREGISTERED_REFERENCE`` exists to
    prevent). The guarantee is stated in
    ``entity_identity.resolve_fold_name``'s docstring and this test keeps that
    statement honest.

    Mutations that must red this:
      * classifying the miss as the NON-exempt shape (``refused_events`` below
        goes non-empty — the green run would be the bug);
      * dropping the exemption (``rebuild_all`` raises ``NonFoldedEventsError``);
      * registering the mentions at ``add_event`` (the no-minted-entity and
        no-edge assertions below — that is D1's forbidden arm).
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.projection.nonfolded import (
        SHAPE_UNFOLDED_MENTION,
        collect_non_folded,
        refused_events,
    )

    client, events = sdk
    proj = client._get_proj()
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=proj)

    # The producer's OWN call shape (`tortoise/ingest.py`, --capture-metadata).
    with collect_non_folded() as entries:
        api.add_event("ev-capture", "sessionCaptured",
                      subject="pi-agent",
                      object_name="source-abc", object_type="Document",
                      uses=[{"name": "tortoise-capture", "kind": "skill"}])

    shapes = [e.shape for e in entries]
    assert shapes.count(SHAPE_UNFOLDED_MENTION) == 2, (
        "expected the mentioned subject AND the uses[] name to be recorded as "
        f"the bounded exemption; got {shapes}")
    assert not refused_events(entries), (
        "the recorded degradation was classified as a producer omission that "
        f"fails the run: {[str(e) for e in entries]}")
    assert all("no-live-holder" in str(e.detail) for e in entries), (
        "the record does not say WHY it missed: "
        f"{[str(e) for e in entries]}")

    # The loss itself, stated exactly: nothing minted (a mention is not a
    # create — D1) and therefore no attribution edges.
    assert _name_ids(proj, "Subject", "pi-agent") == [], (
        "`add_event` minted an entity out of a mere mention — that is D1's "
        "minting arm, adopted at a legacy door")
    assert _name_ids(proj, "Object", "tortoise-capture") == [], (
        "`add_event` minted the uses[] name")
    for rel in ("performs", "uses"):
        n = proj.g.query(
            f"MATCH (e:Event {{eventId:'ev-capture'}})-[:{rel}]->() "
            "RETURN count(*)", params={}).result_set
        assert n[0][0] == 0, (
            f"an edge `{rel}` appeared — a registration was taken for a "
            f"mention, which is the arm D1 forbids")

    # …and the point of the exemption: the journal still REPLAYS (a mention
    # miss is attribution, not an unregistered identity).
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _name_ids(proj, "Subject", "pi-agent") == []

    # The documented guarantee names the producer it costs (P1-3's deliverable).
    from tortoise.entity_identity import resolve_fold_name
    doc = resolve_fold_name.__doc__ or ""
    assert "capture-metadata" in doc and "ingest" in doc, (
        "the exemption no longer names the live in-repo producer that keeps "
        "losing these edges — the recorded guarantee was deleted")


def test_the_event_api_registration_door_is_idempotent_on_a_live_name(
        sdk, tmp_path: Path):
    """#3590 review (finding 1): ``add_object``/``add_subject`` must not re-mint.

    The fold's MERGE key is ``{id}``, so the duplicate suppression the old
    name-keyed MERGE gave **by construction** is gone (Cypher ``MERGE`` creates
    whenever the exact pattern is absent) and this repo declares range indexes,
    not a uniqueness constraint on ``Object.id``/``Subject.id``. The registration
    emitters minted a fresh ``ulid()`` per call and never consulted
    ``entity_identity``, so a routine re-run of the v1 extractor left the name
    with TWO live carriers — after which every by-name read refuses
    (``AmbiguousEntityName``), ``create_entity`` raises, and
    ``update_entity(name=…)`` raises ``NameAlreadyHeld``, for a name the
    producer registered legitimately.

    FAILS IF: the emitters go back to ``ulid()``. On that tree the two calls
    return different ids and the fold writes two nodes, so both the id assertion
    and the length assertion red — this is the ``#452`` idempotent re-mention,
    at the door D1 (``identity-decision.md``) names as the one that exists to
    grow the entity set.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    client, events = sdk
    proj = client._get_proj()
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=proj)

    first = api.add_object("Door Object")
    second = api.add_object("Door Object")
    assert first == second, (
        "a re-registration of a live name minted a SECOND id — the fold MERGEs "
        f"by id, so the name now has two live holders: {first!r} then {second!r}")
    assert _name_ids(proj, "Object", "Door Object") == [first], (
        "the door wrote more than one Object node for one live name")

    first_s = api.add_subject("Door Subject")
    second_s = api.add_subject("Door Subject")
    assert first_s == second_s, (first_s, second_s)
    assert _name_ids(proj, "Subject", "Door Subject") == [first_s]


def test_the_event_api_door_still_takes_an_explicit_id_and_refuses_ambiguity(
        sdk, tmp_path: Path):
    """The two bounds of the registration door's resolve-or-mint.

    ``explicit_id`` must still win OUTRIGHT — ``mining``/``docs_miner`` pass
    their own canonical id and are already idempotent that way, so routing the
    door through ``resolve_or_mint`` may not override them. And a name two LIVE
    entities hold must REFUSE rather than mint a third carrier: same-name
    coexistence is legal (D2) but a name-keyed door may not pick a carrier for
    the caller (route-then-refuse, R8) — the disposition ``create_entity``
    already takes.

    FAILS IF: the explicit-id arm is dropped (the assert on ``oid`` reds), or
    the ambiguity arm mints a third node (``AmbiguousEntityName`` is not
    raised and the count becomes three).
    """
    from tortoise.api import EventAPI
    from tortoise.entity_identity import AmbiguousEntityName
    from tortoise.log import EventLog

    client, events = sdk
    proj = client._get_proj()
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=proj)

    oid = api.add_object("Mined Object", id="mined-object-1")
    assert oid == "mined-object-1", oid
    assert _name_ids(proj, "Object", "Mined Object") == ["mined-object-1"]

    client.create_entity("object", "Door Dup", _server_id="dup-a",
                         is_episodic=False)
    client.create_entity("object", "Door Dup", _server_id="dup-b",
                         is_episodic=False)
    assert sorted(_name_ids(proj, "Object", "Door Dup")) == ["dup-a", "dup-b"]

    with pytest.raises(AmbiguousEntityName):
        api.add_object("Door Dup")
    assert len(_name_ids(proj, "Object", "Door Dup")) == 2, (
        "the door minted a THIRD carrier of a doubly held name instead of "
        "refusing it")


def test_a_noop_name_rewrite_on_a_doubly_held_name_is_not_refused(sdk):
    """#3590 review (finding 2): a rename that moves nothing is not a rename.

    ``_refuse_ambiguous_rename`` fires on *any* truthy string ``name`` in
    ``props``, and a read-modify-write caller that re-sends the entity's
    CURRENT name (the ordinary ``update_entity(id, name=<unchanged>, …)``
    shape) gets a hard ``NameAlreadyHeld`` whenever ANOTHER live carrier also
    holds that name — even though no name moved. The two-carrier state is
    LEGAL (D2) and this PR's own fixtures build it, so the pre-fix guard made a
    legal graph un-updatable through the public door (reached by
    ``tortoise_update_entity`` and the hosted PATCH surface, both of which
    forward a caller props bag).

    FAILS IF: the no-op arm is removed — every assertion below that expects the
    update to SUCCEED raises ``NameAlreadyHeld`` instead. The negative control
    is in the same test: a real rename ONTO the other carrier is still refused,
    so the fix narrows the gate by the no-op case only.
    """
    from tortoise.exceptions import NameAlreadyHeld

    client, _ = sdk
    proj = client._get_proj()
    client.create_entity("object", "Dup", _server_id="id-noop-a",
                         is_episodic=False)
    client.create_entity("object", "Dup", _server_id="id-noop-b",
                         is_episodic=False)

    # A read-modify-write re-sending the CURRENT name changes only the title.
    node = client.update_entity("id-noop-a", name="Dup", title="Edited")
    assert node.get("name") == "Dup", node
    assert node.get("title") == "Edited", (
        "the property half of the write was dropped: " + repr(node))
    assert sorted(_name_ids(proj, "Object", "Dup")) == [
        "id-noop-a", "id-noop-b"], (
        "the no-op re-write moved a name it did not change")

    # …and the gate it narrows is STILL a gate: a real move onto a held name.
    client.create_entity("object", "Mover", _server_id="id-noop-mover",
                         is_episodic=False)
    with pytest.raises(NameAlreadyHeld):
        client.update_entity("id-noop-mover", name="Dup")
    assert _name_ids(proj, "Object", "Mover") == ["id-noop-mover"], (
        "a refused rename still moved the name")


def test_the_node_id_helper_counts_holders_not_truthy_ids(sdk):
    """#9876 review P2: ``tests/_node_ids.node_id`` counted TRUTHY ids.

    ``ids = [r[0] for r in rows if r[0]]`` then ``assert len(ids) == 1``. The
    query is row-per-holder and an id-less LIVE legacy/hosted stub holder
    (``MERGE (o:Object {name:$n})`` mints those) contributes a row whose value
    is ``None`` — so for one id-carrying **plus** one id-less live carrier, the
    filter dropped the second holder and the helper returned "the" id as if the
    name were singly held. That is verbatim the *"count holders, not truthy
    ids"* defect this PR fixed at ``resolve_or_mint``, ``resolve_fold_name`` and
    ``_refuse_ambiguous_rename``; in a TEST HELPER it makes every future pin
    that asserts single-holder coexistence pass on the duplicate-carrier graph
    it exists to catch.

    FAILS IF: ``if r[0]`` is restored (row 1: the mixed shape returns an id
    instead of raising) or the lone-id-less arm is dropped (row 2 returns
    ``None`` instead of raising).
    """
    from _node_ids import node_id

    client, _ = sdk
    proj = client._get_proj()

    # 1. the mixed D2 shape: one addressable + one id-less LIVE carrier.
    held = client.create_entity("object", "Held", objectKind="core:strategy",
                                is_episodic=False)["node"]["id"]
    proj.g.query("CREATE (o:Object {name:$n}) SET o.objectKind='core:strategy'",
                 params={"n": "Held"})
    assert sorted(_name_ids(proj, "Object", "Held"), key=str) == sorted(
        [held, None], key=str), "fixture did not build the mixed holder shape"
    with pytest.raises(AssertionError) as mixed:
        node_id(proj, "Object", "Held")
    # The count is of HOLDERS over an ANY-status query, so the message does not
    # say "live" (#9876 review round 14, cosmetic): a live + a superseded twin
    # used to report "2 live holders", which misdescribed the shape an operator
    # is being asked to diagnose. The assertion still pins the COUNT.
    assert "2 holder(s)" in str(mixed.value), str(mixed.value)

    # 2. a LONE id-less holder: one holder, but nothing addressable to return.
    proj.g.query("CREATE (o:Object {name:$n})", params={"n": "Bare"})
    with pytest.raises(AssertionError) as bare:
        node_id(proj, "Object", "Bare")
    assert "has no id" in str(bare.value), str(bare.value)

    # 3. …and the guard is not a blanket block: a singly-held addressable name
    #    still resolves, so the fix narrows the helper by the failure cases.
    solo = client.create_entity("object", "Solo", is_episodic=False)["node"]["id"]
    assert node_id(proj, "Object", "Solo") == solo


def test_the_rename_door_locks_the_guard_and_the_write_together(sdk, monkeypatch):
    """#9876 review (P2): the rename door is check-then-act unless it takes the
    create lock.

    ``_refuse_ambiguous_rename`` reads the live holder set, and the label loop
    that follows it writes ``SET n += $props`` — the write that makes the NEW
    holder visible. Two graph round trips, no lock between them. Interleaved with
    a create (which DOES hold ``entity_create_lock``) a rename lands a second
    live carrier of one name: the name is then create-poisoned, every by-name
    resolve refuses ``AmbiguousEntityName``, and ``resolve_fold_name`` records
    the NON-exempt ``ambiguous-entity-name`` shape — so ``rebuild_all`` stops
    over a name this door split.

    Pinned structurally because the race itself is not reproducible on demand:
    the door must (a) acquire the same ``(label, name)`` lock the create takes,
    for both identity labels, and (b) still hold it at the probe AND at the
    journaling that follows the write.

    FAILS IF the lock is dropped from ``_update_entity`` (assert (a)), or is
    taken and released around the guard instead of around guard+write (assert
    (b) at the ``journal`` site).
    """
    from tortoise import entity_identity

    client, _ = sdk
    proj = client._get_proj()
    oid = client.create_entity("object", "Old Name", objectKind="core:strategy",
                               is_episodic=False)["node"]["id"]

    real_holder_lock = entity_identity._holder_lock
    taken: list[tuple[str, str]] = []

    def recording_holder_lock(label, name):
        taken.append((label, name))
        return real_holder_lock(label, name)

    monkeypatch.setattr(entity_identity, "_holder_lock", recording_holder_lock)

    owned_at: dict[str, bool] = {}
    real_holders = entity_identity.live_name_holder_ids

    def probing(g, label, name):
        owned_at[f"probe:{label}"] = real_holder_lock(label, name)._is_owned()
        return real_holders(g, label, name)

    monkeypatch.setattr(entity_identity, "live_name_holder_ids", probing)

    real_journal = TortoiseSDK._journal_entity_mutation

    def journaling(self, *args, **kwargs):
        # The journal append is the statement immediately AFTER the label loop's
        # `SET n += $props`, so "lock still owned here" is "the write happened
        # inside the critical section".
        owned_at["journal"] = real_holder_lock("Object", "New Name")._is_owned()
        return real_journal(self, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, "_journal_entity_mutation", journaling)

    client.update_entity(oid, name="New Name")

    assert ("Object", "New Name") in taken and ("Subject", "New Name") in taken, (
        "the rename door took no create lock — a create can mint and write a "
        f"carrier of 'New Name' between the guard and the write: {taken}")
    assert owned_at.get("probe:Object") is True, (
        "the ambiguity probe ran OUTSIDE the (label, name) lock")
    assert owned_at.get("journal") is True, (
        "the lock was released before the rename's write — the guard-then-write "
        "window is still open")
    # the rename actually happened, so the pin is not vacuous
    assert _name_ids(proj, "Object", "New Name") == [oid]


def test_the_fold_side_subject_stub_takes_the_create_lock(sdk, monkeypatch):
    """#9876 review, second instance: ``_mint_subject_stub`` is the last
    name-keyed WRITE, and it must take the create lock too.

    The create door is id-keyed now: ``resolve_or_mint`` mints an opaque id and
    the write MERGEs on ``{id}``. The stub MERGEs on ``{name}`` and, ON CREATE,
    stamps ``id = name``. The two no longer converge by construction, so a stub
    landing between a create's resolve and its write leaves two LIVE carriers of
    one name — the ambiguity class above. Under the shared lock both orderings
    converge instead: stub first ⇒ the create's resolve finds the stub as the
    single live holder and adopts it; create first ⇒ the stub's name-keyed MERGE
    matches that node and creates nothing.

    FAILS IF the ``entity_create_lock("Subject", name)`` is removed from
    ``projection/edges.py::_mint_subject_stub`` (no acquisition is recorded).
    """
    from tortoise import entity_identity
    from tortoise.projection.edges import _mint_subject_stub

    client, _ = sdk
    proj = client._get_proj()

    real_holder_lock = entity_identity._holder_lock
    taken: list[tuple[str, str]] = []

    def recording_holder_lock(label, name):
        taken.append((label, name))
        return real_holder_lock(label, name)

    monkeypatch.setattr(entity_identity, "_holder_lock", recording_holder_lock)

    _mint_subject_stub(proj.g, "Unregistered Mention")

    assert ("Subject", "Unregistered Mention") in taken, (
        "the fold-side Subject stub minted a name-keyed node with no create "
        f"lock — a concurrent create can mint beside it: {taken}")
    assert _name_ids(proj, "Subject", "Unregistered Mention") == [
        "Unregistered Mention"], "the stub mint did not happen — pin is vacuous"


# ── the PROSE may not argue for the arm the code deleted ─────────────────
_SRC = Path(__file__).resolve().parents[1] / "tortoise"

#: The retired mechanism, stated as CURRENT fact. Every arm names the
#: pre-#3590 world in which a name-keyed MERGE re-id'd the node it matched, so
#: the LAST registration of one name owned the id and the earlier one stopped
#: existing. `_upsert_object` / `_upsert_subject` now MERGE on the record's own
#: `id`; nothing re-ids, nothing evicts.
_RETIRED_IN_PROSE = re.compile(
    r"graph MERGEs (?:Object/Subject|those labels) by name"
    r"|MERGE\s+re-ids the existing node"
    r"|LAST\s+registration of a merge key OWNS its node id")


def _strip_py_comments(path: Path) -> str:
    """`path` with every COMMENT token blanked, keeping code and layout.

    A prose check and a code check need OPPOSITE views of the same file: the
    retired write may be DISCUSSED in a comment (both modules do, correctly, in
    the past tense), but only CODE can issue it. Blanking comments by token —
    not by truncating at the first ``#`` — keeps a ``#`` inside a Cypher string
    from swallowing the statement after it.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    with open(path, encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
    return "\n".join(lines)


def test_no_source_states_the_retired_name_keyed_merge_as_current_fact():
    """A comment arguing for the arm its own code deleted is a live defect.

    #9876 review (P3, round 15) found two of them in ``tortoise/consistency.py``
    — inside the two functions that carry the longest evidence about the
    carrier model: ``_fold_journal_entities``, where the eviction was retired
    eight lines below the comment, and the entity-parity bounds list, whose
    name fallback ``_compare_entities`` had already deleted while the bounds
    bullet still justified it by "the graph MERGEs those labels by name". A
    reader of ``carriers_by_name``, or of the bounds list, takes the prose as
    the current rule and builds on it. That is the same defect class the review
    fixed in ``journal_object_established``'s docstring (round 8) and in
    ``_seed_objects`` (round 12).

    The premise is asserted FIRST, so this cannot pass vacuously: if a writer
    ever goes back to a name-keyed Object/Subject MERGE the prose would be
    TRUE again, and the pin must be retired deliberately rather than the
    comments restored into it.
    """
    entities = _strip_py_comments(_SRC / "projection/entities.py")
    for label in ("Object", "Subject"):
        keyed = f"MERGE ({label[0].lower()}:{label} {{name:"
        assert keyed not in entities, (
            f"the premise of this pin is the RETIRED name-keyed {label} MERGE, "
            f"and `projection/entities.py` now writes `{keyed}…` — the prose "
            "claims below are true again, so retire THIS pin deliberately "
            "instead of deleting the comments it guards")

    text = (_SRC / "consistency.py").read_text(encoding="utf-8")
    hits = [m.group(0) for m in _RETIRED_IN_PROSE.finditer(text)]
    assert not hits, (
        "these comments state the mechanism #3590 removed as CURRENT fact, "
        f"while `projection/entities.py` MERGEs on `id`: {hits}")
