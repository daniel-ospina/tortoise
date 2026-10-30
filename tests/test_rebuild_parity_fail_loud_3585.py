"""#3585 — Stage 0: the projection fails LOUDLY and `rebuild == live` is asserted.

The invariant is ``derived tables == replay(the journal)``
(docs/architecture/STORAGE-ARCHITECTURE.md §3). Before this change it was
checked for **Points only** and **only over content**, and an event the fold
could not resolve was a **warning** — so two equally INCOMPLETE projections
compared equal and the run passed. This file pins the two halves the identity
decision (docs/epics/2026-09-10-2835-capability-registry/identity-decision.md,
R8 + R9) requires:

  R8 — a non-folded event is RECORDED and the run FAILS (a raised structured
       error), except for the explicitly named exemptions.
  R9 — the invariant asserts the non-folded set is empty, AND compares the
       non-Point entities (the shapes in #3573 flip an OBJECT's `status`, which
       the point-only comparison could not see at all).

Doctrine (TEST-DOCTRINE.md, Class B) — every test's docstring answers:
  (1) what value/state makes it fail?  (2) is that value reachable in the
fixture?

⚠️ #3573's disposition (2026-09-25) records that **`main` is CORRECT for both
shapes** (the code that buried them was never merged). So the shape tests below
assert the invariant PASSES on `main`'s state and FAILS on the **burial end
state** the rejected fold produced — a positive assertion on the invariant's
coverage, never a claim that `main` buries.

Runnable with the docker lane:
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    uv run pytest tests/test_rebuild_parity_fail_loud_3585.py -q --tb=short
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tortoise.backup import restore
from tortoise.consistency import (
    _ENTITY_CREATION,
    _ENTITY_CREATION_LABELS,
    _NAME_MERGE_LABELS,
    _fold_journal_entities,
    check_consistency,
    recover_from_log,
)
from tortoise.log import EventLog
from tortoise.projection import (
    _NAME_KEYED_ENTITY_LABELS,
    _REFERENCE_FOLD_ENTITY_LABELS,
    FalkorProjection,
)
from tortoise.projection.nonfolded import (
    _NO_ADDRESS_REMEDY,
    EXEMPT_SHAPES,
    NO_ADDRESS_INSTRUCTION,
    SHAPE_EDGE_MESSAGE_MISS,
    NonFoldedEventsError,
    collect_non_folded,
    refused_events,
)
from tortoise.sdk import TortoiseSDK


def _unique(name: str) -> str:
    return f"test_3585_{name}_{uuid4().hex[:8]}"


@pytest.fixture
def env(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "l6.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _raw(events, **rec):
    """Append one hand-written journal line (the only way to reach a shape a
    public writer does not emit)."""
    rec.setdefault("event_id", "evt-" + uuid4().hex[:8])
    rec.setdefault("ts", "2026-01-01T00:00:00+00:00")
    with open(events / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _objects(proj):
    return sorted(
        (r[0], r[1], r[2]) for r in
        proj.g.query("MATCH (o:Object) RETURN o.id, o.name, o.status").result_set)


def _fresh(tmp_path, tag: str) -> FalkorProjection:
    proj = FalkorProjection(str(tmp_path / f"{tag}.db"),
                            graph_name=_unique(tag))
    # A freshly opened graph carries a `:Meta` FTS marker node; wipe it so
    # `recover_from_log`'s "graph already has nodes" discriminator sees a
    # genuinely empty graph (the same pre-wipe the other replay engines apply).
    proj.g.query("MATCH (n) DETACH DELETE n")
    return proj


def _drive(engine, tmp_path, events, proj):
    """Run the named replay engine on `proj` and return it."""
    if engine == "rebuild_all":
        proj.rebuild_all(str(events), confirm_destructive=True)
    elif engine == "rebuild":
        proj.rebuild(EventLog(str(events / "events.jsonl")), confirm_destructive=True)
    elif engine == "recover_from_log":
        res = recover_from_log(str(events), proj)
        assert res["recovered"] is True, res
    else:  # pragma: no cover - guard against a typo'd engine
        raise AssertionError(engine)
    return proj


ENGINES = ("rebuild_all", "rebuild", "recover_from_log")


# ═══════════════════════════════════════════════════════════════════════
# Shape A — a retraction whose id is anchored to a DIFFERENT name
# (#3573 P1-A). On main the rename is unjournaled (#3377→#4769) and the
# delete hard-deletes by journaled (label,id); the rejected fold resolved by
# NAME and buried the surviving carrier.
# ═══════════════════════════════════════════════════════════════════════

class TestShapeAUnjournaledRenameThenDelete:
    """Shape A — a delete whose id is anchored to a name a SIBLING carrier
    shares (#3573 P1-A). The rejected fold resolved the retraction by NAME and
    buried the surviving carrier.

    ⛔ #3590 retires the fixture this class was written against: the original
    asked the SDK for an UNJOURNALED rename onto a live-held name, and D3 now
    REFUSES that rename (`NameAlreadyHeld` — a live holder would make the name
    ambiguous), so the shape is no longer reachable through a public door.
    The property it pinned is still live and is built here the way a LEGACY
    graph reaches it: two registrations of one name are two entities under the
    id-keyed MERGE, and a delete by id must bury neither. Both assertions are
    RED on the pre-#3590 tree, where the fold resolved by name.
    """

    @staticmethod
    def _build(env, events):
        _sdk, _events = env
        _raw(events, type="ObjectRegistered", id="obj-shape-a-1",
             name="SHARED", status="live", event_id="e-sa-1")
        _raw(events, type="ObjectRegistered", id="obj-shape-a-2",
             name="SHARED", status="live", event_id="e-sa-2")
        _raw(events, type="EntityMutated", label="Object", id="obj-shape-a-1",
             op="delete", event_id="e-sa-del")

    def test_shape_a_matches_live_on_every_engine(self, env, tmp_path):
        """FAILS IF: any engine leaves the SIBLING carrier retracted/absent, or
        the entity-parity leg reports a divergence on a correct projection."""
        _sdk, events = env
        self._build(env, events)
        proj = _drive("rebuild_all", tmp_path, events, _fresh(tmp_path, "sa"))
        try:
            live = _objects(proj)
        finally:
            proj.close()
        assert live == [("obj-shape-a-2", "SHARED", "live")], live
        for engine in ENGINES:
            replay = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            try:
                assert _objects(replay) == live, (
                    f"{engine} buried shape A: {_objects(replay)}")
            finally:
                replay.close()

    def test_shape_a_burial_is_caught_by_entity_parity(self, env, tmp_path):
        """FAILS IF: the invariant does not flag the burial end state, or does
        not expose a per-entity diagnosis.
        REACHABLE: the exact state the rejected fold wrote — the surviving
        SHARED Object's status flipped to 'retracted'. Asserting the CONTENT
        comparison alone is not enough (it was point-only before #3585)."""
        _sdk, events = env
        self._build(env, events)
        proj = _drive("rebuild_all", tmp_path, events, _fresh(tmp_path, "sa2"))
        try:
            surviving = _objects(proj)[0][0]
            proj.g.query("MATCH (o:Object {id:$i}) SET o.status='retracted'",
                         params={"i": surviving})
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is False, "a buried live Object must fail the invariant"
        assert r["divergent_entity_count"] >= 1
        fields = {(d["id"], d["field"]) for d in r["divergent_entities"]}
        assert (surviving, "status") in fields, r["divergent_entities"]


# ═══════════════════════════════════════════════════════════════════════
# Shape B — a retraction preceding the only journaled registration
# (#3573 P1-B). The rejected fold MIS-resolved (it did not fail to resolve),
# so it is the INVARIANT's job to catch it — R8 alone cannot.
# ═══════════════════════════════════════════════════════════════════════

class TestShapeBDeleteThenRegister:
    def _build(self, tmp_path, events):
        db = str(tmp_path / "shared.db")
        s1 = TortoiseSDK(db)  # journal-less: the creation is unjournaled
        oid = s1.create_object("K7", objectKind="core:other")["id"]
        s1.close()
        s2 = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
        s2.delete_entity(oid)
        s2.create_object("K7", objectKind="core:other")
        return s2, oid

    def test_shape_b_matches_live_and_delete_miss_is_exempt(self, env,
                                                            tmp_path):
        """FAILS IF: the delete-miss exemption is removed (the run would raise
        on this journal) or any engine buries the re-created Object.
        REACHABLE: the journal is [delete(oid), register(oid)] — a delete that
        matches 0 rows on a from-scratch replay, which is the NAMED exemption
        (#4743); the re-created Object must be live everywhere."""
        _sdk, events = env
        s2, _oid = self._build(tmp_path, events)
        proj = s2._get_proj()
        live = _objects(proj)
        assert live and live[0][2] == "live", live
        for engine in ENGINES:
            replay = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            assert _objects(replay) == live, f"{engine} buried shape B: {_objects(replay)}"
            replay.close()
        r = check_consistency(str(events / "events.jsonl"), proj)
        assert r["ok"] is True, r["divergence"]
        s2.close()

    def test_shape_b_burial_is_caught_by_entity_parity(self, env, tmp_path):
        """FAILS IF: the invariant does not flag an Object the journal leaves
        live but the graph holds retracted (the rejected fold's end state).
        REACHABLE: flip the RE-CREATED Object's status — the shape B burial.

        ⛔ Restated for #3590, and the fixture is the reason. The row used to
        flip the DELETED id: under the pre-#3590 name-keyed `MERGE` the
        re-creation of `K7` landed back on that same node, so the flip was the
        live carrier's. The `MERGE` key is the ID now, so the re-creation mints
        a FRESH id and the old node is hard-deleted — flipping it writes
        nothing and the invariant correctly reports nothing, which is what this
        row was red on. The burial is flipped on the carrier the journal leaves
        LIVE, and the identity assertion below keeps the two shapes from
        silently becoming one again."""
        _sdk, events = env
        s2, oid = self._build(tmp_path, events)
        proj = s2._get_proj()
        live_ids = [r[0] for r in _objects(proj) if r[2] == "live"]
        assert live_ids and oid not in live_ids, (
            "premise: the re-creation mints a NEW id (the MERGE key is the id), "
            f"so the deleted {oid!r} is not the live carrier: {_objects(proj)}")
        for lid in live_ids:
            proj.g.query("MATCH (o:Object {id:$i}) SET o.status='retracted'",
                         params={"i": lid})
        r = check_consistency(str(events / "events.jsonl"), proj)
        assert r["ok"] is False
        assert any(d["label"] == "Object" and d["field"] == "status"
                   for d in r["divergent_entities"]), r["divergent_entities"]
        s2.close()


# ═══════════════════════════════════════════════════════════════════════
# R8 — the fail-closed rebuild
# ═══════════════════════════════════════════════════════════════════════

class TestFailClosedRebuild:
    def test_state_op_miss_fails_rebuild_all(self, env):
        """FAILS IF: rebuild_all returns normally (the pre-#3585 warning-only
        behaviour) instead of raising.
        REACHABLE: a hand-written `EntityMutated op=restatus` for an id no
        journaled creation ever made — the shape #4743's fold cannot apply."""
        sdk, events = env
        _raw(events, type="EntityMutated", op="restatus", label="Object",
             id="obj-never-existed", state={"status": "archived"},
             event_id="e-state-miss")
        with pytest.raises(NonFoldedEventsError) as ei:
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        msg = str(ei.value)
        assert "e-state-miss" in msg and "state-op-miss" in msg, msg

    def test_unknown_event_type_fails_rebuild_all(self, env):
        """FAILS IF: an unrecognized journal type is skipped silently.
        REACHABLE: a type outside `_NO_PROJECTION_FOLD` (a downgrade replaying a
        newer writer's record) — the fold has no arm and must not guess."""
        sdk, events = env
        _raw(events, type="FutureRecordType", event_id="e-future")
        with pytest.raises(NonFoldedEventsError) as ei:
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert "unknown-event-type" in str(ei.value)

    def test_unknown_event_type_fails_rebuild_log_too(self, env):
        """FAILS IF: the apply-based `rebuild(log)` engine warns and RETURNS on
        an unrecognized record while `rebuild_all` refuses it — the asymmetry
        #3585 exists to close, where a lost DB can be "recovered" onto a
        projection `rebuild_all` calls incomplete (this was the recut's
        regression: the `apply()` else-arm recorded nothing).
        REACHABLE: a type outside the projection vocabulary, folded through
        `rebuild(log)`'s `apply()` dispatch."""
        sdk, events = env
        _raw(events, type="FutureRecordType", event_id="e-future-log")
        with pytest.raises(NonFoldedEventsError) as ei:
            sdk._get_proj().rebuild(
                EventLog(str(events / "events.jsonl")),
                confirm_destructive=True)
        assert "unknown-event-type" in str(ei.value), str(ei.value)

    def test_unknown_event_type_is_not_recovered(self, env, tmp_path):
        """FAILS IF: transparent recovery reports success while the journal
        holds an unrecognized record — the apply-based engine would return
        `recovered: True` over an incomplete projection (R8/R9 asymmetry; the
        recut's regression measured exactly `recovered: True`).
        REACHABLE: a healthy `PointAdded` (so the log set is unambiguous and
        the graph is non-empty) plus one `FutureRecordType`."""
        sdk, events = env
        _raw(events, type="PointAdded", event_id="e-p1-unk",
             point={"id": "p1-unk", "content": "x", "kind": "statement",
                    "status": "live",
                    "createdAt": "2026-01-01T00:00:00Z"})
        _raw(events, type="FutureRecordType", event_id="e-future-rec")
        sdk.close()
        proj = _fresh(tmp_path, "recover-unk")
        try:
            res = recover_from_log(str(events), proj)
            assert res["recovered"] is False, res
            assert "unknown-event-type" in res["reason"], res["reason"]
        finally:
            proj.close()

    def test_delete_miss_is_exempt_and_reported(self, env, caplog):
        """FAILS IF: a delete matching 0 rows fails the run — it is the NAMED
        exception (#4743), and the run must still complete.
        REACHABLE: `EntityMutated op=delete` for an id with no creation."""
        sdk, events = env
        _raw(events, type="EntityMutated", op="delete", label="Object",
             id="obj-never-existed", event_id="e-del-miss")
        with caplog.at_level("WARNING"):
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)  # must NOT raise
        assert any("EXEMPT" in r.getMessage() for r in caplog.records), \
            "an exempt non-folded event must still be reported"

    def test_rebuild_log_fails_closed(self, env):
        """FAILS IF: only rebuild_all is wrapped — the apply-based engine must
        fail too (the issue: 'across all apply-based engines').
        REACHABLE: same missing-target state op, folded through rebuild(log)."""
        sdk, events = env
        _raw(events, type="EntityMutated", op="rename", label="Object",
             id="obj-never-existed", state={"name": "x"},
             event_id="e-rebuild-log")
        with pytest.raises(NonFoldedEventsError):
            sdk._get_proj().rebuild(EventLog(str(events / "events.jsonl")), confirm_destructive=True)

    def test_recover_from_log_reports_not_recovered(self, env):
        """FAILS IF: transparent recovery reports success while the journal
        holds an unfoldable event.
        REACHABLE: a healthy PointAdded (so the log is non-empty and the log
        set unambiguous) plus one state-op miss."""
        sdk, events = env
        _raw(events, type="PointAdded", event_id="e-p1",
             point={"id": "p1", "content": "x", "kind": "statement",
                    "status": "live",
                    "createdAt": "2026-01-01T00:00:00Z"})
        _raw(events, type="EntityMutated", op="restatus", label="Object",
             id="obj-never-existed", state={"status": "archived"},
             event_id="e-miss")
        sdk.close()
        proj = _fresh(env[1].parent, "recover")
        res = recover_from_log(str(events), proj)
        assert res["recovered"] is False, res
        assert "non-folded" in res["reason"].lower() or \
            "could not" in res["reason"], res["reason"]
        proj.close()


# ═══════════════════════════════════════════════════════════════════════
# R9 — the invariant asserts the set, not just the two projections
# ═══════════════════════════════════════════════════════════════════════

class TestNonFoldedSetIsAsserted:
    def test_the_refused_set_fails_a_run_the_content_check_would_pass(
            self, env):
        """THE VACUITY PROOF (R9). FAILS IF: the non-folded set is not
        asserted — `hash_match` is True (both sides are empty) and `ok` must
        STILL be False. `ok` coming only from `hash_match` would pass here.
        REACHABLE: a PointRetracted for a point no event created — the
        reference fold misses it, and so would a graph."""
        sdk, events = env
        _raw(events, type="PointRetracted", event_id="e-retract-miss", id="p-none")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["hash_match"] is True, "fixture must be content-clean"
        assert r["non_folded_refused_count"] >= 1
        assert r["ok"] is False
        assert r["divergence"] == "non-folded", r["divergence"]
        assert r["action"], "a non-folded run must name its action"

    def test_healthy_graph_reports_an_empty_set(self, env):
        """FAILS IF: the new keys are missing (a reverted collector) or a
        healthy run reports a non-empty set.
        REACHABLE: a normal SDK-created point, applied through the journal."""
        sdk, events = env
        sdk.create_point(content="hello", kind="statement")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["ok"] is True, r["divergence"]
        assert r["non_folded_count"] == 0
        assert r["non_folded_events"] == []
        assert r["divergent_entity_count"] == 0

    def test_point_superseded_without_new_id_is_exempt(self, env):
        """FAILS IF: the documented no-op shape fails the run.
        REACHABLE: a `PointSuperseded` with no `new_id` — the fold's decision
        (mirrored by `_fold_journal`) is that it changes nothing."""
        sdk, events = env
        _raw(events, type="PointSuperseded", event_id="e-sup-no-new", id="p-none")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["non_folded_count"] >= 1
        assert r["non_folded_refused_count"] == 0
        assert r["ok"] is True, r["divergence"]


class TestEntityParityBounds:
    def test_a_journaled_hard_delete_of_a_live_graph_node_is_a_divergence(
            self, env):
        """FAILS IF: the entity leg only checks registered entities and never
        the resurrect direction.
        REACHABLE: graph holds an Object the journal hard-deletes with no later
        registration (an unjournaled creation) — presence divergence."""
        sdk, events = env
        oid = sdk.create_entity("object", name="live-only",
                                objectKind="k")["node"]["id"]
        _raw(events, type="EntityMutated", op="delete", label="Object",
             id=oid, event_id="e-del")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["divergent_entity_count"] >= 1
        assert any(d["field"] == "presence" and d["id"] == oid
                   for d in r["divergent_entities"]), r["divergent_entities"]

    def test_no_points_graph_has_no_entity_divergence_when_consistent(self, env):
        """FAILS IF: a fully journaled Object round-trip trips the entity leg
        (a false positive would make the invariant unusable).
        REACHABLE: create via the journaled SDK path, then compare."""
        sdk, events = env
        sdk.create_entity("object", name="A", objectKind="k")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["divergent_entity_count"] == 0, r["divergent_entities"]
        assert r["ok"] is True, r["divergence"]

    def test_subject_document_event_do_not_false_positive(self, env):
        """FAILS IF: the status comparison is applied to a kind that stores its
        status under a different prop (`subjectKind`/`doc_status`/`eventStatus`),
        which would red a healthy graph. REACHABLE: one of each kind via the
        journaled SDK path."""
        sdk, events = env
        sdk.create_entity("subject", name="S")
        sdk.create_entity("document", name="D", documentKind="spec")
        sdk.create_entity("event", name="E", eventKind="meeting")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["divergent_entity_count"] == 0, r["divergent_entities"]
        assert r["ok"] is True, r["divergence"]


# ═══════════════════════════════════════════════════════════════════════
# Review round 1 — the fixes that made the invariant honest
# (each pins a defect a fresh reviewer found in the first cut)
# ═══════════════════════════════════════════════════════════════════════

class TestReferenceFoldMirrorsTheWriters:
    def test_flat_and_nested_event_ids_are_resolved(self, env):
        """FAILS IF: the reference fold reads only a top-level `eventId`.

        `EventAPI.add_event` journals a FLAT record with `id`, and the miner
        journals a NESTED `{event: {...}}` one; `_upsert_event` reads `id` OR
        `eventId`, nested or flat. A reference fold that assumes the third
        shape registers NOTHING, so a buried Event is never reported — the
        silent false negative #3585 exists to remove.
        REACHABLE: both writer shapes, then the Event nodes are removed from
        the graph (the burial) and `check_consistency` must see it."""
        sdk, events = env
        _raw(events, type="EventRecorded", id="evt-flat", name="EF",
             eventType="meeting")
        _raw(events, type="EventRecorded",
             event={"id": "evt-nested", "name": "EN",
                    "eventType": "meeting"})
        proj = sdk._get_proj()
        proj.rebuild_all(str(events), confirm_destructive=True)
        proj.g.query("MATCH (e:Event) DETACH DELETE e")
        r = check_consistency(str(events / "events.jsonl"), proj)
        buried = {d["id"] for d in r["divergent_entities"]
                  if d["label"] == "Event" and d["field"] == "presence"}
        assert buried == {"evt-flat", "evt-nested"}, r["divergent_entities"]

    def test_second_registration_of_one_name_is_not_a_divergence(self, env):
        """FAILS IF: the entity leg resolves presence by NAME (#3590).

        A second registration of one name under a fresh id is TWO nodes — the
        writers merge on `id`, not on `name` — so both ids are present and the
        verdict is clean for that reason. The name-resolution fallback this pin
        used to justify ("the graph MERGEs these labels by NAME, so the name is
        ONE node carrying the LAST id") was retired by #3590: it made a buried
        id resolve onto an impostor of the same name and report CLEAN.
        REACHABLE: a second `ObjectRegistered` for the same name (the
        `EventAPI.add_object` shape mints a fresh id per call)."""
        sdk, events = env
        sdk.create_entity("object", name="SAME", objectKind="k")
        _raw(events, type="ObjectRegistered", id="obj-second", name="SAME",
             objectKind="k", status="live")
        proj = sdk._get_proj()
        proj.rebuild_all(str(events), confirm_destructive=True)
        r = check_consistency(str(events / "events.jsonl"), proj)
        assert r["divergent_entity_count"] == 0, r["divergent_entities"]
        assert r["ok"] is True, r["divergence"]

    def test_the_ambiguity_exclusion_is_reported(self, env):
        """FAILS IF: an ambiguous name-only supersede is silently skipped.

        The bound must be REPORTED (`entity_parity_ambiguous*`), not absorbed:
        a caller reading `ok` has to be able to see what the verdict did NOT
        compare.
        REACHABLE: two registrations of one name make a name-only
        `ObjectSuperseded` unresolvable (>1 carrier)."""
        sdk, events = env
        sdk.create_entity("object", name="AMB", objectKind="k")
        _raw(events, type="ObjectRegistered", id="obj-amb-2", name="AMB",
             objectKind="k", status="live")
        _raw(events, type="ObjectSuperseded", name="AMB",
             supersedes_by="other", event_id="e-amb")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["entity_parity_ambiguous_count"] >= 1, r
        assert r["entity_parity_bounds"]["direction"] == "journal->graph"

    def test_a_noncanonical_delete_label_is_replayed_id_wide(self, env,
                                                            tmp_path):
        """FAILS IF: the reference fold scopes ANY string label.

        `_delete_entity_by_id` scopes only a CANONICAL label and falls back to
        the id-wide delete otherwise. A bare `isinstance(label, str)` guard
        left the `(Object, id)` entry registered while the replayed graph had
        been id-wiped — a FALSE `absent-from-graph` on a faithful replay.
        REACHABLE: `label="Objects"` (non-canonical, a pre-#3860 shape), then
        compare against the graph the same journal produces."""
        sdk, events = env
        oid = sdk.create_entity("object", name="NC",
                                objectKind="k")["node"]["id"]
        _raw(events, type="EntityMutated", op="delete", label="Objects",
             id=oid, event_id="e-nc")
        sdk.close()
        proj = _fresh(tmp_path, "nc")
        proj.rebuild_all(str(events), confirm_destructive=True)
        try:
            assert not proj.g.query(
                "MATCH (o:Object {id:$id}) RETURN o",
                params={"id": oid}).result_set, (
                "fixture: the id-wide delete must have removed the node")
            r = check_consistency(str(events / "events.jsonl"), proj)
            assert r["divergent_entity_count"] == 0, r["divergent_entities"]
        finally:
            proj.close()


class TestOneClassifierForTheNonFoldedSet:
    def test_retract_miss_fails_the_graph_engines_too(self, env):
        """FAILS IF: only the reference fold refuses a retract-miss.

        `_retract` is a blind MATCH-SET (no miss detection) in the apply/rebuild
        engines, so `check_consistency` said `non-folded` while `rebuild_all`
        returned counts — the two classifiers must agree.
        REACHABLE: `PointRetracted` for an id no event created."""
        sdk, events = env
        _raw(events, type="PointRetracted", event_id="e-retract-2", id="p-none")
        with pytest.raises(NonFoldedEventsError) as ei:
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert "point-retracted-miss" in str(ei.value), str(ei.value)

    def test_unimplemented_op_is_recorded_by_the_reference_fold(self, env):
        """FAILS IF: `_apply_one`'s pending-op branch only logs.

        The graph fold records `SHAPE_UNIMPLEMENTED_OP` and fails; if the
        reference fold does not, `check_consistency` returns ok=True on a
        journal `rebuild_all` refuses.
        REACHABLE: `EntityMutated op="retract"` (a `_ENTITY_MUTATION_PENDING_OPS`
        member on an id that is) folded by the reference fold only."""
        sdk, events = env
        oid = sdk.create_entity("object", name="PEND",
                                objectKind="k")["node"]["id"]
        _raw(events, type="EntityMutated", op="retract", label="Object",
             id=oid, event_id="e-pend")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["non_folded_refused_count"] >= 1, r["non_folded_events"]
        assert r["divergence"] == "non-folded", r["divergence"]
        assert "unimplemented-op" in " ".join(r["non_folded_events"])

    def test_a_foreign_kind_delete_does_not_exempt_a_point_supersede(self, env):
        """FAILS IF: the exemption discriminator is a bare ID set.

        A delete of `(Object, X)` must not exempt a `PointSuperseded` miss on
        `Point X` — that turns a genuine burial into a green pass (the
        fail-open a kind-scoped discriminator closes).
        REACHABLE: an Object hard-delete plus an unfoldable `PointSuperseded`
        for a Point that merely shares the id."""
        sdk, events = env
        _raw(events, type="EntityMutated", op="delete", label="Object",
             id="shared-X", event_id="e-del-X")
        _raw(events, type="PointSuperseded", id="shared-X", new_id="p-new",
             event_id="e-sup-X")
        with pytest.raises(NonFoldedEventsError) as ei:
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert "point-superseded-miss" in str(ei.value), str(ei.value)


class TestReReviewRoundTwo:
    def test_name_only_supersede_of_never_registered_name_is_exempt_both_engines(
            self, env):
        """#3590 D2 (adopted on #3590, bounded): an id-less `ObjectSuperseded`
        whose target name the journal NEVER registered is EXEMPT and RECORDED.

        FAILS IF: the exemption is removed (the run raises again — a damaged
        journal cannot be rebuilt at all), OR the shape stops being recorded
        (an exemption still requires the record, #5380), OR the reference fold
        disagrees with the graph engine (the classifier asymmetry #3585
        exists to close).
        REACHABLE: a journal whose only record supersedes a name that was never
        registered — no registration exists that could have created it, so
        live folded nothing either and live/replay agree.

        ⚠️ RETIRED CONTRACT: this test previously asserted the OPPOSITE
        (`object-superseded-miss`, refused). D2 reverses exactly that, and the
        reversal is bounded by the test below, which keeps the refusal for a
        name the journal DID register.
        """
        sdk, events = env
        _raw(events, type="ObjectSuperseded", name="NOPE",
             supersedes_by="other", event_id="e-nope")
        proj = sdk._get_proj()
        # No raise: the exemption lets a damaged journal be rebuilt.
        proj.rebuild_all(str(events), confirm_destructive=True)
        r = check_consistency(str(events / "events.jsonl"), proj)
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
        assert r["non_folded_count"] >= 1, (
            "the exempt miss was not RECORDED at all — an exemption must never "
            f"be silent: {r['non_folded_events']}")
        assert any("object-superseded-unregistered" in e
                   for e in r["non_folded_events"]), r["non_folded_events"]
        assert r["divergence"] != "non-folded", r["divergence"]

    def test_name_only_supersede_of_registered_then_deleted_name_still_refuses(
            self, env, caplog):
        """#3590 D2's BOUND: the exemption covers ONLY the never-registered
        case.

        FAILS IF: the address decision is inferred from the GRAPH ("no live
        holder") instead of the JOURNAL. A name the journal registered and
        then hard-deleted has no live holder either, so a graph-probe
        implementation would exempt it — and a supersede that COULD have
        landed becomes skippable, which is the one thing the bound forbids.
        REACHABLE: register `GONE`, hard-delete it by id, then supersede it by
        name alone (no id), so `target_deleted` cannot fire (it reads the
        record's id, which is absent).
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="GONE")["node"]["id"]
        sdk.delete_entity(oid)
        _raw(events, type="ObjectSuperseded", name="GONE",
             supersedes_by="other", event_id="e-gone")
        proj = sdk._get_proj()
        with caplog.at_level(logging.WARNING, logger="tortoise.projection"), \
                pytest.raises(NonFoldedEventsError) as ei:
            proj.rebuild_all(str(events), confirm_destructive=True)
        assert "object-superseded-miss" in str(ei.value), str(ei.value)
        # ⛔ THE HARD-DELETE LIMB (#10211 review round 8). This record is NOT a
        # divergence — live folded nothing either, because the delete is folded
        # by id before the deferred sweep — and the only reason it misses is
        # that this record carries no `id` for the anchored bound to read. The
        # message must say so, or it sends the operator after a merge/rename or
        # an unjournaled producer, neither of which exists here.
        report = " ".join(r.getMessage() for r in caplog.records)
        assert "HARD-DELETED under an `id` this record DOES NOT CARRY" in report, (
            f"the miss arm does not name the hard-delete limb: {report}")
        assert "CONSERVATIVE refusal" in report, (
            f"the miss arm calls a conservative refusal a divergence: {report}")
        # ⚠️ AND NOT "carries no `id`" — the bound reads the record's OWN id, so
        # a record carrying an UNRELATED id (of another node) lands here too and
        # would be falsified by the operator's own report line on the next
        # output row. MEASURED (#10211 review round 9).
        assert "object-superseded-unregistered" not in str(ei.value), str(ei.value)
        r = check_consistency(str(events / "events.jsonl"), proj)
        assert r["non_folded_refused_count"] >= 1, r["non_folded_events"]
        assert r["divergence"] == "non-folded", r["divergence"]
    def test_deleted_target_with_an_unrelated_id_never_claims_no_id(
            self, env, caplog):
        """#10211 review round 9: the hard-delete limb must not assert the
        record carries NO `id`.

        FAILS IF: the limb says "this record carries no `id`". The anchored
        `supersede-target-deleted` bound reads the record's OWN id, so a record
        carrying an UNRELATED id (of a different node) reaches this arm too —
        and the same warning line sits beside a report row that visibly shows
        the id, so the message is falsified by the operator's own output.

        REACHABLE: a raw/legacy producer pairing a deleted target's NAME with a
        stale/unrelated id.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="GONE2")["node"]["id"]
        sdk.delete_entity(oid)
        _raw(events, type="ObjectSuperseded", name="GONE2", id="o-junk",
             supersedes_by="other", event_id="e-gone2")
        proj = sdk._get_proj()
        with caplog.at_level(logging.WARNING, logger="tortoise.projection"), \
                pytest.raises(NonFoldedEventsError):
            proj.rebuild_all(str(events), confirm_destructive=True)
        report = " ".join(r.getMessage() for r in caplog.records)
        assert "HARD-DELETED under an `id` this record DOES NOT CARRY" in report, (
            f"the miss arm does not name the hard-delete cause generically: "
            f"{report}")
        assert "carries no `id`" not in report, (
            f"the miss arm asserts the record carries no id, which is false for "
            f"this record (it carries 'o-junk'): {report}")

    def test_state_op_writing_a_name_other_than_rename_is_covered(
            self, env, caplog):
        """#10211 review round 9: the non-creation limb's name route is ANY
        entity-mutation state op, not only a rename.

        FAILS IF: the limb enumerates "a rename's `SET`". `journal_object_established`
        takes the name from any op in `_ENTITY_MUTATION_STATE_OPS` carrying
        `state.name`, so an `op=supersede` (or any other op) writes an
        established name the same way — and a cause list that names only
        `rename` leaves that record in neither limb, the defect round 6 fixed
        one spelling over.

        REACHABLE: a raw/legacy producer emitting a state op that is not a
        rename, then superseding the name it wrote.
        """
        sdk, events = env
        _raw(events, type="ObjectRegistered", id="o-anchor", name="ANCHOR",
             status="live", event_id="e-anchor")
        _raw(events, type="EntityMutated", op="supersede", label="Object",
             id="o-ghost", state={"name": "GHOST2"}, event_id="e-ghost")
        _raw(events, type="ObjectSuperseded", name="GHOST2",
             supersedes_by="other", event_id="e-ghost-sup")
        proj = sdk._get_proj()
        with caplog.at_level(logging.WARNING, logger="tortoise.projection"), \
                pytest.raises(NonFoldedEventsError):
            proj.rebuild_all(str(events), confirm_destructive=True)
        report = " ".join(r.getMessage() for r in caplog.records)
        assert "a state op's `SET`" in report, (
            f"the non-creation limb does not name the state-op SET route: {report}")
        assert "a rename's `SET`" not in report, (
            f"the limb still narrows the name route to a rename: {report}")

    def test_id_only_absent_target_supersede_is_exempt_both_engines(
            self, env, tmp_path, caplog):
        """#10204: an `ObjectSuperseded` carrying ONLY an `id` the journal never
        established is EXEMPT — the amended D2 bound keys on ADDRESSES, not on
        a name.

        FAILS IF: the exemption still requires a name (the retired `#10107`
        clause), so an id-only record stays `object-superseded-miss` and the
        run ABORTS; OR the reference fold records NOTHING for it, which is the
        classifier asymmetry `#3585` exists to close (an exemption still
        requires the record, `#5380`).

        REACHABLE: a hand-written id-only supersede for an id the journal never
        registered — the spelling a model that keys on ids emits (the writer
        emits both, so this is a raw/legacy producer).

        ⚠️ RETIRED CONTRACT: this test previously asserted the OPPOSITE
        (`object-superseded-miss`, refused, `divergence="non-folded"`). The
        owner amended the bound on `#3590` comment `6453059051` (issue
        `#10204`); the deciding reason is `#10183`'s admission test — for a
        target nothing established there is no journal repair to prescribe, so
        the surface must not refuse. The refusal it pinned did not disappear:
        it moved to any record carrying an ESTABLISHED address, pinned by
        `test_collapsed_away_id_with_an_unestablished_name_still_refuses`
        (established id) and
        `test_name_only_supersede_of_registered_then_deleted_name_still_refuses`
        (established name).
        """
        sdk, events = env
        # ⛔ ONE unrelated anchor registration, so the journal does not replay to
        # an EMPTY graph: `recover_from_log` refuses an empty replay with its own
        # pre-existing guard, which would mask the verdict under test.
        _raw(events, type="ObjectRegistered", id="obj-d2-anchor",
             name="D2-ANCHOR", status="live", event_id="e-d2-anchor")
        _raw(events, type="ObjectSuperseded", id="obj-never-registered",
             supersedes_by="other", event_id="e-sup-idonly")
        proj = sdk._get_proj()
        # ⛔ The GRAPH surface's own report is asserted, not only the reference
        # fold's: `assert_no_non_folded` names every exempt record, so a
        # `record_non_folded` that silently dropped this shape would pass a
        # reference-fold-only assertion — the green pass over an unfolded event
        # R8/#5380 exists to prevent.
        with caplog.at_level(logging.WARNING,
                             logger="tortoise.projection.nonfolded"):
            proj.rebuild_all(str(events), confirm_destructive=True)  # no raise
        report = " ".join(r.getMessage() for r in caplog.records)
        assert "[object-superseded-unregistered]" in report, report
        assert "never established" in report, report
        # ⛔ NO LOSS VERDICT (#10211 review round 3). This shape's OWN
        # `EXEMPT_SHAPES` entry carries a DEGRADED GUARANTEE — "what is lost is
        # the supersede a journal could not address" — and `NonFoldedEvent.__str__`
        # appends that prose to the receipt, so a receipt claiming "nothing was
        # lost" put both claims on ONE operator-visible line. The predicate
        # cannot prove the claim (its RESIDUAL is the case where the supersede
        # IS lost), so the receipt states what it establishes and stops.
        assert "nothing was lost" not in report.lower(), (
            "the receipt asserts a loss verdict the predicate cannot prove and "
            f"the shape's own DEGRADED GUARANTEE contradicts: {report}")
        assert "applies no supersede here" in report, (
            f"the receipt was not rendered for the exempt record: {report}")
        assert "no repair to make" not in report.lower(), (
            "the receipt asserts the absence of a repair, which the shape's "
            f"DEGRADED GUARANTEE contradicts (a repair DOES exist for that "
            f"class): {report}")
        # ⛔ The SECOND message must not name a cause the receipt denies: the
        # genuine-miss arm's causes ("unjournaled capture SDK", "delete race")
        # are exactly what an operator must not be sent to look for on an
        # exempt record (#10183). MEASURED: deleting this shape's `_sup_why`
        # arm otherwise leaves the file green.
        # ⚠️ NOT "unjournaled capture SDK": the `EXEMPT_SHAPES` prose for this
        # shape legitimately names that producer class in its RESIDUAL, so that
        # phrase cannot discriminate the two messages. "delete race" is
        # emitted by the genuine-miss arm alone.
        assert "delete race" not in report, report
        assert "the journal never established the target this record " \
               "addresses" in report, report
        r = check_consistency(str(events / "events.jsonl"), proj)
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
        assert r["non_folded_count"] == 1, (
            "the exempt miss was not RECORDED exactly once — an exemption must "
            f"never be silent: {r['non_folded_events']}")
        assert any("object-superseded-unregistered" in e
                   for e in r["non_folded_events"]), r["non_folded_events"]
        assert r["divergence"] != "non-folded", r["divergence"]
        # The exemption is a RUN-VERDICT decision, so it must hold at EVERY
        # engine's own run boundary — a journal `rebuild_all` accepts cannot be
        # refused by `rebuild()` or by `recover_from_log`.
        #
        # ⛔ And each of those boundaries must still RECEIVE the record: they
        # are a SECOND `record_non_folded` call site (`apply()`), reached by
        # `rebuild`/`recover_from_log`/`restore`, not the sweep the assertion
        # above covers. MEASURED: recording the shape only when it is REFUSED —
        # i.e. silently dropping the exempt record on the one-record engines —
        # leaves this whole file GREEN.
        for engine in ("rebuild", "recover_from_log"):
            caplog.clear()
            with caplog.at_level(logging.WARNING,
                                 logger="tortoise.projection.nonfolded"):
                proj = _drive(engine, tmp_path, events,
                              _fresh(tmp_path, engine))
                proj.close()
            boundary = " ".join(r.getMessage() for r in caplog.records)
            # The engine's own label is not asserted: `rebuild`'s boundary
            # reports itself as `rebuild(log)` and the recovery one differs
            # again, so keying on it would pin an incidental string. What must
            # hold is that THIS run reported the exempt record and rendered its
            # receipt.
            assert "1 non-folded journal event(s) recorded but EXEMPT" in boundary, (
                f"{engine} did not record the exempt miss at its own run "
                f"boundary (#5380): {boundary}")
            assert "[object-superseded-unregistered]" in boundary, boundary
            assert "applies no supersede here" in boundary, boundary

    def test_pointsmerged_then_supersede_is_exempt_in_both_engines(
            self, env, tmp_path):
        """FAILS IF: the graph fold does not tag a `PointsMerged` hard delete.

        `journal_hard_delete_seqs` (the reference fold's anchor source) counts a
        merge as a hard delete, so a later supersede of a merged Point is the
        NAMED exemption. If the engine's discriminator misses it, `rebuild_all`
        refuses a journal `check_consistency` passes.
        REACHABLE: `PointsMerged` for a Point no event created, then a
        `PointSuperseded` with a new_id."""
        sdk, events = env
        _raw(events, type="PointsMerged", merge_ids=["p-merged"],
             point={"keep_id": "p-keep"}, event_id="e-merge")
        _raw(events, type="PointSuperseded", id="p-merged", new_id="p-new",
             event_id="e-sup-merged")
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)  # must NOT raise
        sdk.close()
        proj = _fresh(tmp_path, "merged")
        proj.rebuild_all(str(events), confirm_destructive=True)             # must NOT raise
        try:
            r = check_consistency(str(events / "events.jsonl"), proj)
            assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
        finally:
            proj.close()

    def test_ambiguous_status_does_not_hide_a_presence_burial(self, env):
        """FAILS IF: the ambiguity bound also skips the PRESENCE leg.

        Presence is decidable by ID (#3590: the record's own id is the
        identity), so a buried node must still be reported even when the same
        journal also holds a name-only supersede whose carriers are ambiguous —
        otherwise the bound hides exactly the loss R9 exists to catch.
        REACHABLE: two registrations of one name (making a name-only supersede
        unresolvable) then the nodes removed from the graph.

        #3590: the name-only supersede is now itself a REFUSAL
        (`object-superseded-miss` — the name IS established, and an id-less
        record is unaddressable on the id-keyed fold), so the rebuild is
        expected to raise. The GRAPH it built is what the parity leg reads,
        which is the property under test.
        """
        sdk, events = env
        sdk.create_entity("object", name="AMB2", objectKind="k")
        _raw(events, type="ObjectRegistered", id="obj-amb2-b", name="AMB2",
             objectKind="k", status="live")
        _raw(events, type="ObjectSuperseded", name="AMB2",
             supersedes_by="other", event_id="e-amb2")
        proj = sdk._get_proj()
        with pytest.raises(NonFoldedEventsError):
            proj.rebuild_all(str(events), confirm_destructive=True)
        proj.g.query("MATCH (o:Object) DETACH DELETE o")
        r = check_consistency(str(events / "events.jsonl"), proj)
        assert r["entity_parity_ambiguous_count"] >= 1, r
        assert any(d["field"] == "presence" and d["label"] == "Object"
                   for d in r["divergent_entities"]), r["divergent_entities"]


