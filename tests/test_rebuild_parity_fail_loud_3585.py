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
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.consistency import check_consistency, recover_from_log
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection
from tortoise.projection.nonfolded import (
    NonFoldedEventsError,
)
from tortoise.sdk import TortoiseSDK, _entity_name_id


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
        proj.rebuild_all(str(events))
    elif engine == "rebuild":
        proj.rebuild(EventLog(str(events / "events.jsonl")))
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
    def _build(self, sdk):
        sdk.create_entity("object", name="NAME_A")
        sdk.create_entity("object", name="SHARED")
        x = _entity_name_id("Object", "NAME_A")
        sdk.update_entity(x, name="SHARED")   # unjournaled (#3377)
        sdk.delete_entity(x)
        return x

    def test_shape_a_matches_live_on_every_engine(self, env, tmp_path):
        """FAILS IF: any engine leaves the surviving Object retracted/absent, or
        the entity-parity leg reports a divergence on a correct projection.
        REACHABLE: the fixture's unjournaled rename + id-keyed delete is exactly
        #3573's P1-A, and two SHARED carriers exist so the delete can bury one."""
        sdk, events = env
        self._build(sdk)
        live = _objects(sdk._get_proj())
        assert live == [(live[0][0], "SHARED", "live")], live
        for engine in ENGINES:
            proj = _drive(engine, tmp_path, events, _fresh(tmp_path, engine))
            assert _objects(proj) == live, f"{engine} buried shape A: {_objects(proj)}"
            proj.close()
        r = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
        assert r["ok"] is True, r["divergence"]
        assert r["divergent_entity_count"] == 0

    def test_shape_a_burial_is_caught_by_entity_parity(self, env):
        """FAILS IF: the invariant does not flag the burial end state, or does
        not expose a per-entity diagnosis.
        REACHABLE: the exact state the rejected fold wrote — the surviving
        SHARED Object's status flipped to 'retracted'. Asserting the CONTENT
        comparison alone is not enough (it was point-only before #3585)."""
        sdk, events = env
        self._build(sdk)
        proj = sdk._get_proj()
        surviving = _objects(proj)[0][0]
        proj.g.query("MATCH (o:Object {id:$i}) SET o.status='retracted'",
                     params={"i": surviving})
        r = check_consistency(str(events / "events.jsonl"), proj)
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
        REACHABLE: flip the re-created Object's status — the shape B burial."""
        _sdk, events = env
        s2, oid = self._build(tmp_path, events)
        proj = s2._get_proj()
        proj.g.query("MATCH (o:Object {id:$i}) SET o.status='retracted'",
                     params={"i": oid})
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
            sdk._get_proj().rebuild_all(str(events))
        msg = str(ei.value)
        assert "e-state-miss" in msg and "state-op-miss" in msg, msg

    def test_unknown_event_type_fails_rebuild_all(self, env):
        """FAILS IF: an unrecognized journal type is skipped silently.
        REACHABLE: a type outside `_NO_PROJECTION_FOLD` (a downgrade replaying a
        newer writer's record) — the fold has no arm and must not guess."""
        sdk, events = env
        _raw(events, type="FutureRecordType", event_id="e-future")
        with pytest.raises(NonFoldedEventsError) as ei:
            sdk._get_proj().rebuild_all(str(events))
        assert "unknown-event-type" in str(ei.value)

    def test_delete_miss_is_exempt_and_reported(self, env, caplog):
        """FAILS IF: a delete matching 0 rows fails the run — it is the NAMED
        exception (#4743), and the run must still complete.
        REACHABLE: `EntityMutated op=delete` for an id with no creation."""
        sdk, events = env
        _raw(events, type="EntityMutated", op="delete", label="Object",
             id="obj-never-existed", event_id="e-del-miss")
        with caplog.at_level("WARNING"):
            sdk._get_proj().rebuild_all(str(events))  # must NOT raise
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
            sdk._get_proj().rebuild(EventLog(str(events / "events.jsonl")))

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
