"""The *unjournaled durable mutation* class — #3312, #3300. (#3377 deferred.)

CLASS DEFINITION (this is what the suite is for, not three separate bugs):
a write that durably changes the graph has no journal record capable of
reproducing that change on replay, so ``rebuild_all`` silently reverts the
graph to an earlier state. Four mechanisms were identified in scoping; this
lane closes the two that share a seam and cites the other two:

  1. **No carrier for property mutations.** ``_update_entity`` applied caller
     props with a live ``SET n += $props`` and emitted NOTHING for the five
     non-Point canonical labels, so a status change or other revise on a
     Subject, Object, Document, Source or Event reverted to the creation
     snapshot on rebuild. (#3312 statuses)  ← closed here

     ⚠️ **A `name` change (a "rename") is NOT journaled** — #3377 is
     RETURNED TO OPEN and deferred to **#4769**. Journalling it here is a
     verified regression: `_fold_object_superseded` falls back to matching by
     NAME for legacy id-less records, and that fold is DEFERRED to a sweep that
     runs after this inline one, so journalling a rename renames the node before
     the sweep matches and DROPS the supersede (`rebuild_all` returns the object
     `live` while live/`apply()` keep `superseded`). The producer withholds the
     record and WARNS on every matching occurrence — a declared deferral, not a
     silent loss. Pinned by `test_a_name_change_is_not_journaled_and_says_so`.

     ⚠️ The producer covers all five labels; whether the FOLD can match them
     depends on the CREATION door. `Document` creation via
     `create_entity(type="document")` / `create_document` goes through
     `_create_entity`, which writes **neither** a JSONL record **nor** a
     `DocumentCreated` :GraphEvent (`DocumentCreated` is not in
     `_GRAPH_EVENT_TYPES`) — so a mutation of a Document created THAT way journals
     an `EntityMutated label="Document"` whose fold can only warn, and a rebuild
     destroys the Document. Pre-existing and tracked by **#2296**. (The
     ingest/frontmatter route DOES journal `DocumentCreated`, and a Document
     created that way folds fine — so this is a caveat about the creation door,
     not about the label.) Disclosed, not fixed here;
     `test_non_point_labels_share_the_one_seam` names it rather than claiming
     five universally-working labels.
  2. **One event type, two live end-states.** ``delete_point`` hard-deletes,
     ``retract_point`` tombstones, and BOTH emitted
     ``_emit_event("PointRetracted", …, id=id)``. The fold has exactly one
     meaning (tombstone), so a hard-deleted Point came back on rebuild as
     ``status='retracted'``. (#3300)  ← closed here
  3. **No carrier for edges / tags.**  cited, not closed: #2296 / #2897
  4. **Carrier present but unfolded** (``_NO_PROJECTION_FOLD``). cited:
     #1048

So this PR claims **two of four** mechanisms — not class closure. Mechanism 4
IS asserted below as a *documented absence* (`_NO_PROJECTION_FOLD` non-empty), so
a reader cannot mistake this suite for a proof that the class is gone. Mechanism
3 (no carrier for edges/tags, #2296/#2897) is **cited but NOT pinned** — no
assertion exists for it, and this docstring does not pretend otherwise.

THE DESIGN (one seam, not two patches):

  C1  ``_journal_entity_mutation(label, id, op, *, state, name)`` is the ONE
      ``EntityMutated`` builder. ``state`` carries **the write's own keys
      holding the values the GRAPH STORED** — not a ``properties(n)``
      snapshot. The distinction is load-bearing and both halves are asserted:

        * a key set to ``None`` is REMOVED live, so ``properties(n)`` omits it;
          a snapshot-based replay would resurrect it (``test_removal_…``);
        * a snapshot also carries props the write never touched, and
          ``properties(n)`` returns a ``VectorF32`` as a plain list, so
          re-applying it DEMOTES the vector and silently breaks dense
          retrieval (``test_state_keys_are_exactly_the_writes_keys``).

  C2  ``delete_point`` splits its one event into two stores, one meaning each:
      the ``:GraphEvent`` ``PointRetracted`` row keeps the subscriber surface
      unchanged, and a JSONL-only ``EntityMutated op="delete"`` carries the
      honest hard-delete for replay.

NON-FOLDED SET (the #3312 lesson): ``rebuild == live`` ALONE IS VACUOUS —
a mutation that is journaled and then silently not folded also leaves live and
replay equal whenever the property happens to match. Every round-trip row
therefore ALSO asserts **zero fold-miss / unknown-op warnings** (``caplog``),
so "replayed correctly" cannot be satisfied by "never noticed".

Runnable (docker lane):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    uv run pytest tests/test_unjournaled_mutation_class.py -q --tb=short
"""
from __future__ import annotations

import ast
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.log import EventLog
from tortoise.projection import (
    _ENTITY_MUTATION_IMPLEMENTED_OPS,
    _ENTITY_MUTATION_OPS,
    _ENTITY_MUTATION_PENDING_OPS,
    _ENTITY_MUTATION_STATE_OPS,
    classify_entity_mutation_op,
)
from tortoise.sdk import TortoiseSDK

_REC = "EntityMutated"
_REPO = Path(__file__).resolve().parent.parent

# Warning substrings emitted by the fold — the non-folded-set signal.
#
# ``_MISS`` is deliberately the DISTINCTIVE tail of this lane's state-op warning,
# not the shared ``"fold matched no entity"`` prefix: pass-1b already emits
# ``"EntityMutated delete fold matched no entity"`` for a delete, and a shared
# substring would make ``test_delete_miss_does_not_warn`` unable to tell the two
# apart (it caught exactly that on first run).
_MISS = "claims a mutation whose entity never re-existed"
_UNKNOWN = "unknown EntityMutated op"
_PENDING = "has no fold arm yet"