# ═══════════════════════════════════════════════════════════════════════
# Re-review round 3 — the apply-based engines vs the DEFERRED types
# ═══════════════════════════════════════════════════════════════════════

class TestReReviewRoundThree:
    def test_a_journaled_supersede_is_replayable_by_every_engine(
            self, env, tmp_path):
        """P0. FAILS IF: a recognized type that `rebuild_all` folds in a
        DEFERRED sweep is classified `unknown-event-type` by `apply()`, so
        `rebuild(log)` raises and `recover_from_log` returns
        `recovered: False` on a journal `rebuild_all` accepts — which would
        make a LOST-DB RECOVERY impossible for any journal holding a supersede.
        REACHABLE: two real Points plus a real `supersede_point`, i.e. the
        ordinary journal every superseded fact produces (not a hand-written
        line).

        The apply engines are NOT asserted to reproduce the re-stamp: that
        fold lives in `rebuild_all`'s deferred sweep only, and their skipping
        it is the pre-existing, warned parity gap. What this pins is that an
        ACCEPTED journal stays REPLAYABLE."""
        sdk, events = env
        p1 = sdk.create_point(content="old", kind="statement")["id"]
        p2 = sdk.create_point(content="new", kind="statement")["id"]
        sdk.supersede_point(p1, p2)
        live = sorted(
            (r[0], r[1]) for r in sdk._get_proj().g.query(
                "MATCH (p:Point) RETURN p.id, p.status").result_set)
        assert (p1, "superseded") in live, live
        # rebuild_all folds the deferred re-stamp: parity with live.
        proj = _drive("rebuild_all", tmp_path, events, _fresh(tmp_path, "ra"))
        got = sorted(
            (r[0], r[1]) for r in proj.g.query(
                "MATCH (p:Point) RETURN p.id, p.status").result_set)
        assert got == live, f"rebuild_all mangled a supersede: {got}"
        proj.close()
        # The apply-based engines accept the SAME journal (warn, never refuse).
        for engine in ("rebuild", "recover_from_log"):
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            ids = {r[0] for r in proj.g.query(
                "MATCH (p:Point) RETURN p.id").result_set}
            assert {p1, p2} <= ids, f"{engine} refused a supersede journal: {ids}"
            proj.close()

    def test_an_invalidate_is_replayable_by_every_engine(self, env, tmp_path):
        """P0 (the second deferred type). FAILS IF: `PointInvalidated` — the
        other member of the deferred re-stamp family — is treated as unknown by
        `apply()`, refusing a recovery.
        REACHABLE: a real `invalidate_point` journal (the #2488 writer)."""
        sdk, events = env
        p1 = sdk.create_point(content="a", kind="statement")["id"]
        p2 = sdk.create_point(content="b", kind="statement")["id"]
        sdk.invalidate_point(p1, p2)
        proj = _drive("rebuild_all", tmp_path, events, _fresh(tmp_path, "ra2"))
        got = proj.g.query("MATCH (p:Point) RETURN p.id").result_set
        assert {r[0] for r in got} == {p1, p2}, got
        proj.close()
        for engine in ("rebuild", "recover_from_log"):
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            ids = {r[0] for r in proj.g.query(
                "MATCH (p:Point) RETURN p.id").result_set}
            assert {p1, p2} <= ids, f"{engine} refused an invalidate journal: {ids}"
            proj.close()

    def test_a_nonpoint_state_op_miss_refuses_the_reference_fold(self, env):
        """P1. FAILS IF: the reference fold drops an EntityMutated state op on
        an Object/Subject/Document/Event that no creation made — the graph fold
        records `state-op-miss` (refused), so an asymmetry would let
        `check_consistency` pass on a journal `rebuild_all` refuses.
        REACHABLE: a hand-written `op=restatus` for an id no creation made."""
        sdk, events = env
        _raw(events, type="EntityMutated", op="restatus", label="Object",
             id="obj-never-registered", state={"status": "archived"},
             event_id="e-r3-state")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["ok"] is False, r["divergence"]
        assert r["divergence"] == "non-folded", r["divergence"]
        assert any("state-op-miss" in e for e in r["non_folded_events"]), \
            r["non_folded_events"]

    def test_a_noncanonical_state_op_label_refuses_the_reference_fold(
            self, env):
        """P1 (re-review). FAILS IF: the reference fold only checks whether the
        id is in its index. `_fold_entity_mutation` refuses a label outside
        `_CANONICAL_ENTITY_LABELS` UNCONDITIONALLY, so a real Point id does not
        make a `Widget` state op foldable.
        REACHABLE: a hand-written state op naming a non-canonical label for an
        id that IS a live Point."""
        sdk, events = env
        p1 = sdk.create_point(content="x", kind="statement")["id"]
        _raw(events, type="EntityMutated", op="restatus", label="Widget",
             id=p1, state={"status": "archived"}, event_id="e-r3-widget")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["ok"] is False, r["divergence"]
        assert any("state-op-miss" in e for e in r["non_folded_events"]), \
            r["non_folded_events"]

    def test_a_state_less_state_op_refuses_the_reference_fold(self, env):
        """P1 (re-review). FAILS IF: a state op with no applied map is treated
        as a no-op. `_fold_entity_mutation` refuses it (there is nothing to
        `SET`), so the reference fold must too.
        REACHABLE: `op=restatus` with no `state` key, on a REAL Point."""
        sdk, events = env
        p1 = sdk.create_point(content="x", kind="statement")["id"]
        _raw(events, type="EntityMutated", op="restatus", label="Point",
             id=p1, event_id="e-r3-nostate")
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["ok"] is False, r["divergence"]
        assert any("state-op-miss" in e for e in r["non_folded_events"]), \
            r["non_folded_events"]

    def test_a_never_created_belief_write_refuses_the_reference_fold(
            self, env):
        """P1 (re-review). FAILS IF: the reference fold stays silent for a
        belief/annotator write whose Point NO record in the journal creates.
        `rebuild_all` hoists every creation and still finds no Point, so it
        records `point-belief-miss` and raises — the asymmetry #3585 exists to
        eliminate. The distinction from the forward-reference guard is
        JOURNAL-WIDE creation, not the pass-so-far index.
        REACHABLE: a write for an id no `PointAdded`/`OperatorAdded` names."""
        sdk, events = env
        _raw(events, type="ConfidenceChanged", id="p-never-created",
             confidence=0.9, event_id="e-r3-never")
        with pytest.raises(NonFoldedEventsError):
            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["ok"] is False, r["divergence"]
        assert r["divergence"] == "non-folded", r["divergence"]
        assert any("point-belief-miss" in e
                   for e in r["non_folded_events"]), r["non_folded_events"]

    def test_a_nonstring_state_op_id_is_not_refused_by_the_reference_fold(
            self, env):
        """P2 (re-review) — the mirror's edge. FAILS IF: a state op with a
        non-str id is refused here. `_fold_entity_mutation` returns 0 for it
        SILENTLY (#331 parity: a malformed id must not crash the fold), so
        `rebuild_all` accepts the journal and `check_consistency` must too.
        REACHABLE: `id` as an int instead of a string."""
        sdk, events = env
        _raw(events, type="EntityMutated", op="restatus", label="Point",
             id=12345, state={"status": "archived"}, event_id="e-r3-intid")
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)  # graph accepts it
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]

    def test_a_forward_referenced_belief_write_is_not_a_non_folded_event(
            self, env):
        """P2 (re-review) — the REGRESSION GUARD. FAILS IF: the reference fold
        refuses a belief write whose Point is created LATER in the journal.
        Neither the live write nor `rebuild_all` applies a write that precedes
        its Point (the live system no-op'd it; #3585 P1-1 makes the fold skip
        it too), so refusing it here would red a journal the graph reproduces
        exactly.
        REACHABLE: the write precedes its own creation (an append the SDK does
        not emit, but a partial/merged journal can)."""
        sdk, events = env
        _raw(events, type="ConfidenceChanged", id="p-later", confidence=0.9,
             event_id="e-r3-fwd")
        _raw(events, type="PointAdded", event_id="e-r3-fwd-add",
             point={"id": "p-later", "content": "x", "kind": "statement",
                    "status": "live",
                    "createdAt": "2026-01-01T00:00:00Z"})
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)  # skips it: no refusal
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
        # #3585 (P1-1): the write PRECEDES its Point, so the live system
        # no-op'd it and BOTH folds now skip it (nothing is applied). What must
        # NOT happen is a fail-LOUD verdict on a record that simply did not
        # exist yet.
        assert r["divergence"] != "non-folded", r["divergence"]


