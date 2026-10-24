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

Every assertion here is RED on the pre-#3590 tree: the id was the name's hash,
a rename was withheld rather than journaled, and a re-create resurrected the
buried id. That is the point of the pins — each one names the behaviour that
regresses if the MERGE key ever moves back to `{name}`.
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