# Props that ``rebuild_all`` does not reproduce on a Point, PRE-EXISTING on base
# (``1f5d6efc4``) and filed as **#4666** — verified identical with and without
# this lane. Excluded from the global live==replay comparison so that the
# comparison asserts the lane's own contract rather than failing on someone
# else's open gap; scoped to ``:Point`` only, so an equivalent gap on the five
# labels this lane fixes still fails here.
_KNOWN_POINT_REPLAY_GAP = frozenset({
    "note",          # journaled PointRevised annotation, never restored (#4666)
    "updatedAt",     # regenerated at replay, not replayed (#4666)
    "ep_dirty",      # episodic-dirty marker, set live only (#4666)
    "ep_dirty_at",
})


# ── fixtures / helpers ────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path):
    """``(sdk, events_dir)`` with the JSONL journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "ujm.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _mutations(events) -> list[dict]:
    return [e for e in _journal(events) if e.get("type") == _REC]


def _rows(sdk, label: str, id_val: str) -> list:
    """``[]`` iff the node is ABSENT (node absence only — see ``_prop``)."""
    return sdk._get_proj().g.query(
        f"MATCH (n:{label} {{id:$i}}) RETURN n", params={"i": id_val}
    ).result_set


def _props(sdk, label: str, id_val: str, *keys: str) -> dict:
    """Read props as ``{k: v}``; a missing prop reads ``None``.

    Uses ``RETURN n.k`` (never ``RETURN n {.*}``) so that an absent prop is
    distinguishable from a null one only where Cypher can distinguish it —
    which is exactly the contract ``SET n += {k: null}`` implements.
    """
    if not _rows(sdk, label, id_val):
        return {k: None for k in keys}
    ret = ", ".join(f"n.{k} AS {k}" for k in keys)
    res = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{id:$i}}) RETURN {ret}", params={"i": id_val}
    ).result_set
    return dict(zip(keys, res[0], strict=True)) if res \
        else {k: None for k in keys}


def _has_prop(sdk, label: str, id_val: str, key: str) -> bool:
    """Does the node carry ``key`` AT ALL? (NOT ``!= None`` — see docstring.)"""
    res = sdk._get_proj().g.query(
        f"MATCH (n:{label} {{id:$i}}) RETURN n.{key} IS NULL AS missing",
        params={"i": id_val},
    ).result_set
    return bool(res) and res[0][0] is False


def _append_raw(events, **rec) -> None:
    """Append a hand-written journal line.

    The ONLY door for a record `_emit_event` cannot write: it journals nothing
    when neither `point=` nor `id=` is given, so an `id`-less
    `ObjectSuperseded` (the legacy shape `_fold_object_superseded`'s #2164
    ISSUE-B name fallback exists for) can only arrive as a raw line. Using it
    here is the point: it is the realistic producer, not a synthetic one.
    """
    rec.setdefault("event_id", "raw-" + str(rec.get("type", "?")))
    rec.setdefault("ts", "2026-01-01T00:00:00+00:00")
    with open(events / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _fold_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if _MISS in r.getMessage() or _UNKNOWN in r.getMessage()
            or _PENDING in r.getMessage()]


def _snapshot_all(sdk) -> dict:
    """Every canonical entity's identity + property map, sorted.

    The fidelity spine: asserted equal across a rebuild. A fix that "cures"
    divergence by dropping data fails here.

    ``:Point`` props in ``_KNOWN_POINT_REPLAY_GAP`` are subtracted — see that
    constant for why (pre-existing, #4666, verified on base). Nothing is
    subtracted for the five labels this lane fixes.
    """
    out = {}
    for label, prop in (("Point", "id"), ("Object", "id"), ("Subject", "id"),
                        ("Document", "id"), ("Source", "id"), ("Event", "eventId")):
        rows = sdk._get_proj().g.query(
            f"MATCH (n:{label}) RETURN n.{prop}, properties(n)"
        ).result_set
        for rid, props in rows:
            skip = _KNOWN_POINT_REPLAY_GAP if label == "Point" else frozenset()
            out[(label, rid)] = {k: props[k] for k in sorted(props)
                                 if k not in skip}
    return out


def _assert_round_trip(sdk, events, caplog):
    """Live == replay over EVERY canonical entity, with a clean fold."""
    caplog.clear()
    live = _snapshot_all(sdk)
    sdk._get_proj().rebuild_all(str(events))
    replay = _snapshot_all(sdk)
    assert _fold_warnings(caplog) == [], (
        "fold reported a mutation it could not replay: "
        f"{_fold_warnings(caplog)}")
    # Report the DIFFERING KEYS, not a dict repr — a repr of two long property
    # maps is unreadable and this assertion has to be actionable.
    problems = []
    for key in sorted(set(live) | set(replay), key=str):
        a, b = live.get(key), replay.get(key)
        if a is None or b is None:
            problems.append(f"{key}: entity present on only one side")
            continue
        missing = sorted(set(a) ^ set(b))
        changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
        if missing or changed:
            problems.append(
                f"{key}: present-on-one-side={missing} value-differs={changed}")
    assert not problems, "rebuild diverged from live:\n  " + "\n  ".join(problems)


# ══ C1 — property mutations of the five non-Point canonical labels ═══════

class TestPropertyMutationRoundTrip:
    """Mechanism 1: rename / restatus / revise / removal, one seam."""

    # `rename` is NOT here: #3377 is deferred to #4769 (journalling a `name`
    # change renames the node before the deferred `ObjectSuperseded` sweep
    # matches on that name, dropping a legacy id-less supersede). See
    # `test_a_name_change_is_not_journaled_and_says_so`.
    @pytest.mark.parametrize("op,prop,new", [
        ("restatus", "status", "archived"),
        ("revise", "objectKind", "risks"),
    ])
    def test_object_mutation_survives_rebuild(self, env, caplog, op, prop, new):
        sdk, events = env
        oid = sdk.create_entity("object", name="Original",
                                objectKind="opportunities")["node"]["id"]
        sdk.update_entity(oid, **{prop: new})
        assert _props(sdk, "Object", oid, prop)[prop] == new

        recs = [r for r in _mutations(events) if r.get("id") == oid]
        assert [r["op"] for r in recs] == [op]
        assert recs[0]["label"] == "Object"
        assert recs[0]["state"] == {prop: new}

        _assert_round_trip(sdk, events, caplog)
        assert _props(sdk, "Object", oid, prop)[prop] == new

    def test_removal_survives_rebuild(self, env, caplog):
        """The row a ``properties(n)`` SNAPSHOT provably cannot replay.

        ``SET n += {note: null}`` REMOVES the prop live and ``properties(n)``
        omits it — so a snapshot journal journals the *absence*, and replaying
        the snapshot re-applies nothing, leaving the prop... no: worse, the
        creation record still carries ``note``, so replay RESURRECTS it. The
        mutation's-own-keys state carries ``note: None``, and the fold runs the
        identical ``SET n += {note: None}``, which removes it again.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="HasNote",
                                objectKind="opportunities", note="doomed"
                                )["node"]["id"]
        assert _has_prop(sdk, "Object", oid, "note")

        sdk.update_entity(oid, note=None)
        assert not _has_prop(sdk, "Object", oid, "note"), \
            "the LIVE write must remove the prop (else this row tests nothing)"

        rec = [r for r in _mutations(events) if r.get("id") == oid][-1]
        assert "note" in rec["state"] and rec["state"]["note"] is None, (
            "state must carry the write's key with the stored (None) value — a "
            "snapshot would omit the key entirely")

        _assert_round_trip(sdk, events, caplog)
        assert not _has_prop(sdk, "Object", oid, "note"), \
            "rebuild RESURRECTED a removed property"

    def test_state_keys_are_exactly_the_writes_keys(self, env):
        """C1's other half: no snapshot, so nothing the write did not touch.

        A snapshot ``state`` carries every prop, and ``properties(n)`` returns
        a ``VectorF32`` as a plain ``list`` — re-applying it demotes the
        vector and silently breaks dense retrieval (``run_vector_query`` →
        ``[]`` / ``query_failed``). Asserting key-exactness rules that out
        transitively, without needing an embedding model in CI.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="A", objectKind="k",
                                confidence=0.5)["node"]["id"]
        sdk.update_entity(oid, confidence=0.9)
        rec = [r for r in _mutations(events) if r.get("id") == oid][-1]
        assert set(rec["state"]) == {"confidence"}, (
            "state must be the WRITE'S keys only; a snapshot would drag in "
            f"every stored prop: {sorted(rec['state'])}")

    def test_mixed_write_records_full_state_under_one_op(self, env, caplog):
        """Labelling precedence must not lose the other transitions.

        ``op`` is the PRIMARY INTENT (name > status > else); ``state`` carries
        the whole applied map, so a consumer wanting every status transition
        reads ``state['status']``, never filters on ``op``. Asserted so the
        documented precedence cannot silently become lossy.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="A",
                                objectKind="k")["node"]["id"]
        sdk.update_entity(oid, status="archived", confidence=0.9,
                          objectKind="k2")

        rec = [r for r in _mutations(events) if r.get("id") == oid][-1]
        assert rec["op"] == "restatus", "status outranks the generic revise"
        assert rec["state"] == {"status": "archived", "confidence": 0.9,
                               "objectKind": "k2"}

        _assert_round_trip(sdk, events, caplog)
        got = _props(sdk, "Object", oid, "status", "confidence", "objectKind")
        assert got == {"status": "archived", "confidence": 0.9,
                       "objectKind": "k2"}

    def test_non_json_native_value_is_journalled_as_stored(self, env, caplog):
        """The silent-loss half of mechanism 1.

        ``EventLog.append`` is ``json.dumps`` with no ``default=``, and
        ``_emit_event`` swallows the resulting ``TypeError`` to a WARNING —
        so journalling the CALLER's ``Decimal`` would drop the record and
        produce exactly the defect this lane fixes, silently. Journalling the
        STORED value (native) cannot raise.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="A",
                                objectKind="k")["node"]["id"]
        sdk.update_entity(oid, confidence=Decimal("0.25"))

        recs = [r for r in _mutations(events) if r.get("id") == oid]
        assert recs, "a non-JSON-native value silently dropped the record"
        assert isinstance(recs[-1]["state"]["confidence"], float)

        _assert_round_trip(sdk, events, caplog)
        assert _props(sdk, "Object", oid, "confidence")["confidence"] == \
            pytest.approx(0.25)

    def test_non_point_labels_share_the_one_seam(self, env, caplog):
        """Two of the five labels on the one seam — and a named CAVEAT.

        The producer journals for all five non-`Point` labels, but only FOUR of
        them can fold: `Document` creation goes through `_create_entity`, which
        writes **neither** a JSONL record **nor** a `DocumentCreated`
        :GraphEvent (`DocumentCreated` is not in `_GRAPH_EVENT_TYPES`;
        pre-existing, #2296). A `Document` mutation therefore journals an
        `EntityMutated label="Document"` whose fold can only ever warn, and a
        rebuild destroys the Document. That is #2296's disclosed residual, NOT
        something this seam fixes — so this test asserts the labels it actually
        exercises and does not claim five.
        """
        sdk, events = env
        sub = sdk.create_subject("Topic")
        sid = (sub.get("node") or sub)["id"]
        oid = sdk.create_entity("object", name="O",
                                objectKind="k")["node"]["id"]
        sdk.update_entity(sid, subjectKind="topic")
        sdk.update_entity(oid, objectKind="k2")
        labels = {r["label"] for r in _mutations(events)}
        assert labels == {"Object", "Subject"}
        _assert_round_trip(sdk, events, caplog)

    def test_object_status_survives_a_supersede_that_came_first(
            self, env, caplog):
        """#4743 review P1 — journal order across `rebuild_all`'s deferral boundary.

        `ObjectSuperseded` folds are DEFERRED to a sweep just after pass 1b (a
        deliberate ordering: the fold is an unconditional
        `SET o.status='superseded'` and must run after every Object-creation
        event), while `EntityMutated` state folds run INLINE. Without an order
        check the sweep wins over a LATER state op, so live and `apply()` read
        `archived` while `rebuild_all` reads `superseded` — the exact revert
        this lane removes, in the very engine it removes it from (and the two
        engines stop agreeing with each other, breaking the #330 parity claim).
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="O", objectKind="k")["node"]["id"]
        sdk._emit_event("ObjectSuperseded", id=oid, name="O",
                        supersedes_by="other", session_id="s", evidence="e")
        sdk.update_entity(oid, status="archived")
        assert _props(sdk, "Object", oid, "status")["status"] == "archived"

        # Compares ONLY the property under test, not the whole map: the
        # synthetic `_emit_event` above journals the supersede but does not
        # perform the live graph write the real producer
        # (`commit_ops.apply_supersessions`) performs alongside it, so the
        # supersede's OWN props (`supersededBy`/`supersededAt`) legitimately
        # exist on the replay side only. `status` is the two-sided value.
        caplog.clear()
        sdk._get_proj().rebuild_all(str(events))
        assert _fold_warnings(caplog) == [], _fold_warnings(caplog)
        assert _props(sdk, "Object", oid, "status")["status"] == "archived", (
            "the deferred ObjectSuperseded sweep clobbered a LATER state op — "
            "rebuild_all reverted the mutation")

    def test_supersede_still_wins_when_it_came_last(self, env):
        """The CONVERSE of the P1 fix: a supersede after the state op must win.

        Without this row the P1 fix could be "re-apply every state fold after
        the sweep", which would invert the other order and make replay
        disagree with live wherever a supersede legitimately landed last.

        Asserts the replay outcome only, not `live == replay`: the synthetic
        `_emit_event` here does NOT perform the live graph write the real
        producer (`commit_ops.apply_supersessions`) performs alongside it, so
        this probe's live side is not a faithful live sequence — only the
        journal, and therefore the replay, is faithful.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="O", objectKind="k")["node"]["id"]
        sdk.update_entity(oid, status="archived")
        sdk._emit_event("ObjectSuperseded", id=oid, name="O",
                        supersedes_by="other", session_id="s", evidence="e")
        sdk._get_proj().rebuild_all(str(events))
        assert _props(sdk, "Object", oid, "status")["status"] == "superseded", (
            "a supersede journalled LAST must win — the P1 fix must not simply "
            "re-apply every state fold")

    def test_every_post_supersede_state_op_is_restored(self, env):
        """#4743 review round 2 — the sweep clobbers the WHOLE inline history.

        Round 1 re-applied only the LAST state op, so `sup -> status=archived ->
        name=New` left `status` clobbered: the terminal event was the rename and
        did not carry `status`, so `rebuild_all` read `superseded` while live and
        `apply()` read `archived`.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="O", objectKind="k")["node"]["id"]
        sdk._emit_event("ObjectSuperseded", id=oid, name="O",
                        supersedes_by="other", session_id="s", evidence="e")
        sdk.update_entity(oid, status="archived")
        sdk.update_entity(oid, objectKind="k2")
        assert _props(sdk, "Object", oid, "status")["status"] == "archived"

        sdk._get_proj().rebuild_all(str(events))
        got = _props(sdk, "Object", oid, "objectKind", "status")
        assert got == {"objectKind": "k2", "status": "archived"}, (
            "a post-supersede state op was lost — only the last one was "
            f"replayed: {got}")

    def test_a_post_supersede_op_on_a_deleted_object_does_not_warn(
            self, env, caplog):
        """#4743 review round 6 — the re-fold must not cry wolf on a correct replay.

        The post-supersede re-fold undoes the sweep's clobber. When the object is
        hard-deleted AFTER the mutation there is nothing to undo, so re-folding it
        matched 0 rows and emitted the non-folded-set warning:

            "... fold matched no entity ... (post-wipe divergence or out-of-order
             journal)"

        Both stated causes are false — the INLINE fold applied the mutation
        correctly. This matters because that warning is the lane's EVIDENCE that a
        replay could not fold a claim: `_fold_entity_mutation` exempts
        `op="delete"` for exactly this reason. A warning on a valid journal
        destroys the evidence's meaning.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="O", objectKind="k")["node"]["id"]
        sdk._emit_event("ObjectSuperseded", id=oid, name="O",
                        supersedes_by="other", session_id="s", evidence="e")
        sdk.update_entity(oid, status="archived")
        sdk.delete_entity(oid)

        caplog.clear()
        with caplog.at_level("WARNING"):
            sdk._get_proj().rebuild_all(str(events))
        misses = _fold_warnings(caplog)
        assert not misses, (
            "the replay was CORRECT (the inline fold applied it); a fold-miss "
            f"warning here is a false positive: {misses}")
        # ...and the isolation control: the same journal MINUS the delete really
        # does restore the mutation, so the row above is not passing because the
        # re-fold never runs at all.
        sdk2, events2 = env
        oid2 = sdk2.create_entity("object", name="O",
                                  objectKind="k")["node"]["id"]
        sdk2._emit_event("ObjectSuperseded", id=oid2, name="O",
                         supersedes_by="other", session_id="s", evidence="e")
        sdk2.update_entity(oid2, status="archived")
        sdk2._get_proj().rebuild_all(str(events2))
        assert _props(sdk2, "Object", oid2, "status")["status"] == "archived", (
            "control: without the delete the post-supersede op IS restored")

    def test_a_name_change_is_not_journaled_and_says_so(self, env, caplog):
        """#3377 is DEFERRED (#4769) — and the deferral must be LOUD.

        Journalling a `name` change renames the node in pass 1b, before the
        deferred `ObjectSuperseded` sweep matches on that name, so a legacy
        id-less supersede is dropped (`rebuild_all` returns the object
        status='live' while live and `apply()` keep 'superseded'). Rather than
        ship that regression, the producer withholds the record — and warns on
        every matching occurrence, so the withhold is a declared deferral and not
        the silent loss this lane exists to fix.

        This row is the pin for that contract: it asserts the write still
        applies to the graph, that NO `EntityMutated` record is written for it,
        and that a warning naming #4769 is emitted.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="A",
                                objectKind="k")["node"]["id"]
        before = len(_mutations(events))
        caplog.clear()
        with caplog.at_level("WARNING"):
            # NAME-ONLY: a write carrying other keys journals THOSE (see
            # `test_a_name_bearing_write_still_journals_its_other_keys`).
            sdk.update_entity(oid, name="B")

        assert _props(sdk, "Object", oid, "name")["name"] == "B", \
            "the live write must still apply — only the JOURNAL is withheld"
        assert len(_mutations(events)) == before, \
            "a `name` change must not be journalled while #3377 is deferred"
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "#4769" in msgs and "NOT journaled" in msgs, (
            "the deferral must be loud and name its tracking issue: " f"{msgs}")

        # ...and it warns on EVERY occurrence, not once per process: a one-shot
        # net would leave the second and later reverts silent, which is the
        # failure mode this whole lane exists to remove.
        caplog.clear()
        with caplog.at_level("WARNING"):
            sdk.update_entity(oid, name="C")
        assert any("#4769" in r.getMessage() for r in caplog.records), \
            "a repeat `name` change must warn again, not go silent"

    def test_a_name_bearing_write_still_journals_its_other_keys(self, env,
                                                                caplog):
        """The withhold must be exactly `name` wide — #3312 for the rest.

        Withholding the WHOLE map (the first cut of this fix) reopened #3312 for
        the ordinary call `update_entity(id, name=..., status=...)`: the status
        rode along with the withheld record and was silently reverted by
        `rebuild_all`. Only `name` moves the node in front of the deferred
        name-keyed supersede sweep, so only `name` needs withholding.
        """
        sdk, events = env
        oid = sdk.create_entity("object", name="A",
                                objectKind="k")["node"]["id"]
        caplog.clear()
        with caplog.at_level("WARNING"):
            sdk.update_entity(oid, name="B", status="archived",
                              confidence=0.9)
        assert _props(sdk, "Object", oid,
                      "name")["name"] == "B", "live write still applies"

        recs = [r for r in _mutations(events) if r.get("id") == oid]
        assert recs, "the non-`name` keys must still journal"
        state = recs[-1]["state"]
        assert "name" not in state, (
            "`name` must NOT be journalled while #3377 is deferred: " f"{state}")
        assert state == {"status": "archived", "confidence": 0.9}, state
        assert recs[-1]["op"] == "restatus", recs[-1]["op"]

        # The payload of this test: the status SURVIVES a rebuild.
        sdk._get_proj().rebuild_all(str(events))
        got = _props(sdk, "Object", oid, "status", "confidence")
        assert got == {"status": "archived", "confidence": 0.9}, (
            "#3312 must be closed for a name-BEARING write too: " f"{got}")

    def test_a_name_write_that_matched_nothing_does_not_cry_wolf(self, env,
                                                                 caplog):
        """A no-op must not warn that a mutation will be reverted."""
        sdk, _events = env
        caplog.clear()
        with caplog.at_level("WARNING"):
            sdk.update_entity("no-such-id-at-all", name="B")
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "#4769" not in msgs, (
            "nothing matched, so nothing will be reverted: " f"{msgs}")

    def test_no_record_for_a_write_that_matched_nothing(self, env):
        """No phantom record: the producer mirrors ``_delete_entity``'s
        post-apply ordering contract."""
        sdk, events = env
        before = len(_mutations(events))
        sdk.update_entity("obj-does-not-exist", name="ghost")
        assert len(_mutations(events)) == before