# ═══════════════════════════════════════════════════════════════════════
# Cycle-3 — the FINAL-STATE resolution of a held ObjectSuperseded
# (#5285 re-review). Rounds 1-2 fixed the supersede fold event-by-event and
# each fix opened a new asymmetry; the root cause was that `pending` (a
# forward-referenced supersede) was resolved against the entity index ONLY at
# creation events, never against the journal's final state. These tests pin
# the two headlines the earlier rounds left UNPINNED — reverting FIX 1 (the
# carrier predicate) or FIX 2 (final-state resolution) now reddens the suite.
# ═══════════════════════════════════════════════════════════════════════

class TestCycleThreeFinalStateResolution:
    def test_second_name_only_supersede_of_an_established_name_folds(
            self, env, tmp_path):
        """#3590 D2-(i) — a name-only supersede of an ESTABLISHED name LANDS,
        on EVERY engine.

        ⛔ This pin has been inverted twice and the history is the point, so
        both directions are named here. Pre-#3590 it asserted the name-keyed
        fold. This branch then asserted the opposite (a blanket
        ``object-superseded-miss`` refusal), on the premise that an id-less
        record is UNADDRESSABLE — which made a journal ``main`` folds
        UN-REBUILDABLE, i.e. the durable copy of the data could no longer be
        recovered from its own journal. The owner excluded exactly this shape
        (#3590 D2, 2026-10-26): *"a journal that replays on `main` must keep
        replaying and the record must keep landing — that is a regression to
        repair. I am not asking about it."*

        What replaced the refusal is the name→ID INDEX
        (``entity_identity.unique_name_holder_id``): the record resolves to the
        ONE carrier of ``TERM``, so both supersedes fold (blind, last-wins —
        ``supersededBy='s2'``). 2+ carriers would resolve to nothing; that
        shape is pinned by
        ``test_ambiguous_status_does_not_hide_a_presence_burial`` and by
        ``test_fold_id_miss_resolves_the_name_index_and_never_a_name_merge``.

        R8 requires ONE journal → ONE disposition, so all three replay engines
        are pinned: ``rebuild_all``, ``rebuild(log)`` and ``recover_from_log``
        must all accept it and agree on the graph.

        FAILS IF: an id-less supersede of a single-carrier name stops folding
        (the D2-(i) regression), the fold re-grows a name-keyed MERGE that
        folds several carriers, or any engine refuses a journal the others
        accept.
        """
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-a", name="TERM",
             status="live", event_id="e-a0")
        _raw(events, type="ObjectSuperseded", name="TERM",
             supersedes_by="s1", event_id="e-a1")
        _raw(events, type="ObjectSuperseded", name="TERM",
             supersedes_by="s2", event_id="e-a2")
        for engine in ("rebuild_all", "rebuild", "recover_from_log"):
            proj = _fresh(tmp_path, f"c3-{engine}")
            try:
                _drive(engine, tmp_path, events, proj)
                rows = proj.g.query(
                    "MATCH (o:Object {id:'obj-5285-a'}) "
                    "RETURN o.status, o.supersededBy").result_set
            finally:
                proj.close()
            assert rows == [["superseded", "s2"]], (
                f"{engine} did not fold both name-only supersedes onto the ONE "
                f"carrier of TERM (blind replay is last-wins): {rows}")
        # The reference fold must read the same disposition against the graph
        # the journal's own fold produced (R8: one journal, one disposition).
        proj = _fresh(tmp_path, "c3a")
        try:
            proj.rebuild_all(str(events), confirm_destructive=True)
            assert _objects(proj) == [("obj-5285-a", "TERM", "superseded")], \
                _objects(proj)
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is True, (r["divergence"], r["non_folded_events"])
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]

    def test_rename_materialized_forward_reference_folds_on_every_engine(
            self, env, tmp_path):
        """#3590 D2-(i) — a name-only supersede whose target NAME is materialized
        by a later `op=rename` LANDS in `rebuild_all`: the deferred sweep
        resolves against the journal's END graph, where that name has ONE
        carrier.

        ⛔ This pin was inverted on the premise that the id-keyed fold can never
        resolve a name, so the record had to refuse. That premise made a journal
        `main` folds un-rebuildable, and the owner excluded exactly this shape
        (#3590 D2: a journal that replays on `main` must keep replaying and the
        record must keep landing). The name→ID index keeps the reach at the one
        strength that is a fact — ONE carrier, or nothing.

        ⚠️ The APPLY engines are not asserted to reproduce the fold: they run
        chronologically, so at `e-c1` the name `NEWNAME` is not on the graph yet
        and they accept the record WITHOUT folding it (status stays `live`).
        That is the pre-existing, stated FIX-4 gap (`the ACCEPT verdict is
        shared, the graph is not`), and the ACCEPT/REFUSE half is what this
        file owns — so the assertion here is that no engine REFUSES a journal
        `rebuild_all` folds (a false refusal blocks recovering a sound journal)
        and that the reference fold agrees with the graph that IS produced.

        FAILS IF: the name-index resolution stops reaching a name a LATER record
        supplies (the forward-reference case this test exists for).
        REACHABLE: a merged/partial journal where a supersede by the NEW name
        precedes the Object whose rename supplies that name.
        """
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-w", name="WITNESS",
             status="live", event_id="e-c0")
        _raw(events, type="ObjectSuperseded", name="NEWNAME",
             supersedes_by="WITNESS", event_id="e-c1")
        _raw(events, type="ObjectRegistered", id="obj-5285-x", name="OLDNAME",
             status="live", event_id="e-c2")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-x",
             op="rename", state={"name": "NEWNAME"}, event_id="e-c3")
        proj = _drive("rebuild_all", tmp_path, events, _fresh(tmp_path, "c3c"))
        try:
            assert ("obj-5285-x", "NEWNAME", "superseded") in _objects(proj), \
                _objects(proj)
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is True, (r["divergence"], r["non_folded_events"])
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
        assert r["divergent_entity_count"] == 0, r["divergent_entities"]
        # ACCEPT parity: no apply engine may REFUSE a journal `rebuild_all`
        # folds (false refusals block recovering a sound journal). The graph
        # they produce is the stated FIX-4 gap, deliberately not asserted.
        for engine in ("rebuild", "recover_from_log"):
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, f"c3c-{engine}"))
            proj.close()

    def test_plain_forward_referenced_supersede_by_id_is_not_refused(
            self, env, tmp_path):
        """P2 — FIX 5(iii), id shape: the forward-reference GUARD.

        FAILS IF a fix for FIX 2 over-corrects and treats EVERY
        forward-referenced supersede as a miss: `rebuild_all`'s trailing
        sweep folds it, so refusing it here reds a journal the graph folds.
        REACHABLE: `[Sup(id=x), Reg(x,XOBJ)]` — the supersede precedes its own
        Object's registration."""
        _sdk, events = env
        _raw(events, type="ObjectSuperseded", id="obj-5285-fid",
             supersedes_by="y", event_id="e-fid")
        _raw(events, type="ObjectRegistered", id="obj-5285-fid",
             name="FID", status="live", event_id="e-fid-reg")
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            try:
                assert any(o[0] == "obj-5285-fid" for o in _objects(proj)), \
                    f"{engine}: {_objects(proj)}"
            finally:
                proj.close()
        proj = _drive("rebuild_all", tmp_path, events, _fresh(tmp_path, "c3fid"))
        try:
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is True, r["divergence"]
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]

    def test_plain_forward_referenced_supersede_by_name_refuses(
            self, env, tmp_path):
        """P2 — FIX 5(iii), NAME shape, restated for #3590.

        A forward-referenced supersede-BY-NAME is unaddressable: the trailing
        sweep resolves by ID only, exactly like the graph fold, so the record is
        `object-superseded-miss` on every engine even though its own later
        registration establishes the name. Its ID sibling
        (`…by_id_is_not_refused`, above) is the shape that DOES fold — the pair
        is the point: the retirement is about the NAME, never about forward
        references.

        FAILS IF: a name-keyed resolution re-grows (the journal would fold) or
        the refusal drifts to the exemption (the name IS established).
        REACHABLE: `[Sup(name=N), Reg(x,N)]` from a legacy/raw producer."""
        _sdk, events = env
        _raw(events, type="ObjectSuperseded", name="FNAME",
             supersedes_by="y", event_id="e-fnm")
        _raw(events, type="ObjectRegistered", id="obj-5285-fnm",
             name="FNAME", status="live", event_id="e-fnm-reg")
        for engine in ("rebuild_all", "rebuild"):
            proj = _fresh(tmp_path, f"c3fnm-{engine}")
            try:
                with pytest.raises(NonFoldedEventsError) as ei:
                    _drive(engine, tmp_path, events, proj)
                assert "object-superseded-miss" in str(ei.value), str(ei.value)
                assert "object-superseded-unregistered" not in str(ei.value), (
                    f"the name the registration established was treated as "
                    f"unestablished: {ei.value}")
            finally:
                proj.close()
        proj = _fresh(tmp_path, "c3fnm-check")
        try:
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is False, r["divergence"]
        assert any("object-superseded-miss" in e
                   for e in r["non_folded_events"]), r["non_folded_events"]

    def test_a_forward_reference_folds_the_last_incarnation(
            self, env, tmp_path):
        """REGRESSION — the `[Sup(id=x), Reg(x,X), Del(x), Reg(x,X)]` case.

        FAILS IF a held supersede is consumed at the FIRST creation that
        matches it. The deferred sweep runs after every delete, so it folds
        the supersede onto the LAST incarnation; a creation-time resolution
        left the re-created node `live` and invented a `content` divergence.
        REACHABLE: an id re-created after a delete (incarnation reuse), with a
        forward-referenced supersede for that id."""
        _sdk, events = env
        _raw(events, type="ObjectSuperseded", id="obj-5285-g",
             supersedes_by="y", event_id="e-g0")
        _raw(events, type="ObjectRegistered", id="obj-5285-g", name="G1",
             status="live", event_id="e-g1")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-g",
             op="delete", event_id="e-g2")
        _raw(events, type="ObjectRegistered", id="obj-5285-g", name="G2",
             status="live", event_id="e-g3")
        expected = [("obj-5285-g", "G2", "superseded")]
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            try:
                # APPLY/REFUSE parity is what this case pins: all engines
                # accept. (The apply-based engines leave the forward reference
                # live — the stated FIX-4 parity bound — so the STATUS is
                # asserted against `rebuild_all`'s sweep, not theirs.)
                assert any(o[0] == "obj-5285-g" for o in _objects(proj)), \
                    f"{engine}: {_objects(proj)}"
            finally:
                proj.close()
        proj = _drive("rebuild_all", tmp_path, events, _fresh(tmp_path, "c3g"))
        try:
            assert _objects(proj) == expected, _objects(proj)
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is True, r["divergence"]
        assert r["divergent_entity_count"] == 0, r["divergent_entities"]

    def test_early_ambiguity_does_not_exempt_a_later_zero_carrier_miss(
            self, env, tmp_path):
        """FIX C guard — the ambiguity carve-out is LOCAL to its event.

        FAILS IF the >1-carrier carve-out is recorded in the never-cleared
        `ambiguous` set and consulted by later events: a second name-only
        supersede of a once-ambiguous name whose carriers are ALL gone is a
        genuine 0-row miss `rebuild_all` refuses, so exempting it would pass a
        journal the graph refuses.
        REACHABLE: two carriers of one name (ambiguous first supersede), both
        deleted, then a second name-only supersede of that name."""
        sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-amb-a", name="AMB3",
             status="live", event_id="e-amb3-0")
        _raw(events, type="ObjectRegistered", id="obj-5285-amb-b", name="AMB3",
             status="live", event_id="e-amb3-1")
        _raw(events, type="ObjectSuperseded", name="AMB3",
             supersedes_by="s", event_id="e-amb3-2")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-amb-a",
             op="delete", event_id="e-amb3-3")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-amb-b",
             op="delete", event_id="e-amb3-4")
        _raw(events, type="ObjectSuperseded", name="AMB3",
             supersedes_by="s", event_id="e-amb3-5")
        with pytest.raises(NonFoldedEventsError):
            sdk._get_proj().rebuild_all(str(events),
                                        confirm_destructive=True)
        proj = _fresh(tmp_path, "c3amb")
        try:
            # `rebuild_all` refuses, so the reference fold must refuse too.
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is False, r
        assert r["divergence"] == "non-folded", r["divergence"]
        assert any("object-superseded-miss" in e
                   for e in r["non_folded_events"]), r["non_folded_events"]


# ═══════════════════════════════════════════════════════════════════════
# Cycle-3 — the FOURTH replay engine: backup.restore (#5285 FIX 3)
# ═══════════════════════════════════════════════════════════════════════

class TestBackupRestoreFailsLoud:
    def _backup_with(self, root: Path, records) -> Path:
        src = root / "backup_src"
        src.mkdir(parents=True)
        (src / "events.jsonl").write_text(
            "\n".join(json.dumps(rec) for rec in records) + "\n")
        (src / "manifest.json").write_text(
            '{"backed_up_at":"2026-01-01","db":"tortoise.db",'
            '"events":"events.jsonl"}')
        # No tortoise.db: force the JSONL replay fallback.
        return src

    def test_restore_refuses_a_no_address_target_supersede(self, tmp_path):
        """P2 — FIX 3, re-pointed at the `#10204` no-address shape. FAILS IF
        the restore replay passes `apply(ev)` with no journal context:
        `apply()`'s ObjectSuperseded refusal gate is keyed on
        `journal_object_surviving is not None`, so without it a no-address
        supersede restored silently and `restore` returned `{"status": "ok"}`
        while `rebuild_all` refuses the identical journal.

        ⚠️ RETIRED CONTRACT: this used to use an ID-ONLY absent-target record,
        which `#10204` now EXEMPTS — an exempt record passes whatever the
        restore supplies, so it could no longer discriminate the wiring. A
        no-address record is the SMALLEST journal whose refusal depends on that
        context (an established-address miss depends on it too, but needs a
        carrier collapse to reach the gate), so the pin keeps its discriminating
        power and gains an end-to-end pin on the new shape through the fourth
        replay engine.
        REACHABLE: a backup journal whose only record is a supersede carrying no
        name and no id."""
        src = self._backup_with(tmp_path, [{
            "type": "ObjectSuperseded", "supersedes_by": "y",
            "event_id": "e-restore-d",
            "ts": "2026-01-01T00:00:00+00:00"}])
        with pytest.raises(NonFoldedEventsError) as ei:
            restore(str(src), db_path=str(tmp_path / "r.db"),
                    events_path=str(tmp_path / "r.jsonl"), into_falkor=True)
        assert "object-superseded-no-address" in str(ei.value), str(ei.value)

    def test_restore_accepts_an_exempt_id_only_supersede(self, tmp_path, caplog):
        """`#10204`: the FOURTH replay engine accepts a journal whose only
        misaddress is an id-only supersede no event established — the same
        verdict `rebuild_all` and `rebuild()` reach.

        FAILS IF: `restore`'s own run boundary refuses a journal the graph
        engines accept. It supplies `journal_object_established` too
        (`backup.py`), so the derived `object_address` must land on
        UNESTABLISHED here exactly as it does there; a wiring gap in either
        direction is a lost-DB restore blocked (refusal) or an unmetered
        divergence (exemption without the record). BOTH directions are
        asserted below: the `status == "ok"` leg covers the refusal, and the
        caplog leg covers the record on THIS boundary — `restore` reaches
        `record_non_folded` through `apply()`, not through `rebuild_all`'s
        sweep, so the sweep-side assertion in the exhaustiveness test does not
        speak for it (MEASURED: unpinned, dropping this boundary's record
        survived).

        REACHABLE: a backup journal holding one registration and an id-only
        supersede for an id nothing established.
        """
        src = self._backup_with(tmp_path, [{
            "type": "ObjectRegistered", "id": "obj-10204-anchor",
            "name": "ANCHOR-10204", "status": "live",
            "event_id": "e-10204-anchor",
            "ts": "2026-01-01T00:00:00+00:00"}, {
            "type": "ObjectSuperseded", "id": "obj-10204-never-seen",
            "supersedes_by": "y", "event_id": "e-10204-sup",
            "ts": "2026-01-01T00:00:00+00:00"}])
        with caplog.at_level(logging.WARNING,
                             logger="tortoise.projection.nonfolded"):
            result = restore(str(src), db_path=str(tmp_path / "d2.db"),
                             events_path=str(tmp_path / "d2.jsonl"),
                             into_falkor=True)
        assert result["status"] == "ok", result
        boundary = " ".join(r.getMessage() for r in caplog.records)
        assert "restore: 1 non-folded journal event(s) recorded but EXEMPT" in boundary, (
            f"restore accepted the journal but did not record the exempt miss "
            f"(#5380): {boundary}")
        assert "[object-superseded-unregistered]" in boundary, boundary

    def test_restore_still_succeeds_on_a_healthy_journal(self, tmp_path):
        """FAILS IF the new run boundary refuses a journal the graph folds:
        a false refusal here breaks every legitimate restore.
        REACHABLE: one plain ObjectRegistered."""
        src = self._backup_with(tmp_path, [{
            "type": "ObjectRegistered", "id": "obj-5285-ok",
            "name": "OKOBJ", "status": "live", "event_id": "e-restore-ok",
            "ts": "2026-01-01T00:00:00+00:00"}])
        result = restore(str(src), db_path=str(tmp_path / "ok.db"),
                         events_path=str(tmp_path / "ok.jsonl"),
                         into_falkor=True)
        assert result["status"] == "ok", result


# ═══════════════════════════════════════════════════════════════════════
# Cycle-4 — the single supersede sweep + the name-MERGE id collapse
# (#5285 re-review). Rounds 2-4 each found the previous round's fix had
# introduced a new asymmetry. Cycle 4 found the root fix was only HALF
# applied (inline supersedes still resolved mid-journal) and a fail-open
# (the id branch ignored the graph's name-MERGE collapse). These tests pin
# BOTH — the cycle-3 tests pinned only the forward ordering and caught
# neither.
# ═══════════════════════════════════════════════════════════════════════

class TestCycleFourSingleSweepAndNameCollapse:
    def test_inline_supersede_survives_a_delete_and_recreate(
            self, env, tmp_path):
        """FIX 1 (#5285 cycle 4). FAILS IF a supersede is applied INLINE
        instead of in the one trailing sweep.

        The emitter's real shape (`commit_ops` journals `ObjectSuperseded`
        with id+name): `[Reg(x,X), Sup(id=x,name=X), Del(x), Reg(x,X)]`.
        `rebuild_all`'s sweep folds the supersede AFTER the delete+recreate,
        so the re-created incarnation is `superseded`. An inline reference
        fold resolved it at its own seq, the delete discarded the record, and
        the recreate left it `live` — `check_consistency(<rebuild_all graph>)`
        then reported `divergence='content'` with `{field:'status',
        expected:'live', found:'superseded'}` on a correct journal.

        All engines must ACCEPT; the rebuild_all graph must be `superseded`;
        and the reference fold must agree with that graph."""
        _sdk, events = env
        oid = "obj-5285-inline"
        _raw(events, type="ObjectRegistered", id=oid, name="INLINE",
             status="live", event_id="e-i0")
        _raw(events, type="ObjectSuperseded", id=oid, name="INLINE",
             supersedes_by="y", event_id="e-i1")
        _raw(events, type="EntityMutated", label="Object", id=oid,
             op="delete", event_id="e-i2")
        _raw(events, type="ObjectRegistered", id=oid, name="INLINE",
             status="live", event_id="e-i3")
        # APPLY/REFUSE parity: every engine ACCEPTS the journal.
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            proj.close()
        proj = _drive("rebuild_all", tmp_path, events,
                      _fresh(tmp_path, "c4single"))
        try:
            assert _objects(proj) == [(oid, "INLINE", "superseded")], \
                _objects(proj)
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["ok"] is True, r["divergence"]
        assert r["divergent_entity_count"] == 0, r["divergent_entities"]
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]

    @pytest.mark.parametrize("also_supersede_survivor", [False, True])
    def test_two_carriers_of_one_name_fold_their_own_supersedes(
            self, env, tmp_path, also_supersede_survivor):
        """#3590 — the name-carrier COLLAPSE this test was written for is
        retired, and the assertion is INVERTED to pin that.

        ⛔ The pre-#3590 graph MERGEed Objects by NAME, so `Reg(a,N), Reg(b,N)`
        was ONE node carrying `b` and `Sup(id=a)` folded 0 rows (a refusal).
        The MERGE key is the ID now: two registrations of one name are TWO
        entities, so BOTH ids fold their own supersede and nothing is refused.

        FAILS IF: the name-keyed MERGE returns (then `Sup(id=a)` misses the
        collapsed carrier and the run refuses a journal it used to fold), or
        the two halves of one disposition disagree (R8: every engine here, plus
        the reference fold, must ACCEPT the same journal)."""
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-a", name="COLL",
             status="live", event_id="e-c-a")
        _raw(events, type="ObjectRegistered", id="obj-5285-b", name="COLL",
             status="live", event_id="e-c-b")
        _raw(events, type="ObjectSuperseded", id="obj-5285-a",
             supersedes_by="y", event_id="e-c-sup-a")
        if also_supersede_survivor:
            _raw(events, type="ObjectSuperseded", id="obj-5285-b",
                 supersedes_by="y", event_id="e-c-sup-b")
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            try:
                assert _objects(proj) == [
                    ("obj-5285-a", "COLL", "superseded"),
                    ("obj-5285-b", "COLL",
                     "superseded" if also_supersede_survivor else "live"),
                ], f"{engine}: {_objects(proj)}"
            finally:
                proj.close()
        # The health gate compares CONTENT, so it must read a graph the journal
        # REBUILT — a fresh empty projection diverges on presence for every id
        # the journal registered (the helper states the same reason).
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "c4collapse")


class TestCycleFiveStateOpNameMergeParity:
    """#5285 cycle 5 — the STATE-OP arm of the name-MERGE parity, RESTATED for
    #3590.

    Cycle 4/5 pinned the collapsed-away carrier: the pre-#3590 graph MERGEed
    Objects by NAME, so `Reg(a,N), Reg(b,N)` was ONE node carrying `b` and a
    state op naming `a` folded 0 rows. The MERGE key is the ID now, so two
    registrations of one name are two entities and BOTH state ops fold — the
    refusal these pins asserted is unreachable through a carrier that no longer
    exists.

    What the class still owns: the DISPOSITION must be R8-uniform across all
    four engines (a state op that folds on one surface may not be refused on
    another). The genuine `state-op-miss` polarity keeps its own home
    (`test_state_op_target_id_with_an_unestablished_name_still_refuses`, and
    the unusable-name pins below)."""

    def test_state_op_on_each_own_id_is_accepted_by_all_four(
            self, env, tmp_path):
        """FAILS IF: a state op on an id the journal registered is refused, or
        the reference fold disagrees with the graph engines about it (R8).

        ⛔ The pre-#3590 tree refused this journal: the two same-name
        registrations collapsed onto ONE node, so a restatus naming the FIRST
        id matched 0 rows (`state-op-miss`). Under the id-keyed MERGE each
        registration is its own entity and the state op folds.

        The registrations and the restatus carry the SAME status so the
        ORTHOGONAL per-id content comparison cannot supply a different reason
        the gate fails — the R8 DISPOSITION is the property under test.
        REACHABLE: one name registered twice under fresh ids, then a restatus
        addressing one of them."""
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-sa", name="SCOLL",
             status="superseded", event_id="e-so-0")
        _raw(events, type="ObjectRegistered", id="obj-5285-sb", name="SCOLL",
             status="superseded", event_id="e-so-1")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-sa",
             op="restatus", state={"status": "superseded"}, event_id="e-so-2")
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            try:
                assert _objects(proj) == [
                    ("obj-5285-sa", "SCOLL", "superseded"),
                    ("obj-5285-sb", "SCOLL", "superseded"),
                ], f"{engine}: {_objects(proj)}"
            finally:
                proj.close()
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "so-ok")

    def test_state_op_on_the_surviving_id_is_accepted_by_all_four(
            self, env, tmp_path):
        """CONTROL for the pair above, restated for #3590: a state op naming
        the SECOND of two same-name carriers folds on every engine.

        ⛔ Pre-#3590 this was the CONTROL because only `b` survived the name
        MERGE; both ids exist now, so the property is simply that a state op
        on either carrier folds and the health gate agrees with the replayed
        graph. The registrations carry `superseded` so the ORTHOGONAL per-id
        status comparison stays clean — the property under test is the state
        op's DISPOSITION (fold vs refuse), not the applied value."""
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-ca", name="SCOLLC",
             status="superseded", event_id="e-co-0")
        _raw(events, type="ObjectRegistered", id="obj-5285-cb", name="SCOLLC",
             status="superseded", event_id="e-co-1")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-cb",
             op="restatus", state={"status": "superseded"}, event_id="e-co-2")
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events,
                          _fresh(tmp_path, f"co-{engine}"))
            try:
                assert _objects(proj) == [
                    ("obj-5285-ca", "SCOLLC", "superseded"),
                    ("obj-5285-cb", "SCOLLC", "superseded"),
                ], f"{engine}: {_objects(proj)}"
            finally:
                proj.close()
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "co-ok")

    def test_state_op_that_names_the_carrier_before_the_collapse_is_folded(
            self, env, tmp_path):
        """DEFECT 1, SEQ-AWARENESS. FAILS IF the guard uses the journal-END
        `surviving_ids` key set instead of the carrier at the state op's own
        seq.

        `Reg(a,N), restatus(a), Reg(b,N)`: at the state op `a` IS the carrier,
        so `MATCH (n:Object {id:'a'})` folds one row; the LATER registration
        collapses the node onto `b`. `journal_object_surviving_keys` reports
        `{b}`, so a journal-END guard would record `state-op-miss` and refuse a
        state op that genuinely folded. All three replay engines must ACCEPT,
        and the health gate must not REFUSE it — its residual `content`
        divergence on the collapsed-away id is the orthogonal, pre-existing
        per-id status model, not a fold miss.
        REACHABLE: an interleaved journal where the state op precedes the
        same-name re-registration."""
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-qa", name="SQ",
             status="live", event_id="e-sq-0")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-qa",
             op="restatus", state={"status": "superseded"}, event_id="e-sq-1")
        _raw(events, type="ObjectRegistered", id="obj-5285-qb", name="SQ",
             status="live", event_id="e-sq-2")
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events,
                          _fresh(tmp_path, f"sq-{engine}"))
            proj.close()
        proj = _fresh(tmp_path, "sq-check")
        try:
            r = check_consistency(str(events / "events.jsonl"), proj)
        finally:
            proj.close()
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
        assert r["divergence"] != "non-folded", r["divergence"]
        assert not any("state-op-miss" in e for e in r["non_folded_events"]), \
            r["non_folded_events"]

    def test_name_only_supersede_resolution_is_linear(self):
        """DEFECT 2, restated for #3590. Guards the NAME INDEX that keeps
        `_object_target` linear.

        `_object_target` rebuilt the carrier list by scanning ALL of `entities`
        on EVERY name-only supersede: O(N^2), measured 4000 supersedes = 17.5s
        and 8000 = 61.7s — a denial of service on the health gate itself. The
        ONCE-built `name -> [keys]` index preserves the scan's exact semantics
        and makes the fold linear; this test pins the VERDICT at a size that
        stays fast in CI, not the wall clock.

        ⛔ The verdict CHANGED with #3590: the name branch of the supersede
        fold is retired, so every one of these name-only records is the
        non-exempt `object-superseded-miss` (each name IS established by its
        registration) instead of folding. What is pinned is that the verdict is
        UNIFORM — one shape per record, nothing silently folded and nothing
        drifting — which is what the index must deliver at scale.
        REACHABLE: a journal of independent name-only supersedes (the
        multi-op commit shape from a legacy/raw producer)."""
        n = 500
        events: list = []
        for i in range(n):
            events.append({
                "type": "ObjectRegistered", "id": f"obj-5285-lin-{i}",
                "name": f"LIN{i}", "status": "live",
                "event_id": f"e-lin-reg-{i}"})
        for i in range(n):
            events.append({
                "type": "ObjectSuperseded", "name": f"LIN{i}",
                "supersedes_by": "s", "event_id": f"e-lin-sup-{i}"})
        with collect_non_folded() as nf:
            entities, _deleted, _ambiguous = _fold_journal_entities(events)
        refused = refused_events(nf)
        assert len(refused) == n, (len(refused), n)
        assert {e.shape for e in refused} == {"object-superseded-miss"}, \
            {e.shape for e in refused}
        assert len(entities) == n, len(entities)
        assert all(r.get("status") == "live"
                   for r in entities.values()), entities

    def test_rename_onto_an_existing_name_does_not_refuse_a_live_id(self):
        """MIRROR GUARD (found while fixing DEFECT 1). FAILS IF the carrier
        index collapses a name to ONE id.

        A rename `SET`s `name` in place, so when it lands on a name another id
        already carries the graph holds TWO live nodes under that name and
        BOTH MATCH by id (`_fold_entity_mutation` matches the primary key, not
        the name) — no miss. A single-id carrier map refused the SECOND one, a
        false `state-op-miss`: a disagreement with the graph fold in the
        fail-CLOSED direction, the one error this guard must never make. The
        carrier value is therefore a SET of live ids.
        REACHABLE: `Reg(a,N1), Reg(b,N2), rename(a -> N2), restatus(b)`."""
        events = [
            {"type": "ObjectRegistered", "id": "obj-5285-rn-a",
             "name": "RN1", "status": "live", "event_id": "e-rn-0"},
            {"type": "ObjectRegistered", "id": "obj-5285-rn-b",
             "name": "RN2", "status": "live", "event_id": "e-rn-1"},
            {"type": "EntityMutated", "label": "Object",
             "id": "obj-5285-rn-a", "op": "rename",
             "state": {"name": "RN2"}, "event_id": "e-rn-2"},
            {"type": "EntityMutated", "label": "Object",
             "id": "obj-5285-rn-b", "op": "restatus",
             "state": {"status": "superseded"}, "event_id": "e-rn-3"},
        ]
        with collect_non_folded() as nf:
            entities, _deleted, _ambiguous = _fold_journal_entities(events)
        assert not refused_events(nf), [str(e) for e in nf]
        assert entities[("Object", "obj-5285-rn-b")]["status"] == \
            "superseded", entities


# ═══════════════════════════════════════════════════════════════════════
# #3585 P1-1 — the ordered EXISTENCE map, both directions
#
# `plan_point_restamp_folds` already gated the terminalizer family on the
# SEQUENCE of first materialization. The retract / state-op / belief arms were
# not: `rebuild_all` hoists every Point/Operator creation, so it folded a
# record that PRECEDED its target's creation — inventing an order the live
# system never had — while the chronological reference fold missed it. These
# tests pin the two directions the map restores:
#
#   * `first > seq`  — a forward reference is a no-op on EVERY engine (live
#                      no-op'd it); it is neither folded nor recorded.
#   * `first <= seq` — a target created and then HARD-DELETED is a genuine
#                      miss and still refuses (the create→delete→fold trap the
#                      previous, set-membership attempt could not see).
#
# ⛔ The ACCEPT cases assert `check_consistency` `ok=True` against a graph that
# was actually REBUILT — not merely "no non-folded record", and never against a
# fresh empty graph (which cannot see a content divergence).
# ═══════════════════════════════════════════════════════════════════════


def _raw_point(events, pid, *, status="live", event_id=None):
    """Append one complete `PointAdded` snapshot for `pid`."""
    _raw(events, type="PointAdded",
         event_id=event_id or f"e-add-{pid}",
         point={"id": pid, "content": "x", "kind": "statement",
                "status": status, "createdAt": "2026-01-01T00:00:00Z"})


def _point_status(proj, pid):
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.status",
        params={"id": pid}).result_set
    return rows[0][0] if rows else None


def _replay_accept(engine, tmp_path, events, tag):
    """Run `engine`; assert it ACCEPTS, and return the replayed projection."""
    proj = _fresh(tmp_path, tag)
    if engine == "recover_from_log":
        res = recover_from_log(str(events), proj)
        assert res["recovered"] is True, f"{engine} refused: {res}"
        return proj
    if engine == "rebuild_all":
        proj.rebuild_all(str(events), confirm_destructive=True)
    else:
        proj.rebuild(EventLog(str(events / "events.jsonl")),
                     confirm_destructive=True)
    return proj


def _expect_refusal(engine, tmp_path, events, tag, shape):
    """Run a rebuild-family `engine`; assert it REFUSES with `shape`."""
    proj = _fresh(tmp_path, tag)
    try:
        with pytest.raises(NonFoldedEventsError) as ei:
            if engine == "rebuild_all":
                proj.rebuild_all(str(events), confirm_destructive=True)
            else:
                proj.rebuild(EventLog(str(events / "events.jsonl")),
                             confirm_destructive=True)
    finally:
        proj.close()
    assert shape in str(ei.value), str(ei.value)


def _expect_recovery_refusal(tmp_path, events, tag, shape):
    proj = _fresh(tmp_path, tag)
    try:
        res = recover_from_log(str(events), proj)
    finally:
        proj.close()
    assert res["recovered"] is False, res
    assert shape in res["reason"], res["reason"]


def _expect_check_refusal(sdk, events, shape):
    r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert r["ok"] is False, r
    assert r["divergence"] == "non-folded", r["divergence"]
    assert r["non_folded_refused_count"] >= 1, r["non_folded_events"]
    assert any(shape in e for e in r["non_folded_events"]), r["non_folded_events"]


class TestNameMergeCarrierKeying:
    """#5285 review round 6: the carrier index must be keyed on the GRAPH's MERGE
    key, not on the fold's display name. Two FALSE REFUSALS that the state-op
    guard introduced are pinned here — one for a `title`-only registration
    (whose empty `name` creates no graph node), one for a falsy
    `state["name"]` rename (which `SET n += $s` still applies)."""

    def test_a_state_op_on_an_id_whose_creation_made_no_node_refuses(
            self, env, tmp_path):
        """FAILS IF: the carrier guard treats "this id has no MERGE key" as an
        unmodelled shape and fails open. Review round 7, P1 — a fail-OPEN
        regression, in which the reference fold accepted a `state-op-miss` all
        three replay engines refuse (these journals AGREED before it).

        `_writable_id` skips an empty / non-writable merge key, so the graph
        creates NO node for these ids and its state-op `MATCH (n:$label
        {id:$rid})` is a 0-row miss."""
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-n1", name="",
             title="NODE1", status="live", event_id="e-n1-0")
        _raw(events, type="ObjectRegistered", id="obj-5285-n2", name="",
             title="NODE1", status="live", event_id="e-n1-1")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-n1",
             op="restatus", state={"status": "superseded"}, event_id="e-n1-2")
        _expect_refusal("rebuild_all", tmp_path, events, "n1-ra",
                        "state-op-miss")
        _expect_refusal("rebuild", tmp_path, events, "n1-rb", "state-op-miss")
        _expect_recovery_refusal(tmp_path, events, "n1-rec", "state-op-miss")
        _expect_check_refusal(env[0], events, "state-op-miss")

    def test_an_unusable_name_recreation_preserves_the_merge_key(
            self, env, tmp_path):
        """FAILS IF: a re-CREATION of a live id with an UNUSABLE name evicts it
        from the carrier index, so a state op on it is refused.

        ⛔ Restated for #3590, and the verdict is INVERTED. The pre-#3590
        fixture expected `state-op-miss` on every engine because the two
        same-name registrations collapsed onto one node; the MERGE key is the
        ID now, so `obj-5285-m1` keeps its own node through the empty-name
        re-creation and the restatus folds. What still has to hold is the
        property review round 7 found: the empty name must not EVICT the live
        id, or the carrier index lies and the fold refuses a state op the graph
        applied (a FALSE REFUSAL, the direction this guard must never fail).
        """
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-m1", name="MERGE1",
             status="live", event_id="e-m1-0")
        _raw(events, type="ObjectRegistered", id="obj-5285-m1", name="",
             status="live", event_id="e-m1-1")
        _raw(events, type="ObjectRegistered", id="obj-5285-m2", name="MERGE1",
             status="live", event_id="e-m1-2")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-m1",
             op="restatus", state={"status": "superseded"}, event_id="e-m1-3")
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            try:
                assert _objects(proj) == [
                    ("obj-5285-m1", "MERGE1", "superseded"),
                    ("obj-5285-m2", "MERGE1", "live"),
                ], f"{engine}: {_objects(proj)}"
            finally:
                proj.close()
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "m1-ok")

    def test_a_title_only_registration_does_not_evict_a_live_carrier(
            self, env, tmp_path):
        """FAILS IF: the carrier index is keyed on the fold's DISPLAY name
        (``payload["name"] or payload["title"]``) rather than the graph's
        MERGE key — review round 6, P1, a FALSE REFUSAL that guard introduced.

        ``_upsert_object`` MERGEs on ``name`` ALONE and ``_writable_id`` skips
        an EMPTY name (no node is created), so a registration whose ``name`` is
        empty and whose ``title`` is set NEVER touches the graph's node. Keyed
        on the display name, that record EVICTED the real carrier for the same
        display name, and a state op on the LIVE id was then refused
        ``state-op-miss`` while all three replay engines folded it.
        REACHABLE: ``EventAPI.add_object(name="", title=T)`` validates
        neither."""
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-t1", name="TITLE1",
             status="live", event_id="e-t1-0")
        _raw(events, type="ObjectRegistered", id="obj-5285-t2", name="",
             title="TITLE1", status="live", event_id="e-t1-1")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-t1",
             op="restatus", state={"status": "superseded"}, event_id="e-t1-2")
        for engine in ("rebuild_all", "rebuild", "recover_from_log"):
            proj = _replay_accept(engine, tmp_path, events, f"t1-{engine}")
            proj.close()
        # The DISPOSITION is the property under test: no engine may refuse this
        # journal with a NON-FOLDED record. (The reference fold still reports a
        # CONTENT divergence for the title-only record's phantom entry — the
        # pre-existing fail-open review round 6 also names, in which a record
        # with an empty MERGE key creates no graph node while the index still
        # holds it. The `non-folded` class OUTRANKED that divergence before this
        # fix, which is exactly why the false refusal masked it. It is not this
        # change's defect and is not fixed here.) So assert the class, not
        # `ok`, and pin that the state op really folded.
        proj = _fresh(tmp_path, "t1-check")
        try:
            proj.rebuild_all(str(events), confirm_destructive=True)
            r = check_consistency(str(events / "events.jsonl"), proj)
            rows = proj.g.query(
                "MATCH (o:Object) RETURN o.id, o.name, o.status"
            ).result_set
        finally:
            proj.close()
        assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
        assert r["divergence"] != "non-folded", r["divergence"]
        assert rows == [["obj-5285-t1", "TITLE1", "superseded"]], rows

    def test_a_falsy_rename_does_not_evict_a_live_carrier(self, env, tmp_path):
        """FAILS IF: a state op whose ``state`` carries a ``name`` key that is
        NOT a usable non-empty str is ignored by the carrier bookkeeping —
        review round 6, P2, a FALSE REFUSAL that guard introduced.

        The graph runs ``SET n += $s``, so ANY ``name`` key rewrites the MERGE
        key, and the node KEEPS its id. Leaving the index keyed on the OLD name
        let a later registration of it evict the still-live id, and a state op
        on that id was then refused ``state-op-miss`` while every replay engine
        folded it."""
        _sdk, events = env
        _raw(events, type="ObjectRegistered", id="obj-5285-f1", name="FALSY1",
             status="live", event_id="e-f1-0")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-f1",
             op="restatus", state={"name": ""}, event_id="e-f1-1")
        _raw(events, type="ObjectRegistered", id="obj-5285-f2", name="FALSY1",
             status="live", event_id="e-f1-2")
        _raw(events, type="EntityMutated", label="Object", id="obj-5285-f1",
             op="restatus", state={"status": "superseded"}, event_id="e-f1-3")
        for engine in ("rebuild_all", "rebuild", "recover_from_log"):
            proj = _replay_accept(engine, tmp_path, events, f"f1-{engine}")
            proj.close()
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "f1-check")