# ══ C2 — delete_point: one event type must not mean two end-states ══════

class TestPointDeleteTwoStore:
    """Mechanism 2 (#3300)."""

    def test_hard_delete_is_durable(self, env, caplog):
        sdk, events = env
        pid = sdk.create_point("observation", "delete me")["id"]
        sdk.delete_point(pid)
        assert _rows(sdk, "Point", pid) == [], "premise: live hard-delete"

        _assert_round_trip(sdk, events, caplog)
        assert _rows(sdk, "Point", pid) == [], \
            "rebuild RESURRECTED a hard-deleted Point"

    def test_delete_journals_honest_op_and_no_retract_line(self, env):
        """The two stores, one meaning each."""
        sdk, events = env
        pid = sdk.create_point("observation", "x")["id"]
        sdk.delete_point(pid)

        recs = [r for r in _mutations(events) if r.get("id") == pid]
        assert [(r["op"], r["label"]) for r in recs] == [("delete", "Point")]
        assert "state" not in recs[0], \
            "#3299's recorded shape carries nothing for a delete"

        assert _journal(events) and \
            not [e for e in _journal(events)
                 if e.get("type") == "PointRetracted"], (
            "PointRetracted must NOT be in the JSONL — its fold tombstones, "
            "which is the wrong meaning for a hard delete")

    def test_subscriber_surface_unchanged(self, env):
        """The ``:GraphEvent`` row — and therefore ``events_poll`` — is intact.

        This is the property that makes the split acceptable: the fix changes
        which store carries the *replay* meaning, not what subscribers see.
        """
        sdk, _events = env
        pid = sdk.create_point("observation", "x")["id"]
        sdk.delete_point(pid)

        types = [r[0] for r in sdk._get_proj().g.query(
            "MATCH (e:GraphEvent) RETURN e.type").result_set]
        assert "PointRetracted" in types
        polled = sdk.events_poll(after=None)["events"]
        assert any(e["type"] == "PointRetracted"
                   and e["payload"] == {"id": pid} for e in polled)

    def test_retract_still_tombstones(self, env, caplog):
        """The CONTRAST row: splitting delete must not change retract.

        Without this, C2's fix could be "stop emitting PointRetracted" and the
        hard-delete row would pass while retract silently broke.
        """
        sdk, events = env
        pid = sdk.create_point("observation", "retract me")["id"]
        sdk.retract_point(pid)
        assert _rows(sdk, "Point", pid) != [], "premise: retract keeps the node"
        assert _props(sdk, "Point", pid, "status")["status"] == "retracted"

        _assert_round_trip(sdk, events, caplog)
        assert _rows(sdk, "Point", pid) != [], \
            "rebuild LOST a retracted Point (tombstone must survive)"
        assert _props(sdk, "Point", pid, "status")["status"] == "retracted"

    def test_delete_entity_point_agrees_with_delete_point(self, env, caplog):
        """The two Point-delete doors must end in the same graph state."""
        sdk, events = env
        a = sdk.create_point("observation", "via delete_point")["id"]
        b = sdk.create_point("observation", "via delete_entity")["id"]
        sdk.delete_point(a)
        assert sdk.delete_entity(b)

        _assert_round_trip(sdk, events, caplog)
        assert _rows(sdk, "Point", a) == [] and _rows(sdk, "Point", b) == [], \
            "the two Point-delete doors diverge after rebuild"