class TestExistenceMapForwardReferences:
    """A record whose target the journal materializes LATER must be a no-op
    on every engine — the live write no-op'd it, and `rebuild_all`'s creation
    hoist must not invent the ordering. `check_consistency` is asserted `ok`
    against the REBUILT graph, so a content divergence is visible."""

    def test_retract_before_its_own_creation_leaves_it_live(self, env,
                                                            tmp_path):
        """FAILS IF: `rebuild_all` folds the hoisted-creation retract (p ends
        `retracted`) while the chronological arms skip it, or the reference
        fold records a forward-reference miss. The journal is
        `[PointRetracted(p); PointAdded(p)]` — reachable by hand-writing the
        file (a partial/merged journal), never by the SDK's append order."""
        _sdk, events = env
        pid = "p-fwd-retract"
        _raw(events, type="PointRetracted", id=pid, event_id="e-fwd-ret")
        _raw_point(events, pid, event_id="e-fwd-ret-add")
        for engine in ENGINES:
            proj = _replay_accept(engine, tmp_path, events, f"fwdret-{engine}")
            try:
                assert _point_status(proj, pid) == "live", (
                    f"{engine} folded a retract that preceded its creation")
            finally:
                proj.close()
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "fwdret-check")

    def test_state_op_before_its_own_creation_is_a_noop(self, env, tmp_path):
        """FAILS IF: `rebuild_all` applies the hoisted-creation state op (the
        point would exist to receive it) while the chronological arms skip it.
        The journal is `[EntityMutated restatus(p); PointAdded(p)]`."""
        _sdk, events = env
        pid = "p-fwd-state"
        _raw(events, type="EntityMutated", op="restatus", label="Point",
             id=pid, state={"status": "archived"}, event_id="e-fwd-state")
        _raw_point(events, pid, event_id="e-fwd-state-add")
        for engine in ENGINES:
            proj = _replay_accept(engine, tmp_path, events, f"fwdstate-{engine}")
            try:
                assert _point_status(proj, pid) == "live", (
                    f"{engine} applied a state op that preceded its creation")
            finally:
                proj.close()
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "fwdstate-check")

    def test_belief_before_its_own_creation_is_not_applied(self, env,
                                                           tmp_path):
        """FAILS IF: `rebuild_all` folds the belief write onto the hoisted
        Point while the live system (and the chronological arms and the
        reference fold) no-op'd it. Asserts the FORWARD value (`posterior_
        alpha=5`) never lands, and that the rebuilt graph is content-consistent
        with the reference fold."""
        _sdk, events = env
        pid = "p-fwd-conf"
        _raw(events, type="ConfidenceChanged", id=pid, posterior_alpha=5,
             event_id="e-fwd-conf")
        _raw_point(events, pid, event_id="e-fwd-conf-add")
        for engine in ENGINES:
            proj = _replay_accept(engine, tmp_path, events, f"fwdconf-{engine}")
            try:
                rows = proj.g.query(
                    "MATCH (p:Point {id:$id}) RETURN p.posterior_alpha",
                    params={"id": pid}).result_set
                assert rows, f"{engine} lost the point"
                assert rows[0][0] != 5, (
                    f"{engine} applied a belief write that preceded its Point")
            finally:
                proj.close()
        _assert_check_consistency_rebuilt_ok(tmp_path, events, "fwdconf-check")

    def test_created_then_hard_deleted_then_state_op_still_refuses(
            self, env, tmp_path):
        """THE TRAP. FAILS IF: the gate is set membership ("the journal creates
        this id") instead of the ORDER of first materialization. `p` IS created
        (seq 0) and then hard-deleted (seq 1); the state op at seq 2 is a
        genuine miss — live had the node when the delete happened and no node
        when the state op ran. `rebuild_all` and `rebuild` must both refuse
        `state-op-miss`."""
        _sdk, events = env
        pid = "p-del-state"
        _raw_point(events, pid, event_id="e-del-state-add")
        _raw(events, type="EntityMutated", op="delete", label="Point", id=pid,
             event_id="e-del-state-del")
        _raw(events, type="EntityMutated", op="restatus", label="Point",
             id=pid, state={"status": "archived"},
             event_id="e-del-state-mut")
        _expect_refusal("rebuild_all", tmp_path, events, "delstate-ra",
                        "state-op-miss")
        _expect_refusal("rebuild", tmp_path, events, "delstate-rb",
                        "state-op-miss")
        _expect_recovery_refusal(tmp_path, events, "delstate-rec",
                                 "state-op-miss")
        _expect_check_refusal(env[0], events, "state-op-miss")

    def test_created_then_hard_deleted_then_retract_still_refuses(
            self, env, tmp_path):
        """THE TRAP, retract shape. FAILS IF: the gate exempts a retract of an
        id that was created and then hard-deleted. Both rebuild engines refuse
        `point-retracted-miss`."""
        _sdk, events = env
        pid = "p-del-retract"
        _raw_point(events, pid, event_id="e-del-ret-add")
        _raw(events, type="EntityMutated", op="delete", label="Point", id=pid,
             event_id="e-del-ret-del")
        _raw(events, type="PointRetracted", id=pid, event_id="e-del-ret-ret")
        _expect_refusal("rebuild_all", tmp_path, events, "delret-ra",
                        "point-retracted-miss")
        _expect_refusal("rebuild", tmp_path, events, "delret-rb",
                        "point-retracted-miss")
        _expect_recovery_refusal(tmp_path, events, "delret-rec",
                                 "point-retracted-miss")
        _expect_check_refusal(env[0], events, "point-retracted-miss")

    def test_retract_of_a_never_created_target_refuses(self, env, tmp_path):
        """CONTROL. FAILS IF: the forward-reference gate is broadened to
        exempt a target no record ever materializes — the genuine miss every
        engine must refuse."""
        _sdk, events = env
        _raw(events, type="PointRetracted", id="p-never-fwd",
             event_id="e-never-ret")
        _expect_refusal("rebuild_all", tmp_path, events, "neverret-ra",
                        "point-retracted-miss")
        _expect_refusal("rebuild", tmp_path, events, "neverret-rb",
                        "point-retracted-miss")
        _expect_recovery_refusal(tmp_path, events, "neverret-rec",
                                 "point-retracted-miss")
        _expect_check_refusal(env[0], events, "point-retracted-miss")

    def test_state_op_on_a_never_created_target_refuses(self, env, tmp_path):
        """CONTROL, state-op shape. FAILS IF: the gate exempts an id the
        journal never materializes."""
        _sdk, events = env
        _raw(events, type="EntityMutated", op="restatus", label="Point",
             id="p-never-fwd", state={"status": "archived"},
             event_id="e-never-mut")
        _expect_refusal("rebuild_all", tmp_path, events, "nevermut-ra",
                        "state-op-miss")
        _expect_refusal("rebuild", tmp_path, events, "nevermut-rb",
                        "state-op-miss")
        _expect_recovery_refusal(tmp_path, events, "nevermut-rec",
                                 "state-op-miss")
        _expect_check_refusal(env[0], events, "state-op-miss")

    def test_confidence_write_on_a_never_created_target_refuses(
            self, env, tmp_path):
        """CONTROL + ASYMMETRY. FAILS IF: an apply()-based engine accepts a
        belief write whose target no record materializes. `rebuild_all` and the
        reference fold both refuse `point-belief-miss`; before the matching
        change to `apply()`'s arm, `rebuild` / `recover_from_log` discarded the
        fold count and recorded nothing — so they reported a successful
        recovery of a journal the health gate rejects."""
        _sdk, events = env
        _raw(events, type="ConfidenceChanged", id="p-never-conf",
             posterior_alpha=5, event_id="e-never-conf")
        _expect_refusal("rebuild_all", tmp_path, events, "neverconf-ra",
                        "point-belief-miss")
        _expect_refusal("rebuild", tmp_path, events, "neverconf-rb",
                        "point-belief-miss")
        _expect_recovery_refusal(tmp_path, events, "neverconf-rec",
                                 "point-belief-miss")
        _expect_check_refusal(env[0], events, "point-belief-miss")

    def test_annotator_on_a_never_created_target_refuses(self, env, tmp_path):
        """The same asymmetry, annotator arm — an annotation whose target no
        record materializes must refuse on every engine, not only
        `rebuild_all`."""
        _sdk, events = env
        _raw(events, type="OperatorAnnotated", id="p-never-ann",
             annotator_bias=0.5, event_id="e-never-ann")
        _expect_refusal("rebuild_all", tmp_path, events, "neverann-ra",
                        "point-belief-miss")
        _expect_refusal("rebuild", tmp_path, events, "neverann-rb",
                        "point-belief-miss")
        _expect_recovery_refusal(tmp_path, events, "neverann-rec",
                                 "point-belief-miss")
        _expect_check_refusal(env[0], events, "point-belief-miss")


def _assert_check_consistency_rebuilt_ok(tmp_path, events, tag):
    """`check_consistency` `ok=True` against a graph the journal REBUILT.

    Asserting only "no non-folded record" would pass on a graph the replay
    never touched; the point of this leg is the CONTENT comparison, so the
    graph is produced by `rebuild_all` and the point must be present."""
    proj = _fresh(tmp_path, tag)
    try:
        proj.rebuild_all(str(events), confirm_destructive=True)
        r = check_consistency(str(events / "events.jsonl"), proj)
    finally:
        proj.close()
    assert r["ok"] is True, r
    assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
    return r


class TestReferenceFoldEntityLabelSetHasOneHome:
    """#3585 P1-2. The GRAPH fold records a state-op miss only for a label the
    REFERENCE fold models; the reference fold records only for its own labels.
    If the two sets drift, one classifier refuses a journal the other accepts.
    The set therefore lives in ONE place (``projection._REFERENCE_FOLD_ENTITY_LABELS``,
    in the lower module, because ``consistency`` imports from ``projection`` and
    the reverse import would be circular) and ``consistency._ENTITY_CREATION_LABELS``
    aliases it."""

    def test_the_label_sets_have_one_home(self):
        """FAILS IF: the shared set drifts from `consistency._ENTITY_CREATION`,
        so a new creation row (or a removed label) silently stops matching what
        the graph fold will refuse. (`_ENTITY_CREATION_LABELS` is an alias of
        the projection constant, so this pins the TABLE to it.)"""
        assert _ENTITY_CREATION_LABELS == _REFERENCE_FOLD_ENTITY_LABELS
        assert frozenset(
            label for label, _status in _ENTITY_CREATION.values()
        ) == _REFERENCE_FOLD_ENTITY_LABELS

    def test_the_name_merge_label_sets_have_one_home(self):
        """#7936 round-5 P2. The NAME-keyed label set is declared TWICE — once
        in ``projection._NAME_KEYED_ENTITY_LABELS`` (read by
        ``journal_epistemic_existence``'s node-table simulation) and once in
        ``consistency._NAME_MERGE_LABELS`` (read by the reference fold's
        state-op guard). Both models mirror the SAME graph merge keys
        (``_upsert_object`` / ``_upsert_subject``), so drift makes one fold
        speak about a merge key the other does not know — exactly the
        one-classifier-accepts-what-the-other-refuses class #3585 removes.
        FAILS IF: a graph fold starts (or stops) MERGEing a label by name and
        only one declaration is updated."""
        assert _NAME_KEYED_ENTITY_LABELS == _NAME_MERGE_LABELS


# ═══════════════════════════════════════════════════════════════════════
# #7719 — the apply-path terminalizer fold-miss was WARN-ONLY, so
# `rebuild(log)` / `recover_from_log` / `backup.restore` accepted a journal
# `rebuild_all` refuses and `check_consistency` reds. The fail-closed set was
# not one set. The miss is now recorded at the SHARED consumer, through ONE
# classifier, so every surface reaches one disposition.
# ═══════════════════════════════════════════════════════════════════════

class TestApplyPathTerminalizerMissIsRecorded:
    """The shared consumer (`apply_journal_point_restamp`) records the miss."""

    def _seed_missing_target(self, sdk, events, *, kind="PointSuperseded"):
        sdk.create_point("statement", "kept", status="live")
        rec = {"type": kind, "id": "never-created-id", "event_id": "ghost-1"}
        if kind == "PointSuperseded":
            rec["new_id"] = "also-never-created"
        else:
            rec["corrected_by"] = "also-never-created"
        _raw(events, **rec)

    @pytest.mark.parametrize("kind,const", [
        ("PointSuperseded", "SHAPE_POINT_SUPERSEDED_MISS"),
        ("PointInvalidated", "SHAPE_POINT_INVALIDATED_MISS"),
    ])
    def test_a_missing_terminalizer_target_is_refused_on_every_engine(
            self, env, tmp_path, kind, const):
        """FAILS IF: any replay engine accepts the journal — pre-#7719
        `rebuild` and `recover_from_log` merely warned while `rebuild_all`
        raised. REACHABLE: not through a public writer — every public writer
        guards its target — but a legacy pre-journaling graph or an unjournaled
        producer can still produce the shape."""
        import tortoise.projection.nonfolded as nf

        shape = getattr(nf, const)
        sdk, events = env
        self._seed_missing_target(sdk, events, kind=kind)
        # `rebuild_all` and `rebuild(log)` fail closed by RAISING.
        for engine in ("rebuild_all", "rebuild"):
            proj = _fresh(tmp_path, f"{kind}-{engine}")
            try:
                with pytest.raises(NonFoldedEventsError) as ei:
                    _drive(engine, tmp_path, events, proj)
                assert shape in str(ei.value), (engine, str(ei.value))
            finally:
                proj.close()
        # `recover_from_log` REPORTS the refusal instead — it must never claim
        # success over a journal it could not fully fold (the false PASS
        # #7719 closes), so it is asserted directly, not through `_drive`
        # (whose contract is a recovered run).
        proj = _fresh(tmp_path, f"{kind}-recover")
        try:
            res = recover_from_log(str(events), proj)
        finally:
            proj.close()
        assert res["recovered"] is False, res
        assert "refused" in res.get("reason", ""), res

    def test_check_consistency_refuses_the_same_journal(self, env):
        """FAILS IF: the reference fold and the graph engines disagree on the
        same journal — the 'not one set' defect #7719 reports."""
        sdk, events = env
        self._seed_missing_target(sdk, events)
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["ok"] is False, r

    def test_a_successor_does_not_make_a_missing_target_exempt(
            self, env, tmp_path):
        """FAILS IF: `has_successor` defaults to False at a call site — a
        target-miss WITH a `new_id` would be mislabelled the EXEMPT
        `point-superseded-no-new-id`, `refused_events` would filter it, and the
        run would PASS (a fail-OPEN, worse than the bug being fixed)."""
        sdk, events = env
        self._seed_missing_target(sdk, events)
        proj = _fresh(tmp_path, "has-succ")
        try:
            with pytest.raises(NonFoldedEventsError) as ei:
                _drive("rebuild", tmp_path, events, proj)
            assert "point-superseded-miss" in str(ei.value), str(ei.value)
            assert "point-superseded-no-new-id" not in str(ei.value)
        finally:
            proj.close()

    def test_the_consumer_requires_the_hard_deleted_context(self):
        """FAILS IF: the required kwarg gains a default — the fail-OPEN this
        change closes would silently return on any unmigrated caller."""
        from tortoise.projection.entities import _EntityHandlers

        with pytest.raises(TypeError):
            _EntityHandlers.apply_journal_point_restamp(None, {}, 0, {})

    def test_recover_from_log_refuses_a_projection_without_the_context(
            self, tmp_path):
        """FAILS IF: `recover_from_log`'s pre-loop signature probe is removed
        or weakened. A projection whose consumer cannot accept
        `hard_deleted=` must make the run REFUSE, not count the resulting
        `TypeError` as crash damage (`torn`) and return `recovered=True` over a
        journal whose terminalizer was never folded — the false PASS #7719
        closes. The journal carries a REAL terminalizer, so the probe's
        non-empty-`restamp_plan` guard is exercised (an empty plan legitimately
        skips a projection that never gets called).

        Unlike the signature-only guard above, this drives the recovery path
        end to end: delete the probe and this returns a success-shaped dict."""
        from tortoise.consistency import _RestampSignatureError, recover_from_log

        events = tmp_path  # recover_from_log reads a DIR of *.jsonl
        _raw_point(events, "p1")
        _raw(events, type="PointSuperseded", id="never-created",
             new_id="also-never-created", event_id="ghost-1")

        class _Result:
            def __init__(self, node):
                self.result_set = [[node]]

        class _Pre7719Proj:
            """The #7719 target: a consumer with no `hard_deleted=` kwarg."""

            def __init__(self):
                self.count_calls = 0

            def query(self, cypher):
                self.count_calls += 1
                return _Result(0 if self.count_calls == 1 else 3)

            def apply(self, ev):
                return None

            def apply_journal_point_restamp(self, ev, seq, plan):
                return None

        with pytest.raises(_RestampSignatureError) as ei:
            recover_from_log(str(events), _Pre7719Proj())
        assert "hard_deleted" in str(ei.value), str(ei.value)


class TestSharedTerminalizerClassifier:
    """ONE classifier decides the shape on every surface."""

    def test_every_returned_shape_is_declared(self):
        """FAILS IF: an arm is re-derived by hand at a call site and drifts
        from the recorded decision table, or a new terminalizer type slips
        through as a green pass (the default must be fail-CLOSED)."""
        import tortoise.projection.nonfolded as nf

        cases = [
            ("PointSuperseded", dict(has_successor=False),
             nf.SHAPE_POINT_SUPERSEDED_NO_NEW_ID),
            ("PointSuperseded", dict(has_successor=True, target_deleted=True),
             nf.SHAPE_SUPERSEDE_TARGET_DELETED),
            ("PointSuperseded", dict(has_successor=True),
             nf.SHAPE_POINT_SUPERSEDED_MISS),
            ("PointInvalidated", dict(target_deleted=True),
             nf.SHAPE_SUPERSEDE_TARGET_DELETED),
            ("PointInvalidated", dict(), nf.SHAPE_POINT_INVALIDATED_MISS),
            ("PointRetracted", dict(), nf.SHAPE_POINT_RETRACTED_MISS),
            ("ObjectSuperseded", dict(target_deleted=True),
             nf.SHAPE_SUPERSEDE_TARGET_DELETED),
            ("ObjectSuperseded", dict(), nf.SHAPE_OBJECT_SUPERSEDED_MISS),
            ("SomethingElse", dict(), nf.SHAPE_UNCLASSIFIED_TERMINALIZER),
        ]
        for etype, kwargs, expected in cases:
            got = nf.classify_terminalizer_miss(etype, **kwargs)
            assert got == expected, (etype, kwargs, got, expected)
        assert nf.SHAPE_POINT_SUPERSEDED_NO_NEW_ID in nf.EXEMPT_SHAPES
        assert nf.SHAPE_SUPERSEDE_TARGET_DELETED in nf.EXEMPT_SHAPES
        assert nf.SHAPE_UNCLASSIFIED_TERMINALIZER not in nf.EXEMPT_SHAPES

    def test_the_gated_map_is_not_the_ordered_reader(self):
        """FAILS IF: `hard_deleted_pairs` is substituted for the ordered
        `journal_hard_delete_seqs` boundary (or fed to
        `_hard_delete_suppresses`). It is anchor-gated BY DESIGN: a delete a
        later same-kind re-creation superseded never removed the node, so it
        must NOT exempt a terminalizer."""
        from tortoise.projection import _hard_deleted_any, hard_deleted_pairs

        events = [
            {"type": "PointAdded", "point": {"id": "p"},
             "event_id": "e1"},
            {"type": "EntityMutated", "op": "delete", "label": "Point",
             "id": "p", "event_id": "e2"},
            {"type": "PointAdded", "point": {"id": "p"},
             "event_id": "e3"},
        ]
        gated = hard_deleted_pairs(events)
        assert ("Point", "p") not in gated, gated
        assert _hard_deleted_any(gated, "Point", "p") is False
        # A re-creation anchor is seeded only for a WRITABLE id, so an
        # unwritable id cannot silently suppress its own delete.
        unwritable = [
            {"type": "PointAdded", "point": {"id": ""}, "event_id": "e1"},
            {"type": "EntityMutated", "op": "delete", "label": "Point",
             "id": "", "event_id": "e2"},
        ]
        assert _hard_deleted_any(
            hard_deleted_pairs(unwritable), "Point", "") is True

    def test_the_delete_after_a_later_recreate_stands(self):
        """FAILS IF: the anchor gate is dropped — the very journal the
        ordered boundary exists for ([create, delete, recreate]) would then be
        falsely refused on the gated map."""
        from tortoise.projection import _hard_deleted_any, hard_deleted_pairs

        events = [
            {"type": "PointAdded", "point": {"id": "p"},
             "event_id": "e1"},
            {"type": "EntityMutated", "op": "delete", "label": "Point",
             "id": "p", "event_id": "e2"},
            {"type": "PointAdded", "point": {"id": "p"},
             "event_id": "e3"},
            {"type": "EntityMutated", "op": "delete", "label": "Point",
             "id": "p", "event_id": "e4"},
        ]
        assert _hard_deleted_any(
            hard_deleted_pairs(events), "Point", "p") is True

    def test_a_foreign_kind_delete_does_not_exempt_a_point(self):
        """FAILS IF: the kind scoping is lost, so an Object delete exempts a
        Point supersede (the exemption is same-kind only)."""
        from tortoise.projection import _hard_deleted_any, hard_deleted_pairs

        events = [
            {"type": "EntityMutated", "op": "delete", "label": "Object",
             "id": "x", "event_id": "e1"},
        ]
        gated = hard_deleted_pairs(events)
        assert _hard_deleted_any(gated, "Object", "x") is True
        assert _hard_deleted_any(gated, "Point", "x") is False


# ═══════════════════════════════════════════════════════════════════════
# #5380 — the edge-message fold's misses are RECORDED, not only warned
#
# R8 requires every fold miss to enter the run-scoped non-folded set. The
# `EdgeMessageChanged` arm warned and recorded NOTHING, so a `set` miss (the
# edge the record names was never re-created) vanished into a log line, and an
# unrecognised `clear` scope was a SILENT no-op that resurrected the seeds the
# live graph dropped (#330/#1242) — the exact divergence the clear leg was
# added to prevent. Both shapes are EXEMPT (a plain direct edge is
# legitimately absent after a replay (the journal never recorded it; the
# journaled DirectEdgeCreated shape IS replayed since #8499) — and a future
# producer's fourth scope must not make a recovery run un-runnable), but an
# exemption
# still REQUIRES the record: that is the whole point of EXEMPT_SHAPES.
# ═══════════════════════════════════════════════════════════════════════


def test_edge_message_set_miss_is_recorded_as_non_folded(tmp_path, caplog):
    """A `set` whose edge is absent is a RECORDED non-folded event.

    Pre-fix it was only warned. The run boundary's own exempt report is the
    observable proof the record exists — the shape name appears nowhere else.
    """
    events = tmp_path / "events"
    events.mkdir()
    _raw(events, type="EdgeMessageChanged", id="ghost->missing:IMPL",
         op="set", src="ghost", tgt="missing", rel="IMPL",
         msg_alpha=0.5, msg_beta=0.5)
    proj = _fresh(tmp_path, "em-miss")
    try:
        with caplog.at_level(logging.WARNING):
            proj.rebuild_all(str(events), confirm_destructive=True)
    finally:
        proj.close()
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "edge-message-miss" in joined, (
        "the EdgeMessageChanged `set` miss was NOT recorded as a non-folded "
        f"event — R8 requires a structured entry, not only a warning:\n{joined}")


def test_edge_message_unknown_clear_scope_is_recorded_and_exempt(tmp_path):
    """An unrecognised `clear` scope is a recorded, EXEMPT non-folded event.

    Exempt because refusing would make a recovery run un-runnable for a
    journal a future producer could legitimately extend; recorded because a
    silent no-op resurrects the seeds the live clear dropped (#330).
    """
    proj = _fresh(tmp_path, "em-scope")
    try:
        with collect_non_folded() as nf:
            n = proj._fold_edge_message_changed(
                {"type": "EdgeMessageChanged", "op": "clear",
                 "scope": "not-a-scope", "event_id": "e-em-scope"})
        assert n == 0
        assert [e.shape for e in nf] == ["edge-message-unreplayed"], (
            "an unrecognised clear scope returned 0 INDISTINGUISHABLY from a "
            f"legitimate empty drop — no non-folded record: {nf}")
        assert refused_events(nf) == [], (
            "the shape must be EXEMPT — refusing a clear record blocks "
            "recovery")
    finally:
        proj.close()


def test_edge_message_clear_with_no_usable_point_ids_is_recorded(tmp_path):
    """A known scope with no usable `point_ids` is the same silent no-op."""
    proj = _fresh(tmp_path, "em-ids")
    try:
        with collect_non_folded() as nf:
            n = proj._fold_edge_message_changed(
                {"type": "EdgeMessageChanged", "op": "clear",
                 "scope": "touching", "point_ids": ["", None, 7],
                 "event_id": "e-em-ids"})
        assert n == 0
        assert [e.shape for e in nf] == ["edge-message-unreplayed"], nf
        assert refused_events(nf) == [], nf
    finally:
        proj.close()


def test_edge_message_known_scope_with_ids_still_folds(tmp_path):
    """GUARD: the new recording must NOT fire on a legitimate `clear`.

    Without this, a mutation that recorded every clear (or inverted the scope
    check) would pass the tests above while breaking recovery.
    """
    proj = _fresh(tmp_path, "em-ok")
    try:
        proj.g.query("CREATE (:Point {id:'a'})-[:IMPL]->(:Point {id:'b'})")
        proj.g.query("MATCH ()-[r:IMPL]->() SET r.msg_alpha=0.5")
        with collect_non_folded() as nf:
            n = proj._fold_edge_message_changed(
                {"type": "EdgeMessageChanged", "op": "clear",
                 "scope": "touching", "point_ids": ["a"],
                 "event_id": "e-em-ok"})
        assert n == 1, n
        assert [e.shape for e in nf] == [], nf
    finally:
        proj.close()


# ═══════════════════════════════════════════════════════════════════════
# #3590 D2 — the object-supersede exemption is scoped by the JOURNAL, not
# by the graph.
#
# The exemption covers a record that is unaddressable BY THE JOURNAL'S OWN
# CONTENT: no id, and a name no registration ever created. The bound is what
# makes it safe — a name the journal DID register keeps the refusal, so a
# supersede that could have landed never becomes skippable. These tests pin
# the two polarities at the classifier level and the mention exclusion at the
# oracle level; the end-to-end polarities live in TestReReviewRoundTwo above.
# ═══════════════════════════════════════════════════════════════════════


def test_classify_object_supersede_unregistered_is_its_own_exempt_shape():
    """The never-established case is a DISTINCT, exempt shape — keyed on
    ADDRESSES since `#10204`, and the address-less case has its own refusing
    label.

    FAILS IF: the exemption is expressed by exempting
    `object-superseded-miss` wholesale (which would also exempt a target the
    journal registered — the fail-OPEN direction), or the exemption shape is
    left non-exempt (the rebuild aborts again, the defect D2 exists to fix), or
    the no-address case is exempted or left sharing `object-superseded-miss`
    (a caller then cannot tell the unrepairable case from the repairable one —
    the `#9202` defect shape `#10204` closes).
    """
    from tortoise.projection.nonfolded import (
        EXEMPT_SHAPES,
        OBJECT_ADDRESS_ESTABLISHED,
        OBJECT_ADDRESS_NONE,
        OBJECT_ADDRESS_UNESTABLISHED,
        SHAPE_OBJECT_SUPERSEDED_MISS,
        SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS,
        SHAPE_OBJECT_SUPERSEDED_UNREGISTERED,
        SHAPE_SUPERSEDE_TARGET_DELETED,
        classify_terminalizer_miss,
    )

    assert classify_terminalizer_miss(
        "ObjectSuperseded", object_address=OBJECT_ADDRESS_UNESTABLISHED,
    ) == SHAPE_OBJECT_SUPERSEDED_UNREGISTERED
    assert SHAPE_OBJECT_SUPERSEDED_UNREGISTERED in EXEMPT_SHAPES
    assert SHAPE_OBJECT_SUPERSEDED_UNREGISTERED != SHAPE_OBJECT_SUPERSEDED_MISS

    # `#10204`: a record carrying NO address is a DIFFERENT, refusing shape.
    assert classify_terminalizer_miss(
        "ObjectSuperseded", object_address=OBJECT_ADDRESS_NONE,
    ) == SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS
    # ⛔ THE PREDICATE IS SYMMETRIC (#10211 review round 5): every one of these
    # CARRY something that is not an address — `{"name": 123}` and
    # `{"name": ""}` carry a NAME — so the operator strings must say "neither
    # `name` nor `id` is a non-empty string", not "carries no name", which was
    # false for exactly these records.
    from tortoise.projection import (
        journal_object_established,
        supersede_object_address,
    )
    _journal = [{"type": "ObjectRegistered", "id": "obj-sym", "name": "SYM"}]
    _est = journal_object_established(_journal)
    for _rec in ({"name": 123}, {"name": ""}, {"id": ""}, {"id": None}, {}):
        assert supersede_object_address(_rec, _est) == OBJECT_ADDRESS_NONE, _rec
    assert SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS not in EXEMPT_SHAPES, (
        "the no-address case has nothing checkable in it, so 'the journal "
        "never established it' is unfalsifiable — it must keep refusing")

    # The established case keeps the refusal: exempting the OLD shape would make
    # every object-supersede miss skippable.
    assert classify_terminalizer_miss(
        "ObjectSuperseded") == SHAPE_OBJECT_SUPERSEDED_MISS, (
        "the default must stay fail-CLOSED — a caller that forgets the "
        "address decision refuses")
    assert classify_terminalizer_miss(
        "ObjectSuperseded",
        object_address=OBJECT_ADDRESS_ESTABLISHED,
    ) == SHAPE_OBJECT_SUPERSEDED_MISS
    assert SHAPE_OBJECT_SUPERSEDED_MISS not in EXEMPT_SHAPES

    # The sibling exemption keeps its own bound and is never absorbed.
    assert classify_terminalizer_miss(
        "ObjectSuperseded", target_deleted=True,
        object_address=OBJECT_ADDRESS_UNESTABLISHED,
    ) == SHAPE_SUPERSEDE_TARGET_DELETED

    # A record naming NOTHING is not "never established" — nothing is
    # addressable, so the address decision is NONE and the caller must refuse.
    from tortoise.projection import (
        JournalObjectEstablished,
        supersede_object_address,
    )
    # ⛔ The NAMED tuple, not a plain 2-tuple (review P2). `journal_object_
    # surviving_keys` returns `(ids, names)` — the opposite order — so only the
    # named type keeps a wrong-oracle call from silently inverting BOTH limbs.
    # These lines are what exercise that contract.
    _none_established = JournalObjectEstablished(frozenset(), frozenset())
    assert supersede_object_address(
        {"name": None}, _none_established) == OBJECT_ADDRESS_NONE
    assert supersede_object_address(
        {"name": ""}, _none_established) == OBJECT_ADDRESS_NONE
    assert supersede_object_address(
        {"id": ""}, _none_established) == OBJECT_ADDRESS_NONE
    assert supersede_object_address(
        {"supersedes_by": "x"}, _none_established) == OBJECT_ADDRESS_NONE
    assert supersede_object_address({"name": "X"}, None) == (
        OBJECT_ADDRESS_ESTABLISHED), (
        "the one-record LIVE path supplies no journal and must stay "
        "fail-closed")
    # `#10204`: an id-only record is the SAME assertion in the other spelling —
    # unestablished here, so exempt.
    assert supersede_object_address(
        {"id": "never-seen"}, _none_established) == (
        OBJECT_ADDRESS_UNESTABLISHED)
    # The ID limb: an id the journal established blocks the exemption even
    # when the name is one nothing established (review P1).
    _id_established = JournalObjectEstablished(
        frozenset(), frozenset({"o-1"}))
    assert supersede_object_address(
        {"name": "GHOST", "id": "o-1"}, _id_established) == (
        OBJECT_ADDRESS_ESTABLISHED)
    assert supersede_object_address(
        {"name": "GHOST", "id": "never-seen"}, _id_established) == (
        OBJECT_ADDRESS_UNESTABLISHED)
    assert supersede_object_address(
        {"id": "o-1"}, _id_established) == OBJECT_ADDRESS_ESTABLISHED
    # ⛔ PRESENCE, not WRITABILITY — the boundary this predicate is defined on.
    # A NUL-bearing or lone-surrogate id is a non-empty `str`, so it IS an
    # address: a record carrying one is the SAME assertion as any other and
    # must land on UNESTABLISHED (exempt), not on NONE (refused). Gating this
    # limb on `_writable_id` instead re-opens #3590 D2 for that spelling and
    # aborts a rebuild over a record the journal established neither address
    # of; MEASURED: this limb survived as a mutant until it was pinned.
    for _unwritable in ("a\x00b", "\ud800"):
        assert supersede_object_address(
            {"id": _unwritable}, _none_established) == (
            OBJECT_ADDRESS_UNESTABLISHED), (
            f"{_unwritable!r} is PRESENT, which is the whole test — the "
            "predicate must not consult writability")
        assert supersede_object_address(
            {"name": _unwritable}, _none_established) == (
            OBJECT_ADDRESS_UNESTABLISHED)


def test_refusal_remedy_is_empty_for_an_empty_set_and_splits_by_shape():
    """PIN `#10204` (`refusal_remedy`'s three polarities, unit-level).

    FAILS IF: the empty-set guard is dropped (then a caller that ever reaches
    it is told to reconcile a journal with no record to reconcile — the same
    "a remedy the caller cannot perform" defect the split exists to remove),
    OR the no-address-only set keeps the reconcile sentence, OR the reconcile
    sentence is narrowed away from a genuine miss, OR a mixed set loses either
    half.

    ⚠️ The empty-set branch is unreachable from today's two callers (both guard
    on a non-empty refused set), which is exactly why it needs a unit pin
    rather than a reachability argument: a future refactor that computes
    `divergence` from the FULL non-folded set would make an all-exempt journal
    render `action="… Reconcile the journal"`, and nothing else would notice
    (MEASURED: unpinned, this limb survived as a mutant).
    """
    from tortoise.projection.nonfolded import (
        SHAPE_OBJECT_SUPERSEDED_MISS,
        SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS,
        NonFoldedEvent,
        refusal_remedy,
    )

    assert refusal_remedy(()) == "", "an empty set must prescribe NOTHING"
    assert refusal_remedy([]) == "", "an empty set must prescribe NOTHING"

    _no_addr = [NonFoldedEvent(SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS)]
    _miss = [NonFoldedEvent(SHAPE_OBJECT_SUPERSEDED_MISS)]
    _only_no_addr = refusal_remedy(_no_addr)
    assert "Reconcile the journal" not in _only_no_addr, _only_no_addr
    assert "supply the target's name or id" in _only_no_addr, _only_no_addr
    _only_miss = refusal_remedy(_miss)
    assert "Reconcile the journal" in _only_miss, _only_miss
    assert "supply the target's name or id" not in _only_miss, _only_miss
    _mixed = refusal_remedy(_miss + _no_addr)
    assert "Reconcile the journal" in _mixed, _mixed
    assert "supply the target's name or id" in _mixed, _mixed


def test_miss_remedy_does_not_contradict_the_per_record_hard_deleted_case():
    """PIN `#10211` review round 8: the run boundary and the per-record receipt
    must not prescribe OPPOSITE repairs for ONE record.

    The `object-superseded-miss` shape has two causes. Case (3) of
    `projection.rebuild_all`'s receipt is a NAME-only record over a target the
    journal HARD-DELETED under an id the record does not carry: nothing is out
    of order, no creation is missing, and the receipt says so explicitly —
    "not to reconcile the journal" (`#10224`). `refusal_remedy` emitted the
    reconcile sentence alone for that same shape, so an operator reading the
    abort was told to do the one thing the record's own message forbade. That
    is the class `refusal_remedy` exists as the single home to end.

    FAILS IF: the miss arm drops `_MISS_NAME_ONLY_DELETED_REMEDY` (restoring the
    contradiction), OR the reconcile sentence is REPLACED by it rather than
    joined by it (a genuine divergence is still repaired by reconciling, and
    dropping that limb would prescribe an incomplete remedy for it), OR the
    sentence stops naming the deleted-target-id repair.
    """
    from tortoise.projection.nonfolded import (
        MISS_NAME_ONLY_DELETED_INSTRUCTION,
        SHAPE_OBJECT_SUPERSEDED_MISS,
        NonFoldedEvent,
        refusal_remedy,
    )

    _only_miss = refusal_remedy([NonFoldedEvent(SHAPE_OBJECT_SUPERSEDED_MISS)])
    # BOTH halves: the divergence repair AND the conservative sub-case's repair.
    assert "Reconcile the journal" in _only_miss, _only_miss
    assert MISS_NAME_ONLY_DELETED_INSTRUCTION in _only_miss, _only_miss
    # Scoped to the deleted-target-id repair, not a generic "look harder".
    assert "deleted target's id" in MISS_NAME_ONLY_DELETED_INSTRUCTION
    assert "#10224" in MISS_NAME_ONLY_DELETED_INSTRUCTION

    # And the sentence must NOT leak on to a set that has no miss at all.
    from tortoise.projection.nonfolded import SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS

    _only_no_addr = refusal_remedy(
        [NonFoldedEvent(SHAPE_OBJECT_SUPERSEDED_NO_ADDRESS)])
    assert MISS_NAME_ONLY_DELETED_INSTRUCTION not in _only_no_addr, _only_no_addr


def test_refusal_remedy_docstring_states_every_limb_it_emits():
    """PIN `#10211` review round 8 (follow-up): the remedy's OWN HOME must
    describe every limb it emits, not the subset it emitted first.

    `refusal_remedy` is documented as the single home for the remedy split, so a
    lane re-deriving the split reads THIS docstring before the body. It framed
    the split as "TWO epistemically different records" and enumerated exactly
    two — divergence and no-address — while the function emits a THIRD sentence
    for the miss shape's conservative sub-case. A home whose prose is NARROWER
    than its predicate is the defect class this PR already fixed once for
    `EXEMPT_SHAPES` (commit `12faa006`): the next editor deleting the miss limb
    as undocumented noise would restore the very contradiction round 8 removed,
    with nothing but a test name to warn them.

    FAILS IF: the docstring reverts to the two-limb framing ("TWO epistemically
    different"), OR drops any of the three limbs by name — the reconcile
    sentence, the SUPPLY-the-address limb, or the miss shape's deleted-target-id
    limb and its JOINED-not-swapped rule.
    """
    from tortoise.projection.nonfolded import refusal_remedy

    doc = refusal_remedy.__doc__ or ""
    assert "TWO epistemically different" not in doc, (
        "the docstring is back to a two-limb framing, but the function emits a "
        "third sentence for the miss shape")
    assert "THREE limbs" in doc, doc
    # Limb 1 — the divergence repair, by the phrase the seller of the remedy uses.
    assert "reconcile the journal" in doc, doc
    # Limb 2 — the no-address record: supply the address, never reconcile.
    assert "SUPPLY the address" in doc, doc
    # Limb 3 — the miss shape's conservative sub-case, and the JOIN rule.
    assert "HARD-DELETED" in doc, doc
    assert "deleted-target-id sentence" in doc, doc
    assert "joined by" in doc, (
        "the docstring no longer states that the miss sentence is JOINED to the "
        "reconcile sentence rather than swapped for it — the swap is the "
        "incomplete-remedy half of the round-8 finding")


def test_exempt_shapes_names_every_state_op_that_can_carry_a_name():
    """PIN `#10211` review round 8: the RECORD of the bound must not be narrower
    than the bound, in the FAIL-OPEN direction.

    `journal_object_established` counts `state.name` for EVERY op in
    `_ENTITY_MUTATION_STATE_OPS` (`rename`, `restatus`, `revise`, `supersede`) —
    pinned by `test_state_op_writing_a_name_other_than_rename_is_covered`. The
    `EXEMPT_SHAPES` entry and `identity-decision.md` described that source as
    "a rename", so a lane re-deriving the bound from the record would treat a
    name carried by `restatus`/`revise`/`supersede` as UNestablished and exempt
    a supersede that could have landed — the fail-open direction, and the exact
    narrowing this PR fixed in the code and the `_sup_why` text.

    FAILS IF: either record drifts back to a rename-only illustration.
    """
    from pathlib import Path

    from tortoise.projection.nonfolded import EXEMPT_SHAPES, SHAPE_OBJECT_SUPERSEDED_UNREGISTERED

    _entry = EXEMPT_SHAPES[SHAPE_OBJECT_SUPERSEDED_UNREGISTERED]
    assert "(a rename" not in _entry, (
        "EXEMPT_SHAPES is back to a rename-only NAME source — the record is then "
        "narrower than the predicate, which exempts a landable supersede")
    for _op in ("rename", "restatus", "revise", "supersede"):
        assert _op in _entry, (
            f"EXEMPT_SHAPES must name `{_op}`, one of "
            "_ENTITY_MUTATION_STATE_OPS — all four can SET the merge key")

    _doc = (Path(__file__).resolve().parent.parent
            / "docs/epics/2026-09-10-2835-capability-registry/identity-decision.md")
    _text = _doc.read_text(encoding="utf-8")
    assert "state op (a rename)" not in _text, (
        "identity-decision.md is back to a rename-only NAME source")
    assert "`restatus`" in _text, (
        "identity-decision.md's Name-sources clause must name every state op")


def test_journal_object_established_counts_every_address_and_separates_ids():
    """The oracle counts EVERY route that puts a name in the merge key, and
    keeps the established ids separately.

    FAILS IF: the oracle reads `ObjectRegistered` alone. The `EntityMutated`
    state-op source is a route the fold can MATCH on, so missing it exempts a
    supersede the journal could have addressed: a rename carries a name in via
    `SET n += $state` (review P1).

    ⛔ #3590 REMOVED the `EventRecorded` mention-stub source from BOTH halves.
    It existed because `_event_plain_merge` MERGEed an Object by the mention's
    name with `ON CREATE` id = the name-derived deterministic id — and the
    fold no longer mints anything (D9: a fold-time mint is not
    replay-deterministic). Counting a mention here now would exempt a
    supersede of a carrier the journal never created, against a graph that
    folds nothing: the fail-OPEN direction. The retired name and its
    deterministic id are asserted ABSENT below, so re-growing either mint reds
    this test.

    The `ids` half exists for the other review P1: a record carrying an id the
    journal established is ADDRESSABLE even when the name it also carries is
    one nothing established, so it must not be exempted on the name alone.
    """
    from tortoise.projection import journal_object_established

    events = [
        {"type": "ObjectRegistered", "id": "o-1", "name": "KNOWN"},
        {"type": "EventRecorded", "id": "e-1", "eventKind": "k",
         "object": "MENTIONED"},
        {"type": "ObjectRegistered", "id": "o-2", "name": ""},
        {"type": "EntityMutated", "op": "rename", "label": "Object",
         "id": "o-1", "state": {"name": "RENAMED"}},
        {"type": "EntityMutated", "op": "rename", "label": "Object",
         "id": "o-3", "state": {"name": "FRESH"}},
        {"type": "EntityMutated", "op": "rename", "label": "Point",
         "id": "p-1", "state": {"name": "POINT-NAME"}},
    ]
    names, ids = journal_object_established(events)
    assert names == frozenset({"KNOWN", "RENAMED", "FRESH"}), names
    assert "MENTIONED" not in names, (
        "an EventRecorded mention was counted as an established Object name — "
        "the retired stub mint is back (D9: the fold never mints)")
    assert "POINT-NAME" not in names, (
        "a Point rename is not an Object name — the label gate is load-bearing")
    assert ids == frozenset({"o-1", "o-2", "o-3"}), (
        "a state op's TARGET id is established (o-1/o-3) and so is every "
        f"ObjectRegistered id: {ids}")
    assert "p-1" not in ids, (
        "the label gate governs the id limb too — a Point rename's id is not "
        "an Object id")
    assert "" not in names, names


def test_name_from_a_rename_counts_as_established_both_engines(env):
    """#10107 review P1: a name a RENAME carried into the merge key is
    ESTABLISHED, so the exemption must not fire for it.

    FAILS IF: the oracle ignores `EntityMutated` state ops, i.e. it reads
    `ObjectRegistered` alone.
    Then "B" looks unregistered, the sweep EXEMPTS the record and `rebuild_all`
    silently drops a supersede, while `rebuild()` — which folds chronologically,
    when the node was still named "B" — applies it: one journal, two graphs, and
    `check_consistency` agreeing with the WRONG one. `main` refused here, so the
    regression direction is fail-OPEN and silent.

    REACHABLE: rename A→B, supersede "B" by name, rename B→A. At sweep time the
    node is "A", so the name-only supersede genuinely misses — the class that
    must stay refused.
    """
    sdk, events = env
    oid = sdk.create_entity("object", name="A")["node"]["id"]
    _raw(events, type="EntityMutated", op="rename", label="Object", id=oid,
         state={"name": "B"}, event_id="e-ren-1")
    _raw(events, type="ObjectSuperseded", name="B", supersedes_by="C",
         event_id="e-sup-b")
    _raw(events, type="EntityMutated", op="rename", label="Object", id=oid,
         state={"name": "A"}, event_id="e-ren-2")
    proj = sdk._get_proj()
    with pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert "object-superseded-miss" in str(ei.value), str(ei.value)
    assert "object-superseded-unregistered" not in str(ei.value), (
        "a name the journal established by RENAME was treated as unregistered "
        f"— the exemption widened past its bound: {ei.value}")


def test_nested_record_spelling_agrees_on_every_engine(env):
    """#10107 review P2: the three classifying surfaces read the SAME name.

    FAILS IF: `rebuild_all` normalises the record (`_norm` splices the nested
    payload) while `consistency`'s reference fold reads the raw envelope. Then
    the graph engines exempt the record and the reference fold refuses it —
    the #3585 asymmetry, introduced by making the two halves of ONE predicate
    read different spellings.
    """
    sdk, events = env
    _raw(events, type="ObjectSuperseded", point={"name": "GHOST-NESTED"},
         supersedes_by="C", event_id="e-nested")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] == 0, (
        "the reference fold refused a record both graph engines exempt — the "
        f"nested spelling split the predicate: {r['non_folded_events']}")
    assert r["divergence"] != "non-folded", r["divergence"]