# ══ The op vocabulary — one declaration, enforced ═══════════════════════

class TestOpVocabulary:
    """``#2901``'s lesson: a hand-written subset at a call site is how a value
    is silently omitted from a reader. So the vocabulary is ONE declaration and
    its relationships are asserted, not derived."""

    def test_ops_partition_into_implemented_and_pending(self):
        assert set(_ENTITY_MUTATION_OPS) == (
            _ENTITY_MUTATION_IMPLEMENTED_OPS | _ENTITY_MUTATION_PENDING_OPS)
        assert not (_ENTITY_MUTATION_IMPLEMENTED_OPS
                    & _ENTITY_MUTATION_PENDING_OPS)

    def test_state_ops_plus_delete_is_the_implemented_set(self):
        assert _ENTITY_MUTATION_STATE_OPS | {"delete"} == \
            _ENTITY_MUTATION_IMPLEMENTED_OPS
        assert "delete" not in _ENTITY_MUTATION_STATE_OPS

    @pytest.mark.parametrize("op", sorted(_ENTITY_MUTATION_PENDING_OPS))
    def test_pending_ops_are_the_documented_ones(self, op):
        assert op in {"retract", "supersede"}, (
            "the pending set is hand-maintained so that adding an op to "
            "_ENTITY_MUTATION_OPS cannot auto-absorb it into 'pending' and "
            "leave every other assertion green")

    def test_classifier_codomain_equals_the_state_ops(self):
        """AST, not a call: a codomain check must not depend on which inputs
        the suite happens to think of."""
        src = (_REPO / "tortoise/projection/__init__.py").read_text()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "classify_entity_mutation_op")
        returned = {
            n.value.value for n in ast.walk(fn)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
        }
        assert returned == set(_ENTITY_MUTATION_STATE_OPS)

    @pytest.mark.parametrize("props,expected", [
        ({"name": "x"}, "rename"),
        ({"status": "s"}, "restatus"),
        ({"name": "x", "status": "s"}, "rename"),
        ({"objectKind": "k"}, "revise"),
    ])
    def test_classifier_precedence(self, props, expected):
        assert classify_entity_mutation_op(props) == expected

    def test_exactly_one_entity_mutated_builder(self):
        """One shape, one builder — a second hand-rolled record is the drift
        this class is made of."""
        src = (_REPO / "tortoise/sdk.py").read_text()
        tree = ast.parse(src)
        builder = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
             and n.name == "_journal_entity_mutation"), None)
        assert builder is not None, \
            "_journal_entity_mutation is the one EntityMutated builder"

        def _calls(node):
            for c in ast.walk(node):
                if (isinstance(c, ast.Call)
                        and isinstance(c.func, ast.Attribute)
                        and c.func.attr == "_emit_event"):
                    yield c

        emit_calls = [c for c in _calls(tree)
                      if c.args and isinstance(c.args[0], ast.Name)
                      and c.args[0].id == "_ENTITY_MUTATION_RECORD_TYPE"]
        assert len(emit_calls) == 1, (
            "exactly one _emit_event call may emit the EntityMutated record")
        assert emit_calls[0] in list(_calls(builder)), \
            "that call must live inside _journal_entity_mutation"

    def test_no_literal_entity_mutated_emit(self):
        """The record type is a constant, never a string literal at a site."""
        tree = ast.parse((_REPO / "tortoise/sdk.py").read_text())
        for c in ast.walk(tree):
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "_emit_event" and c.args):
                assert not (isinstance(c.args[0], ast.Constant)
                            and c.args[0].value == _REC), (
                    f"line {c.lineno}: EntityMutated emitted from a literal — "
                    "route it through _journal_entity_mutation")

    def test_builder_rejects_an_unimplemented_op(self, env):
        sdk, _ = env
        with pytest.raises(ValueError, match="not an implemented"):
            sdk._journal_entity_mutation("Object", "o1", "retract")
        with pytest.raises(ValueError, match="not an implemented"):
            sdk._journal_entity_mutation("Object", "o1", "typo")