def test_collapsed_away_id_with_an_unestablished_name_still_refuses(env, caplog):
    """#10107 review P1: an id the journal ESTABLISHED is addressable even
    when the name the record also carries is one nothing established.

    FAILS IF: the predicate is decided on the NAME alone. A record that carries
    an id the journal ESTABLISHED and a name it did not must keep refusing; on
    the name alone it looks unaddressable and is exempted, so `rebuild_all` and
    the health gate both report clean while the supersede did not land — the
    #5285 cycle-4 FIX 2 fail-open, re-opened by the exemption.

    ⛔ Restated for #3590: the pre-change fixture built the miss from a
    same-name COLLAPSE (`Reg(a,N), Reg(b,N)` onto one node), which the id-keyed
    MERGE no longer produces — both ids are their own entity and `Sup(id=a)`
    folds. The still-live route to "established address, no carrier" is a state
    op's TARGET id, which the journal holds without creating a node.

    REACHABLE: a raw or legacy producer that carries a stale/renamed name
    alongside the id of a target it mutated but never registered.
    """
    sdk, events = env
    _raw(events, type="EntityMutated", label="Object", id="obj-p1-a",
         op="restatus", state={"status": "live"}, event_id="e-p1-a")
    _raw(events, type="ObjectSuperseded", id="obj-p1-a", name="GHOST-OF-COLL2",
         supersedes_by="y", event_id="e-p1-sup")
    proj = sdk._get_proj()
    with caplog.at_level(logging.WARNING, logger="tortoise.projection"), \
            pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert "object-superseded-miss" in str(ei.value), str(ei.value)
    assert "object-superseded-unregistered" not in str(ei.value), (
        "an established id was exempted because its NAME was unestablished — "
        f"the exemption widened past its bound: {ei.value}")
    # ⛔ THE MISS ARM'S DIAGNOSED CAUSE (#10211 review round 4). This arm used to
    # say "object not re-created by any journaled event (pre-#2194 journal,
    # unjournaled capture SDK, legacy unjournaled Object, or delete race)" —
    # written for the OLD miss population, which still held records the journal
    # had established NOTHING for. The amendment splits both of those out
    # (`no-address`, `unregistered`), so a record now reaches this arm ONLY with
    # an ESTABLISHED address — ⛔ NOT necessarily with a creation the journal
    # holds (#10211 review round 6): the state-op fixture 18 lines below reaches
    # this SAME arm with NO `ObjectRegistered` in the journal at all, because a
    # state op's target id is an address the journal holds without creating a
    # node, and THIS fixture is now exactly that shape (#3590: the collapse
    # route the fixture used before is retired). The retired
    # text sent an operator hunting an unjournaled producer that the journal in
    # their hands disproves. MEASURED: without this pin the arm can regress to
    # any wording and the file stays green.
    # ⚠️ Asserted on the SWEEP'S WARNING, which is where `_sup_why` renders —
    # the raised error carries `refusal_remedy`, not this arm, so an assertion
    # on `str(ei.value)` can never fail for the right reason.
    report = " ".join(r.getMessage() for r in caplog.records)
    assert "not re-created by any journaled event" not in report, (
        f"the miss arm still describes the retired population: {report}")
    assert "DID establish this record's address" in report, (
        f"the miss arm does not name the real cause (an established address "
        f"with no Object carrying it): {report}")
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] >= 1, r["non_folded_events"]


def test_state_op_target_id_with_an_unestablished_name_still_refuses(env):
    """#10107 review round 6: a state op NAMES an id the journal could address.

    FAILS IF: the id limb counts `ObjectRegistered` and mention-stub ids only.
    `EntityMutated` addresses its target BY ID, so the journal DID produce that
    address — the exemption's premise ("nothing could have produced it") is
    false for such a record, and the oracle consulted only the state op's NAME
    and exempted it as "addressed by nothing at all".

    The divergence that limb prevents is on the LIVING path: `apply()` against
    a graph that already holds the carrier matches by that id and lands the
    supersede, while `rebuild_all` (wipe, no carrier) would have skipped it.
    In THIS fixture the rename itself misses, so the run is red either way and
    what is pinned is the record's CLASSIFICATION — the value all three
    surfaces key the exemption on.
    """
    sdk, events = env
    _raw(events, type="EntityMutated", op="rename", label="Object",
         id="obj-mut-1", state={"name": "OLD-MUT"}, event_id="e-mut-1")
    _raw(events, type="ObjectSuperseded", id="obj-mut-1", name="GHOST-MUT",
         supersedes_by="y", event_id="e-mut-sup")
    proj = sdk._get_proj()
    with pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert "object-superseded-miss" in str(ei.value), str(ei.value)
    assert "object-superseded-unregistered" not in str(ei.value), (
        "a state op's target id was treated as established by nothing: "
        f"{ei.value}")


def test_rename_carried_name_with_no_creation_still_refuses(env, caplog):
    """#10211 review round 6: the miss arm's NON-CREATION limb has TWO
    spellings, and the NAME one is reachable.

    A rename state op carries a `SET` of the merge key, so the journal
    establishes that NAME — but if the rename's own target never existed, the
    journal holds an address from a write that created nothing, and there is NO
    carrier to collapse or rename. The miss arm therefore cannot illustrate limb
    (2) with a target id alone.

    FAILS IF: the arm enumerates only "a state op's or a mention stub's target
    id" (the round-5 wording), which leaves this record in NEITHER limb and
    sends the operator after a carrier that does not exist — and if nothing
    pins the message, the id-only illustration can come back unnoticed.

    REACHABLE: a raw/legacy producer that renames into a name and then
    supersedes by that name, with no `ObjectRegistered` anywhere in the journal.
    """
    sdk, events = env
    _raw(events, type="EntityMutated", op="rename", label="Object",
         id="obj-mut-9", state={"name": "RENAMED-GHOST"}, event_id="e-mut-9")
    _raw(events, type="ObjectSuperseded", name="RENAMED-GHOST",
         supersedes_by="y", event_id="e-mut9-sup")
    proj = sdk._get_proj()
    with caplog.at_level(logging.WARNING, logger="tortoise.projection"), \
            pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert "object-superseded-miss" in str(ei.value), str(ei.value)
    assert "object-superseded-unregistered" not in str(ei.value), (
        "a name the journal established via a rename was exempted as "
        f"unestablished: {ei.value}")
    report = " ".join(r.getMessage() for r in caplog.records)
    assert "DID establish this record's address" in report, report
    assert "a name it writes" in report, (
        f"the miss arm's non-creation limb does not name the NAME spelling, so "
        f"this record falls outside both limbs: {report}")


def test_unestablished_id_with_an_established_name_still_refuses(env):
    """#3590: the D2 exemption needs BOTH limbs unestablished — the id limb is
    not a licence to exempt a supersede whose name the journal DID establish.

    FAILS IF: the record's `id` is read as "nothing to address" and the
    classifier then exempts on the name alone. `ACME-MS` IS established by the
    journal's own `ObjectRegistered`, so a supersede of it COULD have landed:
    exempting it hides a genuine divergence (`object-superseded-miss`).

    ⛔ This replaces `test_mention_stub_id_with_an_unestablished_name_still_
    refuses`. Its premise was a MENTION-STUB id (`entity_name_id("Object", …)`)
    that no longer exists — the fold never mints (D9) — but the property it
    pinned was never about the stub: an id that addresses nothing must not
    exempt a name the journal established. The id is now a plain
    unestablished one, which is the same limb without the retired mint.
    """
    sdk, events = env
    _raw(events, type="ObjectSuperseded", id="obj-never-established",
         name="ACME-MS", supersedes_by="y", event_id="e-ms-sup")
    _raw(events, type="ObjectRegistered", id="obj-real-ms", name="ACME-MS",
         status="live", event_id="e-ms-reg")
    proj = sdk._get_proj()
    with pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert "object-superseded-miss" in str(ei.value), str(ei.value)
    assert "object-superseded-unregistered" not in str(ei.value), (
        "an id that addresses nothing exempted a supersede whose NAME the "
        f"journal established: {ei.value}")


def test_a_mention_establishes_nothing_and_mints_nothing(env):
    """#3590 D9: a mention is not an address and not a carrier.

    Two halves of one decision, pinned together because they are one defect
    seen from both sides:

    * a name-only supersede of a name ONLY a mention carries is the bounded
      `object-superseded-unregistered` exemption (the journal established
      neither limb, so live and replay agree by construction) — NOT a
      `object-superseded-miss` refusal; and
    * replay creates NO Object node for the mentioned name — a fold-time mint
      is not replay-deterministic, so `rebuild_all` may not fabricate one.

    FAILS IF: any mint returns (the retired name-derived stub, or a `ulid()`
    stub): the node appears, the supersede folds onto it, and `rebuild != live`
    for a journal whose live pass created nothing. Also fails if the exemption
    is withdrawn (the run would refuse a journal it can never make resolvable —
    the door may not register a mention of a name nothing holds, D9) or if the
    reference fold disagrees with the graph engine about it (R8: one journal,
    one disposition on every surface).

    ⛔ This replaces `test_mention_established_then_deleted_name_still_refuses`
    and `test_mention_stub_only_carrier_folds_in_graph_and_reference_fold`,
    whose shared premise — a mention MINTS an Object stub a supersede can fold
    onto — is the retired contract.
    """
    sdk, events = env
    _raw(events, type="EventRecorded", id="e-ment-only", eventKind="k",
         object="MENT-ONLY", event_id="e-ment-only-rec")
    _raw(events, type="ObjectSuperseded", name="MENT-ONLY", supersedes_by="y",
         event_id="e-ment-only-sup")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id, o.status",
        params={"n": "MENT-ONLY"}).result_set
    assert rows == [], (
        f"replay MINTED an Object for a mention-only name: {rows} — the fold "
        "is the minter again (#3590 D9), and a fold-time mint is not "
        "replay-deterministic")
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["ok"] is True, (
        f"the reference fold refused the bounded exemption: {r['divergence']} "
        f"{r['non_folded_events']}")
    assert r["non_folded_refused_count"] == 0, r["non_folded_events"]


# ⛔ #3590 DELETED `test_mention_established_then_deleted_name_still_refuses`
# and `test_mention_stub_only_carrier_folds_in_graph_and_reference_fold` here.
# Both pinned ONE retired premise: a mention MINTS an Object stub node that a
# name-only supersede folds onto (and whose hard-delete therefore made the
# record refuse). The fold never mints any more (D9), so there is no stub to
# fold onto and none to delete: `_event_plain_merge` RESOLVES the mention and
# leaves the edge unwired when nothing holds the name. The properties both
# tests existed for are pinned by `test_a_mention_establishes_nothing_and_
# mints_nothing` above (no node is created; the record takes the bounded
# exemption; the two engines agree) and by
# `test_id_wide_delete_of_a_registered_object_refuses_on_every_engine` below
# (a target the journal really removes still refuses on every surface).


def test_established_id_with_an_unestablished_name_folds_by_id(env):
    """#10157: the ID is the address, so a record carrying an ESTABLISHED id
    folds even when the name it also carries was never established.

    FAILS IF: the reference fold resolves by NAME alone. The graph fold issues
    `MATCH (o:Object {id:$id})` and nothing else (#3590), so
    `Sup(id=<registered id>, name=<stale name>)` lands — a name-keyed
    resolution found no carrier under the stale name, recorded
    `object-superseded-miss`, and reported `divergence="non-folded"` on a
    journal every graph engine accepts (a false RED on a healthy graph).

    ⛔ Migrated from `test_mention_stub_carrier_resolves_BY_ID_too`: the id is a
    REAL registration's, not a mention stub's — the stub no longer exists —
    and the property (id-addressed resolution on both halves) is unchanged.
    """
    sdk, events = env
    _raw(events, type="ObjectRegistered", id="obj-a2", name="ACME-A2",
         status="live", event_id="e-a2-reg")
    _raw(events, type="ObjectSuperseded", id="obj-a2", name="GHOST-A2",
         supersedes_by="y", event_id="e-a2-sup")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status",
        params={"n": "ACME-A2"}).result_set
    assert [r[0] for r in rows] == ["superseded"], (
        f"the graph did not fold the supersede by ID: {rows}")
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] == 0, (
        "the reference fold refused an ID-addressed supersede the graph "
        f"folded: {r['non_folded_events']}")
    assert r["divergence"] != "non-folded", r["divergence"]


# ⛔ #3590 DELETED `test_mention_stub_rementioned_after_delete_folds_again`
# here. Its property was "a re-mention REVIVES a stub the journal deleted",
# which only exists while the fold mints the stub: with no mint there is no
# node to delete and none to revive, and the supersede takes the same bounded
# `object-superseded-unregistered` exemption as any other mention-only name
# (`test_a_mention_establishes_nothing_and_mints_nothing`). The `deleted`-mark
# discard it also pinned still has real coverage on a REGISTERED object, in
# `test_registered_object_recreated_after_delete_folds_again` below.


def test_registered_object_recreated_after_delete_folds_again(env):
    """#10157 (migrated to #3590 identity): a delete then a RE-CREATION of the
    same identity leaves a node to fold onto, so the supersede folds.

    FAILS IF: a journal `delete` leaves a stale `deleted` mark that a later
    CREATION record does not clear. Then the reference fold calls a node that
    exists at the journal's END gone, records `object-superseded-miss`, and
    reports `divergence="non-folded"` on a journal all three engines accept.

    ⛔ Migrated from `test_mention_stub_rementioned_after_delete_folds_again`:
    the revival source is a REGISTRATION (the only carrier source the fold has
    left), not a re-mention mint.
    """
    sdk, events = env
    _raw(events, type="ObjectRegistered", id="obj-b2", name="ACME-B2",
         status="live", event_id="e-b2-reg1")
    _raw(events, type="EntityMutated", op="delete", label="Object",
         id="obj-b2", event_id="e-b2-del")
    _raw(events, type="ObjectRegistered", id="obj-b2", name="ACME-B2",
         status="live", event_id="e-b2-reg2")
    _raw(events, type="ObjectSuperseded", id="obj-b2", name="ACME-B2",
         supersedes_by="y", event_id="e-b2-sup")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status",
        params={"n": "ACME-B2"}).result_set
    assert [r[0] for r in rows] == ["superseded"], (
        f"the re-created Object was not folded: {rows}")
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] == 0, (
        "the reference fold treated a re-created Object as still deleted: "
        f"{r['non_folded_events']}")
    assert r["divergence"] != "non-folded", r["divergence"]
    # ⛔ `ok`/`divergence`, not just the refusal count (review 11, P3): with
    # only the two asserts above, DELETING the seeding's `deleted.discard`
    # still passed — the false red it causes is `divergence="content"`
    # (hard-deleted-in-journal / present-in-graph), which neither line looks
    # at. The line is load-bearing; this is what pins it.
    assert r["ok"] is True, r
    assert r["divergence"] is None, r["divergence"]


def test_renamed_away_carrier_supersede_is_named_in_the_report(env):
    """#10157 review round 11, P1: a rename moves the NAME, not the id.

    FAILS IF: the reference fold treats a name that no node carries any more as
    an address. `journal_object_surviving_keys` models a rename in its NAMES
    half — the id survives under a new name — so a supersede carrying the OLD
    name addresses nothing, and EVERY engine refuses it. The reference fold
    must name it in the report, not pass the journal.

    ⛔ Asserts the SUPERSEDE is among the refused records — asserting only
    `refused >= 1` would pass on the rename's own `state-op-miss` and pin
    nothing. Migrated from `test_renamed_away_stub_supersede_is_named_in_the_
    report`: the carrier is a REGISTRATION (the only source left, D9), and
    `_stub_hit` — the arm that used to mask this — is deleted with the mint.
    """
    sdk, events = env
    _raw(events, type="ObjectRegistered", id="obj-rn", name="ACME-RN",
         status="live", event_id="e-rn-reg")
    _raw(events, type="EntityMutated", op="rename", label="Object",
         id="obj-rn", state={"name": "ACME-RN-NEW"}, event_id="e-rn-mut")
    _raw(events, type="ObjectSuperseded", name="ACME-RN", supersedes_by="y",
         event_id="e-rn-sup")
    proj = sdk._get_proj()
    with pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert "object-superseded-miss" in str(ei.value), str(ei.value)
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert any("ObjectSuperseded" in e or "supersede matched no" in e
               for e in r["non_folded_events"]), (
        "the reference fold still treated the renamed-away carrier's OLD name "
        f"as an address: {r['non_folded_events']}")


def test_id_wide_delete_of_a_registered_object_refuses_on_every_engine(env):
    """#10157 review round 10, P1: a target the journal deletes UNSCOPED is gone.

    FAILS IF: the reference fold's survival limb fails to model an id-wide
    `EntityMutated delete` (`_delete_entity_by_id`'s fallback when the label is
    missing). Then the record still looks addressable, the sweep sees a hit,
    and the supersede is not merely exempt but ABSENT from the report.

    MEASURED before the fix: `rebuild_all` REFUSED `object-superseded-miss`
    while `check_consistency` returned `ok=True refused=0 nf=[]` — the health
    gate certifying a journal every engine refuses. The oracle is the journal's
    own `journal_object_surviving_keys`, which models this delete.

    ⛔ Migrated from `test_id_wide_delete_of_a_mention_stub_refuses_on_every_
    engine`: the target is a REGISTRATION (a mention no longer mints a carrier,
    D9). The supersede stays NAME-ONLY on purpose: carrying the id would route
    the record to the anchored `supersede-target-deleted` bound instead.
    """
    sdk, events = env
    _raw(events, type="ObjectRegistered", id="obj-wide", name="WIDE",
         status="live", event_id="e-w-reg")
    _raw(events, type="EntityMutated", op="delete", id="obj-wide",
         event_id="e-w-del")  # ⛔ NO label: the id-wide fallback
    _raw(events, type="ObjectSuperseded", name="WIDE", supersedes_by="y",
         event_id="e-w-sup")
    # ⛔ The supersede is NAME-ONLY on purpose (review 13, P3): carrying the
    # stub id would route the record to the anchored `supersede-target-deleted`
    # bound instead, and it would stop being this shape. Consequence stated
    # rather than papered over: this test pins the NAME limb and the report
    # naming; the `& surviving_ids` filter is pinned by
    # `test_mention_stub_reid_by_registration_refuses_on_every_engine`, whose
    # supersede IS id-carrying.
    _raw(events, type="ObjectSuperseded", name="WIDE", supersedes_by="y",
         event_id="e-w-sup")
    proj = sdk._get_proj()
    with pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    assert "object-superseded-miss" in str(ei.value), str(ei.value)
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] >= 1, (
        "the reference fold passed a supersede whose target the journal "
        f"deleted id-wide: {r['non_folded_events']}")
    assert r["ok"] is not True, (
        "check_consistency certified a journal rebuild_all refuses")


def test_second_registration_of_a_name_adds_a_carrier_not_a_rekey(env):
    """#8864 / #3590: a registration of a name another carrier holds ADDS a
    carrier — it does not re-key the existing node.

    Pinned because the OPPOSITE assumption was mine when I migrated the retired
    `test_mention_stub_reid_by_registration_refuses_on_every_engine` (whose
    "re-id" was performed by a mention STUB mint that no longer exists): I
    wrote the journal as if the later `ObjectRegistered` moved the node onto
    its own id, so the supersede's id would address nothing and every engine
    would refuse. MEASURED: `rebuild_all` did NOT refuse — the id still
    addresses the first carrier, and the supersede folds onto it. The model and
    the graph agree, so the honest pin is this one.

    FAILS IF: a second registration of a shared name RE-KEYS the existing node
    (the pre-#8864 name-MERGE that stamped one id onto every carrier): the
    supersede's id would then address nothing and the same-name coexistence the
    identity model legalises (D2) would be unaddressable.

    ⛔ The reference-fold divergence this docstring used to file as KNOWN and
    out of scope ("`journal_object_surviving_keys` DOES re-key on a second
    registration of one name … do not fix it") is FIXED (#3590 review F1): its
    registration arm adds a carrier on ANY count and never evicts the earlier
    id, so the model says `{obj-first-ri, obj-real-ri}` for this journal — the
    same set the graph holds — and the supersede below is no longer reported as
    `object-superseded-miss`. The pin stays on the graph property (the model has
    its own pin in `tests/test_8864_shared_name_merge_key.py`).
    """
    sdk, events = env
    _raw(events, type="ObjectRegistered", id="obj-first-ri", name="ACME-RI",
         status="live", event_id="e-ri-reg1")
    _raw(events, type="ObjectRegistered", id="obj-real-ri", name="ACME-RI",
         status="live", event_id="e-ri-reg2")
    _raw(events, type="ObjectSuperseded", id="obj-first-ri", name="ACME-RI",
         supersedes_by="y", event_id="e-ri-sup")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = proj.g.query(
        "MATCH (o:Object {name:$n}) RETURN o.id, o.status",
        params={"n": "ACME-RI"}).result_set
    assert sorted(r[0] for r in rows) == ["obj-first-ri", "obj-real-ri"], (
        f"the second registration did not coexist with the first: {rows}")
    status = {r[0]: r[1] for r in rows}
    assert status["obj-first-ri"] == "superseded", (
        f"the supersede did not fold onto the id it addresses: {status}")
    assert status["obj-real-ri"] == "live", (
        "the supersede leaked onto the OTHER carrier of the shared name — a "
        f"name-keyed fold is back: {status}")


def test_label_less_rename_does_not_move_the_surviving_name():
    """#10162 review round 12, P3: a STATE OP is not id-wide — only `delete` is.

    FAILS IF: `journal_object_surviving_keys` applies a state op's `state`
    without the canonical-label gate the fold uses. `_fold_entity_mutation`
    returns 0 for a label that is not a canonical label string
    (`state-op-miss`), so the graph never applies `state` and the node keeps its
    OLD name — but the model moved the name, and a supersede the graph FOLDS
    was then reported as `object-superseded-miss` (a false RED), while a
    supersede of the name the graph never took was exempted.

    ⛔ Asserted on the ORACLE, not through a fold (migrated for #3590): the
    name-keyed supersede fold is retired, so a name-only record can no longer
    fold and the end-to-end polarity this test used to read no longer
    exercises the label gate at all. The gate is what is pinned, and the
    oracle is where it lives — with both polarities asserted (no label: does
    not move; `label="Object"`: moves) so a blanket "ignore state ops"
    regression reds it too.
    """
    from tortoise.projection import journal_object_surviving_keys

    base = [{"type": "ObjectRegistered", "id": "obj-nl",
             "name": "ACME-NL"}]
    labelled = [*base, {"type": "EntityMutated", "op": "rename",
                        "label": "Object", "id": "obj-nl",
                        "state": {"name": "ACME-NL-NEW"}}]
    label_less = [*base, {"type": "EntityMutated", "op": "rename",
                          "id": "obj-nl",
                          "state": {"name": "ACME-NL-NEW"}}]
    ids, names = journal_object_surviving_keys(label_less)
    assert "obj-nl" in ids, ids
    assert "ACME-NL" in names, (
        "a label-less state op MOVED the surviving name, so the reference "
        f"fold treats a name the graph still carries as dead: {names}")
    assert "ACME-NL-NEW" not in names, names
    ids, names = journal_object_surviving_keys(labelled)
    assert "obj-nl" in ids, ids
    assert names == frozenset({"ACME-NL-NEW"}), (
        "a canonical-label rename did NOT move the surviving name — the gate "
        f"is now a blanket ignore: {names}")