# ══ The non-folded set — a silent fold is the class's own defect ════════

class TestNonFoldedSet:
    """``rebuild == live`` alone is vacuous. These rows make the fold's
    silence observable: anything the journal claims and the fold cannot replay
    must WARN, and a legitimately-idempotent miss must NOT."""

    def _raw(self, events, **rec):
        rec.setdefault("event_id", "evt-" + rec.get("op", "?"))
        rec.setdefault("ts", "2026-01-01T00:00:00+00:00")
        with open(events / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    @pytest.mark.parametrize("op", sorted(_ENTITY_MUTATION_STATE_OPS))
    def test_state_op_miss_warns(self, env, caplog, op):
        """The journal claims a mutation whose entity never re-existed."""
        sdk, events = env
        self._raw(events, type=_REC, op=op, label="Object",
                  id="obj-never-existed", state={"name": "x"})
        with caplog.at_level("WARNING"):
            sdk._get_proj().rebuild_all(str(events))
        assert _MISS in " ".join(_fold_warnings(caplog)), \
            f"a fold that matched nothing for op={op} must warn"
        assert "obj-never-existed" in " ".join(_fold_warnings(caplog)), \
            "the message must carry the id that locates the journal line"

    @pytest.mark.parametrize("op", sorted(_ENTITY_MUTATION_PENDING_OPS))
    def test_pending_op_warns_and_says_why(self, env, caplog, op):
        sdk, events = env
        self._raw(events, type=_REC, op=op, label="Object", id="obj-1")
        with caplog.at_level("WARNING"):
            sdk._get_proj().rebuild_all(str(events))
        msgs = " ".join(_fold_warnings(caplog))
        assert _PENDING in msgs, (
            f"op={op} is RECORDED (#3299) but unimplemented — it must say so "
            "rather than be reported as 'unknown'")

    def test_unknown_op_warns(self, env, caplog):
        sdk, events = env
        self._raw(events, type=_REC, op="teleported", label="Object",
                  id="obj-1", state={"name": "x"})
        with caplog.at_level("WARNING"):
            sdk._get_proj().rebuild_all(str(events))
        assert _UNKNOWN in " ".join(_fold_warnings(caplog)), \
            "an unrecognised op silently drops a mutation — must be loud"

    def test_delete_miss_does_not_warn(self, env, caplog):
        """The deliberate exemption: a delete matching 0 rows is legitimately
        idempotent (retried delete; restore replaying onto a non-empty graph).
        Warning here would turn valid journals into false positives — and the
        pass-1b warning already covers the rebuild_all case."""
        sdk, events = env
        self._raw(events, type=_REC, op="delete", label="Object",
                  id="obj-never-existed")
        with caplog.at_level("WARNING"):
            sdk._get_proj().rebuild_all(str(events))
        assert _MISS not in " ".join(_fold_warnings(caplog))

    def test_miss_is_visible_through_apply_the_shared_fold_entry_point(
            self, env, caplog):
        """``apply()`` DISCARDS the returned count, so the warning must live
        INSIDE the fold rather than at a call site.

        SCOPE, stated honestly: this row exercises ``apply()`` only. That
        ``recover_from_log`` / ``backup.restore`` inherit it is a claim about the
        CODE PATH (they reach this same fold through ``projection.apply``), not
        something asserted here — the name must not read as four engines."""
        sdk, _events = env
        proj = sdk._get_proj()
        caplog.clear()
        with caplog.at_level("WARNING"):
            proj.apply({"type": _REC, "op": "rename", "label": "Object",
                        "id": "obj-never-existed", "state": {"name": "x"},
                        "event_id": "e1"})
        assert _MISS in " ".join(_fold_warnings(caplog)), \
            "apply() must surface the fold-miss too"

    def test_label_outside_the_allowlist_never_reaches_cypher(self, env, caplog):
        """A journal ``label`` is never interpolated into the Cypher label
        position — it must be a canonical member or the fold refuses.

        Asserted BEHAVIOURALLY (the fold is called directly, no wipe): with an
        injected label like ``Object) DETACH DELETE n //`` a non-refusing fold
        would delete every ``:Object`` in one statement, and a wipe+replay
        would mask it by recreating them. So a canary Object plus the fold
        called in isolation is the only shape that can actually fail.
        """
        sdk, _events = env
        canary = sdk.create_entity("object", name="canary",
                                   objectKind="k")["node"]["id"]
        other = sdk.create_entity("object", name="sibling",
                                  objectKind="k")["node"]["id"]
        caplog.clear()
        with caplog.at_level("WARNING"):
            sdk._get_proj().apply({
                "type": _REC, "op": "rename",
                "label": "Object) DETACH DELETE n //",
                "id": canary, "state": {"name": "pwned"},
                "event_id": "e-inject",
            })
        assert _rows(sdk, "Object", canary) != [], \
            "an injected label deleted the canary Object"
        assert _rows(sdk, "Object", other) != [], \
            "an injected label deleted a sibling Object"
        assert _props(sdk, "Object", canary, "name")["name"] == "canary", \
            "the refused record must not have applied either"
        assert _MISS in " ".join(_fold_warnings(caplog)) \
            or _UNKNOWN in " ".join(_fold_warnings(caplog)), \
            "refusing a non-canonical label must be reported, not silent"


# ══ The write path's fail-open contract ══════════════════════════════

class TestJournalWriteFailure:
    """A journal append failure must be LOUD and must not pretend the write
    failed.

    This is the write-path twin of the non-folded-set contract above, and it is
    the residual obligation of the whole design: the builder is best-effort
    (``_emit_event`` swallows the append error), so the ONE thing that keeps
    that acceptable is that the failure is *observable*. #3585 owns the
    fail-closed half (refusing the write when the journal cannot record it);
    until that lands, the warning is the entire safety net — so it is pinned.
    """

    def test_append_failure_is_loud_and_the_write_still_lands(
            self, env, caplog, monkeypatch):
        sdk, events = env
        oid = sdk.create_entity("object", name="A",
                                objectKind="k")["node"]["id"]
        before = len(_mutations(events))

        from tortoise.log import EventLog

        def _boom(self, event):
            raise OSError("disk full")

        monkeypatch.setattr(EventLog, "append", _boom)
        caplog.clear()
        with caplog.at_level("WARNING"):
            # A JOURNALED op — a `name` change is deliberately withheld now
            # (#3377/#4769), so it would never reach the append this row tests.
            sdk.update_entity(oid, status="archived")

        # 1. LOUD — a silently dropped mutation is this class's own defect.
        msgs = [r.getMessage() for r in caplog.records]
        assert any("failed to append EntityMutated event" in m for m in msgs), (
            f"a dropped journal append must name the record it lost: {msgs}")
        # 2. Fail-OPEN, deliberately: the live SET already committed, so the
        #    SDK must neither raise nor roll back. The price is that THIS write
        #    is not durable — which is exactly why (1) must hold.
        assert _props(sdk, "Object", oid, "status")["status"] == "archived", \
            "the live mutation must still have applied (fail-open, not rollback)"
        # 3. ...and the loss is real, so the assertion above is not vacuous.
        assert len(_mutations(events)) == before, \
            "the append really did fail — there must be no new record"


# ══ Legacy / hand-written journal shapes ═══════════════════════════════

class TestLegacyJournalShapes:
    """Rows whose input can only come from a raw journal line.

    These are the `rebuild_all`-deferral guard's own regression rows. They were
    briefly dropped as "untested defensive code" — which was the wrong call and
    for the wrong reason. The guard was never untestable: the FIRST attempt
    failed only because a name-only `ObjectSuperseded` cannot be written by
    `_emit_event` (no `point=`/`id=` → no JSONL line at all), so the probe was
    exercising a `:GraphEvent`-only record that `rebuild_all` never reads.
    Constructing the line directly — the way the other legacy rows in this file
    do — tests exactly the path the name fallback exists for.

    The defect these pin: the deferred `ObjectSuperseded` sweep applies AFTER an
    inline `EntityMutated` state fold, so without consulting journal order the
    sweep wins over a later state op (and a blanket re-fold would invert the
    other direction). Both directions are asserted.
    """

    def test_legacy_name_keyed_supersede_beats_an_earlier_state_op(
            self, env):
        """Supersede journalled LAST must win — the guard must NOT re-fold."""
        sdk, events = env
        oid = sdk.create_entity("object", name="Legacy",
                                objectKind="k")["node"]["id"]
        sdk.update_entity(oid, status="archived")
        _append_raw(events, type="ObjectSuperseded", name="Legacy",
                    supersedes_by="other", session_id="s", evidence="e")

        sdk._get_proj().rebuild_all(str(events))
        assert _props(sdk, "Object", oid, "status")["status"] == "superseded", (
            "the state fold overwrote a LATER name-keyed supersede")

    def test_state_op_after_a_legacy_name_keyed_supersede_wins(self, env):
        """The converse: the state op journalled LAST must win, so the guard
        must re-fold it rather than being disabled entirely."""
        sdk, events = env
        oid = sdk.create_entity("object", name="Legacy",
                                objectKind="k")["node"]["id"]
        _append_raw(events, type="ObjectSuperseded", name="Legacy",
                    supersedes_by="other", session_id="s", evidence="e")
        sdk.update_entity(oid, status="archived")

        sdk._get_proj().rebuild_all(str(events))
        assert _props(sdk, "Object", oid, "status")["status"] == "archived", (
            "the deferred supersede sweep clobbered a LATER state op")


# ══ Green-on-arrival guards ════════════════════════════════════════════

class TestRegressionGuards:
    """Behaviour this lane must NOT change. These pass on base too — their job
    is to fail if the seam is widened."""

    def test_point_annotator_path_unchanged(self, env, caplog):
        """The ``:Point`` branch of ``_update_entity`` still emits
        ``PointRevised`` with only the annotator props, and still journals no
        ``EntityMutated`` (its own record type already carries it)."""
        sdk, events = env
        pid = sdk.create_point("observation", "x")["id"]
        sdk.update_point(pid, note="annotated")
        kinds = [e.get("type") for e in _journal(events)]
        assert "PointRevised" in kinds
        assert not [r for r in _mutations(events) if r.get("id") == pid], (
            "the Point annotator path must not double-journal via the new seam")
        _assert_round_trip(sdk, events, caplog)

    def test_class_mechanisms_3_and_4_are_still_open(self, env):
        """Documented ABSENCE, asserted so this suite cannot be read as class
        closure: the PR closes two of four mechanisms."""
        # 3a/3b: no carrier for edges/tags; carrier-but-unfolded.
        from tortoise.projection import _NO_PROJECTION_FOLD
        assert _NO_PROJECTION_FOLD, (
            "if this is empty, mechanism 4 (carrier-but-unfolded) changed — "
            "update the class docstring with the citation")