def test_nested_supersede_of_an_established_name_folds_on_both_halves(
        env, tmp_path):
    """#10107 review P2, restated for #3590: the NESTED envelope resolves as
    the FLAT one does, on both halves.

    The record is a NESTED envelope (`point={'name': …}`) carrying a NAME and
    no id — the spelling a legacy/raw producer adds. `_norm` splices the nested
    payload, so the fold sees the name it would see flat; #3590 then resolves
    it through the name→ID index (ONE carrier — here the registration above),
    and the record LANDS. ⛔ The branch earlier asserted the opposite (a
    non-exempt `object-superseded-miss`), on the premise that no fold resolves
    a name — which refused a journal `main` folds, the D2-(i) regression the
    owner excluded.

    What this file has always owned is unchanged and is what is pinned here:
    the two halves of ONE disposition must not disagree — the graph engines
    fold it and `check_consistency` must report it folded, not a divergence.

    FAILS IF: the reference fold stops splicing the nested envelope (the graph
    folds, the reference half does not → one journal, two dispositions), or
    the graph fold stops resolving the name.
    """
    _sdk, events = env
    _raw(events, type="ObjectRegistered", id="obj-nest-1", name="NEST-OK",
         status="live", event_id="e-nest-reg")
    _raw(events, type="ObjectSuperseded", point={"name": "NEST-OK"},
         supersedes_by="y", event_id="e-nest-sup")
    for engine in ("rebuild_all", "rebuild", "recover_from_log"):
        proj = _fresh(tmp_path, f"nest-{engine}")
        try:
            _drive(engine, tmp_path, events, proj)
            rows = proj.g.query(
                "MATCH (o:Object {id:'obj-nest-1'}) "
                "RETURN o.status, o.supersededBy").result_set
        finally:
            proj.close()
        assert rows == [["superseded", "y"]], (engine, rows)
    # The REFERENCE fold must read the same disposition — against the graph the
    # journal's own fold produced, so presence cannot supply the verdict.
    proj = _fresh(tmp_path, "nest-check")
    try:
        proj.rebuild_all(str(events), confirm_destructive=True)
        r = check_consistency(str(events / "events.jsonl"), proj)
    finally:
        proj.close()
    assert r["ok"] is True, (r["divergence"], r["non_folded_events"])


# ═══════════════════════════════════════════════════════════════════════
# #10204 — the D2 exemption keys on ADDRESSES, not on a name.
#
# #3590's D2 exemption (comment `6428025133`) required the record to carry a
# NAME. The owner amended it (comment `6453059051`): the record must carry at
# least one address — a name, an id, or both — and the journal must have
# established NONE of the addresses it carries. The deciding reason is
# `#10183`'s admission test: a refusal must name what the caller can do, and
# for a target the journal never established there is no creation to add,
# nothing out of order, and no producer to journal.
#
# The pins below are the ones the ADDRESS-keyed bound adds or re-points. The
# established-name and established-id polarities keep their own homes above
# (`test_name_only_supersede_of_registered_then_deleted_name_still_refuses`,
# `test_name_from_a_rename_counts_as_established_both_engines`,
# `test_collapsed_away_id_with_an_unestablished_name_still_refuses`,
# `test_state_op_target_id_with_an_unestablished_name_still_refuses`,
# `test_mention_stub_id_with_an_unestablished_name_still_refuses`) and must
# stay green: the change only ever WIDENS the exemption to a record no
# address of which the journal established.
# ═══════════════════════════════════════════════════════════════════════


def test_no_address_supersede_refuses_with_its_own_shape_and_remedy(
        env, tmp_path, caplog):
    """PIN `#10204` (4/6): a supersede carrying NO address at all refuses,
    reporting the NEW distinct shape — and its refusal does not prescribe a
    repair that does not exist.

    FAILS IF: the no-address record keeps sharing `object-superseded-miss`
    with a genuine divergence (a caller cannot tell the unrepairable case from
    the repairable one — the `#9202` defect shape), OR it is exempted (nothing
    in it is checkable, so "the journal never established it" is
    unfalsifiable), OR the abort still says "Reconcile the journal" (there is
    no creation to add, nothing out of order, no producer to journal — the
    `#10183` third part cannot be written for it).

    REACHABLE: a raw/legacy producer that emits `{supersedes_by}` with no
    target address — the shape the writer never produces.
    """
    sdk, events = env
    _raw(events, type="ObjectSuperseded", supersedes_by="other",
         event_id="e-noaddr")
    proj = sdk._get_proj()
    with caplog.at_level(logging.WARNING,
                         logger="tortoise.projection"), \
            pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    msg = str(ei.value)
    assert "object-superseded-no-address" in msg, msg
    assert "object-superseded-miss" not in msg, msg
    assert "Reconcile the journal" not in msg, (
        "the no-address refusal prescribed a repair the caller cannot perform "
        f"(#10183): {msg}")
    assert "supply the target's name or id" in msg.lower(), msg
    # ⛔ ...and the REMEDY, not merely the receipt: `__str__` embeds every
    # refused record's detail, whose no-address receipt carries the same
    # INSTRUCTION, so only the appended SENTENCE discriminates
    # `_NO_ADDRESS_REMEDY` (MEASURED: unpinned, that phrase alone survived
    # deleting the remedy). Pinned by the sentence rather than by its trailing
    # words — a terminator match passes on any rewording that keeps the ending,
    # which is exactly how two copies of one instruction drift apart.
    assert _NO_ADDRESS_REMEDY in msg, (
        f"the abort did not carry the no-address REMEDY sentence: {msg[-200:]}")
    # ⛔ The sweep's SECOND message for this record must not name the
    # genuine-miss causes either (#10183): MEASURED — deleting this shape's
    # `_sup_why` arm otherwise leaves the file green.
    report = " ".join(r.getMessage() for r in caplog.records)
    assert "delete race" not in report, report
    assert "there is nothing to check it against" in report, report
    # ⛔ The refusal is a RUN VERDICT, so every engine's own boundary must
    # reach it — not only `rebuild_all` (whose ACCEPT leg is the pinned one).
    # The `apply()` call site is shared, so a divergence introduced AT a
    # boundary would otherwise escape (MEASURED: unpinned, a boundary-only
    # mutation survives). Each
    # engine reports its own way: `rebuild` raises, `recover_from_log` returns
    # `recovered: False` with the record in its reason.
    # ⚠️ These legs get their OWN projections: `proj` above is the graph
    # `rebuild_all` produced and the assertions below read it (the receipt, the
    # reference fold, the health gate), so rebinding it here would run them
    # against a scratch/closed store — the embedded lane raises a connection
    # error on that, the docker lane silently reads a different graph.
    with caplog.at_level(logging.WARNING), \
            pytest.raises(NonFoldedEventsError,
                          match="object-superseded-no-address"):
        _drive("rebuild", tmp_path, events, _fresh(tmp_path, "no-addr-rb"))
    _rec_proj = _fresh(tmp_path, "no-addr-rec")
    try:
        res = recover_from_log(str(events), _rec_proj)
        assert res["recovered"] is False, res
        assert "object-superseded-no-address" in res["reason"], res["reason"]
    finally:
        _rec_proj.close()
    # The RECORD's own receipt, not only the aggregate abort text: the two are
    # separate copies of the same instruction, and only the abort text is
    # satisfied by `refusal_remedy`'s sentence.
    detail = ei.value.events[0].detail
    assert "neither `name` nor `id` is a non-empty string" in detail, detail
    # ⛔ THE ONE-HOME TIE (#10211 review round 2): the receipt must render the
    # SAME instruction constant the run-boundary remedy appends. Measured: the
    # pin below (`NO_ADDRESS_INSTRUCTION in _NO_ADDRESS_REMEDY`) ties the two
    # module CONSTANTS, so drifting the receipt to its own wording left all 107
    # tests green while one record got two different repairs on two surfaces —
    # the defect this change exists to remove. Asserted on the RENDERED detail,
    # which is the only place the drift is observable.
    assert NO_ADDRESS_INSTRUCTION in detail, (
        f"the per-record receipt does not render the one-home instruction "
        f"constant (two copies have drifted): {detail}")
    # The reference fold (the third classifying surface) agrees, and it is not
    # exempt there either.
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] >= 1, r["non_folded_events"]
    assert r["divergence"] == "non-folded", r["divergence"]
    assert any("object-superseded-no-address" in e
               for e in r["non_folded_events"]), r["non_folded_events"]
    # The health gate's `action` reads the SAME remedy home as the abort, so a
    # no-address-only refusal must not be told to reconcile a journal here
    # either (MEASURED: it still did while the abort did not).
    assert "Reconcile the journal" not in r["action"], r["action"]
    assert "supply the target's name or id" in r["action"], r["action"]


def test_exempt_receipt_says_the_target_was_never_established(env):
    """PIN `#10204` (AX receipt, #9202 rule 3 / `#10183`): the exempt record's
    in-band receipt says the target was never established and makes NO LOSS
    VERDICT — the loss claim it used to carry is the shape's own DEGRADED
    GUARANTEE, which `__str__` appends to the same line.

    FAILS IF: the receipt keeps the bare "supersede matched no Object" — which
    is accurate and useless: it sends a caller hunting a repair that does not
    exist (the exact AX defect `#10204` exists to close), and it reads
    identically to the receipt for a genuine, repairable divergence.

    REACHABLE: an id-only supersede no journal event ever established.
    """
    sdk, events = env
    _raw(events, type="ObjectSuperseded", id="obj-never-seen",
         supersedes_by="other", event_id="e-receipt")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)  # exempt: no raise
    r = check_consistency(str(events / "events.jsonl"), proj)
    receipts = [e for e in r["non_folded_events"]
                if "object-superseded-unregistered" in e]
    assert receipts, r["non_folded_events"]
    receipt = receipts[0]
    assert "never established" in receipt, receipt
    assert "applies no supersede here" in receipt, receipt
    # ⛔ NO "there is no repair" VERDICT (#10211 review round 7): the same
    # unprovable inference the loss verdict was, one clause over — for the
    # shape's own RESIDUAL class (an establishing line never written) the
    # supersede IS lost and the repair (journal the creation) DOES exist.
    assert "no repair" not in receipt.lower(), receipt
    # The loss verdict is gone by design (#10211 review round 3): this receipt
    # shares a line with the shape's DEGRADED GUARANTEE, which says a supersede
    # IS lost when the establishing line was never written.
    assert "nothing was lost" not in receipt.lower(), receipt


def test_a_nul_bearing_id_counts_as_an_address_end_to_end(env):
    """PIN `#10204` (the PRESENCE boundary, end to end): a record whose only
    address is a non-empty but UNWRITABLE string is EXEMPT, not refused.

    FAILS IF: the predicate is gated on `_writable_id`. That flips this record
    from exempt to `object-superseded-no-address` and aborts a rebuild over a
    target the journal established neither address of — #3590 D2 re-opened
    for that spelling, with a remedy that is factually wrong for it. The
    unit-level pin is in
    `test_classify_object_supersede_unregistered_is_its_own_exempt_shape`;
    this is the surface an operator actually meets, measured as a surviving
    mutation (MEASURED: unpinned, the writability gate survived).

    REACHABLE: an unjournaled/legacy producer that carried a raw id through a
    JSON escape (a NUL or a lone surrogate from a foreign source), which the
    journal faithfully records and the graph could not store.
    """
    sdk, events = env
    # ⛔ An unrelated anchor so the journal does not replay to an EMPTY graph
    # (`recover_from_log` has its own pre-existing empty-replay guard).
    _raw(events, type="ObjectRegistered", id="obj-nul-anchor",
         name="NUL-ANCHOR", status="live", event_id="e-nul-anchor")
    _raw(events, type="ObjectSuperseded", id="a\x00b",
         supersedes_by="other", event_id="e-nul-sup")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)  # exempt: no raise
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
    assert any("object-superseded-unregistered" in e
               for e in r["non_folded_events"]), r["non_folded_events"]


def test_hard_deleted_target_keeps_its_own_bound_and_precedence(
        env, caplog):
    """PIN `#10204` (5/6): a hard-deleted target still reports
    `supersede-target-deleted`, and that bound keeps PRECEDENCE over the
    address exemption.

    FAILS IF: `target_deleted` is consulted after the address decision, or the
    address exemption absorbs the deletion case — the deleted target's anchor
    bound is load-bearing (a same-kind delete the fold ACTUALLY applied,
    verified by the anchor-gated map), and collapsing it into the
    unregistered shape would drop that verification while still passing.

    The shape is read from the run boundary's own report (`assert_no_non_folded`
    logs every exempt record) because the reference fold records NOTHING for
    this shape — its `_object_hard_deleted` arm `pass`es by design — so the
    graph engine's classification is the surface under test here. What the two
    surfaces must share is the ACCEPT verdict, asserted below.

    REACHABLE: register an Object, hard-delete it, then supersede it BY ID —
    the record carries an established id AND a deleted target, so both bounds
    could claim it.
    """
    sdk, events = env
    _raw(events, type="ObjectRegistered", id="obj-del-1", name="DELOBJ",
         status="live", event_id="e-del-reg")
    _raw(events, type="EntityMutated", op="delete", label="Object",
         id="obj-del-1", event_id="e-del-del")
    _raw(events, type="ObjectSuperseded", id="obj-del-1",
         supersedes_by="other", event_id="e-del-sup")
    proj = sdk._get_proj()
    with caplog.at_level(logging.WARNING, logger="tortoise.projection.nonfolded"):
        proj.rebuild_all(str(events), confirm_destructive=True)  # exempt: no raise
    report = " ".join(r.getMessage() for r in caplog.records)
    # ⛔ The BRACKETED token: the `object-superseded-unregistered` exemption text
    # itself names "supersede-target-deleted", so a bare substring match would
    # be satisfied by the wrong shape's prose.
    assert "[supersede-target-deleted]" in report, report
    assert "[object-superseded-unregistered]" not in report, (
        "the address exemption absorbed the hard-delete bound: " + report)
    # ⛔ The SECOND message for this record (the sweep's own fold-miss warning)
    # must not prescribe the genuine-miss causes: a deleted target is a
    # legitimate absence, and "pre-#2194 journal, unjournaled capture SDK,
    # legacy unjournaled Object, or delete race" sends the operator hunting a
    # producer defect that does not exist (MEASURED: the branch covered
    # only the two unaddressable shapes).
    assert "delete race" not in report, report
    assert "hard-deleted the target" in report, report
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
    assert r["divergence"] != "non-folded", r["divergence"]


def test_a_later_state_op_still_establishes_the_id(env):
    """PIN `#10204` (6/6) — THE PROPERTY THAT MAKES THE WIDENING FAIL-SAFE:
    the oracle is JOURNAL-WIDE, so an address a LATER record carries still
    counts and an id-only record carrying it REFUSES.

    FAILS IF: `journal_object_established` is computed with a POSITION filter
    (or lazily at the record's own seq). Then an id whose only established
    route is a LATER record reads as "never established", the supersede is
    EXEMPTED — and at the record's own seq nothing established it while the
    journal-wide reading says something did: one journal, one verdict decided
    by position, in the fail-OPEN direction. The id half enumerates every route
    by which an id reaches a node, computed ONCE over the whole journal with no
    position filter — that is what makes granting on an unestablished id open
    no new hole.

    The route is a state op's TARGET id, not the mention stub this pin used
    before #3590: a mention establishes nothing now (D1/D9 — the fold never
    mints and no door registers a mention), so the stub route is retired and
    `journal_object_established` no longer counts an `EventRecorded` at all.
    The property is unchanged; only its live route moved.
    """
    sdk, events = env
    # No `ObjectRegistered` anywhere: the ONLY route by which the journal knows
    # this id is the state op that appears AFTER the supersede.
    _raw(events, type="ObjectSuperseded", id="obj-late", supersedes_by="y",
         event_id="e-late-sup")
    _raw(events, type="EntityMutated", op="restatus", label="Object",
         id="obj-late", state={"status": "live"}, event_id="e-late-mut")
    proj = sdk._get_proj()
    with pytest.raises(NonFoldedEventsError) as ei:
        proj.rebuild_all(str(events), confirm_destructive=True)
    msg = str(ei.value)
    assert "object-superseded-miss" in msg, msg
    assert "object-superseded-unregistered" not in msg, (
        "a LATER state op's target id was not counted — the oracle is not "
        f"journal-wide: {msg}")
    r = check_consistency(str(events / "events.jsonl"), proj)
    assert r["non_folded_refused_count"] >= 1, r["non_folded_events"]
    assert r["divergence"] == "non-folded", r["divergence"]


def test_the_repairable_refusal_still_prescribes_reconcile(env):
    """PIN `#10204` (the POSITIVE polarity of `refusal_remedy`):
    a genuine, repairable divergence still tells the caller to reconcile the
    journal.

    FAILS IF: the remedy is narrowed for every shape instead of only for the
    no-address one. `#10204` retires "Reconcile the journal" for a record
    carrying NO address — there is no creation to add and nothing out of order
    — but a target the journal DID establish and the fold missed is exactly the
    case where all three of those repairs exist. Dropping the prescription
    there removes the one instruction that names the repair, and (unlike a
    missing exemption) nothing else in the message replaces it.

    REACHABLE: a state op whose target id the journal establishes without
    creating a node (a registration the graph never wrote), plus a supersede of
    that id. ⛔ #3590 retired the same-name COLLAPSE this fixture used before:
    the MERGE key is the id, so two registrations of one name are two entities
    and the supersede folds instead of missing.
    """
    sdk, events = env
    _raw(events, type="EntityMutated", label="Object", id="obj-rm-a",
         op="restatus", state={"status": "live"}, event_id="e-rm-a")
    _raw(events, type="ObjectSuperseded", id="obj-rm-a",
         supersedes_by="other", event_id="e-rm-sup")
    with pytest.raises(NonFoldedEventsError) as ei:
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    msg = str(ei.value)
    assert "object-superseded-miss" in msg, msg
    assert "Reconcile the journal" in msg, (
        "a repairable divergence lost the only instruction that names its "
        f"repair: {msg}")
    # ⛔ And the SAME prescription on the health gate, which reads the same
    # remedy home (`refusal_remedy`). Before #10204 it kept the reconcile
    # prescription for BOTH records — so fixing only the raised error would
    # leave one record with two contradictory instructions depending on which
    # surface you read (MEASURED: the abort was split while this surface was
    # not).
    r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert r["divergence"] == "non-folded", r["divergence"]
    assert "Reconcile the journal" in r["action"], r["action"]


def test_a_mixed_abort_carries_both_remedies(env):
    """PIN `#10204`: when ONE run refuses both a repairable miss and a
    no-address record, the abort names BOTH repairs.

    FAILS IF: the remedy branch short-circuits to the no-address text whenever
    such a record is present (the repair for the other, genuinely repairable
    record then disappears), or the reverse.

    REACHABLE: a journal with a state-op-established miss AND a target-less
    supersede. ⛔ #3590 retired the same-name COLLAPSE this fixture used before
    (the MERGE key is the id, so neither registration folds the other away).
    """
    sdk, events = env
    _raw(events, type="EntityMutated", label="Object", id="obj-mx-a",
         op="restatus", state={"status": "live"}, event_id="e-mx-a")
    _raw(events, type="ObjectSuperseded", id="obj-mx-a",
         supersedes_by="other", event_id="e-mx-miss")
    _raw(events, type="ObjectSuperseded", supersedes_by="other",
         event_id="e-mx-noaddr")
    with pytest.raises(NonFoldedEventsError) as ei:
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    msg = str(ei.value)
    assert "[object-superseded-miss]" in msg, msg
    assert "[object-superseded-no-address]" in msg, msg
    assert "Reconcile the journal" in msg, msg
    assert "supply the target's name or id" in msg.lower(), msg
    # The REMEDY sentence itself, not only the receipt's copy of the phrase
    # (MEASURED: without it the phrase is satisfied by the receipt).
    assert _NO_ADDRESS_REMEDY in msg, (
        f"the mixed abort did not carry the no-address remedy sentence: "
        f"{msg[-200:]}")
    # ⛔ ONE HOME (#10211 review): the instruction the run-boundary remedy
    # appends and the instruction the per-record receipt renders are the SAME
    # constant, so a reword cannot leave two surfaces telling one caller two
    # different repairs.
    assert NO_ADDRESS_INSTRUCTION in _NO_ADDRESS_REMEDY, _NO_ADDRESS_REMEDY
    # Both on the health gate too — the mixed case is where the two remedy
    # renderers would drift apart silently (MEASURED: one was split, the other
    # was not).
    r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert "Reconcile the journal" in r["action"], r["action"]
    assert "supply the target's name or id" in r["action"], r["action"]


_DECISION_DOC = (Path(__file__).resolve().parent.parent
                 / "docs" / "epics" / "2026-09-10-2835-capability-registry"
                 / "identity-decision.md")


def test_the_decision_row_scopes_the_retired_edge_miss_justification_like_the_code():
    """The `edge-message-miss` row states the SAME scope the code does.

    FAILS IF: the row states the retirement of the A10 justification
    unqualified — as it did before this pin (`is **RETIRED**: `#8499` landed
    that fold, so a miss is no longer explained by a missing creation path and
    every remaining occurrence is evidence of a real divergence`). That reading
    is FALSE for the engine it does not name: `#8499` retired the justification
    for `rebuild_all`, which folds `DirectEdgeCreated` in pass-2b, while the
    one-record `apply()` engine has no pass-2b and still keeps
    `DirectEdgeCreated` in `_APPLY_WARN_ONLY_TYPES` (`projection/__init__.py`),
    so on `rebuild(log)` / `recover_from_log` a plain direct edge is still
    legitimately absent for a missing-creation-path reason. An unqualified row
    therefore tells an operator that this doc — the record a lane reads to
    decide what the gate permits — declares a legitimately-absent edge a
    divergence, and it contradicts the row BELOW it (`object-superseded-
    unregistered`), which carves the one-record leg out explicitly.

    REACHABLE: the row is committed prose; the assertion reads it. The premise
    (that the code's own `EXEMPT_SHAPES` entry carries the two-engine split) is
    asserted first, so a future edit to the code home reddens here instead of
    silently retiring this pin.
    """
    prose = EXEMPT_SHAPES[SHAPE_EDGE_MESSAGE_MISS]
    assert "RETIRED **for `rebuild_all`**" in prose, prose
    assert "NOT retired for the one-record" in prose, prose

    rows = [ln for ln in _DECISION_DOC.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| `edge-message-miss` |")]
    assert len(rows) == 1, f"expected exactly one edge-message-miss row: {rows}"
    row = rows[0]
    assert "RETIRED for `rebuild_all`" in row, (
        f"the decision row does not scope the retirement to `rebuild_all`: {row}")
    assert "NOT retired for the one-record `apply()` engine" in row, (
        "the decision row omits the one-record `apply()` leg, which still has "
        f"no pass-2b and keeps `DirectEdgeCreated` unfolded: {row}")
    assert "every remaining occurrence is evidence of a real divergence" in row, row
    # The unqualified construction is the defect: a retirement asserted with no
    # engine named reads as true of every engine.
    assert "is **RETIRED**:" not in row, (
        f"the retirement is stated with no engine named: {row}")
