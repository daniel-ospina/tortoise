"""The record projection's contract, made executable (#7969, epic #7879).

WHAT THIS PINS. Interface A — *"journal → Postgres, replayable and idempotent"* —
is five claims, and each one is a test here rather than a sentence in a design
doc:

1. **TOTAL.** Every record type the journal can carry has a declared mutation,
   and a type with no handler is a **failure, not a skip** (`#5285`'s rule
   applied to the record's destination). Three legs: the registry covers the
   types the graph fold dispatches on, the types `tortoise/log.py` classifies as
   torn-tail-harmless, and — the one that makes "a NEW producer reds the suite"
   true — the record types the producers actually emit.
2. **IDEMPOTENT.** Structural, not sampled: every node mutation is an
   `INSERT … ON CONFLICT … DO UPDATE`, every patch writes absolute values, every
   delete is a `DELETE`, and the journal position is `ON CONFLICT DO NOTHING`.
   A second replay therefore writes the same values onto the same rows. The
   end-to-end half (replay twice against a real Postgres) runs in the PGlite
   harness — `supabase/tests/pglite/validate.mjs` — because this suite is
   deliberately DB-free so it runs in every Python lane.
3. **A FIELD IS PRIMARY IFF THE JOURNAL CARRIES IT.** The property→column
   decision is read from `#7968`'s schema **on disk**, never restated here, and
   §17.5's placement table is the expected set: every PRIMARY property is
   storable, no DERIVED/VOLATILE property is ever stored as authority.
4. Exact-SQL evidence for the shapes above (the emitted statements are asserted,
   not merely executed).
5. **TENANCY IS PER CALL (#9192).** `(org_id, graph_id)` are REQUIRED keyword
   arguments of every entry point here — there is no ambient tenant, no
   environment fallback and no ContextVar at this layer. A process serving
   several tenant graphs (the hosted lane builds one SDK per tenant graph, and
   its capture executor is process-wide) must not be able to file one tenant's
   rows under another: the row would be well-formed and the FK to
   `public.graphs` only checks that the graph exists, so nothing would raise.
   Five legs: two tenants in one process never cross-write; a third tenant
   planted in the ENVIRONMENT never reaches a plan a caller asked for; a caller
   that cannot name its tenant REFUSES rather than inheriting one (each
   coordinate on its own); the module is scanned to prove it has no ambient
   reader to inherit FROM; and the same substitution is pinned at the write
   site (`tests/test_record_writer_seam.py`).

   ⛔ SCOPE, so the pin is not over-read: this file holds the fold every writer
   funnels through. The write site — where a caller names the pair — is pinned
   in `tests/test_record_writer_seam.py`, and BOTH legs are needed: a tenant
   supplied correctly and then dropped between the call and the plan is only
   visible at the seam, and an ambient read that is DORMANT on every fixture
   path is only visible to the scan in this file — a read that FIRES reds the
   projection legs, and the seam leg where its three-record fixture reaches the
   same builder. A caller-level
   ARMING leg ("a multi-tenant process must not be armable from env") belongs to
   #9192's own ask 2, and it cannot be pinned at either layer until this tree
   carries that carrier: neither entry point here has a tenant env pair or a
   ContextVar, which is what the AST scan below turns into an executable
   statement — within the limits that scan declares.

WHY THE PLACEMENT TABLE IS IMPORTED, NOT RE-DECLARED. `#7968`'s
`tests/test_record_schema_placement.py` already parses §17.5 and resolves its
eight rows (`RESOLUTIONS`); a second parser here would be a second declaration
of the placement, and the second declaration is the one that goes stale.

NO DATABASE IS NEEDED. The DB-level half of S1 is the PGlite harness, whose
subject is the same statement set this module emits.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_record_schema_placement import parse_placement
from tortoise import record_projection as rp

REPO_ROOT = Path(__file__).resolve().parent.parent

#: section in §17.5's table → the record table the projection materializes it to
SECTION_TABLE = {
    "Event": "event",
    "Source": "source",
    "Session": "session",
    "Object": "object",
    "Subject": "subject",
    "Point": "point",
}


# ════════════════════════════════════════════════════════════════════════════
# 1. TOTALITY
# ════════════════════════════════════════════════════════════════════════════


def _fold_dispatch_vocabulary() -> set[str]:
    """Every record type `tortoise/projection/` dispatches on.

    The graph fold's dispatch IS the journal's consumed vocabulary — a type the
    fold handles and the record does not is exactly the silent-skip defect.
    """
    found: set[str] = set()
    for name in ("__init__.py", "entities.py", "edges.py"):
        text = (REPO_ROOT / "tortoise" / "projection" / name).read_text(
            encoding="utf-8"
        )
        # `(?<![A-Za-z_])` — without it `unit == "TiB"` (a byte-unit
        # comparison in the fold's own formatting helper) matches as a record
        # type named `TiB`. A word boundary is the whole fix; a denylist of
        # non-record words would hide a real type the day one is named that way.
        found |= set(re.findall(r'(?<![A-Za-z_])t\s*==\s*"([A-Z][A-Za-z]+)"', text))
        found |= set(re.findall(r'ev\.get\("type"\)\s*==\s*"([A-Z][A-Za-z]+)"', text))
        found |= set(re.findall(r'"type":\s*"([A-Z][A-Za-z]+)"', text))
    return found


def _log_torn_tail_harmless_vocabulary() -> set[str]:
    """`tortoise/log.py`'s executable torn-tail-harmless classification set.

    This is NOT the full journal vocabulary: log.py's refused/terminal types
    live in prose, not in an executable list. The full producer surface is
    pinned by :func:`_producer_emit_vocabulary` and
    :func:`_fold_dispatch_vocabulary`; this leg only proves that no type
    log.py *classifies* is missing from the registry.
    """
    text = (REPO_ROOT / "tortoise" / "log.py").read_text(encoding="utf-8")
    block = re.search(
        r"TORN_TAIL_HARMLESS_EVENT_TYPES\s*=\s*frozenset\(\{(.*?)\n\}\)",
        text,
        re.S,
    )
    assert block is not None, "TORN_TAIL_HARMLESS_EVENT_TYPES not found in log.py"
    return set(re.findall(r'"([A-Z][A-Za-z]+)"', block.group(1)))


def test_registry_and_vocabulary_agree():
    """The import-time assert, pinned as a test so a reader sees the expectation."""
    assert set(rp.RECORD_MUTATIONS) == set(rp.JOURNAL_EVENT_TYPES)


def test_every_type_the_fold_dispatches_on_has_a_record_mutation():
    missing = sorted(_fold_dispatch_vocabulary() - set(rp.JOURNAL_EVENT_TYPES))
    assert not missing, (
        f"{missing} are folded by tortoise/projection/ but have no declared "
        "record mutation — Interface A is total, and an event the graph fold "
        "consumes but the record skips is a silent hole"
    )


def test_every_torn_tail_harmless_type_has_a_record_mutation():
    missing = sorted(_log_torn_tail_harmless_vocabulary() - set(rp.JOURNAL_EVENT_TYPES))
    assert not missing, (
        f"{missing} are in log.py's TORN_TAIL_HARMLESS_EVENT_TYPES but have no "
        "declared record mutation"
    )


def test_unknown_record_type_is_a_failure_not_a_skip():
    with pytest.raises(rp.UnknownJournalRecordType) as excinfo:
        rp.mutations_for(
            {"type": "PointTeleported", "id": "p1"}, org_id="o", graph_id="g"
        )
    assert "PointTeleported" in str(excinfo.value)
    # The failure must NAME the remedy, not only the symptom.
    assert "RECORD_MUTATIONS" in str(excinfo.value)


# ── one minimal, realistic record per type, so totality is exercised, not read ─

_SNAPSHOT = {
    "id": "p1",
    "content": "c",
    "pointKind": "observation",
    "createdAt": "2026-10-12T00:00:00Z",
}
_MINIMAL_RECORDS: dict[str, dict] = {
    "PointAdded": {"id": "p1", "point": dict(_SNAPSHOT)},
    "OperatorAdded": {"id": "p1", "point": dict(_SNAPSHOT, is_operator=True, op_type="IMPL")},
    "PointPromoted": {"id": "p1", "point": dict(_SNAPSHOT)},
    "OperatorPromoted": {"id": "p1", "point": dict(_SNAPSHOT)},
    "DedupeRecorded": {"id": "p1", "point": dict(_SNAPSHOT)},
    "DedupeRejected": {"id": "p1", "point": dict(_SNAPSHOT)},
    # The PRODUCERS' shape, not an invented one: both are emitted FLAT
    # (`sdk._emit_event(_BATCH_ID_RECORD_TYPE, id=point_id, **extra)` and
    # `subject_binding`'s `EntityBindingRefused, id=point_id, ...`). This fixture
    # used to supply a `point` snapshot neither producer emits, which is how
    # both mutations stayed declared as snapshot-carried and refused every real
    # write in silence (#9078 review, measured).
    "EntityBindingRefused": {"id": "p1", "point_id": "p1", "role": "subject",
                             "name": "n1", "kind": "Person", "outcome": "refused",
                             "reason": "no declared subject"},
    "BatchIdStamped": {"id": "p1", "batch_id": "b1", "content_hash": "h1"},
    "PointRevised": {"id": "p1", "new_content": "c2"},
    "OperatorAnnotated": {"id": "p1", "annotator_bias": 0.5},
    "PointRetracted": {"id": "p1"},
    # #9892: the PRODUCER's shape (`api.py::merge_points`), which carries no
    # `op` — the fixture that used to stand in for this type (in the refusal
    # test) invented `op: "merge"`, a key no writer emits, so the declared
    # refusal never fired for the only producer and the type was neither
    # refused nor projected. A fixture that cannot be produced pins nothing.
    "PointsMerged": {"keep_id": "p1", "merge_ids": ["p2"],
                     "corrects": None},
    "PointSuperseded": {"id": "p1", "new_id": "p2",
                        # The real producer payload (#2423): the bi-temporal
                        # stamps the fold replays verbatim.
                        "valid_from": "2026-10-12T00:00:00Z",
                        "valid_to": "2026-10-12T00:00:24Z",
                        "expired_at": "2026-10-12T00:00:24Z"},
    "PointInvalidated": {"id": "p1", "corrected_by": "p2",
                         "valid_to": "2026-10-12T00:00:25Z",
                         "expired_at": "2026-10-12T00:00:25Z"},
    "ConfidenceChanged": {"id": "p1", "confidence": 0.4},
    # #5380: the edge-message write-back. IDENTITY is the flat composite
    # (an edge has no node id of its own), the same shape
    # `DirectEdgeCreated` uses.
    "EdgeMessageChanged": {"id": "p1->p2:IMPL", "op": "set",
                          "src": "p1", "tgt": "p2", "rel": "IMPL",
                          "msg_alpha": 0.5, "msg_beta": 0.5},
    "ObjectRegistered": {"id": "o1", "name": "n1", "objectKind": "document"},
    "ObjectSuperseded": {"id": "o1", "name": "n1", "supersedes_by": "o2"},
    "SubjectAdded": {"id": "s1", "name": "sub1", "subjectKind": "other"},
    "EventRecorded": {"id": "e1", "eventId": "e1", "eventKind": "x"},
    "SourceCreated": {"id": "u1", "url": "u1", "sourceKind": "web"},
    "SourceVersioned": {"id": "u1", "url": "u1", "contentHash": "h2",
                        "previousContentHash": "h1", "previousValidTo": "t0"},
    "DocumentCreated": {"id": "u1", "url": "u1"},
    "SessionRecorded": {"id": "sess1", "turn_count": 2, "is_episodic": True},
    # #9555/R7: the narrative carrier's PRODUCER shape
    # (`hosted_api._execute_commit_writes` → `sdk._emit_event("SessionNarrated",
    # id=event_id, sessionId=, story_arc=, summary=, capturedAt=)`), not an
    # invented one. It carries the narrative, so it DOES produce a statement
    # through the shape-keyed secondary projection while declaring
    # `NoRecordEffect` — see the branch in the totality test below.
    "SessionNarrated": {"id": "e1", "sessionId": "sess1",
                       "story_arc": "the session, told as prose",
                       "summary": "one line"},
    # #10017: the raw-TURN carrier. Its producer is step 7, so this is the shape
    # that producer will emit: the capture writer's own per-turn snapshots
    # (`record_projection.TURNS_SNAPSHOT_KEYS`) under `turns`, with the
    # `contains_session` link on the ENVELOPE (#3947) — not an invented shape.
    # It declares `NoRecordEffect`, so it must produce NO statement at all.
    "TurnsRecorded": {"id": "e1", "sessionId": "sess1",
                      "contains_session": "sess1",
                      "turns": [{"id": "sess1_t0", "content": "hi",
                                 "pointKind": "event", "speaker": "user",
                                 "is_episodic": True, "status": "draft",
                                 "createdAt": "2026-10-25T00:00:00Z",
                                 "embedding": None}]},
    "SessionDeleted": {"id": "sess1"},
    "EntityLinked": {"id": "p1", "source_label": "Point", "source_id": "p1",
                     "target_label": "Object", "target_id": "o1",
                     "edge_type": "aboutObject"},
    "DirectEdgeCreated": {"id": "p1->p2:IMPL", "src": "p1", "tgt": "p2",
                          "edge_type": "IMPL"},
    "DirectEdgeRepoint": {"id": "p1->p2:IMPL", "src": "p1", "tgt": "p2",
                          "edge_type": "IMPL"},
    "IngestStarted": {"run_id": "r1"},
    "CalibrationRecorded": {"value": 0.5},
}


def test_there_is_a_minimal_record_for_every_declared_mutation():
    """The fixture below must not silently lag the registry."""
    assert set(_MINIMAL_RECORDS) == set(rp.JOURNAL_EVENT_TYPES) - {
        "EntityMutated",
    }


@pytest.mark.parametrize("record_type", sorted(_MINIMAL_RECORDS))
def test_each_declared_type_materializes_or_declares_a_no_effect(record_type):
    record = {"type": record_type, "ts": "2026-10-12T00:00:00Z",
              **_MINIMAL_RECORDS[record_type]}
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    effect = rp.RECORD_MUTATIONS[record_type].effect
    if isinstance(effect, rp.NoRecordEffect):
        # #9555/R7: a declared no-effect record can still carry a SECONDARY
        # projection's payload — the §9.1 narrative home is shape-keyed on
        # purpose (any producer carrying `story_arc` lands it, including one
        # nobody listed in a type whitelist), so "no declared NODE effect" does
        # not mean "no statements". Asserting `statements == []` unconditionally
        # would force every future narrative carrier into the very whitelist
        # #9555 removed.
        if record.get("story_arc"):
            assert statements, (
                f"{record_type} carries the §9.1 narrative and produced no "
                "statement — the shape-keyed secondary projection did not fire")
            assert any(rp.NARRATIVE_TABLE in s.sql for s in statements), (
                f"{record_type}'s statements do not touch {rp.NARRATIVE_TABLE}")
        else:
            assert statements == []
        # A declared no-op must say WHY. "No reason" is the silent skip.
        assert len(effect.reason) > 40, (
            f"{record_type} declares NoRecordEffect with a reason too thin to "
            "be a decision a reviewer can check"
        )
    else:
        assert statements, f"{record_type} produced no mutation and no reason"


def test_entity_mutated_dispatches_on_op_and_refuses_undeclared_ops():
    base = {"type": "EntityMutated", "ts": "T", "id": "p1", "label": "Point"}
    delete = rp.mutations_for({**base, "op": "delete"}, org_id="o", graph_id="g")
    # The hard delete is the identity parent's removal (cascades the node and
    # its incident relations, matching the fold's `DETACH DELETE`) then the
    # node row itself.
    assert len(delete) == 2, delete
    assert delete[0].sql.startswith("DELETE FROM public.node_identity"), delete
    assert delete[1].sql.startswith("DELETE FROM public.point"), delete

    patch = rp.mutations_for(
        {**base, "op": "restatus", "state": {"status": "retracted"}},
        org_id="o", graph_id="g",
    )
    assert len(patch) == 1 and patch[0].sql.startswith("UPDATE public.point")
    assert "status" in patch[0].sql and "retracted" in patch[0].params

    # #9881 — a rename is a PATCH against the node's stable key. This pair used
    # to assert a `RefusedJournalOperation` (`#3377`/`#4769`) because `node_key`
    # WAS the name, so applying it would have moved the key. The key is the id
    # now, so the applied `state` is written as it stands.
    rename = rp.mutations_for(
        {**base, "op": "rename", "state": {"name": "n2"}},
        org_id="o", graph_id="g",
    )
    assert len(rename) == 1 and rename[0].sql.startswith("UPDATE public.point")
    # A Point has no `name` column, so the applied value lands where the graph
    # itself puts it — the node's property tail (`SET n += $state`).
    assert "n2" in str(rename[0].params)
    _renamed = rename[0].sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "node_key" not in _renamed, (
        "a rename moved the row's KEY — the one thing #9881 establishes it must "
        f"never do; the key is the stable id ({_renamed})")

    # ...and a rename that names no id is REFUSED, not silently applied to 0
    # rows: pre-#3590 "rename the entity whose name is X" cannot say WHICH of
    # two same-name entities was renamed.
    with pytest.raises(rp.RecordProjectionError) as excinfo:
        rp.mutations_for({**base, "id": None, "op": "rename",
                          "state": {"name": "n2"}},
                         org_id="o", graph_id="g")
    assert "STABLE id" in str(excinfo.value)

    with pytest.raises(rp.RecordProjectionError):
        rp.mutations_for({**base, "op": "levitate"}, org_id="o", graph_id="g")


def test_9881_a_rename_moves_the_name_and_never_the_objects_key():
    """The keystone: the record ACCEPTS the producer's rename record (#9881).

    Measured before the fix — this is the reproduction, and it is the reason the
    test is written against the exact record `sdk._update_entity` journals:

        >>> rp.mutations_for({"type": "EntityMutated", "id": "obj-1",
        ...                   "label": "Object", "op": "rename",
        ...                   "state": {"name": "AcmeRenamed"}}, ...)
        RefusedJournalOperation: EntityMutated op='rename': #3377/#4769 — ... a
        rename MOVES the canonical key (Object/Subject key = `name`), so it is a
        key migration, not a patch.

    So the whole write half of #3590 could not land: the first public
    `update_entity(id, name=…)` on a record-armed deployment raised.

    What the fix establishes, in one sentence: the row's key is the STABLE ID, so
    the rename is an ordinary column patch. Three assertions, one per clause —
    it applies (not refused), it writes the NAME column, and it never touches
    `node_key` (which is what made the old refusal honest).
    """
    record = {
        "type": "EntityMutated", "ts": "T",
        "id": "1a16d368511-efb0", "label": "Object",
        "op": "rename", "state": {"name": "AcmeRenamed"},
    }
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    assert len(statements) == 1, statements
    sql, params = statements[0].sql, statements[0].params
    assert sql.startswith("UPDATE public.object"), sql
    assert "name = %s" in sql, f"the rename did not write the name column: {sql}"
    assert "AcmeRenamed" in params, params
    sets_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "node_key" not in sets_clause, (
        "the rename moved the row's KEY. Under the id key a rename is a patch; a "
        "key move would have to cascade `node_identity` and every incident "
        "`relation` endpoint, which is exactly the migration #9881 removes")
    # Matched by the stable id, and the name spelling is still accepted (a
    # structural edge's descriptor names an Object by NAME — §1.3).
    assert "id = %s" in sql, sql

    # TWO incarnations, ONE name. This is what a name-keyed row could not do, and
    # it is the whole point of moving the key: the second rename targets a
    # DIFFERENT row with the SAME new name and produces its own UPDATE, so no
    # primary-key collision and no duplicated/orphaned row is possible.
    second = rp.mutations_for(
        {**record, "id": "1a16d368512-aa11"}, org_id="o", graph_id="g")
    assert len(second) == 1, second
    assert second[0].params != statements[0].params, (
        "two entities renaming to one name produced the SAME statement — a "
        "name-keyed record would collapse them into one row")
    assert second[0].params[-1] == "1a16d368512-aa11", second[0].params


def test_9881_an_objects_patch_with_no_id_is_refused_and_never_name_matched():
    """#9948 review P3 — `ObjectSuperseded`'s note described a fallback that
    does not exist, in the direction that matters (fail-OPEN).

    The note said the `name` column is *"kept as the natural-key match so a
    legacy line carrying no id still resolves"*. Neither half is true after
    #9881: `_key_value(node, record, "id")` has NO `name` fallback (that
    absence-keyed fallback exists only for `url`/`eventId`), so a name-only
    record RAISES `RecordProjectionError`; and the patch goes through
    `_patch_statement`'s default `match=("node_key",)`, which IS the id — never
    `name`.

    Why the refusal has to be at the DOOR, measured: a name-matched patch would
    match ZERO rows now that `node_key` is the id — silence, not an error. The
    schema is not a backstop here either: this mutation emits exactly ONE
    statement (the `NodePatch` branch; there is no `node_identity` insert on this
    path, unlike the snapshot branch), and its `SET` never rewrites
    `name`/`id`/`node_key`, so `chk_object_node_key_is_contract_key` is
    re-evaluated on an unchanged key triple and can never fire.

    Non-vacuity: on the pre-#9881 tree the SAME record is accepted
    (`Node("object", "Object", "name")`, `key_from="name"`) and returns an
    UPDATE — so the `pytest.raises` below fails.
    """
    record = {"type": "ObjectSuperseded", "ts": "T", "name": "acme",
              "superseded_by": "o2"}
    with pytest.raises(rp.RecordProjectionError) as excinfo:
        rp.mutations_for(record, org_id="o", graph_id="g")
    assert "'id'" in str(excinfo.value), excinfo.value

    statements = rp.mutations_for({**record, "id": "o1"}, org_id="o", graph_id="g")
    assert len(statements) == 1, statements
    sql = statements[0].sql
    assert sql.startswith("UPDATE public.object"), sql
    where = sql.split(" WHERE ", 1)[1]
    assert "node_key = %s" in where, (
        "the patch is not matched on `node_key` (the contract key since "
        f"20261027000001): {where}")
    assert "name" not in where, (
        "the patch matched on `name` — a name is a MUTABLE natural key since "
        f"#3590, so it can address a different row: {where}")


def test_the_declared_refusal_mechanism_still_refuses(monkeypatch):
    """`refused_ops` is EMPTY after #9881 — the MECHANISM must still bite.

    Empty is not the same as absent. The declaration is how a future op the fold
    cannot apply is refused loudly instead of guessed (`#5285`'s silent-skip
    rule), and an empty mapping that nothing exercises is a mapping that could
    quietly stop being consulted. So this constructs one:

    the real record is re-dispatched with a synthetic refusal, proving that an
    entry in that mapping raises :class:`rp.RefusedJournalOperation` — and that
    the REAL registry has no member left, which is the #9881 outcome (a refusal
    that reappears where it does not belong reds this leg too).
    """
    import dataclasses

    assert rp.RECORD_MUTATIONS["EntityMutated"].effect.refused_ops == {}, (
        "a declared refusal came back to `EntityMutated` — #9881 established "
        "that every state op the fold implements, `rename` included, is "
        "representable once the key is the stable id")

    effect = dataclasses.replace(
        rp.RECORD_MUTATIONS["EntityMutated"].effect,
        refused_ops={"levitate": "synthetic — a test owns this reason"})
    patched = dataclasses.replace(rp.RECORD_MUTATIONS["EntityMutated"], effect=effect)
    monkeypatch.setitem(rp.RECORD_MUTATIONS, "EntityMutated", patched)

    with pytest.raises(rp.RefusedJournalOperation) as excinfo:
        rp.mutations_for(
            {"type": "EntityMutated", "ts": "T", "id": "o1", "label": "Object",
             "op": "levitate", "state": {}},
            org_id="o", graph_id="g")
    assert "synthetic" in str(excinfo.value)


def test_9881_the_semantic_layer_keys_on_id_and_the_reader_sees_the_forward_ddl():
    """Indicator (3) on the Python side: the key column, and the DDL as read.

    The schema half is pinned in `supabase/tests/` (the CHECK is rewritten and
    two same-name ids persist); this leg pins that the PROJECTION agrees with it
    — a projection keyed by `name` against a schema keyed by `id` writes a row
    its own CHECK refuses, mid-transaction.
    """
    assert rp.NODE_TABLES["object"] == ("Object", "id")
    assert rp.NODE_TABLES["subject"] == ("Subject", "id")
    key_props = rp.key_properties()
    assert key_props["Object"] == "id", key_props
    assert key_props["Subject"] == "id", key_props
    # The OTHER labels are untouched: this was a two-label key move, not a
    # re-spelling of the whole contract.
    assert key_props["Point"] == "id"
    assert key_props["Event"] == "eventId"
    assert key_props["Source"] == "url"
    assert key_props["Session"] == "id"


def test_points_merged_deletes_the_losers_and_leaves_the_survivor():
    """#9892 — the merge's record mutation is the GRAPH fold's, and it fires.

    The declared refusal this replaces (a) never fired for the only producer
    (`api.py::merge_points` emits no `op`, so the record raised a generic "no
    `op`" fold failure instead — the gap was misclassified, not declared), and
    (b) described a mutation no code performs: `keep_id` is read nowhere in
    `tortoise/` outside the record module, and the graph's `PointsMerged` arm is
    one `DETACH DELETE` per `merge_ids` entry.

    What the record must therefore emit per loser is the pair
    `EntityMutated op=delete` emits — the `node_identity` parent (whose
    composite FKs cascade the incident relation rows, the record's `DETACH`)
    and then the node row.
    """
    statements = rp.mutations_for(
        {"type": "PointsMerged", "ts": "T", "keep_id": "p1",
         "merge_ids": ["p2", "p3"]},
        org_id="o", graph_id="g",
    )
    assert len(statements) == 4, statements          # 2 per loser
    assert statements[0].sql.startswith("DELETE FROM public.node_identity")
    assert statements[1].sql.startswith("DELETE FROM public.point")
    assert "p2" in statements[1].params
    assert statements[2].sql.startswith("DELETE FROM public.node_identity")
    assert statements[3].sql.startswith("DELETE FROM public.point")
    assert "p3" in statements[3].params
    # The survivor is NOT patched: no statement names it, so the record cannot
    # invent a state the graph fold never writes.
    assert not any("p1" in s.params for s in statements), statements
    assert not any(s.sql.startswith("UPDATE") for s in statements), statements


def test_points_merged_reads_the_nested_carrier_too():
    """#325's nested shape is a SUPPORTED journal shape — the record too.

    `_norm` splices `ev["point"]` over the envelope before the fold reads
    `merge_ids`, and `tests/test_projection.py::test_falkor_apply_points_merged_
    nested_format` pins that the graph deletes from it. A record that read only
    the flat carrier would leave the loser in Postgres while the graph dropped
    it — the two rebuild routes disagreeing, in silence.
    """
    flat = rp.mutations_for(
        {"type": "PointsMerged", "ts": "T", "keep_id": "p1",
         "merge_ids": ["p2"]}, org_id="o", graph_id="g")
    nested = rp.mutations_for(
        {"type": "PointsMerged", "ts": "T",
         "point": {"keep_id": "p1", "merge_ids": ["p2"]}},
        org_id="o", graph_id="g")
    assert [s.params for s in flat] == [s.params for s in nested]
    assert [s.sql for s in flat] == [s.sql for s in nested]


def test_a_nested_carrier_wins_over_an_envelope_copy():
    """`_norm` is `{**ev, **ev["point"]}` — the NESTED value wins.

    A record read flat-first would delete by the envelope's `merge_ids` on a
    journal whose `point` sub-dict says something else, while the graph deletes
    by the nested one: two rebuilds, two different graphs, from one journal.
    """
    statements = rp.mutations_for(
        {"type": "PointsMerged", "ts": "T",
         "merge_ids": ["envelope-loser"],
         "point": {"keep_id": "p1", "merge_ids": ["nested-loser"]}},
        org_id="o", graph_id="g")
    deleted = {s.params[-1] for s in statements
               if s.sql.startswith("DELETE FROM public.point")}
    assert deleted == {"nested-loser"}, deleted


def test_points_merged_with_no_ids_is_the_folds_no_op():
    """`merge_ids: []`/absent deletes nothing and does not raise.

    The graph fold's arm is `for mid in ev.get("merge_ids") or []`, so an empty
    merge is a 0-row no-op on both sides (`tests/test_projection.py` applies a
    bare `{"type": "PointsMerged"}` and requires no error). A record that
    refused it would fail a write the graph completes.
    """
    for record in ({"type": "PointsMerged", "ts": "T"},
                   {"type": "PointsMerged", "ts": "T", "merge_ids": []},
                   {"type": "PointsMerged", "ts": "T",
                    "merge_ids": [None, 3]}):
        assert rp.mutations_for(record, org_id="o", graph_id="g") == [], record


def test_the_record_deletes_exactly_the_points_the_fold_removes():
    """The parity pin, stated as the one question that matters.

    Not "does the record emit a delete" — *does the record delete the same
    points the graph fold's replay removes?* The graph side is
    `projection.fold`, the in-memory fold every replay path is pinned against;
    the record side is the key set its DELETE statements carry. A divergence
    here is a record-sourced rebuild that resurrects a merged-away Point, which
    is the data loss #9892 measured.
    """
    from tortoise.projection import fold

    events = [
        {"type": "PointAdded", "point": {"id": "a", "content": "A"}},
        {"type": "PointAdded", "point": {"id": "b", "content": "B"}},
        {"type": "PointAdded", "point": {"id": "c", "content": "C"}},
        {"type": "PointsMerged", "keep_id": "a", "merge_ids": ["b", "c"]},
    ]
    surviving = set(fold(events))
    merge = events[-1]
    deleted = {
        params[2]
        for statement in rp.mutations_for(merge, org_id="o", graph_id="g")
        for params in [statement.params]
        if statement.sql.startswith("DELETE FROM public.point")
    }
    assert deleted == {"b", "c"}, deleted
    assert surviving == {"a"}, surviving
    assert surviving | deleted == {"a", "b", "c"}


# ════════════════════════════════════════════════════════════════════════════
# 2. IDEMPOTENCE — structural
# ════════════════════════════════════════════════════════════════════════════


def _everything_records() -> list[dict[str, Any]]:
    """One record per declared type — every type the registry declares, since
    #9892 removed the ONE that had no projecting leg (`PointsMerged`) — plus the
    `delete_only` leg of a repoint. Five of the 30 carry `NoRecordEffect`
    (`EntityBindingRefused`,
    `ConfidenceChanged`, `EdgeMessageChanged`, `IngestStarted`,
    `CalibrationRecorded`): their `mutations_for` is empty, which
    `test_each_declared_type_materializes_or_declares_a_no_effect` is the leg
    for, and `replay_plan` adds only their `journal_event` position row —
    pinned for `IngestStarted` by
    `test_the_journal_position_is_recorded_do_nothing`.

    TWO records are added to the registry's minimal payloads, for two different
    reasons. The `delete_only` leg (#2489) emits a statement otherwise never
    emitted at all — a relation `DELETE`, and it is the only shape the
    `delete_only` flag adds; `DirectEdgeRepoint` is also the one type carrying
    two records. `EntityMutated` is otherwise absent from the record set
    entirely: its `UPDATE` text is already emitted by `PointRetracted`, so what
    it adds is the DOOR, not the shape, and a door is what a producer regression
    arrives through.

    A statement shape the record set cannot reach is a shape no per-statement
    leg can hold, which is what made the tenancy legs' "the WHOLE plan" claim
    wider than their fixture. It is narrower than "every shape the builders can
    emit": an `EntityLinked` whose endpoint is a `Source` (the
    `(id = %s OR node_key = %s)` resolve) is still unrepresented here — checked
    by hand against `_tenant_bindings` and mapped correctly, but not pinned.
    """
    records: list[dict[str, Any]] = [
        {"type": t, "ts": "2026-10-12T00:00:00Z", **payload}
        for t, payload in _MINIMAL_RECORDS.items()
    ]
    records.append({"type": "EntityMutated", "ts": "T", "id": "p1",
                    "label": "Point", "op": "restatus",
                    "state": {"status": "live"}})
    # #2489/#7969: the pass-2b delete of a resurrected phantom edge. The same
    # record type WITHOUT the flag is the upsert above, so this is the only way
    # `_relation_delete_statement` is reached from a record.
    records.append({"type": "DirectEdgeRepoint", "ts": "T",
                    "id": "p1->p2:IMPL", "src": "p1", "tgt": "p2",
                    "edge_type": "IMPL", "delete_only": True})
    return records


def _plan_for_everything() -> list[rp.Statement]:
    return rp.replay_plan(_everything_records(), org_id="o", graph_id="g")


def test_replay_plan_is_deterministic():
    """The same journal twice ⇒ the same statements, byte for byte."""
    first = [(s.sql, s.params) for s in _plan_for_everything()]
    second = [(s.sql, s.params) for s in _plan_for_everything()]
    assert first == second


def test_every_node_write_is_an_upsert_and_every_patch_is_absolute():
    """Idempotence by construction — no record type appends or increments.

    An `INSERT` without an `ON CONFLICT` clause is the double-apply defect
    (S1's *"a non-idempotent projection ⇒ a second replay double-applies"*),
    so the assertion is over the WHOLE plan, not a sample.
    """
    for statement in _plan_for_everything():
        sql = statement.sql
        if sql.startswith("INSERT "):
            assert "ON CONFLICT" in sql, f"non-idempotent insert: {sql}"
        elif sql.startswith("UPDATE "):
            assert "= %s" in sql and "||" not in sql.replace("props ||", ""), (
                f"a patch must write absolute values, not accumulate: {sql}"
            )
        elif sql.startswith("DELETE "):
            pass
        else:  # pragma: no cover - the vocabulary has four shapes, no fifth
            raise AssertionError(f"unclassified statement shape: {sql}")


def test_the_journal_position_is_recorded_do_nothing():
    plan = rp.replay_plan([{"type": "IngestStarted", "ts": "T", "run_id": "r1"}],
                          org_id="o", graph_id="g")
    assert len(plan) == 1
    assert plan[0].sql.startswith("INSERT INTO public.journal_event")
    assert "ON CONFLICT (org_id, graph_id, seq) DO NOTHING" in plan[0].sql
    assert plan[0].params[2] == 1  # seq


def test_identity_row_precedes_its_node_row():
    """`node_identity` is the FK parent — a node row without it is refused."""
    plan = rp.replay_plan(
        [{"type": "PointAdded", "ts": "T", "id": "p1", "point": dict(_SNAPSHOT)}],
        org_id="o", graph_id="g",
    )
    kinds = ["identity" if "public.node_identity" in s.sql else
             "point" if "public.point" in s.sql else "journal" for s in plan]
    assert kinds == ["identity", "point", "journal"]


# ════════════════════════════════════════════════════════════════════════════
# 2b. The capture's ENVELOPE edge — `contains_session` → a `relation` row
#     (#9553 R5 class 2)
# ════════════════════════════════════════════════════════════════════════════
#
# The turn loop's `PointAdded` carries the container id on the ENVELOPE (never
# in the point payload — a caller-supplied property of that name would forge a
# link on replay). `_RECORD_ONLY_KEYS` dropped it, correctly, and dropped the
# EDGE with it, so a record-sourced rebuild restored the turn Points with no
# `(:Session)-[:CONTAINS]->(:Point)` above them while a journal rebuild restored
# the link. These legs pin the three things that make the fix correct and none
# of which a row count can see: WHICH records may carry it, the ORDER, and that
# the cache projection can materialise the row it produces.

_TURN_RECORD = {
    "type": "PointAdded",
    "ts": "2026-10-12T00:00:00Z",
    "point": dict(_SNAPSHOT, pointKind="event", is_episodic=True),
    "contains_session": "sess1",
}


def _relation_statements(record: dict[str, Any]) -> list[rp.Statement]:
    return [s for s in rp.mutations_for(record, org_id="org-a", graph_id="g1")
            if s.sql.startswith("INSERT INTO public.relation")]


def test_a_capture_turn_lands_its_CONTAINS_relation_after_the_point_identity():
    """The edge lands, and it lands LAST — its endpoints are FK children.

    Ordering is the whole fix: `relation.src_key`/`tgt_key` are composite FKs
    into `node_identity`, so a relation insert that ran before the Point's own
    identity row would raise an FK violation — which lands on
    `record_write_failures`, invisible without #9551.
    """
    plan = rp.replay_plan([_TURN_RECORD], org_id="org-a", graph_id="g1")
    kinds = []
    for statement in plan:
        sql = statement.sql
        # `relation` FIRST: its endpoint subqueries read `public.point`, so a
        # table-name classifier that tested `point` before it would file the
        # edge under the node and pass whatever the order was.
        kinds.append("identity" if "public.node_identity" in sql else
                     "relation" if "public.relation" in sql else
                     "point" if "public.point" in sql else "journal")
    assert kinds == ["identity", "point", "relation", "journal"], kinds

    relation = plan[2]
    # (org, graph, rel_type, src_label, tgt_label, idx) precede the `props`
    # value and the two correlated endpoint subqueries, which resolve each key
    # from the record's OWN node row rather than trusting the journal id.
    assert relation.params[:6] == ("org-a", "g1", "CONTAINS", "Session",
                                   "Point", 0), relation.params
    assert "FROM (SELECT node_key FROM public.session" in relation.sql
    assert "(SELECT node_key FROM public.point" in relation.sql
    assert relation.params[7:10] == ("org-a", "g1", "sess1"), relation.params
    assert relation.params[10:] == ("org-a", "g1", _SNAPSHOT["id"]), relation.params
    # Idempotent: a replayed turn converges on the one row.
    assert "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, " in relation.sql


def test_an_operator_turn_lands_the_same_CONTAINS_relation():
    """`OperatorAdded` is the fold's OTHER `contains_session` consumer."""
    operator = {**_TURN_RECORD, "type": "OperatorAdded",
                "point": dict(_SNAPSHOT, is_operator=True, op_type="IMPL")}
    relations = _relation_statements(operator)
    assert len(relations) == 1, [s.sql for s in relations]
    assert relations[0].params[2:5] == ("CONTAINS", "Session", "Point")


def test_a_contains_session_the_fold_ignores_lands_no_relation():
    """Only `PointAdded`/`OperatorAdded` may carry the directive.

    The graph fold reads `contains_session` for exactly those two types
    (`projection/__init__.py::apply`). A label-keyed rule would let a stray key
    on ANY Point record mint a `relation` row the graph never wrote — the record
    richer than the graph, the one direction Interface A forbids.
    """
    base = {k: v for k, v in _TURN_RECORD.items() if k != "contains_session"}
    for record_type in ("PointPromoted", "OperatorPromoted", "DedupeRecorded",
                        "DedupeRejected", "EntityBindingRefused"):
        record = {**base, "type": record_type, "contains_session": "sess1"}
        assert _relation_statements(record) == [], record_type


def test_an_absent_or_unwritable_contains_session_lands_no_relation():
    """A non-string / EMPTY / BLANK / unstorable id is the fold's own skip.

    The set is `projection._writable_container_id`'s — the gate `_link_session`
    applies — not an invention at this layer. The BLANK cases look like a
    divergence from the fold's truthiness test and are not: an indexed graph
    cannot match a whitespace-only key, so the fold writes no edge for one
    either (see `_writable_endpoint_id`; the parity pin is
    `test_the_envelope_endpoint_gate_is_the_folds_own_rule` below).
    """
    for value in (None, "", "   ", "\t\n", 7, ["sess1"], "a\x00b", "\ud800"):
        record = {**_TURN_RECORD, "contains_session": value}
        assert _relation_statements(record) == [], value
    assert len(_relation_statements(_TURN_RECORD)) == 1


def test_a_session_whose_id_equals_the_point_lands_its_relation():
    """A same-id pair IS an edge: the fold's two MATCHes bind two nodes.

    `_link_session` matches `(s:Session {id:$sid})` and `(t:Point {id:$tid})`
    as separate bound variables, so with `sid == tid` the differing labels
    still bind two distinct nodes and the MERGE writes the edge — executed
    proof, not inference: `tests/test_rebuild_turn_points_3947.py::
    test_the_fold_writes_contains_when_the_session_id_equals_the_point_id`.
    `relation` holds it too: `node_identity` (and both composite FKs) is keyed
    on `(label, key)`, so `(Session, x)` and `(Point, x)` are two identities.
    An earlier draft refused this pair on the false premise that one node
    cannot be both ends; the producer cannot emit it today
    (`_capture_turn_id` suffixes the session id), which is exactly why the
    record must follow the fold rather than encode today's producer.
    """
    same = _SNAPSHOT["id"]
    relation = _relation_statements({**_TURN_RECORD, "contains_session": same})
    assert len(relation) == 1, [s.sql for s in relation]
    # src and tgt address the same KEY under two labels.
    assert relation[0].params[3] == "Session"
    assert relation[0].params[4] == "Point"
    assert relation[0].params[9] == same, relation[0].params
    assert relation[0].params[12] == same, relation[0].params


def test_the_envelope_endpoint_gate_is_the_folds_own_rule():
    """Non-vacuity for the legs above: the two predicates agree, value by value.

    The record restates the fold's endpoint gate rather than importing it (this
    module is loaded STANDALONE by the PGlite harness, where a package-relative
    import raises), so the two spellings must be pinned EQUAL — a drift in
    either one is a red, not a quiet divergence between a journal rebuild and
    a record rebuild.
    """
    from tortoise.projection import _writable_container_id

    values = (None, "", "   ", "\t\n", " \u00a0 ", 7, 0, ["sess1"], {"a": 1},
              b"x", True, "sess1", "p1", "  padded  ", "a\x00b", "\ud800")
    for value in values:
        fold_writes = _writable_container_id(value)
        assert rp._writable_endpoint_id(value) is fold_writes, (
            f"the record's endpoint gate disagrees with the fold's for "
            f"{value!r}: record={rp._writable_endpoint_id(value)} "
            f"fold={fold_writes} (the journal and the record would rebuild to "
            f"two different graphs)"
        )
    # Non-vacuity: the table must exercise BOTH answers, or "equal" is trivial.
    assert any(_writable_container_id(v) for v in values)
    assert not all(_writable_container_id(v) for v in values)


def test_the_contains_session_key_never_becomes_a_point_property():
    """It is an ENVELOPE directive — a node prop would let a caller forge it.

    The GRAPH fold reads it off the raw envelope, before `_norm` can splice the
    payload over it, precisely so a Point property cannot shadow (and forge) the
    link; the record must not store what the graph refuses to hold.
    """
    plan = rp.replay_plan([_TURN_RECORD], org_id="org-a", graph_id="g1")
    point = [s for s in plan if s.sql.startswith("INSERT INTO public.point")]
    assert len(point) == 1
    props = point[0].params[-1]
    assert "contains_session" not in props, props
    assert props == "{}", props


def test_a_record_sourced_rebuild_materialises_the_session_container():
    """S3: the row this lands is one the cache projection can re-create.

    A `relation` row is worthless if the record→FalkorDB leg refuses its
    predicate or cannot resolve its endpoints: `cache_projection`
    allowlist-validates the type and MATCHes both nodes by `(label, key)`. This
    is parity the journal rebuild already had (`_link_session` MERGEs the same
    edge) and the record path did not.
    """
    from tortoise import cache_projection as cp

    statement = _relation_statements(_TURN_RECORD)[0]
    rel_type, src_label, tgt_label = statement.params[2:5]
    rel = cp.relation_from_row({
        "rel_type": rel_type,
        # Params: 0-6 are the row's own values; 7-9 / 10-12 are the two
        # `(org, graph, key)` triples the endpoint subqueries bind, in that
        # order.
        "src_label": src_label, "src_key": statement.params[9],
        "tgt_label": tgt_label, "tgt_key": statement.params[12],
        "idx": 0, "props": {},
    })
    built = cp.relation_statement(rel)
    assert built.cypher == (
        "MATCH (src:Session {id:$src_key}), (tgt:Point {id:$tgt_key}) "
        "MERGE (src)-[r:CONTAINS {idx:$idx}]->(tgt) SET r += $props"
    ), built.cypher
    assert built.params["src_key"] == "sess1"
    assert built.params["tgt_key"] == _SNAPSHOT["id"]


# ════════════════════════════════════════════════════════════════════════════
# 2c. The POINT-PROPERTY edge — `extractedFrom` → a `relation` row
#     (#9553 R5 class 1)
# ════════════════════════════════════════════════════════════════════════════
#
# `_upsert_point_edges` derives this family from the Point's OWN payload
# (`_link_source`), so a journal rebuild restores
# `(:Point)-[:extractedFrom]->(:Source)` while a record-sourced rebuild restored
# nothing: the point row carried `extracted_from`, and `relation` stayed empty of
# the provenance edge. These legs pin the four things a row count cannot see —
# WHICH records may carry it, the ORDER, the FAN-OUT the fold applies, and the
# endpoint/family parity that keeps the two rebuilds from disagreeing.

_EXTRACTED_RECORD = {
    "type": "PointAdded",
    "ts": "2026-10-12T00:00:00Z",
    "id": "p1",
    "point": dict(_SNAPSHOT, extractedFrom="src1"),
}


#: The realised identity defect of #9956, in the shape the issue states it:
#: a registered source and a document whose id is a VARIANT of it.
_VARIANT_SOURCE = {"type": "SourceCreated", "ts": "2026-10-25T00:00:00Z",
                   "url": "https://example.com/a/b"}
_VARIANT_DOCUMENT = {"type": "DocumentCreated", "ts": "2026-10-25T00:00:01Z",
                     "id": "https://Example.COM/a/b/?utm_source=x#frag",
                     "title": "report"}
_VARIANT_CANONICAL = "https://example.com/a/b"


def _source_inserts(plan: list) -> list:
    return [s for s in plan if "INSERT INTO public.source" in s.sql]


def _node_key_slot(statement) -> str:
    """The SQL standing in the `node_key` value slot of a `source` INSERT.

    Walked rather than regexed because the slot may be a literal `%s` OR a
    scalar subquery with commas and its own placeholders (#9956): one comma-split
    would cut the fragment in half.
    """
    values = statement.sql.split(") VALUES (", 1)[1]
    depth, items, current = 0, [], ""
    for char in values:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        current += char
    items.append(current.strip())
    return items[3]


def test_a_url_variant_document_folds_onto_the_registered_source_row():
    """#9956 — the record half of #5140 finding 12: ONE identity, ONE row.

    THE MEASUREMENT. The graph folds a URL-variant document identity onto ONE
    `:Source` (`_upsert_document` → `resolve_source_key`, #9952); this layer is
    journal-derived and reads no graph, so it keyed `public.source` on the
    record's RAW id. Measured on a real Postgres 16 (migrations applied, the plan
    executed statement by statement): `SourceCreated('https://example.com/a/b')`
    + `DocumentCreated` on `'https://Example.COM/a/b/?utm_source=x#frag'` wrote
    **2 rows / 1 graph node**, in BOTH orders — and a record-sourced rebuild
    therefore resurrected the duplicate source row silently.

    THE DISCRIMINATOR, at the plan seam where the row count is decided: the two
    records plan two `INSERT`s onto the SAME `(org_id, graph_id, node_key)` —
    `ON CONFLICT` makes that ONE row — versus two distinct literal keys before
    the fix. Before #9956 both slots read `%s` and carried the two raw
    spellings, so both `sql` and the bound key VALUE differed (two rows). Now
    both slots are the SAME resolution subquery, and what it binds is the
    identity rather than the spelling: the two statements agree on the
    canonical form and differ only on the raw spelling they offer it.
    """
    plan = rp.replay_plan([_VARIANT_SOURCE, _VARIANT_DOCUMENT],
                          org_id="org-a", graph_id="g1")
    inserts = _source_inserts(plan)
    assert len(inserts) == 2, [s.sql for s in inserts]

    first, second = _node_key_slot(inserts[0]), _node_key_slot(inserts[1])

    # Both rows are keyed by the RESOLVER, not by the record's spelling — this
    # is what fails on `main`: there both slots were the literal `%s` and the
    # second bound the variant, so the plan addressed TWO rows.
    assert first != "%s", (
        "the source row is keyed by the record's RAW spelling again — a URL "
        f"variant plans a second row for one graph node (#9956): {first}"
    )
    # Non-vacuous form: the fragment TEXT is identical by construction (every
    # differing datum lives in `params`), so the discriminating assertions are
    # on what each statement BINDS. Both offer the SAME canonical identity, and
    # only the document offers the variant spelling to it — which is exactly
    # "one row, resolved".
    assert first == second
    assert first == rp._source_key_resolution("org-a", "g1", _VARIANT_SOURCE["url"]).sql
    # The shared identity is bound by BOTH (a regression that fed the resolver
    # the raw spelling instead of the canonical form would drop it here).
    assert inserts[0].params.count(_VARIANT_CANONICAL) >= 2, inserts[0].params
    assert inserts[1].params.count(_VARIANT_CANONICAL) >= 2, inserts[1].params
    # The VARIANT is what the document OFFERS the resolver, and it is a bound
    # VALUE — never the key itself: on `main` it WAS the key (`%s`, asserted
    # above), so these two lines are the pair that moved.
    assert _VARIANT_DOCUMENT["id"] in inserts[1].params, inserts[1].params
    assert _VARIANT_DOCUMENT["id"] not in inserts[0].params, inserts[0].params

    # The `url` COLUMN carries the same resolution, because
    # `chk_source_node_key_is_contract_key` pins `node_key = url`: a resolved
    # key beside a raw `url` is refused by the schema, AFTER the identity insert
    # has already run inside the caller's transaction. Two occurrences in the
    # document INSERT (the `node_key` slot and the `url` column) is that pin.
    assert inserts[1].sql.count(rp._source_key_resolution(
        "org-a", "g1", _VARIANT_DOCUMENT["id"]).sql) == 2, inserts[1].sql


def test_a_source_created_url_variant_folds_too_not_just_a_document():
    """The rule is the layer's, not `DocumentCreated`'s (#9956).

    The graph resolves on EVERY source writer (`_upsert_source`,
    `_mint_source_stub`, `link_source_to_entity`, `_upsert_document`), so a
    record that resolved only the document arm would drift on the others.
    """
    plan = rp.replay_plan([_VARIANT_SOURCE,
                           {"type": "SourceCreated", "ts": "2026-10-25T00:00:02Z",
                            "url": "https://Example.COM/a/b/?utm_source=x#frag"}],
                          org_id="org-a", graph_id="g1")
    inserts = _source_inserts(plan)
    assert len(inserts) == 2
    assert _node_key_slot(inserts[0]) == _node_key_slot(inserts[1])
    assert _node_key_slot(inserts[1]) != "%s"


def test_the_source_row_key_reuses_the_layers_ONE_source_resolver():
    """The node key IS the edge endpoint resolver — not a second copy of it.

    Two copies of a normalisation rule is how these two layers drifted apart in
    the first place (#9953 class), so the record's source-key resolution lives in
    ONE place: `_endpoint_resolution`, which the `extractedFrom` edge endpoint
    already uses. This pins the node key to a scalar-subquery USE of that same
    fragment, with the raw key as the not-registered fallback, so a future
    rewrite of the precedence cannot move one and not the other.
    """
    raw = _VARIANT_DOCUMENT["id"]
    canonical = rp._normalize_source_url(raw)
    expr = rp._source_key_resolution("org-a", "g1", raw)
    endpoint, params = rp._endpoint_resolution(
        "org-a", "g1", "Source", raw,
        match=("node_key", "node_key", "canonical_url"),
        values=(raw, canonical, canonical),
        order_by="node_key",
        prefer=("canonical_url", canonical),
    )
    assert expr.sql == f"COALESCE({endpoint}, %s)"
    assert expr.params == (*params, raw)
    # ... and the precedence inside it is `resolve_source_key`'s own: the
    # CANONICAL match first, the LOWEST key on ties, then adopt-on-touch by
    # EITHER spelling (the graph's step 1/3 before step 2).
    assert "(node_key = %s OR node_key = %s OR canonical_url = %s)" in endpoint
    # `COLLATE "C"` is the BYTE-order tiebreak (#9967 review P1): a bare
    # `ORDER BY node_key` sorts by the DATABASE's collation, which is neither
    # the graph's byte order nor stable across Postgres builds (glibc vs musl
    # disagree on a host-case variant, measured). Drop the clause and this
    # assertion is the only thing that notices.
    assert ('ORDER BY CASE WHEN canonical_url = %s THEN 0 ELSE 1 END, '
            'node_key COLLATE "C" LIMIT 1') in endpoint, endpoint
    assert canonical == _VARIANT_CANONICAL


def test_the_row_key_and_the_edge_endpoint_bind_the_same_adoption_tiers():
    """One identity cannot resolve to two rows INSIDE this layer (#9956).

    `resolve_source_key` adopts a pre-canonical node by the inbound raw spelling
    OR by the canonical spelling itself (`source_identity.py`: "for candidate in
    ([url, canonical] …)", because "a node registered canonically before this
    feature has the canonical string as its url"). A record whose ROW KEY
    adopted both while its EDGE endpoint adopted one would file the node under a
    row the edge then failed to find — the silent provenance loss #9553 exists
    to prevent, reintroduced from the other side.

    So both call sites must bind the SAME triple. Measured on trunk before this
    tier existed: a `public.source` row filed under the CANONICAL spelling with
    `canonical_url IS NULL` (every row written before the 2026-10-23 derive,
    #9553) was invisible to a variant, and the variant planned a second row.
    """
    raw = _VARIANT_DOCUMENT["id"]
    canonical = _VARIANT_CANONICAL
    # The edge side, through the statement builder the fold's `extractedFrom`
    # leg actually calls.
    statement = rp._relation_upsert_statement(
        "org-a", "g1", rp.RelationUpsert(),
        {"edge_type": "extractedFrom", "src": "p1", "tgt": raw,
         "source_label": "Point", "target_label": "Source"},
        columns=rp.table_columns(), refused=rp.refused_props_names(),
        tgt_match=("node_key", "node_key", "canonical_url"),
        tgt_values=(raw, canonical, canonical),
        tgt_order_by="node_key",
        tgt_prefer=("canonical_url", canonical),
    )
    edge_sql = statement[0].sql
    row_sql = rp._source_key_resolution("org-a", "g1", raw).sql
    # The legacy tier — a row filed under the canonical spelling with no
    # canonical form yet — is bound by BOTH, or neither is sufficient.
    for sql in (edge_sql, row_sql):
        assert "(node_key = %s OR node_key = %s OR canonical_url = %s)" in sql, sql
        assert ('ORDER BY CASE WHEN canonical_url = %s THEN 0 ELSE 1 END, '
                'node_key COLLATE "C" LIMIT 1') in sql, sql


def test_a_non_network_source_key_resolves_onto_itself():
    """`session:` / `corpus://` / `doc_` identities are already canonical.

    `normalize_source_url` returns a non-network identity UNCHANGED (running it
    through URL normalisation would be a category error and could alias a
    session Source onto a corpus-indexed one), so these resolve onto themselves —
    a re-replay keys the same row it just wrote, and the index/CLI paths
    (`doc_<rel-path>`, the bare filename) are unaffected by #9956.
    """
    for url in ("session:abc-123", "corpus://x/strategy.md", "doc_report.md"):
        expr = rp._source_key_resolution("org-a", "g1", url)
        assert rp._normalize_source_url(url) == url
        assert url in expr.params
        assert expr.sql.startswith("COALESCE((SELECT node_key FROM public.source")


def test_the_design_records_state_the_three_arms_the_resolver_binds():
    """#9967 review (cycle 4, P2): the design RECORDS must not fall back to TWO.

    The parity passages in `04-projection.md` and `08-hosted-prerequisites.md`
    are what a future lane re-derives `extractedFrom` parity from. Both said
    "matched on `url` **or** `canonical_url`" — two arms — after the code had
    moved to THREE (`node_key = <raw>`, `node_key = <canonical spelling>`,
    `canonical_url = <canonical>`), and an implementation faithful to that
    sentence reinstates the invisible-pre-`#9553`-row defect the third arm
    exists to close. The tiebreak is pinned too: the graph's `ORDER BY s.url`
    is BYTE order, so a doc that drops the `COLLATE "C"` clause would let a
    locale-collated rewrite look parity-preserving.

    This is the docs half of `test_the_source_row_key_reuses_the_layers_ONE_
    source_resolver` (which pins the emitted SQL to the same three arms): the
    two cannot drift apart without one of them going red.
    """
    passages = {
        "04-projection.md": (
            REPO_ROOT / "docs" / "epics" / "2026-10-10-7879-postgres-record"
            / "04-projection.md"),
        "08-hosted-prerequisites.md": (
            REPO_ROOT / "docs" / "epics" / "2026-10-10-7879-postgres-record"
            / "08-hosted-prerequisites.md"),
    }
    for name, path in passages.items():
        text = path.read_text(encoding="utf-8")
        anchor = text.find("canonical identity")
        assert anchor != -1, (
            f"{name} no longer states the `extractedFrom` parity rule at all — "
            "the passage this pin reads is gone, so re-point it, do not delete it"
        )
        # The passage runs from the identity claim to the tiebreak's end; the
        # window is generous on purpose, so a reflow does not red it while a
        # rewrite that drops a candidate does.
        passage = text[anchor:anchor + 1400]
        assert "canonical spelling" in passage, (
            f"{name} describes the match arms without the CANONICAL SPELLING "
            "candidate — the two-arm rule that leaves every pre-#9553 row filed "
            f"under the canonical spelling invisible (#9956): {passage[:400]!r}"
        )
        assert "canonical_url" in passage, (
            f"{name} no longer names the `canonical_url` arm: {passage[:400]!r}"
        )
        assert 'COLLATE "C"' in passage, (
            f"{name} names the tiebreak without `COLLATE \"C\"` — a bare "
            "`ORDER BY` is the DATABASE's locale, not the graph's byte order "
            f"(#9967): {passage[:400]!r}"
        )
        assert "byte" in passage.lower(), (
            f"{name} must say the tiebreak is BYTE order: {passage[:400]!r}"
        )



def test_a_document_source_row_derives_its_canonical_identity_too():
    """DELIBERATE, PINNED derive (#9553 review P2). Pin it, do not infer it.

    The record's `canonical_url` is a pure function of the row's own key, and it
    is derived for EVERY `source` row — including a `DocumentCreated` one.

    #5140 finding 12 UPDATE: the live `_upsert_document` fold now writes
    `canonicalUrl` as well (it adopts `resolve_source_key`), so this is no longer
    a live-vs-record PROPERTY divergence. The ROW-LEVEL residual that note also
    named — "this layer keys the row on the record's RAW id, so a URL-variant
    `DocumentCreated` plans a second `public.source` row for the identity the
    graph folds onto ONE node" — is **closed by #9956**, below: the row KEY is a
    resolution, so the variant FOLDS onto the registered row.

    The derive is NOT narrowed to the record types OTHER than `DocumentCreated`
    — the tempting reading now that the doc fold writes `canonicalUrl` itself —
    because the records resolver can only match a variant `extractedFrom` ref
    against the row's own `canonical_url` / `node_key` (`_relation_upsert_statement`'s
    `tgt_match=("node_key", "node_key", "canonical_url")`) and a document row without it
    would silently drop that edge — exactly the silent provenance loss the
    resolver exists to prevent. `resolve_source_key`'s adopt-on-touch backfills
    the same value on the first touch, so the two states converge on use.
    Asserted here so a later "tidy-up" that narrows the derive has to delete a
    named decision.
    """
    document = {
        "type": "DocumentCreated", "ts": "2026-10-12T00:00:00Z",
        "id": "https://example.com/a/?utm_source=x",
        "url": "https://example.com/a/?utm_source=x",
    }
    plan = rp.replay_plan([document], org_id="org-a", graph_id="g1")
    source = next(s for s in plan if "INSERT INTO public.source" in s.sql)
    assert "canonical_url" in source.sql, source.sql
    assert rp._normalize_source_url(document["url"]) in source.params, source.params
    # ... and the divergence converges: the value is exactly the canonical form
    # `resolve_source_key` would backfill, so a rebuild and a first live touch
    # agree on the node's identity.
    assert "https://example.com/a" in source.params, source.params


def test_a_point_lands_its_extractedFrom_relation_after_the_point_identity():
    """The provenance edge lands, and it lands LAST — endpoints are FK children."""
    plan = rp.replay_plan([_EXTRACTED_RECORD], org_id="org-a", graph_id="g1")
    kinds = []
    for statement in plan:
        sql = statement.sql
        kinds.append("identity" if "public.node_identity" in sql else
                     "relation" if "public.relation" in sql else
                     "point" if "public.point" in sql else "journal")
    assert kinds == ["identity", "point", "relation", "journal"], kinds

    relation = plan[2]
    assert relation.params[:6] == ("org-a", "g1", "extractedFrom", "Point",
                                   "Source", 0), relation.params
    # The TARGET resolves the way `_mint_source_stub` does, and NOT on the `id`
    # fallback `_ENTITY_MATCH_COLUMNS` gives `EntityLinked`: the fold calls
    # `resolve_source_key` (canonicalise, then any Source registered under that
    # canonical form) before it MERGEs `(s:Source {url:$url})`. So the record
    # matches the raw ref, the canonical SPELLING (the resolver's adopt-on-touch
    # by `[url, canonical]`), OR the canonical FORM, PREFERRING the canonical
    # match and ordered by `node_key` within each tier — `resolve_source_key`'s
    # own order (canonicalUrl first, return there; raw spelling afterwards) and
    # its own tiebreak (`ORDER BY s.url LIMIT 1`). An id match could attach the
    # row to a node the fold never chose. The SQL proves which predicate was
    # emitted.
    assert "public.source WHERE org_id = %s AND graph_id = %s AND " \
        "(node_key = %s OR node_key = %s OR canonical_url = %s) " \
        "ORDER BY CASE WHEN " \
        "canonical_url = %s THEN 0 ELSE 1 END, node_key COLLATE \"C\" LIMIT 1" \
        in relation.sql, relation.sql
    # params: the row's own values, then the two endpoint subquery triples/
    # quadruples the caller joins on — src, then tgt (raw ref, canonical
    # spelling, canonical form, canonical form again for the precedence clause).
    assert relation.params[-9:-6] == ("org-a", "g1", "p1"), relation.params
    assert relation.params[-6:] == ("org-a", "g1", "src1", "src1", "src1",
                                    "src1"), relation.params
    # Idempotent: a replayed capture converges on the one row.
    assert "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, " \
        in relation.sql


def test_a_url_variant_ref_resolves_to_the_recorded_canonical_source():
    """#9553 review P1: the fold resolves a VARIANT ref to the registered Source.

    `_mint_source_stub` calls `resolve_source_key` BEFORE its MERGE, so
    `create_source("https://example.com/a")` + `create_point(extractedFrom=
    "https://example.com/a/?utm_source=x")` lands the edge on the REGISTERED
    node, whose url is the canonical spelling. A record matching the raw ref
    alone resolved onto no `source` row and emitted a relation whose target
    subselect was empty — zero rows, no error, while the journal rebuild
    restored the edge. The record must bind the canonical form too.
    """
    variant = "https://example.com/a/?utm_source=x"
    record = {**_EXTRACTED_RECORD, "point": dict(_SNAPSHOT, extractedFrom=variant)}
    relations = _relation_statements(record)
    assert len(relations) == 1, [s.sql for s in relations]
    # The raw ref is retained as the row's `tgt` intent, but the RESOLUTION
    # binds all three candidates (the canonical one three times: two predicate
    # spellings + precedence), so the subselect can find the canonical row and
    # prefers it.
    assert relations[0].params[-4:] == (variant, "https://example.com/a",
                                       "https://example.com/a",
                                       "https://example.com/a"), \
        relations[0].params
    # A non-network ref normalises to itself — no phantom second spelling.
    plain = _relation_statements(_EXTRACTED_RECORD)[0]
    assert plain.params[-4:] == ("src1", "src1", "src1", "src1"), plain.params


def test_the_source_canonicaliser_is_the_folds_own():
    """Parity pin for the RESTATED `normalize_source_url` (`source_identity.py`).

    The module is loaded STANDALONE by the PGlite harness, so the canonicaliser
    is restated rather than imported. A drift in either spelling would make the
    record resolve a variant to a DIFFERENT `:Source` than the journal lane, on
    the same journal line, with no error anywhere.
    """
    from tortoise.source_identity import normalize_source_url

    values = (
        "https://example.com/a",
        "https://example.com/a/",
        "HTTPS://Example.COM/a/b/?utm_source=x&b=2&a=1#frag",
        "https://example.com/a?fbclid=1",
        "https://example.com:443/a",
        "https://example.com:8443/a",
        "http://example.com:80/a",
        "https://example.com//a//b",
        "https://example.com",
        "https://[2001:db8::1]/a",
        "https://example.com/a?b=&c=1",
        "session:abc-123",
        "corpus://name/a?utm_source=x",
        "release-1.0",
        "",
        None,
        7,
        "https://[bad",
        "https://Example.com/a/../b",
    )
    for value in values:
        assert rp._normalize_source_url(value) == normalize_source_url(value), value
    # Non-vacuity: the table must exercise BOTH a rewrite and an identity.
    assert any(rp._normalize_source_url(v) != v for v in values)
    assert any(rp._normalize_source_url(v) == v for v in values if isinstance(v, str))


def test_an_operator_capture_carries_the_same_provenance_edge():
    """`OperatorAdded` is the fold's OTHER `_upsert_point_edges` caller."""
    operator = {**_EXTRACTED_RECORD, "type": "OperatorAdded",
                "point": dict(_SNAPSHOT, extractedFrom="src1",
                              is_operator=True, op_type="IMPL")}
    relations = _relation_statements(operator)
    assert len(relations) == 1, [s.sql for s in relations]
    assert relations[0].params[2:5] == ("extractedFrom", "Point", "Source")


def test_extractedFrom_fans_out_one_relation_per_ref():
    """Many-to-many (#3263): one edge per ref, not one edge per point."""
    record = {**_EXTRACTED_RECORD,
              "point": dict(_SNAPSHOT, extractedFrom=["src1", "src2", "src3"])}
    relations = _relation_statements(record)
    assert len(relations) == 3, [s.sql for s in relations]
    assert [r.params[-1] for r in relations] == ["src1", "src2", "src3"]
    # A bare STRING is one ref — never iterated character-wise.
    assert len(_relation_statements({**_EXTRACTED_RECORD, "point": dict(
        _SNAPSHOT, extractedFrom="src1")})) == 1


def test_a_ref_container_the_fold_refuses_lands_no_relation():
    """`_link_source` returns on a non-str/non-list container (#8171).

    A dict was `list()`-ed into its KEYS and each key minted as its own
    `:Source` — invented provenance. The record must not iterate it either: a
    row the graph refused to write is the record richer than the graph.
    """
    for value in ({"url": "u", "kind": "k"}, 7, 0, None, ""):
        record = {**_EXTRACTED_RECORD,
                  "point": dict(_SNAPSHOT, extractedFrom=value)}
        assert _relation_statements(record) == [], value


def test_a_ref_the_fold_skips_lands_no_relation():
    """The MEMBER gate: non-str / falsy / unwritable refs land nothing.

    `_link_source` skips a falsy or non-`str` member; `_mint_source_stub`
    refuses an id `_writable_id` rejects and then MATCHes `url=None`, landing no
    edge. The BLANK case is the measured one: `:Source(url)` carries a range
    index, so an indexed equality filter does not match a whitespace-only key
    (#9599) and the fold writes no edge for it either.
    """
    skipped = [7, None, "", "   ", "\t\n", ["a"], {"a": 1}, "a\x00b",
               "\ud800"]
    record = {**_EXTRACTED_RECORD,
              "point": dict(_SNAPSHOT, extractedFrom=["src1", *skipped])}
    relations = _relation_statements(record)
    assert len(relations) == 1, [(r.params[-1]) for r in relations]
    assert relations[0].params[-1] == "src1"


def test_only_the_point_MATERIALISATION_records_carry_point_property_relations():
    """The fold's point-materialisation types — FOUR, and `apply()` is richer.

    `apply()` re-derives this family from four arms — `PointAdded` /
    `OperatorAdded` (`self._upsert(p)`) and `PointPromoted` / `OperatorPromoted`
    (`self._upsert(gated_promote_payload(…))` /
    `self._upsert(_promotion_point_with_operator(…))`, neither wrapper dropping
    `extractedFrom`). `rebuild_all`'s pass 2 re-derives it for the two CREATION
    types only, so the two fold routes already disagree; the record follows
    `apply()`, the chronological engine `rebuild`/`recover_from_log` run, because
    otherwise a promote-only operator (#2256) has the edge in the graph and none
    in the record — the record POORER than the graph.

    Everything else lands nothing: a `PointRevised` never reaches
    `_upsert_point_edges` on any route, so a row there would be the record richer
    than the graph.
    """
    # Non-vacuity for the four: each one carries the SAME edge, once.
    for record_type in ("PointAdded", "OperatorAdded",
                        "PointPromoted", "OperatorPromoted"):
        record = {**_EXTRACTED_RECORD, "type": record_type}
        relations = _relation_statements(record)
        assert len(relations) == 1, (record_type, [s.sql for s in relations])
        assert relations[0].params[2:5] == ("extractedFrom", "Point", "Source")
    for record_type in ("PointRevised", "DedupeRecorded",
                        "DedupeRejected", "PointInvalidated", "BatchIdStamped"):
        record = {**_EXTRACTED_RECORD, "type": record_type}
        assert _relation_statements(record) == [], record_type


def test_the_declared_types_are_the_folds_point_materialisations():
    """Drift guard: the declaration tracks the fold's own type list.

    `_JOURNAL_POINT_MATERIALIZATION_TYPES` is the fold's ONE declaration of what
    materialises a `:Point` — the four types `apply()` folds through `_upsert`,
    and therefore the four whose payload reaches `_upsert_point_edges`. A fifth
    materialisation type added there without a row here would silently re-open
    the record-poorer-than-the-graph gap on that arm.
    """
    from tortoise.projection import _JOURNAL_POINT_MATERIALIZATION_TYPES

    declared = {type_of for type_of, _p, _r, _l in rp._POINT_PROPERTY_RELATIONS}
    assert declared == set(_JOURNAL_POINT_MATERIALIZATION_TYPES), (
        sorted(declared), sorted(_JOURNAL_POINT_MATERIALIZATION_TYPES))
    # Non-vacuity: the sets are non-empty and the reference is the fold's own.
    assert len(_JOURNAL_POINT_MATERIALIZATION_TYPES) == 4


def test_the_read_version_anchor_rides_the_primary_column():
    """#5256: `r.sourceVersion` is a PRIMARY relation column, not a props key.

    The fold stamps it from the Point's `sourceVersionTransit` carrier, keyed by
    the RAW ref. `chk_relation_props_no_derived` is untouched by it (the column
    is where the placement table puts it, `source_version` → `sourceVersion`), so
    the cache projection re-stamps the same edge scalar the journal lane wrote.
    """
    anchor = {**_EXTRACTED_RECORD, "point": dict(
        _SNAPSHOT, extractedFrom=["src1", "src2"],
        sourceVersionTransit=[["src1", "h1"]])}
    relations = _relation_statements(anchor)
    assert len(relations) == 2, [s.sql for s in relations]
    flagged = next(r for r in relations if r.params[-1] == "src1")
    unflagged = next(r for r in relations if r.params[-1] == "src2")
    assert "source_version" in flagged.sql, flagged.sql
    assert flagged.params[6] == "h1", flagged.params
    # A pair for a ref this Point does not carry never reaches the row.
    assert "source_version" not in unflagged.sql, unflagged.sql

    # ALL-OR-NOTHING: one malformed pair and NO ref gets an anchor — the edge
    # still lands (the carrier is not the edge), which is the fold's own split.
    broken = {**_EXTRACTED_RECORD, "point": dict(
        _SNAPSHOT, extractedFrom=["src1"],
        sourceVersionTransit=[["src1", "h1"], ["src2", None]])}
    relations = _relation_statements(broken)
    assert len(relations) == 1, [s.sql for s in relations]
    assert "source_version" not in relations[0].sql, relations[0].sql

    # No refs at all ⇒ the carrier is a transit for an edge that will never be
    # written, so it contributes nothing (the fold's `if not refs: return None`).
    orphan = {**_EXTRACTED_RECORD, "point": dict(
        _SNAPSHOT, sourceVersionTransit=[["src1", "h1"]])}
    assert _relation_statements(orphan) == []


def test_the_source_version_selector_is_the_folds_own():
    """Parity pin: the record restates `_point_source_transit`, so pin it equal.

    The module is loaded STANDALONE by the PGlite harness (a package-relative
    import raises there), which is why the selector is restated at all. A drift
    in either spelling makes the journal and the record rebuild to two different
    anchors with no error anywhere.
    """
    from tortoise.projection.entities import _point_source_transit

    points = (
        {"extractedFrom": "src1", "sourceVersionTransit": [["src1", "h1"]]},
        {"extractedFrom": ["src1", "src2"],
         "sourceVersionTransit": [["src1", "h1"], ["src2", "h2"]]},
        # Duplicate raw ref: the comprehension keeps the LAST pair.
        {"extractedFrom": "src1",
         "sourceVersionTransit": [["src1", "h1"], ["src1", "h2"]]},
        # A carrier naming ONLY a ref this Point does not carry ⇒ no anchor at
        # all (the fold's `kept or None` filter). Outcome-neutral for today's
        # consumer, which only looks up its own refs — pinned so the "equal"
        # claim below is literal rather than approximate.
        {"extractedFrom": "src1", "sourceVersionTransit": [["src2", "h2"]]},
        # Malformed pair: all-or-nothing ⇒ NO anchor.
        {"extractedFrom": "src1",
         "sourceVersionTransit": [["src1", "h1"], ["src2", None]]},
        {"extractedFrom": "src1", "sourceVersionTransit": [["src1", "  "]]},
        # A carrier with no refs contributes nothing.
        {"sourceVersionTransit": [["src1", "h1"]]},
        {"extractedFrom": [], "sourceVersionTransit": [["src1", "h1"]]},
        {"extractedFrom": "src1"},
        {"extractedFrom": "src1", "sourceVersionTransit": []},
        {"extractedFrom": "src1", "sourceVersionTransit": "h1"},
        {"extractedFrom": "src1", "sourceVersionTransit": [["src1"]]},
    )
    for point in points:
        fold = _point_source_transit(point)
        expected = {pair[0]: pair[1] for pair in fold} if fold else {}
        assert rp._point_source_versions(point) == expected, point
    # Non-vacuity: the table must exercise both answers.
    assert any(rp._point_source_versions(p) for p in points)
    assert not all(rp._point_source_versions(p) for p in points)


def test_a_record_sourced_rebuild_materialises_the_provenance_edge():
    """S3: the row lands an edge the cache projection can actually re-create."""
    from tortoise import cache_projection as cp

    statement = _relation_statements({**_EXTRACTED_RECORD, "point": dict(
        _SNAPSHOT, extractedFrom="src1", sourceVersionTransit=[["src1", "h1"]])})[0]
    rel_type, src_label, tgt_label = statement.params[2:5]
    # The tail is [src org, src graph, src key][tgt org, tgt graph, raw ref,
    # canonical spelling, canonical form, canonical form again for the
    # precedence clause] — the endpoint binds FOUR values plus its org/graph
    # (the predicate's two spellings AND the form, then the precedence clause;
    # see `_endpoint_resolution`), so the src key is seventh-from-last and the
    # target the row addresses is the canonical form third-from-last.
    rel = cp.relation_from_row({
        "rel_type": rel_type,
        "src_label": src_label, "src_key": statement.params[-7],
        "tgt_label": tgt_label, "tgt_key": statement.params[-3],
        "idx": 0, "source_version": statement.params[6], "props": {},
    })
    built = cp.relation_statement(rel)
    assert built.cypher == (
        "MATCH (src:Point {id:$src_key}), (tgt:Source {url:$tgt_key}) "
        "MERGE (src)-[r:extractedFrom {idx:$idx}]->(tgt) SET r += $props"
    ), built.cypher
    assert built.params["src_key"] == _SNAPSHOT["id"]
    assert built.params["tgt_key"] == "src1"
    # #5256's anchor survives the record → graph trip, as the same edge scalar
    # `_link_source` stamps (`ON CREATE SET r.sourceVersion`).
    assert rel.props["sourceVersion"] == "h1", rel.props


def test_the_read_version_anchor_is_written_ON_CREATE_never_advanced():
    """#5256 / #5199: a RE-LINK must not ADVANCE a recorded read version.

    The fold writes the anchor through `_anchor_on_create` (`ON CREATE SET
    r.sourceVersion`) — `_link_source`'s own rule. The record writes through ON
    CONFLICT, whose generic `DO UPDATE SET c = EXCLUDED.c` would let a LATER
    record for the same edge overwrite it: a re-emitted `PointAdded`
    (`create_point` appends its record before a DETACH DELETE and may append a
    second), a promote snapshot, or a second ref of one Point resolving onto the
    same Source. The two rebuild routes would then disagree about the same
    journal — exactly the split `test_the_source_version_selector_is_the_folds_own`
    exists to prevent, one layer down.

    So the anchor is written ON CREATE ONLY: the INSERT carries it, and the
    conflict path does not touch the column at all — literally the fold's `ON
    CREATE SET`, not an approximation of it. Asserted on the emitted SQL because
    that is where the rule lives; the DB-level half (a real replay with a changed
    carrier) runs in `supabase/tests/pglite/validate.mjs`.
    """
    anchored = _relation_statements({**_EXTRACTED_RECORD, "point": dict(
        _SNAPSHOT, extractedFrom="src1",
        sourceVersionTransit=[["src1", "h1"]])})[0]
    # The INSERT half still writes the anchor...
    assert "source_version" in anchored.sql.split("FROM", 1)[0], anchored.sql
    assert "h1" in anchored.params, anchored.params
    # ...and the CONFLICT half never rewrites it. A `DO UPDATE` on this column
    # (the pre-fix emission) let a later record ADVANCE the anchor.
    assert "ON CONFLICT" in anchored.sql, anchored.sql
    update_clause = anchored.sql.split("DO UPDATE SET", 1)[1]
    assert "source_version" not in update_clause, anchored.sql
    # Everything ELSE keeps latest-record-wins: `props` is edge state, not a
    # recorded read version.
    assert "props = EXCLUDED.props" in update_clause, anchored.sql


def test_dollar_placeholder_rewrite_is_faithful():
    for statement in _plan_for_everything():
        sql, params = rp.to_dollar_placeholders(statement)
        assert "%s" not in sql
        assert sql.count("$") == len(params)
        for index in range(1, len(params) + 1):
            assert f"${index}" in sql


def test_placeholder_count_mismatch_is_loud():
    with pytest.raises(rp.RecordProjectionError):
        rp.to_dollar_placeholders(rp.Statement("SELECT %s, %s", ("one",)))


# ════════════════════════════════════════════════════════════════════════════
# 3. "A FIELD IS PRIMARY IFF THE JOURNAL CARRIES IT"
# ════════════════════════════════════════════════════════════════════════════


def _placement_by_class() -> dict[str, dict[str, set[str]]]:
    out: dict[str, dict[str, set[str]]] = {}
    for section, prop, klass in parse_placement():
        bucket = out.setdefault(section, {"P": set(), "D": set(), "U": set()})
        bucket.setdefault(klass, set()).add(prop)
    return out


def _stored_names(statements: list[rp.Statement]) -> set[str]:
    """Property names the statements actually persist (columns or `props`)."""
    import json as _json

    stored: set[str] = set()
    for statement in statements:
        if "public.node_identity" in statement.sql or "public.journal_event" in statement.sql:
            continue
        for param in statement.params:
            if isinstance(param, str) and param.startswith("{") and param.endswith("}"):
                try:
                    stored |= set(_json.loads(param))
                except (ValueError, TypeError):  # pragma: no cover
                    continue
        match = re.search(r"public\.(\w+) \(([^)]*)\)", statement.sql)
        if match:
            columns = {c.strip() for c in match.group(2).split(",")}
            stored |= columns - {"org_id", "graph_id", "node_key", "node_label",
                                 "props", "rel_type", "src_label", "src_key",
                                 "tgt_label", "tgt_key", "idx"}
        match = re.search(r"UPDATE public\.(\w+) SET (.*?) WHERE", statement.sql)
        if match:
            stored |= {part.split("=")[0].strip() for part in match.group(2).split(",")}
    return {rp.snake(name) for name in stored}


def test_no_derived_or_volatile_property_is_ever_stored():
    """Capstone S5's record half: a derived value must not be stored as authority.

    Proven on the projection's own output, over every DERIVED/VOLATILE name in
    §17.5, carried by the journal on purpose. The schema's
    `chk_*_props_no_derived` CHECK is the backstop; this is the first line.
    """
    for section, classes in _placement_by_class().items():
        table = SECTION_TABLE.get(section)
        if table is None:
            continue  # the Edges section's grouped predicate rows
        derived = sorted(classes.get("D", set()))
        if not derived:
            continue
        payload = {name: "sentinel" for name in derived}
        record = _synthetic_record_for(table, payload)
        statements = rp.mutations_for(record, org_id="o", graph_id="g")
        stored = _stored_names(statements)
        leaked = sorted(stored & {rp.snake(n) for n in derived})
        assert not leaked, (
            f"{section}: the projection stored DERIVED/VOLATILE {leaked} as "
            "authority — those are recomputed from the structure and must have "
            "no column and no props key (§2.0/§17.5)"
        )


def test_every_primary_property_the_journal_carries_is_storable():
    """Interface A's failure mode: the record must not be poorer than the history."""
    for section, classes in _placement_by_class().items():
        table = SECTION_TABLE.get(section)
        if table is None:
            continue
        primary = sorted(classes.get("P", set()))
        payload = {name: "sentinel" for name in primary}
        record = _synthetic_record_for(table, payload)
        statements = rp.mutations_for(record, org_id="o", graph_id="g")
        stored = _stored_names(statements)
        dropped = sorted({rp.snake(n) for n in primary} - stored)
        assert not dropped, (
            f"{section}: the projection silently dropped PRIMARY {dropped} — a "
            "field the journal carries must be storable (column or the "
            "declared props tail)"
        )


#: §17.5's PRIMARY Point properties whose journal carrier is a PATCH record,
#: not the `PointAdded` full snapshot the generic leg above always builds.
#: The bi-temporal window and the terminal `outdated` flag ride these two
#: carriers, and the fold replays them verbatim (`SET n.outdated=true,
#: n.validTo=$vt, n.expiredAt=$ea`). Payloads are the real producer shapes
#: (`sdk.py::supersede_point` / `invalidate_point`).
_LIFECYCLE_CARRIERS: dict[str, tuple[dict[str, Any], set[str]]] = {
    "PointSuperseded": (
        {"id": "p1", "new_id": "p2", "valid_from": "vf",
         "valid_to": "vt", "expired_at": "ea"},
        {"valid_to", "expired_at", "outdated"},
    ),
    "PointInvalidated": (
        {"id": "p1", "corrected_by": "p2", "valid_to": "vt",
         "expired_at": "ea"},
        {"valid_to", "expired_at", "outdated"},
    ),
}


def test_the_lifecycle_patch_carriers_store_their_primary_columns():
    """Drives the PRIMARY-storable leg from the LIFECYCLE carriers, not the snapshot.

    The generic leg above builds a `PointAdded` for every Point property, so it
    never constructs `PointSuperseded`/`PointInvalidated`; a patch whose
    `from_payload` was empty silently dropped these PRIMARY columns while the
    fold wrote them — a vacuous green for exactly the §17.5 rows that name
    these carriers.
    """
    for record_type, (payload, required) in _LIFECYCLE_CARRIERS.items():
        stored = _stored_names(rp.mutations_for(
            {"type": record_type, "ts": "T", **payload},
            org_id="o", graph_id="g"))
        assert required <= stored, (
            f"{record_type} dropped §17.5 PRIMARY {sorted(required - stored)} — "
            "the fold replays them verbatim, so the record must store them"
        )


def test_point_superseded_does_not_rewrite_the_predecessors_valid_from():
    """`PointSuperseded.valid_from` is the SUCCESSOR's start, not the old point's.

    The producer emits `valid_from=succ_vf` (#4021) and the fold writes it as the
    PREDECESSOR's `validTo` (`valid_to`); neither the live `supersede_point` SET
    nor `_fold_point_superseded` touches the predecessor's `validFrom`. Storing
    it here would overwrite the old point's own window start with the
    successor's boundary.
    """
    stored = _stored_names(rp.mutations_for(
        {"type": "PointSuperseded", "ts": "T", "id": "p1", "new_id": "p2",
         "valid_from": "succ-vf", "valid_to": "succ-vf", "expired_at": "ea"},
        org_id="o", graph_id="g"))
    assert "valid_from" not in stored, (
        "PointSuperseded wrote the successor's window start onto the "
        "predecessor's validFrom — the fold does not"
    )
    assert {"valid_to", "expired_at", "outdated"} <= stored


def _synthetic_record_for(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A record of the type that materializes `table`, carrying `payload`."""
    key = {"point": "id", "object": "name", "subject": "name",
           "event": "eventId", "source": "url", "session": "id"}[table]
    snapshot = {**payload, key: "k1"}
    record_type = {
        "point": "PointAdded", "object": "ObjectRegistered",
        "subject": "SubjectAdded", "event": "EventRecorded",
        "source": "SourceCreated", "session": "SessionRecorded",
    }[table]
    record: dict[str, Any] = {"type": record_type, "ts": "T", "id": "k1",
                              key: "k1"}
    if record_type in ("PointAdded",):
        record["point"] = snapshot  # the snapshot carrier
    else:
        record.update(snapshot)  # the flat carrier
    return record


def test_the_schema_columns_are_read_from_disk_not_restated():
    """A hand-copied column list is the second declaration that goes stale."""
    columns = rp.table_columns()
    assert set(columns) == set(rp.THE_RECORD_TABLES)
    for table in rp.THE_RECORD_TABLES:
        assert "org_id" in columns[table], f"{table} lost its tenancy column"
    # The carve-out names come from the SQL too.
    refused = rp.refused_props_names()
    assert "confidence" in refused["point"]
    assert "ep_alpha" in refused["point"]
    assert "status" in refused["object"]  # projection-owned cache (§2.6)
    # #8263: the SAME refusal on `subject`, carried by the forward migration
    # 20261014000001. Read from disk means the `ALTER TABLE … ADD CONSTRAINT`
    # form is parsed too — a schema read that only understood `CREATE TABLE`
    # would silently miss it and let the projection emit a refused props key.
    assert "status" in refused["subject"]
    # `refused_props_names()` UNIONs a forward ALTER into the CREATE TABLE's
    # names, so `"embedding" in refused["subject"]` stays true even if the
    # ALTER declared only `['status']` — the union is fail-safe against an
    # unknown ADD, but it cannot catch a SHRINKING re-declaration. Assert the
    # ALTER's OWN array names both; the LIVE constraint is pinned by the pgTAP
    # suite (a `{"embedding":…}` subject INSERT must be refused), which the
    # union parse cannot see.
    subject_alters = [arr for table, arr in rp._ALTER_DERIVED_CHECK_RE.findall(
        rp._read_migrations()) if table == "subject"]
    assert subject_alters, (
        "no forward ALTER re-declares chk_subject_props_no_derived — the schema "
        "read would silently miss the refusal"
    )
    assert "status" in subject_alters[-1] and "embedding" in subject_alters[-1], (
        f"the forward ALTER must re-declare BOTH names, got {subject_alters[-1]!r}"
    )
    # #2782: the SAME forward-migration rule for COLUMNS. The five money columns
    # reach `public.point` by `ALTER TABLE … ADD COLUMN` (append-only history),
    # so a `CREATE TABLE`-only read reports the record poorer than it is: the
    # amount would be pushed into the `props` tail instead of its `bigint`
    # column, and `cache_projection` would not project it at all.
    for column in ("amount_minor", "currency", "minor_unit_exponent",
                   "verbatim", "as_of"):
        assert column in columns["point"], (
            f"point.{column} is missing from table_columns() — the forward "
            "ALTER TABLE … ADD COLUMN form is not read"
        )
    base_point = rp._parse_record_tables(rp._read_migrations())["point"]
    for column in ("amount_minor", "currency", "minor_unit_exponent",
                   "verbatim", "as_of"):
        assert column not in base_point, (
            f"point.{column} now comes from the CREATE TABLE, so the ALTER "
            "read above proves nothing"
        )


def test_a_point_amount_lands_in_its_money_column_not_the_props_tail():
    """#2782: the money value is AUTHORITY, so it gets a column, not the tail.

    `amountMinor`/`currency`/`minorUnitExponent`/`verbatim`/`asOf` are
    author-supplied and carried by `PointAdded.point.<field>` — nothing
    recomputes them — so §17.5's placement classes them PRIMARY and the record
    must store each in its own column. In the `props` tail the amount would be
    an unvalidated JSON scalar (the WS-C design's `bigint`-never-float rule
    enforced by the column type instead).
    """
    columns = rp.table_columns()
    refused = rp.refused_props_names()
    props = {
        "id": "p1",
        "amountMinor": 25_000_000,
        "currency": "BRL",
        "minorUnitExponent": 2,
        "verbatim": "R$ 250.000,00",
        "asOf": "2026-10-18",
    }
    col_values, tail = rp._split_properties(
        "point", props, columns=columns, refused=refused
    )
    assert set(col_values) == {
        "id", "amount_minor", "currency", "minor_unit_exponent",
        "verbatim", "as_of",
    }, col_values
    assert tail == {}, f"money left in the props tail: {tail}"


def test_every_record_schema_migration_on_disk_is_read():
    """The schema read's file list must not silently miss a forward migration.

    `_read_migrations` joins the HAND list `RECORD_MIGRATIONS` and skips a
    listed-but-absent file, so a migration that shapes the record schema but is
    not listed would be invisible: `refused_props_names()` would drop no name
    and the projection would emit a props tail the CHECK refuses — the drift the
    forward-ALTER mechanism (#8263) exists to close. Every migration that
    creates a record table or re-declares a `chk_*_props_no_derived` CHECK must
    be listed.
    """
    tables = "|".join(rp.THE_RECORD_TABLES)
    shapes = re.compile(
        rf"CREATE TABLE IF NOT EXISTS public\.(?:{tables})\b"
        rf"|ALTER TABLE public\.(?:{tables})\b[^;]*?chk_[a-z_]+_props_no_derived"
        # #2782: a forward migration can also ADD A COLUMN, and the column set
        # is what `_split_properties` splits on — an unread ADD COLUMN leaves
        # the value in the `props` tail and `cache_projection` unprojected.
        rf"|ALTER TABLE public\.(?:{tables})\b[^;]*?ADD COLUMN"
    )
    migdir = rp.REPO_ROOT / "supabase" / "migrations"
    on_disk = {
        p.name for p in sorted(migdir.glob("*.sql"))
        if shapes.search(p.read_text(encoding="utf-8"))
    }
    assert on_disk, "no record-schema migration found on disk"
    unread = sorted(on_disk - set(rp.RECORD_MIGRATIONS))
    assert not unread, (
        f"record-schema migration(s) shape the schema but are NOT read by "
        f"record_projection.RECORD_MIGRATIONS: {unread} — refused_props_names() "
        f"would silently miss their refusals"
    )


def test_a_record_with_no_usable_key_is_refused():
    with pytest.raises(rp.RecordProjectionError) as excinfo:
        rp.mutations_for({"type": "ObjectRegistered", "ts": "T"},
                         org_id="o", graph_id="g")
    assert "resolves to nothing, in silence" in str(excinfo.value)


def test_a_snapshot_carrying_record_without_its_snapshot_is_refused():
    with pytest.raises(rp.RecordProjectionError) as excinfo:
        rp.mutations_for({"type": "PointAdded", "ts": "T", "id": "p1"},
                         org_id="o", graph_id="g")
    assert "materialize a row the journal never wrote" in str(excinfo.value)


# ════════════════════════════════════════════════════════════════════════════
# 3b. The §2.6 split — the two paths the schema's own CHECKs do not cover
# ════════════════════════════════════════════════════════════════════════════


def test_the_projection_owned_status_cache_is_never_stored_by_a_mutation():
    """`EntityMutated` may not write `status` into Object/Subject `props`.

    `Object.status` and `Subject.status` are the **projection-owned lifecycle
    cache** (§2.6, `_fold_object_superseded`'s own docstring; `ONTOLOGY.md`
    §2/§4.2/§4.3), so a stored copy is a derived value made authority — capstone
    S5. Both `Object.props` and `Subject.props` refuse the name by CHECK —
    `Object` from #8179, `Subject` from the forward migration #8263 — so the
    projection carries the refusal on its own side rather than relying on the
    backstop. (`Subject`'s schema refusal was the gap #8263 closed; before it,
    the props tail accepted the cache name.)

    `Point.status` is the counter-example that keeps the scope honest: a Point's
    lifecycle IS journaled and `point.status` is a PRIMARY column, so
    `PointRetracted`/`PointSuperseded` still write it.
    """
    for label, table in (("Subject", "subject"), ("Object", "object")):
        statements = rp.mutations_for(
            {"type": "EntityMutated", "ts": "T", "id": "k1", "label": label,
             "op": "restatus",
             "state": {"status": "superseded", "supersededBy": "k2",
                       "supersededAt": "2026-10-12T00:00:00Z"}},
            org_id="o", graph_id="g",
        )
        sql, params = statements[0].sql, statements[0].params
        assert sql.startswith(f"UPDATE public.{table}")
        set_clause = sql.split(" SET ", 1)[1].split(" WHERE ")[0]
        assert "status" not in {part.split("=")[0].strip()
                                 for part in set_clause.split(",")}
        assert not any(isinstance(p, str) and p.startswith("{") and "superseded" in p
                       for p in params), (
            f"{label}.status reached the props tail — a projection-owned cache "
            "stored as authority"
        )
        # …and the PRIMARY half of the same record IS stored.
        assert "superseded_by" in sql
        assert "2026-10-12T00:00:00Z" in params

    point = rp.mutations_for(
        {"type": "PointSuperseded", "ts": "T", "id": "p1", "new_id": "p2"},
        org_id="o", graph_id="g",
    )
    assert "status" in point[0].sql and "superseded" in point[0].params


def test_a_subject_snapshot_carrying_the_cache_name_drops_it():
    """The SNAPSHOT door onto the same refusal (#8263).

    `test_the_projection_owned_status_cache_is_never_stored_by_a_mutation`
    covers `EntityMutated`, where `never_store` is the guard. `SubjectAdded`
    is a different door — a flat snapshot routed through
    `refused_props_names()`, the schema read. That read now sees the forward
    migration's `status` in `chk_subject_props_no_derived`; without it the
    projection emitted a `props` tail the CHECK refuses — a crash handed to a
    caller instead of a refusal.
    """
    statements = rp.mutations_for(
        {"type": "SubjectAdded", "ts": "T", "id": "s1", "name": "sub1",
         "subjectKind": "topic", "status": "superseded"},
        org_id="o", graph_id="g",
    )
    stored = _stored_names(statements)
    assert "status" not in stored, (
        "SubjectAdded stored the projection-owned lifecycle cache as authority "
        "— the schema refuses it and the projection must drop it, not crash"
    )
    # The PRIMARY half of the same snapshot still lands (no over-drop).
    assert "subject_kind" in stored


def test_document_created_does_not_store_the_retired_props():
    """§9.5 Q3 retired `content`/`doc_status`/`objectKind` — the fold scrubs them."""
    statements = rp.mutations_for(
        {"type": "DocumentCreated", "ts": "T", "id": "u1", "url": "u1",
         "content": "raw", "doc_status": "captured", "objectKind": "document"},
        org_id="o", graph_id="g",
    )
    insert = statements[-1]
    assert "content" not in insert.sql
    assert not any(isinstance(p, str) and ("doc_status" in p or "objectKind" in p)
                   for p in insert.params), (
        "a retired prop landed in the source row's props tail — the record would "
        "hold state the node scrubbed"
    )


def test_the_stub_completion_discriminator_never_reaches_the_source_row():
    """#5097 review P2: `_stub_completion` is TRANSPORT, never source data.

    `_upsert_source` reads it as a parameter, `entity_projection._META_KEYS`
    keeps it off the `:Source` node, and `sdk._materialize_session_source` is its
    only producer. The RECORD layer is a third home: `SourceCreated`'s mutation
    is a `NodeSnapshot(..., path=None)`, so the record's own flat payload IS the
    property dict — without a `_RECORD_ONLY_KEYS` entry the key fell into the
    `source.props` jsonb tail (measured: `{"_stub_completion": true}`), which
    `cache_projection._props_from_row` then copies back onto the node verbatim
    ("unmapped"), re-materializing a server-managed discriminator the graph
    deliberately keeps off. Same shape as `contains_session`, the capture
    envelope directive that already sits in `_RECORD_ONLY_KEYS`.
    """
    assert "_stub_completion" in rp._RECORD_ONLY_KEYS, (
        "the capture completion discriminator would be stored as source data")
    record = {"type": "SourceCreated", "ts": "T", "event_id": "ev-envelope",
              "id": "u1", "url": "u1", "contentHash": "h", "title": None,
              "_stub_completion": True, "sessionId": "s1"}
    insert = _node_insert(rp.mutations_for(record, org_id="o", graph_id="g"),
                          "source")
    assert "_stub_completion" not in insert.params[-1], (
        "the capture completion discriminator landed in source.props — a cache "
        "restore would put a transport key back on the node")
    assert "stub_completion" not in insert.params[-1]
    # The rest of the payload is still the record's data (the drop is narrow).
    assert "s1" in insert.params


def test_edge_record_only_carries_the_callers_attributes():
    """`confidence`/`weight` are PRIMARY on the EDGE and DERIVED on the Point."""
    statements = rp.mutations_for(
        {"type": "EntityLinked", "ts": "T", "id": "p1", "source_label": "Point",
         "source_id": "p1", "target_label": "Point", "target_id": "p2",
         "edge_type": "IMPL", "confidence": 0.7, "weight": 0.3},
        org_id="o", graph_id="g",
    )
    insert = statements[-1]
    assert "confidence" in insert.sql and "weight" in insert.sql
    assert 0.7 in insert.params and 0.3 in insert.params


# ════════════════════════════════════════════════════════════════════════════
# 4. IDENTITY FIDELITY — record == graph on every key the fold MERGEs on
# ════════════════════════════════════════════════════════════════════════════


def _node_insert(statements: list[rp.Statement], table: str) -> rp.Statement:
    return next(s for s in statements
                if s.sql.startswith(f"INSERT INTO public.{table}"))


def test_the_journal_envelope_event_id_is_never_a_node_property():
    """`event_id` is the `_emit_event` envelope, never a node property.

    `_emit_event` stamps `"event_id": ulid()` on every JSONL line, distinct
    from the node's own `id`/`eventId`. The fold lists it in `_META_KEYS`
    ("structural, never node properties"), so a record that stores it leaks a
    per-record ULID into the node. `ENVELOPE_KEYS` names it, so
    `_RECORD_ONLY_KEYS` must too.
    """
    assert "event_id" in rp.ENVELOPE_KEYS
    records = [
        ("ObjectRegistered", "object", {"id": "o1", "name": "n1"}),
        ("SubjectAdded", "subject", {"id": "s1", "name": "sub1"}),
        ("SessionRecorded", "session", {"id": "sess1", "turn_count": 2}),
        ("SourceCreated", "source", {"id": "u1", "url": "u1"}),
    ]
    for record_type, table, payload in records:
        record = {"type": record_type, "ts": "T", "event_id": "ev-envelope",
                  **payload}
        statements = rp.mutations_for(record, org_id="o", graph_id="g")
        insert = _node_insert(statements, table)
        assert "ev-envelope" not in insert.params, (
            f"{record_type}: the journal envelope `event_id` leaked into the "
            f"{table} row"
        )
        assert "event_id" not in insert.params[-1], (
            f"{record_type}: the journal envelope landed in {table}.props"
        )


def test_event_recorded_keys_on_the_domain_event_id_not_the_envelope():
    """The Event row is keyed by the fold's MERGE key, never the envelope ULID."""
    record = {"type": "EventRecorded", "ts": "T", "event_id": "ev-14",
              "id": "e1", "eventId": "e1", "eventKind": "pointAdded"}
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    insert = _node_insert(statements, "event")
    columns = [c.strip() for c in
               insert.sql.split("(", 1)[1].split(")", 1)[0].split(",")]
    assert insert.params[3] == "e1", (
        "the Event row was keyed by the journal envelope ULID, not the "
        "payload eventId"
    )
    assert insert.params[columns.index("event_id")] == "e1"
    assert "ev-14" not in insert.params
    # A payload lacking `eventId` still keys off the fold's `id` fallback.
    fallback = rp.mutations_for(
        {"type": "EventRecorded", "ts": "T", "event_id": "ev-99", "id": "e2"},
        org_id="o", graph_id="g",
    )
    assert _node_insert(fallback, "event").params[3] == "e2"


def test_entity_mutated_state_ops_match_the_entity_id_column_not_node_key():
    """Object/Subject `node_key` is `name`; the record identity is `id`.

    The graph fold matches an `EntityMutated` by `_ENTITY_ID_PROP[label]`
    (`id` for Object/Subject), so a `node_key = id` predicate silently updates
    0 rows — the exact silent skip Interface A forbids.
    """
    for label, table, ident in (("Subject", "subject", "sub1"),
                                ("Object", "object", "o1")):
        statements = rp.mutations_for(
            {"type": "EntityMutated", "ts": "T", "id": ident, "label": label,
             "op": "restatus",
             "state": {"status": "superseded", "supersededBy": "next",
                       "supersededAt": "2026-10-12T00:00:00Z"}},
            org_id="o", graph_id="g",
        )
        sql, params = statements[0].sql, statements[0].params
        assert sql.startswith(f"UPDATE public.{table}")
        predicate = (sql.split(" WHERE ", 1)[1]
                     .replace("org_id = %s", "").replace("graph_id = %s", ""))
        assert "id = %s" in predicate and "node_key" not in predicate, sql
        assert ident in params
        assert "superseded_by" in sql and "next" in params
        # `status` is the projection-owned cache and must stay out.
        set_clause = sql.split(" SET ", 1)[1].split(" WHERE ")[0]
        assert "status" not in {p.split("=")[0].strip()
                                for p in set_clause.split(",")}

        delete = rp.mutations_for(
            {"type": "EntityMutated", "ts": "T", "id": ident, "label": label,
             "op": "delete"},
            org_id="o", graph_id="g",
        )
        # The record's hard delete is `DETACH DELETE`'s two halves: the
        # identity parent (its removal cascades the node row and the incident
        # relations) resolved from the node id, then the node row itself.
        assert len(delete) == 2, delete
        assert delete[0].sql.startswith("DELETE FROM public.node_identity"), delete
        assert f"node_key IN (SELECT node_key FROM public.{table} " in delete[0].sql
        node_delete = next(s for s in delete
                           if s.sql.startswith(f"DELETE FROM public.{table}"))
        delete_predicate = (node_delete.sql.split(" WHERE ", 1)[1]
                            .replace("org_id = %s", "")
                            .replace("graph_id = %s", ""))
        assert "id = %s" in delete_predicate and "node_key" not in delete_predicate
        assert ident in node_delete.params


def test_entity_mutated_matches_a_url_only_source_by_its_node_key_too():
    """A url-only `:Source` carries no `id`, so the OR-set falls back to `url`."""
    statements = rp.mutations_for(
        {"type": "EntityMutated", "ts": "T", "id": "https://example.test/x",
         "label": "Source", "op": "revise", "state": {"title": "t"}},
        org_id="o", graph_id="g",
    )
    sql = statements[0].sql
    assert sql.startswith("UPDATE public.source")
    predicate = (sql.split(" WHERE ", 1)[1]
                 .replace("org_id = %s", "").replace("graph_id = %s", ""))
    assert "id = %s" in predicate and "node_key = %s" in predicate


def test_direct_edge_repoint_delete_only_deletes_rather_than_upserts():
    """A `delete_only` descriptor is the graph's DELETE leg, not an edge write.

    The producer emits it for the #2489 no-self-edge guard; the fold treats
    `delete_only is True` as a delete. An upsert here re-created the edge the
    graph removed AND minted a phantom `node_identity` row from the
    descriptor's structural `target_label`.
    """
    statements = rp.mutations_for(
        {"type": "DirectEdgeRepoint", "ts": "T",
         "id": "p1->p2:aboutPoint:self", "src": "p1", "tgt": "p2",
         "target_label": "Source", "edge_type": "aboutPoint",
         "delete_only": True},
        org_id="o", graph_id="g",
    )
    assert len(statements) == 1, statements
    sql, params = statements[0].sql, statements[0].params
    assert sql.startswith("DELETE FROM public.relation"), sql
    assert "INSERT" not in sql and "node_identity" not in sql
    # Both endpoints are `:Point` (old point -> successor); the descriptor's
    # structural `target_label` is deliberately NOT the row's tgt_label.
    assert "src_label = 'Point'" in sql and "tgt_label = 'Point'" in sql
    assert params == ("o", "g", "aboutPoint", "p1", "p2", 0)


# ════════════════════════════════════════════════════════════════════════════
# §4.4 pass-2b — a STRUCTURAL `DirectEdgeRepoint` is a MOVE (#9972 + #9973)
# ════════════════════════════════════════════════════════════════════════════
# The DOC half of this pin (ONTOLOGY §3.1 + the INGEST supersede row vs this
# plan) lives in `tests/test_10191_record_move_doc_pin.py`: a
# `docs/INGEST_CONTRACT.md`-only diff selects no surface and runs `tier1` only,
# which does not include this file, so a pin inside this docker-lane module
# would sleep on the exact edit it guards (#8474's shape).


def _structural_repoint(src="p1", tgt="Acme Corp", edge_type="aboutObject",
                       **extra):
    """The 2b descriptor `sdk.supersede_point` journals (sdk.py:16657)."""
    return {"type": "DirectEdgeRepoint", "ts": "T",
            "id": f"{src}->p2:{edge_type}:{tgt}",
            "src": src, "tgt": tgt, "target_label": "Object",
            "edge_type": edge_type, **extra}


def test_the_structural_repoint_map_is_the_folds_own():
    """The restated rel→label map IS the fold's, and it is the ROUTER.

    Two failure modes are pinned, not one: a label that drifted from
    `projection/edges.py::STRUCTURAL_REL_LABELS` would resolve the target in the
    wrong table, and a rel this map lists that the fold does NOT consider
    derivable would route a descriptor through the move path that the graph
    never re-creates (`DERIVABLE_STRUCTURAL_RELS`).
    """
    from tortoise.projection.edges import DERIVABLE_STRUCTURAL_RELS, STRUCTURAL_REL_LABELS

    assert rp._STRUCTURAL_REL_LABELS == STRUCTURAL_REL_LABELS, (
        rp._STRUCTURAL_REL_LABELS, STRUCTURAL_REL_LABELS)
    assert set(rp._STRUCTURAL_REL_LABELS) == set(DERIVABLE_STRUCTURAL_RELS), (
        sorted(rp._STRUCTURAL_REL_LABELS), sorted(DERIVABLE_STRUCTURAL_RELS))


def test_the_terminal_status_set_is_the_canonical_ones():
    """The move's `_src_is_terminal` gate uses the canonical status partition.

    Restated for the STANDALONE load (the PGlite harness loads this module by
    file path, so `tortoise.live` is not importable there), and pinned here: a
    status added to the vocabulary without being classified in `live.py` would
    otherwise silently make the record's gate looser than the fold's.

    The pin is `TERMINAL_EXCLUDED_STATUSES`, NOT `TERMINAL_STATUS_VALUES`: the
    graph's gate is `_terminal_excluded`, whose vocabulary is the point set PLUS
    the legacy `deprecated` member (`live.py` — no writer emits it, legacy graphs
    carry it, and it must never be served as current). Pinning to
    `TERMINAL_STATUS_VALUES` alone let the record's gate be narrower than the
    fold's by exactly that member, so the test certified a drift it existed to
    catch.
    """
    from tortoise.live import TERMINAL_EXCLUDED_STATUSES

    assert frozenset(rp._TERMINAL_POINT_STATUSES) == TERMINAL_EXCLUDED_STATUSES, (
        rp._TERMINAL_POINT_STATUSES, TERMINAL_EXCLUDED_STATUSES)
    predicate = rp._terminal_point_sql("p")
    for status in TERMINAL_EXCLUDED_STATUSES:
        assert f"'{status}'" in predicate, (status, predicate)
    assert "'deprecated'" in predicate, predicate
    # The legacy flag is the partition's second leg (`live._terminal_excluded`)
    # and it lives in the `outdated` COLUMN — the record writes it there (a
    # `NodePatch` literal / a `point` payload key routed to the column by
    # `_split_properties`), so the old `props ->> 'outdated'` form was always
    # NULL and `PointInvalidated` (flag set, status untouched) read as LIVE here
    # while the fold read it as terminal. Assert BOTH directions so a regression
    # to the dead form reds instead of passing quietly.
    assert "coalesce(p.outdated, false) = true" in predicate, predicate
    assert "props ->> 'outdated'" not in predicate, predicate


def test_a_structural_repoint_descriptor_resolves_its_name_keyed_target():
    """#9972 indicator 1 — the target is resolved by NAME, never by id.

    The descriptor's `tgt` is the target's REPLAY KEY, which `stub_key`
    (`projection/edges.py`) mints as a `name` for the about* family — while
    `_ENTITY_MATCH_COLUMNS` files an Object/Subject by its ID. Resolving the
    descriptor against `id` matched `object.id = 'Acme Corp'`: a successful
    0-row `INSERT … SELECT`, no error, nothing on `record_write_failures`.
    """
    statements = rp.mutations_for(_structural_repoint(), org_id="o", graph_id="g")
    assert statements, "the descriptor emitted nothing at all"
    joined = "\n".join(s.sql for s in statements)
    params = [p for s in statements for p in s.params]
    assert "Acme Corp" in params, params          # the descriptor's replay key
    # ⛔ The predicate, not the bare substring: `graph_id = %s` contains "id = %s",
    # so an unqualified assertion passes over the very bug it names.
    assert "t.id = %s" not in joined, joined      # an id can never carry that value
    assert "t.name = %s" in joined, joined        # edges.py resolves by NAME
    # The row is filed under the label the FOLD resolves, which it derives from
    # the REL — the descriptor's own `target_label` is not the router.
    assert "tgt_label = %s" in joined
    assert "Object" in params, params             # the label the fold resolves


def test_a_structural_repoint_moves_its_edge_to_the_final_successor():
    """#9973 indicator 1 — the move has TWO halves: remove at old, create at final.

    The descriptor's `src` is the point being SUPERSEDED (`sdk.py` 2b emission,
    `src=old_id`), so an upsert at `src` both kept the row the graph deleted and
    never wrote the successor's. #9972 alone is unobservable: a target-only fix
    re-writes the same wrong row (and erases its attributes through the
    `ON CONFLICT` path), which is why the two land as ONE change.
    """
    statements = rp.mutations_for(_structural_repoint(), org_id="o", graph_id="g")
    assert len(statements) == 2, statements   # the two halves of ONE mutation
    delete = next((s.sql for s in statements
                   if "DELETE FROM public.relation" in s.sql), "")
    inserts = [s for s in statements if "INSERT INTO public.relation" in s.sql]
    assert delete, "a supersede's transferred edge is never removed from the old point"
    assert inserts, "the successor's row is never written"
    # The delete keys the SUPERSEDED point (`depth = 1` is the chain's root) and
    # the row's resolved target — the fold's own re-query.
    assert "src_key = (SELECT node_key FROM succ WHERE depth = 1)" in delete, delete
    assert "src_label = 'Point'" in delete, delete
    # The insert files the chain's TERMINAL row (`next_id IS NULL`), not `src`.
    insert = inserts[0].sql
    assert "succ WHERE next_id IS NULL" in insert, insert
    assert "'Point', dest.node_key" in insert, insert
    # `_final(src) == src` is a clean NO-OP, not a delete-then-reinsert: a
    # re-created LIVE incarnation owns its edges (`_src_is_terminal`).
    for sql in (delete, insert):
        assert "(SELECT terminal FROM succ WHERE depth = 1)" in sql, sql
        assert "EXISTS (SELECT 1 FROM succ WHERE next_id IS NOT NULL)" in sql, sql
        # ⛔ The DESTINATION conjunct, on BOTH halves. Without it the pair is not
        # one mutation: a cyclic or >32-hop chain satisfies the two conjuncts
        # above (src is terminal and HAS a successor) while the INSERT's `dest`
        # subquery is empty, so the DELETE ran and wrote no replacement — the row
        # left the superseded point and landed nowhere.
        assert "EXISTS (SELECT 1 FROM succ WHERE next_id IS NULL)" in sql, sql
    # NO SELF-EDGE (E2E-11.6): a chain that collapses the endpoints onto one node
    # must not mint `(X)-[:aboutPoint]->(X)`.
    assert "dest.node_key <> tgt.node_key" in insert, insert
    # A bare MERGE is what the fold's transfer is: no attribute SET, so an
    # existing successor row keeps its own value (#9417) — `DO UPDATE` would
    # overwrite it with the descriptor's absent attributes.
    assert insert.rstrip().endswith("DO NOTHING"), insert


def test_the_structural_move_reads_the_records_own_successor_chain():
    """`_final(src)` is a `superseded_by` walk over `public.point` — no graph read.

    The record already holds the successor link (the `PointSuperseded` patch
    writes `props->>'superseded_by'`), which is what makes the move derivable
    from the journal alone (Interface A). The walk is depth-bounded AND
    `path`-guarded: a cycle has no terminal, so nothing moves.
    """
    cte, params = rp._supersede_chain_cte("o", "g", "p1")
    assert cte.startswith("WITH RECURSIVE succ("), cte
    assert "NULLIF(p.props ->> 'superseded_by', '')" in cte, cte
    assert "s.path || n.node_key" in cte, cte
    assert "NOT (n.node_key = ANY (s.path))" in cte, cte
    assert params == ["o", "g", "p1", "o", "g", rp._SUPERSEDE_CHAIN_DEPTH], params


def test_a_name_held_by_two_carriers_resolves_to_nothing():
    """#5509 — the record refuses an ambiguous name instead of picking one.

    The graph's `_refuse_ambiguous_target` records the non-folded entry and
    skips the descriptor; `LIMIT 1` here would land the edge on a carrier the
    graph never addressed (and, with the move, at a DIFFERENT point than the
    graph's).
    """
    for label, rel in (("Object", "aboutObject"), ("Subject", "aboutSubject"),
                       ("Event", "aboutEvent"), ("Point", "aboutPoint")):
        select, params = rp._structural_target_resolution("o", "g", label, rel,
                                                          "Acme Corp", "tgt")
        assert "HAVING count(*) = 1" in select, select
        assert "LIMIT 1" not in select, select
        assert params == ["o", "g", "Acme Corp"], params
    # `public.point` has NO `name` column — a Point's name is a PROPERTY, which is
    # where the record stores it (and what the fold's `MATCH (x:Point {name:$key})`
    # reads). An id match here would silently resolve nothing.
    point_select, _ = rp._structural_target_resolution(
        "o", "g", "Point", "aboutPoint", "N", "tgt")
    assert "props ->> 'name'" in point_select, point_select


def test_aboutdocument_stays_resolve_only_and_document_only():
    """D10/#5206 — a document target is a `:Source` carrying `document_kind`.

    `resolve_structural_target`'s `aboutDocument` arm resolves ONLY a document
    (never mints, never mis-points); `extractedFrom` keeps the canonical
    Source identity triple. A descriptor keyed on `url` alone cannot tell a
    document from a provenance `:Source`, so the kind test is load-bearing on
    the descriptor route — the record must not be looser than the fold.
    """
    document, _ = rp._structural_target_resolution(
        "o", "g", "Source", "aboutDocument", "https://x/doc", "tgt")
    assert "document_kind IS NOT NULL" in document, document
    assert "HAVING count(*) = 1" in document, document
    extracted, params = rp._structural_target_resolution(
        "o", "g", "Source", "extractedFrom", "https://EXAMPLE.com/a", "tgt")
    # The SAME canonical precedence the point-property family passes — one rule
    # for one identity, so the create arm and the repoint arm cannot disagree.
    assert "canonical_url" in extracted, extracted
    assert 'COLLATE "C"' in extracted, extracted
    assert params == ["o", "g", "https://EXAMPLE.com/a",
                      "https://example.com/a", "https://example.com/a",
                      "https://example.com/a"], params


def test_a_direct_edge_repoint_is_still_a_plain_upsert():
    """The 2a DIRECT shape is NOT a move — its `src` is already the successor.

    `sdk.py`'s 2a emission passes `src=new_id`, so deleting at `src` would delete
    the edge the descriptor exists to re-create. The routing key is the rel: only
    the structural set moves.
    """
    statements = rp.mutations_for(
        {"type": "DirectEdgeRepoint", "ts": "T", "id": "p1->p2:IMPL",
         "src": "p2", "tgt": "p3", "edge_type": "IMPL"},
        org_id="o", graph_id="g",
    )
    assert len(statements) == 1, statements
    sql = statements[0].sql
    assert sql.startswith("INSERT INTO public.relation"), sql
    assert "DELETE" not in sql, sql
    # …and the STRUCTURAL `DirectEdgeCreated` create arm is untouched too.
    created = rp.mutations_for(
        {"type": "DirectEdgeCreated", "ts": "T",
         "id": "p1->s1:aboutSubject", "src": "p1", "tgt": "s1",
         "source_label": "Point", "target_label": "Subject",
         "edge_type": "aboutSubject"},
        org_id="o", graph_id="g",
    )
    assert len(created) == 1 and created[0].sql.startswith("INSERT"), created
    assert "succ" not in created[0].sql, created[0].sql


def test_a_node_upsert_merges_the_props_tail_rather_than_replacing_it():
    """The record must not be poorer than the graph on a partial carrier.

    The graph applies an open-set tail with `SET n += $props`, preserving a prop
    an earlier record landed; `props = EXCLUDED.props` would drop it for a
    second `SessionRecorded` / a `SourceVersioned`.
    """
    for record_type, table, payload in (
        ("SessionRecorded", "session", {"id": "sess1", "turn_count": 2}),
        ("SourceVersioned", "source", {"id": "u1", "url": "u1",
                                       "contentHash": "h2"}),
    ):
        insert = _node_insert(
            rp.mutations_for({"type": record_type, "ts": "T", **payload},
                             org_id="o", graph_id="g"),
            table,
        )
        assert f"props = {table}.props || EXCLUDED.props" in insert.sql, insert.sql
        assert "props = EXCLUDED.props" not in insert.sql


def test_a_node_upsert_serializes_jsonb_column_values():
    """A DB-API `list`/`dict` adapted raw is an ARRAY, not a jsonb value."""
    insert = _node_insert(
        rp.mutations_for(
            {"type": "PointAdded", "ts": "T", "id": "p1",
             "point": {"id": "p1", "embedding": [0.1, 0.2], "tags": ["a"]}},
            org_id="o", graph_id="g"),
        "point",
    )
    assert not any(isinstance(p, (dict, list)) for p in insert.params), (
        "a jsonb column value was passed raw; the DB-API driver would adapt it "
        "to an ARRAY and the jsonb insert would fail"
    )
    assert "[0.1, 0.2]" in insert.params and '["a"]' in insert.params


def _insert_columns(statement: rp.Statement) -> list[str]:
    """The column list of an `INSERT INTO public.<table> (…)` statement."""
    return [c.strip() for c in
            statement.sql.split("(", 1)[1].split(")", 1)[0].split(",")]


def test_a_node_property_named_like_a_bookkeeping_column_stays_off_it():
    """A payload may never write the record's own bookkeeping column.

    `create_point(kind, content, **props)` stores a node property verbatim —
    `props=1` is a real node property the graph folds
    (`get_point(pid)["props"] == 1`). Routing it to the table's own column
    emitted `INSERT … (…, props, props)` (Postgres: `column "props" specified
    more than once`) and duplicated `node_key` too. The property must instead
    be preserved as DATA in the `props` tail: dropping it would make the record
    poorer than the graph, the failure Interface A forbids.
    """
    for prop in ("props", "nodeKey", "nodeLabel", "orgId", "graphId"):
        insert = _node_insert(
            rp.mutations_for(
                {"type": "PointAdded", "ts": "T", "id": "p1",
                 "point": {"id": "p1", "content": "c", prop: "sentinel"}},
                org_id="o", graph_id="g"),
            "point",
        )
        columns = _insert_columns(insert)
        assert len(columns) == len(set(columns)), (
            f"{prop}: the payload duplicated a bookkeeping column: {columns}"
        )
        assert prop in json.loads(insert.params[-1]), (
            f"{prop}: the node property was dropped instead of preserved in "
            "the props tail"
        )


def test_a_patch_payload_cannot_rewrite_the_node_key():
    """A `nodeKey` extra would otherwise emit `SET … node_key = %s`.

    `PointRevised`/`OperatorAnnotated` carry their extras verbatim, so a
    property named `nodeKey` is a real node property — it must never rewrite
    the row's canonical key through the patch's `SET node_key = %s`.
    """
    patch = rp.mutations_for(
        {"type": "PointRevised", "ts": "T", "id": "p1", "new_content": "c2",
         "nodeKey": "p-other"},
        org_id="o", graph_id="g",
    )[0]
    set_clause = patch.sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "node_key" not in set_clause, (
        f"a payload property rewrote the node key: {patch.sql}"
    )
    # The property survives as data under its own journal name, not dropped.
    assert json.loads(patch.params[2]) == {"nodeKey": "p-other"}


# ════════════════════════════════════════════════════════════════════════════
# 4b. HARD DELETE — the record's half of the graph's `DETACH DELETE`
# ════════════════════════════════════════════════════════════════════════════


def test_a_hard_delete_cascades_the_identity_and_incident_relations():
    """The graph's hard delete is `MATCH … DETACH DELETE n` — node AND edges.

    `relation`'s endpoints are composite FKs into `node_identity`
    (`ON DELETE CASCADE`), so removing the identity row IS the edge removal. A
    bare node `DELETE` left the identity parent and every incident relation
    alive: the record kept `IMPL(op1 -> p2)` after `p2` was gone, while the fold
    did not. The record identity for Object/Subject is the `id` column, not
    `node_key`, so the identity key is resolved from the node row before it is
    removed — which is why the identity delete is emitted FIRST.
    """
    statements = rp.mutations_for(
        {"type": "EntityMutated", "ts": "T", "id": "p2", "label": "Point",
         "op": "delete"},
        org_id="o", graph_id="g",
    )
    assert len(statements) == 2, statements
    identity = statements[0]
    # EXACT, not `startswith`: the two sites fail DIFFERENTLY, and the substring
    # assertions this pin REPLACES stayed GREEN for either drop — measured. Drop
    # the OUTER
    # predicate and the DELETE is no longer scoped to the caller's tenant at
    # all: it removes another tenant's identity row and cascades to that
    # tenant's node and incident relations. Drop the SUBQUERY predicate and the
    # rows deleted are still the caller's own, but the KEY SET is resolved from
    # other tenants' rows, so the delete can remove a row of the same tenant
    # that the tenant-scoped resolve would not have selected.
    assert identity.sql == (
        "DELETE FROM public.node_identity "
        "WHERE org_id = %s AND graph_id = %s AND node_label = %s "
        "AND node_key IN (SELECT node_key FROM public.point "
        "WHERE org_id = %s AND graph_id = %s AND node_key = %s)"
    ), identity.sql
    assert identity.params == ("o", "g", "Point", "o", "g", "p2")
    assert statements[1].sql.startswith("DELETE FROM public.point")
    # The identity cascade works for a name-keyed Object too (delete matched by
    # `id`, while `node_key` is the name the relation FKs into).
    obj = rp.mutations_for(
        {"type": "EntityMutated", "ts": "T", "id": "o1", "label": "Object",
         "op": "delete"},
        org_id="o", graph_id="g",
    )
    assert obj[0].sql.startswith("DELETE FROM public.node_identity")
    assert "node_key IN (SELECT node_key FROM public.object" in obj[0].sql
    assert "id = %s" in obj[0].sql and "node_key = %s" not in obj[0].sql


def test_session_deleted_removes_the_agent_session_source_stub():
    """The fold removes the `session:{id}` `:Source` in the SAME step.

    `_fold_session_deleted(remove_source=True)` DETACH-DELETEs both the
    `:Session` and its agentSession `:Source` stub; the record's note claimed the
    stub was "removed with it" while the mutation did not remove it. The stub's
    identity row is removed too, so its incident `CONTAINS`/`extractedFrom`
    edges cascade — the graph's DETACH DELETE behaviour.
    """
    statements = rp.mutations_for(
        {"type": "SessionDeleted", "ts": "T", "id": "sess1"},
        org_id="o", graph_id="g",
    )
    node_sql = [s.sql for s in statements]
    assert any(s.startswith("DELETE FROM public.session") for s in node_sql), node_sql
    assert any(s.startswith("DELETE FROM public.source") for s in node_sql), node_sql
    # The source stub is keyed `session:{id}`; its identity row is the FK parent
    # whose removal cascades the stub's edges.
    source = next(s for s in statements
                  if s.sql.startswith("DELETE FROM public.source"))
    assert "session:sess1" in source.params
    assert any(s.startswith("DELETE FROM public.node_identity")
               and "public.source" in s for s in node_sql), node_sql
    assert any(s.startswith("DELETE FROM public.node_identity")
               and "public.session" in s for s in node_sql), node_sql


# ════════════════════════════════════════════════════════════════════════════
# 4c. EDGE ENDPOINTS — the label's canonical key, never the raw journal id
# ════════════════════════════════════════════════════════════════════════════


def test_entity_linked_endpoint_resolves_to_the_name_keyed_object():
    """An `Object` endpoint is addressed by `id` but KEYED by `name`.

    The fold matches `MATCH (t:Object {id:$tid})`
    (`_fold_entity_linked_reason`) and the producer emits the graph `id`
    (`sdk.py::link_entity(proj,"Point",pid,o.id)`), while
    `relation.tgt_key` is a composite FK into `node_identity`, whose Object key
    is `name`. Writing the raw journal id attached the edge to a PHANTOM
    `Object/o1` — and the hard-delete cascade, which resolves the id to the
    name-keyed row, could never remove it. The endpoint's key must therefore be
    RESOLVED from the projection's own object row, and no identity row may be
    minted for the raw id.
    """
    statements = rp.mutations_for(
        {"type": "EntityLinked", "ts": "T", "id": "p1",
         "source_label": "Point", "source_id": "p1",
         "target_label": "Object", "target_id": "o1",
         "edge_type": "aboutObject"},
        org_id="o", graph_id="g",
    )
    assert len(statements) == 1, statements
    insert = statements[0]
    # EXACT, not `startswith` + substrings: this statement carries SIX tenant
    # sites (the column list and two correlated resolves), so the exact text is
    # what pins the tenant scope of every one of them. A predicate dropped from
    # BOTH a resolve's SQL and its parameters leaves every substring assertion
    # below green — measured — while the resolve now reads another tenant's node
    # rows.
    assert insert.sql == (
        "INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, "
        "src_key, tgt_label, tgt_key, idx, props) SELECT %s, %s, %s, %s, "
        "src.node_key, %s, tgt.node_key, %s, %s FROM (SELECT node_key FROM "
        "public.point WHERE org_id = %s AND graph_id = %s AND node_key = %s) "
        "src, (SELECT node_key FROM public.object WHERE org_id = %s AND "
        "graph_id = %s AND id = %s) tgt ON CONFLICT (org_id, graph_id, "
        "rel_type, src_label, src_key, tgt_label, tgt_key, idx) DO UPDATE "
        "SET props = EXCLUDED.props"
    ), insert.sql
    assert insert.params == ("o", "g", "aboutObject", "Point", "Object", 0,
                             "{}", "o", "g", "p1", "o", "g", "o1")
    # The key columns are the JOINED node rows' canonical keys, not `%s` params
    # carrying the journal id.
    assert "src.node_key" in insert.sql and "tgt.node_key" in insert.sql
    assert "(SELECT node_key FROM public.object" in insert.sql, insert.sql
    assert "(SELECT node_key FROM public.point" in insert.sql, insert.sql
    # The resolution matches the journal id exactly as the fold's MATCH does.
    assert "o1" in insert.params and "p1" in insert.params
    # No phantom FK parent: the fold's `MATCH` creates no endpoints either.
    assert not any("node_identity" in s.sql for s in statements), statements


def test_entity_linked_with_an_absent_endpoint_lands_no_edge_and_no_identity():
    """A link whose endpoint has no row is the fold's `\"absent\"` verdict.

    The fold's `MATCH (s),(t) MERGE (s)-[r]->(t)` 0-rows when either endpoint is
    absent — no edge. The projection joined its endpoint lookups, so the same
    absent endpoint yields ZERO source rows for the insert: it must not land an
    edge, and it must not mint the phantom `node_identity` the raw-id write did.
    """
    statements = rp.mutations_for(
        {"type": "EntityLinked", "ts": "T", "id": "p1",
         "source_label": "Point", "source_id": "p1",
         "target_label": "Object", "target_id": "no-such-object",
         "edge_type": "aboutObject"},
        org_id="o", graph_id="g",
    )
    assert len(statements) == 1, statements
    insert = statements[0]
    # Both endpoints are joined (not written as literals), so an absent row
    # suppresses the whole insert rather than fabricating a key.
    assert " FROM (SELECT node_key FROM public.point" in insert.sql, insert.sql
    assert ", (SELECT node_key FROM public.object" in insert.sql, insert.sql
    assert "no-such-object" in insert.params
    assert not any("node_identity" in s.sql for s in statements), statements


def test_the_edge_endpoint_key_and_the_delete_cascade_agree():
    """The link's `tgt_key` and the delete's identity key must be ONE row.

    The hard delete resolves the node's canonical key from its `id` before the
    cascade; the link must resolve the same row. If the link wrote the raw
    journal id while the delete removed the name-keyed identity, the edge
    outlived the object — `Object/o1` was never a `node_identity` row the
    delete could cascade. Both statements read `public.object` by `id`.
    """
    link = rp.mutations_for(
        {"type": "EntityLinked", "ts": "T", "id": "p1",
         "source_label": "Point", "source_id": "p1",
         "target_label": "Object", "target_id": "o1",
         "edge_type": "aboutObject"},
        org_id="o", graph_id="g",
    )[0]
    delete = rp.mutations_for(
        {"type": "EntityMutated", "ts": "T", "id": "o1", "label": "Object",
         "op": "delete"},
        org_id="o", graph_id="g",
    )
    assert "SELECT node_key FROM public.object" in link.sql, link.sql
    identity_delete = delete[0]
    assert identity_delete.sql.startswith("DELETE FROM public.node_identity")
    assert "SELECT node_key FROM public.object" in identity_delete.sql, \
        identity_delete.sql
    assert "o1" in link.params and "o1" in identity_delete.params


# ════════════════════════════════════════════════════════════════════════════
# 5. THE PRODUCER SURFACE — a NEW producer reds the suite
# ════════════════════════════════════════════════════════════════════════════


def _producer_emit_vocabulary() -> set[str]:
    """Every record type the producers actually emit.

    The registry's docstring claims its vocabulary is sourced from the graph
    fold's dispatch AND "the producers' emit sites". The fold leg is
    `test_every_type_the_fold_dispatches_on_has_a_record_mutation`; this is the
    producers' half made executable, so a new `_emit_event("NewType", ...)`
    (or a new `_*_RECORD_TYPE = "NewType"` constant it is called with) reds the
    suite even before a fold arm or a torn-tail classification names it.

    ⛔ THE CONSTANT MAP IS BUILT OVER THE WHOLE PACKAGE, NOT PER FILE, and that
    is the difference between a census and a claim (PR #9862 review, P3). A
    producer may DECLARE the constant in one module and CALL the emit in
    another — #9555 does exactly that: `NARRATIVE_RECORD_TYPE = "SessionNarrated"`
    lives in `record_projection.py` and the
    `sdk._emit_event(NARRATIVE_RECORD_TYPE, ...)` call lives in `hosted_api.py`.
    Two per-file scans pair the two halves in no file at all, so the type was
    INVISIBLE here and a second carrier of that shape would be too. The emit
    regex was already package-wide; the constant map now is as well.
    """
    texts = {path: path.read_text(encoding="utf-8")
             for path in sorted((REPO_ROOT / "tortoise").glob("*.py"))}

    # Pass 1 — every `*_RECORD_TYPE` the package declares, as ONE vocabulary.
    constants: dict[str, str] = {}
    for text in texts.values():
        for name, record_type in re.findall(
                r'([A-Za-z_][A-Za-z0-9_]*_RECORD_TYPE)\s*=\s*"([A-Z][A-Za-z0-9_]*)"',
                text):
            constants[name] = record_type

    # Pass 2 — every emit, resolved against that vocabulary.
    found: set[str] = set()
    for text in texts.values():
        found |= set(re.findall(
            r'(?:_emit_event|_emit)\(\s*"([A-Z][A-Za-z0-9_]*)"', text))
        for name, record_type in constants.items():
            if re.search(r'(?:_emit_event|_emit)\(\s*' + re.escape(name) + r'\b',
                         text):
                found.add(record_type)
    return found


def test_every_type_a_producer_emits_has_a_record_mutation():
    emitted = _producer_emit_vocabulary()
    # Non-vacuity: the scan must resolve the whole producer surface, including
    # the types emitted only through a `_*_RECORD_TYPE` constant. A stale regex
    # would silently stop proving anything.
    assert {"EntityMutated", "BatchIdStamped"} <= emitted
    # ⛔ THE CROSS-MODULE CONSTANT SHAPE, named so it cannot regress silently: the
    # declaration and the emit live in two different files, which is precisely
    # the case a per-file scan cannot see. Without this assertion the census is
    # green over a producer it never found (PR #9862 review, P3).
    assert rp.NARRATIVE_RECORD_TYPE in emitted, (
        "`SessionNarrated` is declared in `record_projection.py` and emitted "
        "in `hosted_api.py` — a per-file scan pairs the two halves in NO file, "
        "so the census stopped seeing this producer (and would see none of "
        "its shape)")
    assert len(emitted) >= 28, (
        f"the producer scan found only {len(emitted)} emit types — the regex "
        "went stale and this leg stopped proving anything"
    )
    missing = sorted(emitted - set(rp.JOURNAL_EVENT_TYPES))
    assert not missing, (
        f"{missing} are emitted by a producer but have no declared record "
        "mutation — a NEW producer must red this suite (Interface A's total "
        "clause), or the silent skip returns"
    )


# ── the id's two legal carriers (#9078) ─────────────────────────────────────


def _point_added(snapshot_id: str, envelope_id: str | None) -> dict:
    record = {
        "type": "PointAdded",
        "event_id": "01JEVENTULID",
        "ts": "2026-10-19T00:00:00+00:00",
        "point": {"id": snapshot_id, "content": "the projection handle is unguarded",
                  "kind": "claim", "pointKind": "claim", "is_episodic": False},
    }
    if envelope_id is not None:
        record["id"] = envelope_id
    return record


def test_a_point_added_key_may_ride_the_snapshot_not_the_envelope():
    """Measured on #9078's lane: the CAPTURE writer emits exactly this shape.

    `PointAdded` from `TortoiseSDK.capture_session` (deleted by #4282; the surviving
    writer is `hosted_api._capture_session_impl`) carries NO envelope `id` —
    the id is only in `payload["point"]` — while this module's table comment
    asserts the opposite. The graph fold always read
    `inner = event.get("point") or event`, so the snapshot was a legitimate
    carrier; without this leg the record refused every captured Point and
    #9078's live wiring silently wrote nothing for them.
    """
    statements = rp.mutations_for(_point_added("pt_snapshot_only", None),
                                  org_id="o", graph_id="g")
    upserts = [s for s in statements if s.sql.startswith("INSERT INTO public.point")]
    assert upserts, [s.sql for s in statements]
    assert "pt_snapshot_only" in upserts[0].params


def test_the_envelope_key_still_wins_when_both_carriers_are_present():
    """The fallback must never re-point a record that names its key twice."""
    statements = rp.mutations_for(_point_added("pt_snapshot", "pt_envelope"),
                                  org_id="o", graph_id="g")
    upserts = [s for s in statements if s.sql.startswith("INSERT INTO public.point")]
    assert "pt_envelope" in upserts[0].params
    assert "pt_snapshot" not in upserts[0].params


# ── the producers' actual carriers (#9078, measured on the live doors) ──────


def test_a_nested_event_record_projects_from_its_nested_carrier():
    """`ConversationMiner` nests the node under `event` — the fold handles it.

    The record did not, so every mined event was refused (`unprojectable`) while
    the journal and the graph both had it (#9078 review). The nested mapping is
    the props source too: keying from it and projecting an empty row would be a
    record that exists and says nothing.
    """
    record = {
        "version": "1.0",
        "type": "EventRecorded",
        "createdAt": "2026-10-19T00:00:00+00:00",
        "event": {"eventId": "ev_nested", "eventKind": "decision",
                  "content": "the record is written in production",
                  "eventStatus": "active", "name": "record wiring"},
    }
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    event_upserts = [s for s in statements
                     if s.sql.startswith("INSERT INTO public.event")]
    assert event_upserts, [s.sql for s in statements]
    assert "ev_nested" in event_upserts[0].params
    assert any("the record is written in production" in str(v)
               for v in event_upserts[0].params), (
        "the row was keyed from the nested carrier but carries none of it")


def test_a_document_record_keys_the_source_by_its_id_like_the_fold_does():
    """`EventAPI.add_document` emits `id` with NO `url`; the fold keys `url or id`."""
    record = {
        "type": "DocumentCreated",
        "id": "doc_9078",
        "title": "the record is written in production",
        "document_kind": "note",
        "ts": "2026-10-19T00:00:00+00:00",
    }
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    source_upserts = [s for s in statements
                      if s.sql.startswith("INSERT INTO public.source")]
    assert source_upserts, [s.sql for s in statements]
    assert "doc_9078" in source_upserts[0].params


def test_a_present_but_unusable_source_url_is_refused_not_silently_repaired():
    """A `url` the producer cannot express is refused, never half-written.

    The `url → id` fallback is gated on ABSENCE, not on unusability. When it
    fired on a *present* but unusable `url`, the row was keyed from `id` while
    its props still carried the unusable `url` — so the emitted statement said
    `node_key = 'd1'` and `url = ''`/`NULL`, which the schema's own
    `chk_source_node_key_is_contract_key` (`url IS NOT NULL AND node_key = url`)
    refuses AFTER the `node_identity` insert has already run inside the caller's
    transaction. The module's rule is to fail where the record is known
    (#9200 review, P2).
    """
    for unusable in ("", None):
        record = {"type": "DocumentCreated", "ts": "T", "id": "d1",
                  "url": unusable, "title": "t"}
        with pytest.raises(rp.RecordProjectionError, match="no usable 'url' key"):
            rp.mutations_for(record, org_id="o", graph_id="g")

    # ABSENCE is the shape a producer really emits (`add_document` sends `id`
    # and no `url`), and it still projects — with the key column agreeing with
    # the resolved key, which is the invariant the schema pins.
    absent = {"type": "DocumentCreated", "ts": "T", "id": "d1", "title": "t"}
    source = next(s for s in rp.mutations_for(absent, org_id="o", graph_id="g")
                  if s.sql.startswith("INSERT INTO public.source"))
    assert "url" in source.sql
    assert list(source.params).count("d1") >= 2, (
        f"node_key and the url column must both be the resolved key: {source.params}"
    )


# ── the two property-leak fixes (#9078 review: both had NO pin) ─────────────


def _props_tail(statements: list[rp.Statement]) -> str:
    """The `props` tail of the node statement, as its serialized argument."""
    return " ".join(str(param) for statement in statements
                    for param in statement.params)


def test_agent_id_is_dropped_like_the_fold_drops_it():
    """`projection/entities.py::_META_KEYS` strips `agent_id` before the node.

    A record that kept it wrote a props key no journal replay ever writes, and a
    record-sourced rebuild copies the tail verbatim — so the two rebuild paths
    disagreed on every API-door record. Without this leg the skip-set entry can
    be deleted again in silence, which is the class #9078 exists to end.
    """
    record = _point_added("pt_agent", None)
    record["agent_id"] = "agent-leak-check"
    record["point"]["agent_id"] = "agent-leak-check"

    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    upsert = next(s for s in statements
                  if s.sql.startswith("INSERT INTO public.point"))
    # Non-vacuity: the row really was materialized, so 'absent' means dropped.
    assert "pt_agent" in upsert.params
    assert "agent-leak-check" not in _props_tail([upsert])
    assert "agent_id" not in _props_tail([upsert])


def test_actor_user_id_is_stored_only_where_the_table_has_the_column():
    """§17.5 makes it a Session PRIMARY and nothing else's property.

    `public.session` is the only record table with the column, so storing it
    everywhere else made a record-sourced rebuild diverge from a journal replay
    on that node. Both halves are asserted: a drop in one place is
    indistinguishable from a skip-set that swallowed the column everywhere.
    """
    # (a) the EPISODIC layer: the column exists, so the value is STORED.
    session = {"type": "SessionRecorded", "ts": "T", "id": "sess1",
               "actor_user_id": "user-1"}
    session_row = next(s for s in rp.mutations_for(session, org_id="o",
                                                  graph_id="g")
                       if s.sql.startswith("INSERT INTO public.session"))
    assert "actor_user_id" in session_row.sql
    assert "user-1" in session_row.params

    # (b) every other layer: no column, so it is a record-only property.
    point = _point_added("pt_actor", None)
    point["actor_user_id"] = "user-1"
    point["point"]["actor_user_id"] = "user-1"
    point_row = next(s for s in rp.mutations_for(point, org_id="o",
                                                 graph_id="g")
                     if s.sql.startswith("INSERT INTO public.point"))
    assert "actor_user_id" not in point_row.sql
    assert "user-1" not in _props_tail([point_row])


def test_a_batch_id_stamp_patches_the_point_and_never_creates_it():
    """#902/A10 restores the stamp with a MATCH, which cannot create a row.

    `BatchIdStamped` is in `projection/__init__.py::_NO_PROJECTION_FOLD`: the
    graph replay applies it in pass-2b as
    `MATCH (p:Point {id:$pid}) SET p.batch_id = $bid`. Declared as a snapshot it
    was an UPSERT, so a stamp whose `PointAdded` sits outside the replayed
    segment (#9187's `first_seq=41`) materialized a point with no creator — the
    same defect as `EntityBindingRefused`, nine lines above in the registry. The
    statement KIND is the assertion: an upsert can create, a patch cannot.
    """
    record = {"type": "BatchIdStamped", "ts": "T", "id": "p1",
              "batch_id": "batch-1", "content_hash": "h1"}
    statements = rp.mutations_for(record, org_id="o", graph_id="g")

    assert not [s for s in statements
                if s.sql.startswith("INSERT INTO public.point")], (
        "a batch stamp must not create the point it stamps: "
        f"{[s.sql for s in statements]}"
    )
    patches = [s for s in statements if s.sql.startswith("UPDATE public.point")]
    assert patches, [s.sql for s in statements]
    # The stamp is the §17.5-PRIMARY `batch_id`, not an empty no-op row.
    assert "batch_id = %s" in patches[0].sql
    assert "batch-1" in patches[0].params
    # `content_hash` is schema-refused (`props` CHECK) — dropped, never raised.
    assert "content_hash" not in patches[0].sql


def test_a_batch_id_stamp_never_writes_the_mitigation_link_into_props():
    """The mitigation path's stamp is FLAT and WIDER than the fold writes.

    `_stamp_batch_id(mid, batch_id, mitigates_operator_id=…,
    mitigation_strength=…)` → `_emit_batch_id_record` carries all four keys on
    the envelope, while pass-2b restores the stamp with `SET p.batch_id = $bid`
    and nothing else. With `from_payload=None` every flat key became a node
    property, so `mitigates_operator_id` landed in `point.props` — a key no
    journal replay writes, and a record-sourced rebuild copies the tail verbatim
    (`cache_projection._props_from_row`), so the two rebuild paths diverged on
    every stamped mitigation point. Same class as the `agent_id` leak
    (#9200 review, P2); the allow-list is the fix, so both halves are asserted:
    the real columns survive, the fold-less keys do not.
    """
    record = {"type": "BatchIdStamped", "ts": "T", "id": "m1",
              "batch_id": "batch-9", "content_hash": "h1",
              "mitigates_operator_id": "op-1", "mitigation_strength": 0.7}
    patches = [s for s in rp.mutations_for(record, org_id="o", graph_id="g")
               if s.sql.startswith("UPDATE public.point")]
    assert patches, record
    # Non-vacuity: the patch really writes the stamp, so 'absent' means dropped.
    assert "batch_id = %s" in patches[0].sql
    assert "batch-9" in patches[0].params
    assert "mitigation_strength = %s" in patches[0].sql
    tail = _props_tail([patches[0]])
    assert "mitigates_operator_id" not in tail, tail
    assert "op-1" not in tail, tail
    assert "content_hash" not in tail, tail


# ── the position watermark (#9187 P0: deleted here, restored) ───────────────


def test_a_mid_journal_segment_keeps_the_positions_it_was_given():
    """Replay of a SEGMENT numbers from the caller's offset, never from 1.

    `_journal_row` keys the consumed position on `(org_id, graph_id, seq)` with
    `ON CONFLICT DO NOTHING`, so a tail segment renumbered to 1.. would have its
    position rows absorbed by the records that already own those numbers: the
    layer rows land but the record's claim about what it consumed is false.
    This pin exists because the parameter was deleted from `replay`/`replay_plan`
    on this branch while `record_writer.write_records` still passed it — a
    merge that reads clean and breaks every armed write (#9200 review, P0).
    """
    records = [_point_added("p1", None), _point_added("p2", None)]
    plan = rp.replay_plan(records, org_id="o", graph_id="g", start_seq=41)
    seqs = [s.params[2] for s in plan if rp._JOURNAL_INSERT_PREFIX in s.sql]
    assert len(seqs) == 2, [s.sql for s in plan]
    assert seqs == [41, 42], seqs

    # The validation that guards it, and the default that preserves old calls.
    with pytest.raises(rp.RecordProjectionError):
        rp.replay_plan(records, org_id="o", graph_id="g", start_seq=0)
    default = rp.replay_plan(records, org_id="o", graph_id="g")
    assert [s.params[2] for s in default
            if rp._JOURNAL_INSERT_PREFIX in s.sql] == [1, 2]


# ---------------------------------------------------------------------------
# TENANCY (#9192) — the pair comes from the CALL, never from the process
# ---------------------------------------------------------------------------

#: Two tenants, distinct in BOTH coordinates, so a plan that mixes the graph
#: while keeping the org is caught too.
_TENANT_A = ("org_a", "graph_a")
_TENANT_B = ("org_b", "graph_b")

#: A THIRD tenant that exists only in the ENVIRONMENT. It must never appear in a
#: plan a caller asked for under another tenant: an ambient fallback carries one
#: value for every tenant the process serves, so it can never BE a tenant's.
_TENANT_ENV = ("env_org", "env_graph")


def _every_param(plan: list[rp.Statement]) -> list[Any]:
    """Every bound parameter of a plan, flattened.

    Tenancy is asserted over the WHOLE plan rather than a sampled statement:
    one statement built from a different tenant is the cross-tenant write, and
    it would sit next to 72 correct ones.
    """
    return [param for statement in plan for param in statement.params]


#: `… WHERE org_id = %s …` — one tenant BINDING SITE, with its column named.
#: Whitespace-tolerant on purpose: `graph_id= %s` is the same binding site, and a
#: pattern that only matched the canonical spelling would miss it — the
#: mention/binding count check below would then fire on a legitimate site,
#: which is a false red with a message that blames the wrong thing.
_TENANT_EQ_SITE = re.compile(r"\b(org_id|graph_id)\s*=\s*%s")
#: A tenant COLUMN named anywhere in SQL, whether or not it is bound as `%s`.
_TENANT_MENTION = re.compile(r"\b(org_id|graph_id)\b")
#: The leading column list of a layer INSERT (`(org_id, graph_id, …)`).
_INSERT_COLUMNS = re.compile(r"^INSERT INTO public\.\w+\s*\(([^)]*)\)")
#: `ON CONFLICT (cols)` names tenant columns without binding them again.
_ON_CONFLICT = re.compile(r"ON CONFLICT\s*\([^)]*\)")


def _tenant_bindings(statement: rp.Statement) -> list[tuple[int, str]]:
    """`(parameter index, tenant column)` for EVERY site this SQL binds one.

    Positional on purpose. Asking *"is the tenant's value among the parameters"*
    cannot see the two ways a statement goes cross-tenant while looking right:
    the coordinates SWAPPED (`graph_id = <org value>` — a silent 0-row update on
    a `WHERE`, and zero rows resolved from a correlated subquery), and one wrong
    site in a statement that binds the pair more than once (5 of the 73 here
    do). Both are invisible to a membership check and to a count, and both are
    caught by asking each site what it was given.

    The index of a site is the number of `%s` placeholders before it, which is
    its parameter offset because this module emits DB-API `%s` in order and
    every parameter is a placeholder (no value is ever interpolated into SQL).

    Fail-closed: a tenant column named in the SQL but NOT bound as `%s` (a
    literal, or a rewrite such as `(org_id, graph_id) = (%s, %s)`) raises rather
    than being skipped, because a skipped site is a cross-tenant clause no leg
    evaluates. The count is taken over the SQL with the INSERT's column list and
    any `ON CONFLICT (…)` removed — those name the columns without binding them
    at a site.
    """
    sql = statement.sql
    bindings = [(sql.count("%s", 0, match.end()) - 1, match.group(1))
                for match in _TENANT_EQ_SITE.finditer(sql)]
    named = _ON_CONFLICT.sub("", sql)
    head = _INSERT_COLUMNS.match(sql)
    insert_bindings: list[tuple[int, str]] = []
    if head is not None:
        # A layer INSERT names its columns and binds them positionally, so the
        # tenant is a column-list position rather than an `= %s` site. The pair
        # leads every layer INSERT, and a swap here puts each value under the
        # other's column — the one case no parameter VALUE check can see. (The
        # tenancy FK on `(org_id, graph_id)` then refuses the row, so the
        # database is the backstop, not the detector: it cannot tell a swapped
        # pair from a mistake, and the assert below names what went wrong.)
        # Asserting the ORDER (rather than filtering whichever of the two
        # leads) keeps a reordered list from silently falling out of the
        # check.
        columns = [column.strip() for column in head.group(1).split(",")]
        assert columns[:2] == ["org_id", "graph_id"], (
            f"{sql[:80]!r} does not lead with (org_id, graph_id) — the tenant "
            f"position of a layer INSERT is pinned, not derived: {columns[:4]}"
        )
        insert_bindings = [(0, "org_id"), (1, "graph_id")]
        named = named.replace(head.group(0), "", 1)
    # Every REMAINING reference to a tenant column must be an `= %s` site; the
    # column list and `ON CONFLICT` name the columns without binding them.
    mentions = len(_TENANT_MENTION.findall(named))
    assert mentions == len(bindings), (
        f"{sql[:80]!r} names a tenant column {mentions} time(s) outside its "
        f"column list but binds it {len(bindings)} time(s) — a tenant "
        f"reference that is not a `%s` binding site is not checked by any "
        f"leg, so it must not exist"
    )
    return bindings + insert_bindings


def _assert_binds(plan: list[rp.Statement], tenant: tuple[str, str]) -> None:
    """Every statement binds `tenant` at EVERY site that names a tenant column.

    Two rules, because each catches a cross-tenant write the other is blind to.
    A membership-only check — what this leg replaces — passes both, since a
    swapped value is still one of the two values it looks for:

    * **EVERY SITE, PAIRED BY ITS OWN COLUMN.** Swapping the two coordinates
      passes a
      membership check, and so does ONE wrong site in a statement that binds
      the pair twice — `DELETE … WHERE org_id = %s … AND node_key IN (… WHERE
      org_id = %s …)`, or the relation upsert's two correlated endpoint
      resolves. A swap in one of those subqueries matches no row at all — a
      silent 0-row no-op, not another tenant's row — while every VALUE is still
      one of the two the check looks for, so only a positional, per-site rule
      can see it.
    * **THE COLUMN LIST OF AN INSERT.** A layer INSERT binds the pair by column
      position, not by `= %s`, so a `(graph_id, org_id, …)` column list swapped
      WITHOUT reordering its parameters puts each value under the other's
      column — invisible to any `= %s` site and to any parameter-value check.
      The tenancy FK then refuses the row rather than filing it under the wrong
      tenant, so the database is a backstop and not a detector: it cannot say
      WHICH column was misplaced, which is why the order is asserted here.
      (That assert is positional, so it also reds a CONSISTENT reorder, which is
      correctly scoped: a false red, not a cross-tenant write.)

    Every statement must bind BOTH coordinates (a statement scoped on
    `graph_id` alone is unscoped across organisations), so the two rules above
    are checked against the site set AND, separately, against the rule that the
    set is the whole pair.

    ⚠️ What this does NOT catch, so it is not over-read: a tenant predicate
    removed from BOTH the SQL and its parameter. The statement still names both
    coordinates and every remaining site is given the right value, so both rules
    above pass — measured, dropping one of the identity `DELETE`'s two
    `graph_id` sites from SQL and params together leaves `_assert_binds` green
    over the whole plan. Such a drop is invisible here because it is a rewrite
    of a statement's TEXT, indistinguishable from the text having always been
    that way. (Removing a whole COORDINATE is caught, by the both-coordinates
    rule.)

    The bound on that gap is TEXT PINS, not a class of statements. In this file
    two statements have their full text pinned: the identity `DELETE`
    (`test_a_hard_delete_cascades_the_identity_and_incident_relations`) and the
    relation `INSERT`
    (`test_entity_linked_endpoint_resolves_to_the_name_keyed_object`); a drop
    inside either shared builder reds its pin — measured: removing the identity
    `DELETE` subquery's `graph_id` site from SQL and params reds that one leg
    and nothing else. `tests/test_record_writer_seam.py` pins more than text —
    its pre-#9187 plan fixture compares the full SQL AND params of 8 statements
    (the `node_identity`, `point`, `object` and `journal_event` INSERTs and a
    `point` UPDATE), so a drop inside any of those builders reds that fixture
    too. What escapes is a shape that appears in NEITHER plan: the
    `Source`-endpoint resolve (`EntityLinked` to a `Source`) is one, and review
    holds it.
    """
    for statement in plan:
        params = list(statement.params)
        bindings = _tenant_bindings(statement)
        assert bindings, (
            f"{statement.sql[:80]!r} names no tenant column at all — tenancy is "
            f"the row key of every layer table, so this statement is unkeyed"
        )
        # BOTH coordinates, not just the ones this statement happens to name: a
        # statement scoped on `graph_id` alone is unscoped across organisations,
        # and the per-site rule cannot see that (every site it found WAS
        # correct). This rule exists because the membership check this change
        # replaced had the property by accident — it demanded both values be
        # present — and a per-site rewrite of it would have lost it.
        named_columns = {column for _index, column in bindings}
        assert named_columns == {"org_id", "graph_id"}, (
            f"{statement.sql[:80]!r} scopes only {sorted(named_columns)} — the "
            f"row key is the PAIR, so a statement naming one coordinate is "
            f"scoped across the other axis"
        )
        for index, column in bindings:
            expected = tenant[0] if column == "org_id" else tenant[1]
            got = params[index] if index < len(params) else "<no such parameter>"
            assert got == expected, (
                f"{statement.sql[:80]!r} binds {column} to {got!r}, not "
                f"{expected!r} (site #{index}, params={params!r}) — a "
                f"statement can escape tenancy"
            )


def test_two_tenants_in_one_process_do_not_cross_write():
    """#9192: writing for A then for B never lets A's tenant reach B's rows.

    This is the leg that stops the regression, and the shape of the failure is
    why it is asserted exhaustively. A process that serves several tenant
    graphs and resolves its tenant from anything ambient (env, a module global,
    a ContextVar set at a pool thread's top level) files tenant A's rows under
    tenant B **without raising**: the row is well-formed and the FK to
    `public.graphs` only checks that the graph exists. Nothing downstream can
    detect it — so it is pinned wherever the pair crosses a boundary: at the
    seam, where a caller names it, and here, where the fold receives it.
    """
    plan_a = rp.replay_plan(_everything_records(), org_id=_TENANT_A[0],
                            graph_id=_TENANT_A[1])
    plan_b = rp.replay_plan(_everything_records(), org_id=_TENANT_B[0],
                            graph_id=_TENANT_B[1])

    # Non-vacuity: a plan that emitted nothing would satisfy "does not carry B".
    assert plan_a, "the record set materialized no statements — the leg is vacuous"
    # The statement SHAPES are tenant-independent, so tenancy is the only thing
    # that may differ between the two plans. A tenant baked into SQL text (which
    # no parameter check would see) shows up here.
    assert [s.sql for s in plan_a] == [s.sql for s in plan_b]

    for plan, tenant, other in ((plan_a, _TENANT_A, _TENANT_B),
                                (plan_b, _TENANT_B, _TENANT_A)):
        _assert_binds(plan, tenant)
        params = _every_param(plan)
        assert other[0] not in params, (
            f"{other[0]} leaked into the plan written for {tenant[0]}"
        )
        assert other[1] not in params, (
            f"{other[1]} leaked into the plan written for {tenant[1]}"
        )


def test_the_environment_cannot_name_the_tenant_of_a_call(
    monkeypatch: pytest.MonkeyPatch,
):
    """#9192 ask 2: an ambient carrier may exist, and may never outrank the call.

    Planted as a THIRD tenant and asserted over the WHOLE record set, which is
    the part that has power. A fallback confined to one path is invisible to a
    single-record leg: measured on this tree, every one of the plan's 73
    statements binds a tenant coordinate, so a fallback where the pair is read
    at the PLAN'S ENTRY misfiles all 73 — while a per-record read
    (`mutations_for`) reaches the 42 statements the mutations emit and leaves
    the 31 journal rows carrying the caller's pair. A leg that builds one
    record only ever sees the statements that record produces.

    Nothing is being forbidden here: the environment is allowed to carry a
    deployment's tenant (a single-tenant install has exactly one). What it may
    not do is answer a question the caller already answered, because on a
    multi-tenant process that one value is tenant B's while the call is A's.
    """
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", _TENANT_ENV[0])
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", _TENANT_ENV[1])

    plan = rp.replay_plan(_everything_records(), org_id=_TENANT_A[0],
                          graph_id=_TENANT_A[1])
    assert plan, "the record set materialized no statements — the leg is vacuous"
    _assert_binds(plan, _TENANT_A)
    params = _every_param(plan)
    assert _TENANT_ENV[0] not in params, (
        f"{_TENANT_ENV[0]} came from the environment into a plan written for "
        f"{_TENANT_A[0]} — an ambient tenant outranked the call"
    )
    assert _TENANT_ENV[1] not in params, (
        f"{_TENANT_ENV[1]} came from the environment into a plan written for "
        f"{_TENANT_A[1]} — an ambient tenant outranked the call"
    )


def test_the_tenancy_section_names_the_carrier_it_cannot_see():
    """The #9231 pointer, executable — the prose it pins has been lost TWICE.

    This is guidance, not a safety property, so it is pinned for a reason the
    review measured rather than for symmetry: the paragraph that tells the next
    lane where the ambient carrier lives was dropped by a rebase (twice — the
    `3752faf9` squash, then the rebase onto `41e958de8`), and each time the PR
    body went on reporting the correction as applied. A docstring cannot red a
    build, so a dropped pointer survives every leg here; nothing else in this
    file notices that the artifact a lane actually reads stopped naming the
    issue.

    WHAT THIS PINS, and what it deliberately does not: a rebase that drops the
    pointer again, and the framing the review found (`WHERE AN AMBIENT CARRIER
    WOULD ARRIVE` — a hypothesis — instead of one that is in flight). It does
    NOT pin the wording of the precedence rule or the symbol list; a reviewer
    rewriting that prose should stay green, which is why the assertions are a
    mention and a phrasing that the review called the defect, not a snapshot.

    Measured non-vacuity: at `bf32ebed` (the head the review found this on)
    `#9231` appears 0 times in `tortoise/record_projection.py` and the tenancy
    section still says `WOULD ARRIVE` — both assertions below red there, and
    only they do (118 of 118 other legs stay green). The fourth assertion (the
    `TORTOISE_RECORD_SINGLE_TENANT` gate) is red at `113b31af0`, the head the
    #9192 review found this prose on: it described the arming surface as the
    env pair alone.
    """
    src = Path(rp.__file__).read_text(encoding="utf-8")
    heading = "TENANCY IS A PER-CALL ARGUMENT AT THIS LAYER"
    assert heading in src, f"the tenancy section is gone from {rp.__file__}"
    section = src.split(heading, 1)[1]
    assert "#9231" in section, (
        "the tenancy section no longer names #9231, the ref carrying the "
        "ambient carrier (`record_writer.use_record_tenant`, `record_tenant()` "
        "and its env fallback) — a lane reading this module would be told the "
        "carrier is hypothetical and pointed at no issue"
    )
    assert "#9192" in section, (
        "the tenancy section no longer names #9192, the acceptance the arming "
        "pin belongs to"
    )
    # #9192 review P3: the prose above stopped being complete when the gate
    # landed — the pair is honoured only behind the single-tenant DECLARATION.
    # Prose cannot red a build, so this pins the one symbol that carries the
    # contract; measured red at `113b31af0` (the section named the pair alone).
    assert "TORTOISE_RECORD_SINGLE_TENANT" in section, (
        "the tenancy section still describes the arming surface as the env "
        "pair alone. Since #9192 the pair is honoured only when the deployment "
        "declared itself single-tenant via TORTOISE_RECORD_SINGLE_TENANT "
        "(undeclared => multi-tenant => the write is REFUSED, never filed): a "
        "lane reading this module would look for a fallback that no longer "
        "arms, and would not know which variable to set"
    )
    assert "WOULD ARRIVE" not in section, (
        "the tenancy section frames the ambient carrier as a hypothesis "
        "(`WHERE AN AMBIENT CARRIER WOULD ARRIVE`) while it is in flight in "
        "#9231 — the claim/artifact mismatch the #9214 review found"
    )


def test_this_module_has_no_ambient_tenant_READER():
    """The "no ambient tenant" rule, executable rather than prose.

    A docstring cannot fail a build, and a statement-level leg cannot see a read
    that never fires on any fixture path. Measured: a DORMANT env read
    (`if False: org_id = os.environ["TORTOISE_RECORD_ORG_ID"] or org_id`) leaves
    117 of 118 green and only this scan reds it.

    The scan is AST-shaped, not textual, so a comment or a docstring naming
    `ContextVar` (this file's own header does) is not a hit. It resolves `os`
    through its aliases (`import os as x` ⇒ `x.environ` is still a hit: an
    alias is the obvious way a reader would arrive) and covers
    `environ`/`environb`/`getenv`/`getenvb`, `from os import …`,
    `from os import *`, and the `contextvars` module. There is no legitimate env
    read in this module — the record DSN is read in `record_writer`, which owns
    it.

    WHERE THE MARGINAL VALUE IS, stated precisely because the value legs above
    are stronger than they look: an *active* env-first fallback IS caught by
    `test_the_environment_cannot_name_the_tenant_of_a_call` (it runs the whole
    plan — measured: a fallback at the fold reds that leg, the refusal leg, the
    seam leg and this scan). What no value-level leg can see is a read that is
    DORMANT on
    every fixture path — one that fires only when the pair is absent, or that
    sits in a branch the record set does not reach. That read is one wire away
    from cross-tenant on the process the rule has to hold on, and this scan is
    the only thing that reds it.

    ⛔ WHAT THIS SCAN CANNOT SEE, measured, because the gap is where the real
    carrier lives: it parses THIS module only, and it matches env and
    `contextvars` names. So a read through an import — a `ContextVar` (or a
    `use_record_tenant`-style getter) defined one layer up and called here — is
    caught by nothing in this file: measured, a sibling-module `ContextVar`
    imported and applied in `mutations_for`
    (`_t = RECORD_TENANT.get(None); if _t is not None: org_id = org_id or
    _t[0]`) leaves all 118 green, because the carrier is unset on every fixture
    path — so even a fold that would honour it is invisible to both layers. That is the shape #9192 names, and
    it is closed by the production owner of the carrier (#9188), not here.
    What this scan misses, and only this scan: an OBFUSCATED read
    (`getattr(os, "environ")`, `__import__("os")`) — an env-first read spelled
    that way still reds the value legs (measured, three of them); only a
    DORMANT spelling of it escapes every leg, like any dormant read. What
    escapes BOTH layers: a read that never fires on a fixture path — a
    module-level constant applied in a function BODY (`org_id = org_id or
    _DEFAULT_TENANT[0]`, signature still required), or a carrier DEFINED
    ELSEWHERE (measured, all 118 green on each). A signature default is NOT in
    either gap; the refusal leg catches it.
    """
    tree = ast.parse(Path(rp.__file__).read_text(encoding="utf-8"))
    ambient_modules = {"contextvars"}
    ambient_names = {"environ", "environb", "getenv", "getenvb"}
    os_aliases = {"os"}
    os_readers: set[str] = set()
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "os":
                    os_aliases.add(alias.asname or root)
                elif root in ambient_modules:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in ambient_modules:
                hits.append(node.module or "")
            if root == "os":
                for alias in node.names:
                    local = alias.asname or alias.name
                    if alias.name == "*":
                        # `from os import *` binds every name this scan looks
                        # for, including one this set does not list yet.
                        hits.append("from os import *")
                    elif alias.name in ambient_names:
                        hits.append(f"os.{alias.name}")
                        os_readers.add(local)

    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in ambient_names
                and isinstance(node.value, ast.Name)
                and node.value.id in os_aliases):
            hits.append(f"{node.value.id}.{node.attr}")
        elif (isinstance(node, ast.Name) and node.id in os_readers
                and isinstance(node.ctx, ast.Load)):
            hits.append(f"{node.id} (from os)")
    assert not hits, (
        f"tortoise/record_projection.py reads an ambient tenant carrier "
        f"({sorted(set(hits))}) — tenancy is a per-call argument here, and an "
        f"ambient value is one value for every tenant the process serves"
    )


def test_a_write_that_cannot_name_its_tenant_refuses_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
):
    """#9192 ask 2: an unnamed tenant is REFUSED, never inherited.

    The refusal is the whole safety property. A fallback is not a mitigation on
    the process this has to hold on: env (and any module-level default) is
    process-global, so ONE fallback value is tenant B's tenant while the process
    is serving tenant A. The entry points therefore take the pair as required
    keyword arguments, and the environment is never consulted — asserted with
    the pair present, since an absent env cannot distinguish "not read" from
    "read and empty".

    Companion to `test_the_environment_cannot_name_the_tenant_of_a_call`: this
    leg proves the pair cannot be OMITTED, that one proves a planted pair cannot
    SUBSTITUTE for one the caller supplied. Both are needed — this one is
    satisfied by any implementation with a required keyword, including one that
    would fall back the moment the argument is optional.

    ⛔ EACH COORDINATE ON ITS OWN, which is the part a "the call raises" check
    misses: a coordinate given a default still leaves the OTHER one required, so
    dropping the pair entirely raises and the leg stays green. Measured — a
    literal default (`org_id: str = "a-literal-default"`, no env read) left
    every other leg green and reddened only this one (117 of 118), which is what
    isolates the per-coordinate loop's value.
    """
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", _TENANT_ENV[0])
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", _TENANT_ENV[1])

    record = {"type": "PointAdded", "ts": "T", "id": "p1",
              "point": dict(_SNAPSHOT)}

    # Every entry point: an unnamed tenant is refused, not defaulted.
    with pytest.raises(TypeError):
        rp.mutations_for(record)
    with pytest.raises(TypeError):
        rp.replay_plan([record])
    with pytest.raises(TypeError):
        rp.replay([record], None)

    # …and each coordinate is required on its own, so neither can acquire a
    # default without this leg going red.
    for only in ({"org_id": _TENANT_A[0]}, {"graph_id": _TENANT_A[1]}):
        with pytest.raises(TypeError):
            rp.mutations_for(record, **only)
        with pytest.raises(TypeError):
            rp.replay_plan([record], **only)
        with pytest.raises(TypeError):
            rp.replay([record], None, **only)

    # And a NAMED tenant is the one that lands — the env pair is not read even
    # as a fallback for the coordinate the caller did supply.
    plan = rp.replay_plan([record], org_id=_TENANT_A[0], graph_id=_TENANT_A[1])
    assert _every_param(plan), "the plan bound no parameters — the leg is vacuous"
    params = _every_param(plan)
    assert _TENANT_ENV[0] not in params and _TENANT_ENV[1] not in params


def test_the_journal_payload_keeps_the_producer_fields_that_are_not_props():
    """A props strip must not silently truncate the record's own event stream.

    `ENVELOPE_KEYS` is ALSO the filter `_journal_row` applies to the DURABLE
    `public.journal_event.payload`, while the JSONL journal keeps `agent_id`
    (`sdk._journal_record_payload` subtracts `_JOURNAL_ENVELOPE_KEYS`, which
    does not name it). Listing `agent_id` in both sets is what makes it a props
    fix AND a payload loss — one stream keeps the field, the other does not, and
    nothing raises. `_RECORD_ONLY_KEYS` alone is the props strip; the payload
    must survive intact.
    """
    record = _point_added("pt_payload", None)
    record["agent_id"] = "agent-kept-in-the-payload"
    record["point"]["agent_id"] = "agent-kept-in-the-payload"

    plan = rp.replay_plan([record], org_id="o", graph_id="g")
    journal = next(s for s in plan if "public.journal_event" in s.sql)
    payload = json.loads(journal.params[4])

    # Non-vacuity: the position row really is this record's, and its payload is
    # not empty (`event_type` is its own column, so it is absent by design).
    assert journal.params[3] == "PointAdded"
    assert payload.get("point")
    assert payload.get("agent_id") == "agent-kept-in-the-payload", (
        "agent_id vanished from journal_event.payload while the JSONL journal "
        "keeps it — the two event streams now disagree on a producer field"
    )
    # ...while the NODE prop is still stripped (the fix this PR is about).
    upsert = next(s for s in plan if s.sql.startswith("INSERT INTO public.point"))
    assert "agent_id" not in _props_tail([upsert])


# ════════════════════════════════════════════════════════════════════════════
# #9553 review P2: the DB-level half of this guard must actually RUN in CI
# ════════════════════════════════════════════════════════════════════════════
#
# `supabase/tests/pglite/validate.mjs` replays the SHIPPED journal through THIS
# module's emitter against a real Postgres (PGlite) — it is the half of the
# #9553 guard that a DB-free suite cannot be. It ran only via `schema-drill`,
# which was gated on `migrations` alone, so a PROJECTION-only change (exactly
# this PR) never ran it: the assertion the PR itself names as its class-2
# evidence could not fire in CI on the change that produced it. The legs below
# execute the `changes` job's REAL gate script rather than scanning it, so a
# predicate that stops covering the harness is a RED, not a review note.

def _gate_outputs(changed: list[str], tmp_path: Path) -> dict[str, str]:
    """Run ci.yml's `changes` gate for a synthetic changed set.

    Lifted out of the step's `run:` exactly as
    `tests/test_website_functions_typecheck.py` does (the shell wrapper only
    feeds it a diff), so this never writes the shared `/tmp/ci-gates.py`.
    """
    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml")
                        .read_text(encoding="utf-8"))
    body = next(s for s in wf["jobs"]["changes"]["steps"]
                if s.get("id") == "gate")["run"]
    marker = "cat > /tmp/ci-gates.py <<'PY'\n"
    start = body.index(marker) + len(marker)
    script = body[start:body.index("\nPY\n", start)]

    src = tmp_path / "ci-gates.py"
    src.write_text(script, encoding="utf-8")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    # Inherit the environment (the script shells out to client/), but force the
    # two scalar inputs the `run:` reads from `env:` so a fleet run that set
    # MAIN_HEALTH cannot turn every gate off under us.
    env = {**os.environ, "MAIN_HEALTH": "", "BASE_SHA": ""}
    proc = subprocess.run(
        [sys.executable, str(src), str(out)],
        input="\n".join(changed), capture_output=True, text=True,
        cwd=str(REPO_ROOT), check=False,
        env=env,
    )
    assert proc.returncode == 0, f"the changes gate script failed:\n{proc.stderr}"
    return dict(line.split("=", 1)
                for line in out.read_text(encoding="utf-8").splitlines()
                if "=" in line)


def test_the_pglite_harness_runs_on_a_projection_only_change(tmp_path):
    """`schema-drill` must fire for the harness, the module it spawns and the fixture.

    Falsifier (measured on the pre-fix `if:`): with `schema-drill` gated on
    `migrations` alone, every case below left `pglite=false` AND
    `migrations=false` — the job was skipped on all three inputs the narrow
    predicate named (`supabase/tests/pglite/`, the module, the fixture), and a
    fourth input it read — the assertion suites under `supabase/tests/*.sql` —
    was skipped by the same gate, so
    `validate.mjs`'s #9553 leg could only ever fail where a human ran it.
    """
    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml")
                        .read_text(encoding="utf-8"))
    assert "pglite" in wf["jobs"]["changes"]["outputs"], (
        "the `changes` job never emits `pglite` — schema-drill's `if:` would "
        "read an empty string and the PGlite harness would run for ever on "
        "nothing but migration changes")
    assert "pglite" in wf["jobs"]["schema-drill"]["if"], (
        "schema-drill does not consume the pglite output, so the gate below "
        "measures nothing")

    # `supabase/tests/` is named WHOLE: `validate.mjs` reads
    # `TESTS_DIR = supabase/tests` for every entry of its explicit `suites`
    # list and for `C1_SUITE`, so an assertion suite is an input it reads —
    # and a `supabase/tests/*.sql`-only edit is neither under
    # `supabase/migrations/` nor under `supabase/tests/pglite/`, which is how
    # the narrower first entry left the only job that runs those suites
    # skipped.
    for path in ("supabase/tests/pglite/validate.mjs",
                 "supabase/tests/20261012000001_record_schema.sql",
                 "tortoise/record_projection.py",
                 "supabase/tests/fixtures/record_projection_journal.jsonl"):
        outs = _gate_outputs([path], tmp_path)
        assert outs.get("pglite") == "true", (
            f"{path} does not set pglite=true, so the PGlite harness never "
            f"runs on a change it reads: {outs}")
        # The distinct-surface half: a projection-only change is NOT a
        # migrations change, which is exactly why the old gate missed it.
        assert outs.get("migrations") == "false", (
            f"{path} was classified as a migrations change — the gate is not "
            f"measuring the input it names: {outs}")

    # ...and an unrelated path still turns it off (not a constant).
    assert _gate_outputs(["docs/README.md"], tmp_path).get("pglite") == "false"


def test_every_pglite_suite_entry_names_a_file_that_exists():
    """A stale `suites` entry silently blanks the harness's tail (#9707).

    `validate.mjs` reads each entry with `readFileSync` INSIDE the loop but
    OUTSIDE its `try`, so one name that does not resolve throws ENOENT
    uncaught: the process exits on a node stack trace, that suite never runs,
    the drill blocks BELOW the loop never execute, and every suite listed
    after the bad entry stops running with it.

    Falsifier (measured on the #9707 head `c142e3318`): its merge commit
    renumbered the MIGRATION to `20261023000002_…sql` and the four textual
    references with it, but the suite FILE on disk stayed
    `20261023000001_blog_author_backfill_atomic.sql` while the `suites` entry
    became `…0002_…`. 23 of 24 entries resolved; `schema-drill` died with
    `ENOENT … supabase/tests/20261023000002_blog_author_backfill_atomic.sql`
    and 16 suites after it never ran.

    A PATH assertion, deliberately — not a content grep. That is the one shape
    the defect is invisible to: `git grep blog_author_backfill_atomic` matches
    every textual reference, but the test file's own NAME is not text in any
    file, so the wrong path was the only hit the grep could not see.
    """
    harness = (REPO_ROOT / "supabase" / "tests" / "pglite" / "validate.mjs"
               ).read_text(encoding="utf-8")
    suite_list = re.search(r"^const suites = \[(.*?)^\];", harness, re.S | re.M)
    assert suite_list is not None, (
        "the explicit `suites` list is gone from validate.mjs — this test "
        "measures nothing (harness wiring moved, re-point it)")
    entries = re.findall(r"'([^']+\.sql)'", suite_list.group(1))
    assert entries, "the `suites` list parsed to nothing — the pin is vacuous"

    tests_dir = REPO_ROOT / "supabase" / "tests"
    missing = [e for e in entries if not (tests_dir / e).is_file()]
    assert not missing, (
        "the PGlite harness's explicit `suites` list names file(s) that do not "
        f"exist under {tests_dir}: {missing}. `readFileSync` runs outside the "
        "loop's `try`, so one stale name aborts the whole harness and silently "
        "skips every suite listed after it")

    # The fix has TWO halves: the name is right, and a future stale name fails
    # with a named one-line error instead of a node stack trace that reads as
    # "the harness is broken" rather than "this entry is stale".
    assert "const missingSuites = suites.filter" in harness, (
        "validate.mjs lost its fail-closed pre-flight over the `suites` list, "
        "so a future stale entry aborts the harness with an uncaught ENOENT "
        "and takes every later suite down with it")


# ════════════════════════════════════════════════════════════════════════════
# R5 / #9553 — class 3: the OPERATOR's own topology
# ════════════════════════════════════════════════════════════════════════════
# An operator's `IMPL`/`NAND`/`hasPart` edge, its reverse `INPUT`, and the
# canonical `mitigated_by` mirror exist ONLY as `_create_edges` Cypher driven
# from the `{op_type, inputs}` descriptor in the Point's snapshot. The record
# materialised the operator's `point` row and emitted NO `relation` row, so a
# JOURNAL rebuild restored the epistemic topology while a RECORD-sourced rebuild
# restored the claims and none of their structure — `compute_operator_weight`
# reads exactly these edges, so the loss is a belief change, not a cosmetic one.
#
# These legs pin the four things a row count cannot see: WHICH records may carry
# it, the mapping and its reverse, the two mitigation gates, and the endpoint
# LABEL resolution (an operator input may be any of four labels, #7902).

def _operator_records(record: dict[str, Any]) -> list[tuple[Any, ...]]:
    """`(rel_type, idx, src journal id, tgt journal id)` per emitted row."""
    out = []
    for statement in _relation_statements(record):
        params = statement.params
        # INSERT params: org, graph, rel_type, idx, then 12 per endpoint
        # (4 labels x org/graph/id); the journal id is the 3rd of each block.
        out.append((params[2], params[3], params[6], params[18]))
    return out


def _operator_added(descriptor, *, point_id: str = "op1", **snapshot) -> dict[str, Any]:
    point = {"id": point_id, "content": "c", "is_operator": True,
             "operator": descriptor, **snapshot}
    return {"type": "OperatorAdded", "ts": "2026-10-12T00:00:00Z",
            "id": point_id, "point": point}


def test_an_operator_lands_its_typed_edge_and_reverse_input():
    """`IMPL` + its reverse `INPUT`, one pair per input, idx carried."""
    rows = _operator_records(_operator_added(
        {"op_type": "IMPL", "inputs": ["p1", "p2"]}))
    assert rows == [
        ("IMPL", 0, "op1", "p1"),
        ("INPUT", 0, "p1", "op1"),
        ("IMPL", 1, "op1", "p2"),
        ("INPUT", 1, "p2", "op1"),
    ], rows


def test_the_operator_edges_land_after_the_point_identity():
    """Ordering is the fix: `relation`'s endpoints are composite FK children."""
    plan = rp.replay_plan(
        [_operator_added({"op_type": "NAND", "inputs": ["p1"]})],
        org_id="org-a", graph_id="g1")
    kinds = []
    for statement in plan:
        sql = statement.sql
        kinds.append("identity" if "public.node_identity" in sql else
                     "relation" if "public.relation" in sql else
                     "point" if "public.point" in sql else "journal")
    assert kinds == ["identity", "point", "relation", "relation", "journal"], kinds


def test_the_typed_edge_has_one_row_per_INPUT_not_one_per_operator():
    """Fan-out: two inputs are two edges, and a duplicate input is its own idx."""
    rows = _operator_records(_operator_added(
        {"op_type": "IMPL", "inputs": ["p1", "p1"]}))
    assert [r[0] for r in rows] == ["IMPL", "INPUT", "IMPL", "INPUT"], rows
    assert [r[1] for r in rows] == [0, 0, 1, 1], rows


def test_an_unknown_op_type_lands_only_the_reverse_input():
    """`_create_edges`: `rel_type` is None → `INPUT` only, never a guessed REL."""
    rows = _operator_records(_operator_added(
        {"op_type": "SOMETHING_NEW", "inputs": ["p1"]}))
    assert rows == [("INPUT", 0, "p1", "op1")], rows


def test_the_composition_spellings_all_land_hasPart():
    """Four `op_type` spellings → ONE predicate, exactly as the fold's map."""
    for op_type in ("composedOf", "decomposesInto", "contains", "wraps"):
        rows = _operator_records(_operator_added(
            {"op_type": op_type, "inputs": ["p1"]}))
        assert rows == [("hasPart", 0, "op1", "p1"),
                        ("INPUT", 0, "p1", "op1")], (op_type, rows)


def test_the_operator_rel_type_map_is_the_folds_own():
    """Parity pin: the mapping is restated, so pin it to `_create_edges`' source.

    The module is loaded STANDALONE by the PGlite harness, so the fold's literal
    cannot be imported. Each spelling and each target predicate is asserted to
    appear in the fold's own source: a predicate the fold adds without a row here
    makes the record poorer than the graph on that op_type, and a row the fold
    does not write makes it richer — both are rebuild-lane divergences.
    """
    import inspect

    from tortoise.projection.edges import _EdgeHandlers

    source = inspect.getsource(_EdgeHandlers._create_edges)
    for op_type, rel_type in rp._OPERATOR_REL_TYPES.items():
        assert f'"{op_type}": "{rel_type}"' in source, (op_type, rel_type)
    assert set(rp._OPERATOR_REL_TYPES.values()) == {"IMPL", "NAND", "hasPart"}
    # Non-vacuity: the fold's map is a LITERAL in the source we just read.
    assert '"NAND": "NAND"' in source and '"IMPL": "IMPL"' in source


def test_a_mitigation_lands_the_canonical_mitigated_by_mirror():
    """#5048 / #4937: `(m)-[:IMPL]->(op)` reaches the record WITH its mirror.

    `mitigate_operator` writes BOTH halves live and the `OperatorAdded` payload
    carries only the IMPL direction, so `compute_operator_weight` read one half
    after a rebuild and silently reverted `w_eff` to the undecayed base.
    """
    rows = _operator_records(_operator_added(
        {"op_type": "IMPL", "inputs": ["op1"]},
        point_id="mid", is_operator=False, mitigation_strength=0.4))
    assert rows == [("IMPL", 0, "mid", "op1"),
                    ("INPUT", 0, "op1", "mid"),
                    ("mitigated_by", 0, "op1", "mid")], rows


def test_the_mitigation_gates_are_the_folds_own():
    """Two gates, value-for-value: the identity AND a writable strength.

    Either alone mints an edge live never wrote: a descriptor on a payload with
    no `is_operator` flag is the EventAPI/extractor shape (every generic IMPL
    operator), and a descriptor with no usable strength is a dampening no writer
    asked for.
    """
    def rows_for(**snapshot):
        return [r[0] for r in _operator_records(_operator_added(
            {"op_type": "IMPL", "inputs": ["op1"]}, **snapshot))]

    assert "mitigated_by" in rows_for(is_operator=False, mitigation_strength=0.4)
    # `is_operator` ABSENT is not `False` — the polarity the fold states.
    assert "mitigated_by" not in rows_for(mitigation_strength=0.4)
    assert "mitigated_by" not in rows_for(is_operator=True, mitigation_strength=0.4)
    # The strength band IS the writer's accepted domain: an out-of-band, a
    # non-numeric, a container, a NaN and an absent value are all refusals.
    for bad in (1.5, -0.1, "0.4", None, [0.4], {"s": 0.4}, 10 ** 400):
        assert "mitigated_by" not in rows_for(
            is_operator=False, mitigation_strength=bad), bad
    # ...and `bool` is ACCEPTED on purpose: `mitigate_operator`'s range check
    # passes for `False`/`True` and the node stores it (measured live).
    assert "mitigated_by" in rows_for(is_operator=False, mitigation_strength=False)
    # A non-IMPL damping is not a mitigation: the fold pins WHICH relation.
    for op_type in ("NAND", "composedOf", "SOMETHING_NEW"):
        rows = [r[0] for r in _operator_records(_operator_added(
            {"op_type": op_type, "inputs": ["op1"]},
            is_operator=False, mitigation_strength=0.4))]
        assert "mitigated_by" not in rows, op_type


def test_the_mitigation_predicates_are_the_folds_own():
    """Parity pin for both restated predicates, over a value table."""
    from tortoise.projection.entities import (
        has_usable_mitigation_strength,
        is_non_operator_payload,
    )

    payloads = (
        {}, {"is_operator": False}, {"is_operator": True},
        {"is_operator": None}, {"is_operator": 0}, {"is_operator": "false"},
        {"mitigation_strength": 0.4}, {"mitigation_strength": 0},
        {"mitigation_strength": 1}, {"mitigation_strength": False},
        {"mitigation_strength": True}, {"mitigation_strength": 1.0000001},
        {"mitigation_strength": -0.0000001}, {"mitigation_strength": "0.4"},
        {"mitigation_strength": None}, {"mitigation_strength": [0.4]},
        {"mitigation_strength": {"v": 0.4}}, {"mitigation_strength": 10 ** 400},
        {"mitigation_strength": -(10 ** 400)},
        {"is_operator": False, "mitigation_strength": 0.4},
    )
    for payload in payloads:
        assert rp._is_non_operator_snapshot(payload) == \
            is_non_operator_payload(payload), payload
        assert rp._has_usable_mitigation_strength(payload) == \
            has_usable_mitigation_strength(payload), payload
    # Non-vacuity: the table exercises both verdicts for both predicates.
    assert any(rp._has_usable_mitigation_strength(p) for p in payloads)
    assert any(not rp._has_usable_mitigation_strength(p) for p in payloads)


def test_the_epistemic_label_order_is_the_folds_own():
    """The resolver's label order is the fold's `EPISTEMIC_LABELS`, restated."""
    from tortoise.live import EPISTEMIC_LABELS

    assert tuple(EPISTEMIC_LABELS) == rp._EPISTEMIC_LABELS, (
        rp._EPISTEMIC_LABELS, EPISTEMIC_LABELS)
    # Non-vacuity: all four labels are probed, each against its own table.
    sql = _relation_statements(_operator_added(
        {"op_type": "IMPL", "inputs": ["p1"]}))[0].sql
    for table in ("public.point", "public.subject", "public.object", "public.event"):
        assert table in sql, (table, sql)
    # The label is a COLUMN of the endpoint subselect, never a parameter: an
    # operator input's label is a fact the record looks up, not one it assumes.
    assert "src.node_label" in sql and "tgt.node_label" in sql, sql


def test_the_input_endpoint_resolves_each_labels_canonical_key():
    """`relation.src_key` is a `node_identity` FK: the key is each label's own.

    Writing the raw journal id for a Subject would address a phantom identity
    the hard-delete cascade can never reach — the defect
    `_endpoint_resolution` exists to prevent.

    ⚠️ The SELECTed key column is DERIVED from :data:`rp.NODE_TABLES` since
    #9881, because it used to be spelled here as `"node_key" if label in
    ("Point","Event") else "name"` — a second declaration of the key contract,
    which the `Object`/`Subject` → `id` move made wrong (the resolver wrote the
    row's NAME while `node_identity` held its `id`, and the FK refused every
    operator edge over a Subject). The fragments below are therefore one rule per
    label rather than two spellings.
    """
    sql = _relation_statements(_operator_added(
        {"op_type": "IMPL", "inputs": ["p1"]}))[0].sql
    for fragment in (
        # Point: keyed on `id` (== `node_key`, the schema's own pin), matched on
        # `node_key` — the fold's `s.id` probe is `_ENTITY_MATCH_COLUMNS`' call.
        "SELECT 'Point' AS node_label, id AS node_key, 0 AS pref "
        "FROM public.point WHERE org_id = %s AND graph_id = %s AND node_key = %s",
        # Subject/Object: matched on the graph id, keyed on `id` too — the name
        # is a mutable column since #9881, never an identity.
        "SELECT 'Subject' AS node_label, id AS node_key, 1 AS pref "
        "FROM public.subject WHERE org_id = %s AND graph_id = %s AND id = %s",
        "SELECT 'Object' AS node_label, id AS node_key, 2 AS pref "
        "FROM public.object WHERE org_id = %s AND graph_id = %s AND id = %s",
        # Event: keyed on `event_id` and matched on the SAME column — `event.id`
        # is nullable and the dominant producers leave it NULL
        # (`test_the_event_endpoint_matches_a_column_the_record_fills`).
        "SELECT 'Event' AS node_label, event_id AS node_key, 3 AS pref "
        "FROM public.event WHERE org_id = %s AND graph_id = %s AND node_key = %s",
        # First label wins; the whole probe is one derived table per endpoint.
        "ORDER BY pref LIMIT 1) src",
    ):
        assert fragment in sql, (fragment, sql)
    # Idempotent, and NOT an upsert: these rows carry no attribute to correct.
    assert "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, " \
        "tgt_label, tgt_key, idx) DO NOTHING" in sql, sql


def test_the_event_endpoint_matches_a_column_the_record_fills():
    """`event.id` is NULL for the dominant producers, so matching it loses the edge.

    `_upsert_event` keys the graph node `id == eventId` unconditionally
    (`props["id"] = eid`), so the fold's `s.id = $sid` probe MATCHES a recorded
    event whose producer emitted no `id` (`mining._make_event`'s nested
    `{eventId: …}`, `github_map.issue_to_event`/`pr_to_event`) and writes both
    edges. The record, matching `event.id`, matched no row at all and filed no
    `relation` row — R5 class 3, silently still open on the Event arm.

    The assertion is the INVARIANT rather than the fix's text: every column the
    resolver matches on must be a column the record's own Event insert FILLS for
    this producer shape. It fails on the pre-fix code (`id` is not written at
    all) and stays honest if the resolver's spelling changes.
    """
    records = [
        # The dominant shape: `eventId` nested under `event`, NO `id` — the fold
        # reads `inner = event.get("event", event)`, and so does the record.
        {"type": "EventRecorded", "ts": "2026-10-23T00:00:01Z",
         "event": {"eventId": "ev1", "eventKind": "decision"}},
        _operator_added({"op_type": "IMPL", "inputs": ["ev1"]}),
    ]
    plan = rp.replay_plan(records, org_id="org-a", graph_id="g1")
    event_insert = next(
        s for s in plan if s.sql.startswith("INSERT INTO public.event "))
    written = set(re.search(r"\(([^)]*)\)", event_insert.sql).group(1).split(", "))
    assert "id" not in written, (written, event_insert.sql)
    assert {"node_key", "event_id"} <= written, written

    relation_sql = _relation_statements(records[1])[0].sql
    # Just the Event subselect: from its `SELECT 'Event'` to the derived table's
    # own close (`) epistemic`), so no neighbouring label's probe leaks in.
    probe = relation_sql.split("SELECT 'Event'")[1].split(") epistemic")[0]
    matched = set(re.findall(r"AND (\w+) = %s", probe))
    assert matched, probe
    # Every column the probe compares the journal id against is one this shape
    # POPULATES — otherwise the subselect returns no row and the endpoint is
    # `absent` for a node the graph wired.
    assert matched <= written, (matched, written, probe)


def test_a_non_string_or_unwritable_input_lands_no_relation():
    """The fold's own member gate: non-str / empty / NUL / lone surrogate.

    The refusing member comes FIRST on purpose: an implementation that filtered
    the list and THEN enumerated would pass when the bad member trails the good
    one, so only this order proves the fold's `continue`-inside-`enumerate`
    numbering (the survivor keeps idx **1**).
    """
    for bad in (7, None, "", "a\x00b", "\ud800", ["a"], {"a": 1}):
        rows = _operator_records(_operator_added(
            {"op_type": "IMPL", "inputs": [bad, "p1"]}))
        assert rows == [("IMPL", 1, "op1", "p1"),
                        ("INPUT", 1, "p1", "op1")], (bad, rows)


def test_a_blank_input_IS_carried_because_the_folds_probe_scans():
    """Round-2 P2: the operator gate is `_writable_id`, NOT the blank refusal.

    `_writable_container_id` refuses a whitespace-only key because
    `_link_session` addresses the container with an INLINE property MATCH that
    an index answers, and FalkorDB's indexed equality filter misses a blank
    string (#9599). This family's probe is a label-disjunctive `WHERE` — a SCAN
    — so the fold really does mint the stub and land BOTH edges for a blank key
    (measured through the fold on a live engine, review round 2). A blank input
    that resolves no record row still lands nothing here (the `absent` verdict),
    but the GATE must not refuse it, or the record is poorer than the graph on
    the edge this change exists to carry.
    """
    rows = _operator_records(_operator_added(
        {"op_type": "IMPL", "inputs": ["   "]}))
    assert rows == [("IMPL", 0, "op1", "   "),
                    ("INPUT", 0, "   ", "op1")], rows
    # Non-vacuity: the neighbouring families still refuse blank — the rule is
    # per family, not one global loosening.
    record = {"type": "PointAdded", "ts": "t", "id": "p1",
              "contains_session": "   ",
              "point": {"id": "p1", "content": "c"}}
    assert not [s for s in _relation_statements(record)
                if s.params[2] == "CONTAINS"], "CONTAINS must still refuse blank"


def test_the_operator_input_gate_is_the_folds_own_rule():
    """Parity pin for the restated `projection._writable_id`."""
    from tortoise.projection import _writable_container_id, _writable_id

    values = ("p1", "   ", "\t\n", "", None, 7, ["p1"], "a\x00b", "\ud800",
              "x" * 40, " " * 40, "ok")
    for value in values:
        assert rp._writable_epistemic_id(value) == _writable_id(value), value
    # Non-vacuity: the two predicates are NOT the same rule, and the difference
    # is exactly the blank case this change turns on.
    assert any(rp._writable_epistemic_id(v) != _writable_container_id(v)
               for v in values)
    assert rp._writable_epistemic_id("   ") is True
    assert _writable_container_id("   ") is False


def test_only_the_point_materialisations_may_carry_operator_relations():
    """The four `_upsert` arms — and `apply()`'s one synthesis.

    `apply()` applies `_promotion_point_with_operator` to the
    `OperatorPromoted` arm ONLY, so a FLAT `PointPromoted` snapshot reaches
    `_create_edges` with no `operator` key and the fold writes no edge. Reading
    `op_type`/`inputs` there would mint a row the graph never wrote.
    """
    from tortoise.projection import _JOURNAL_POINT_MATERIALIZATION_TYPES

    declared = {type_of for type_of, _how in rp._OPERATOR_RELATION_RECORDS}
    assert declared == set(_JOURNAL_POINT_MATERIALIZATION_TYPES), (
        sorted(declared), sorted(_JOURNAL_POINT_MATERIALIZATION_TYPES))

    nested = {"op_type": "IMPL", "inputs": ["p1"]}
    for record_type in ("PointAdded", "OperatorAdded", "PointPromoted"):
        record = {"type": record_type, "ts": "t", "id": "op1",
                  "point": {"id": "op1", "is_operator": True,
                            "operator": nested}}
        assert _operator_records(record), record_type
    # The synthesized arm: FLAT props, no nested descriptor.
    flat = {"type": "OperatorPromoted", "ts": "t", "id": "op1",
            "point": {"id": "op1", "is_operator": True,
                      "op_type": "NAND", "inputs": ["p1"]}}
    assert _operator_records(flat) == [("NAND", 0, "op1", "p1"),
                                       ("INPUT", 0, "p1", "op1")]
    # A FLAT `PointPromoted` snapshot is NOT synthesized by any fold arm.
    not_promoted = {**flat, "type": "PointPromoted"}
    assert _operator_records(not_promoted) == []
    # A non-dict descriptor degrades to no edges in the fold, and to no rows here.
    # `None` included on the SYNTHESIZED arm too: the synthesis gate is KEY
    # PRESENCE (`_promotion_point_with_operator`: `"operator" in p` → no
    # synthesis) and `_upsert_point_edges` only calls `_create_edges` on a
    # TRUTHY descriptor, so `{"operator": None, "is_operator": True,
    # "op_type": …}` is edge-less on both fold halves — reading it as "absent"
    # here would emit a typed edge the graph never wrote.
    for bad in ("IMPL", 7, ["IMPL"], None):
        for record_type in ("OperatorAdded", "OperatorPromoted"):
            record = {"type": record_type, "ts": "t", "id": "op1",
                      "point": {"id": "op1", "is_operator": True,
                                "op_type": "IMPL", "inputs": ["p1"],
                                "operator": bad}}
            assert _operator_records(record) == [], (bad, record_type)
    # A custom `Mapping` is not a `dict`, and the fold refuses anything that is
    # not a `dict` (`isinstance(op, dict)`), so the record must not accept one.
    class _NotADict(Mapping):
        def __init__(self, inner):
            self._inner = inner

        def __getitem__(self, key):
            return self._inner[key]

        def __iter__(self):
            return iter(self._inner)

        def __len__(self):
            return len(self._inner)

    mapping = _NotADict({"op_type": "IMPL", "inputs": ["p1"]})
    # Asserted on the READER, not through `mutations_for`: a non-`dict` value in
    # the point snapshot is refused earlier, by the props serialiser, so routing
    # this through the record would test the JSON encoder instead of the gate.
    assert rp._operator_descriptor(
        "OperatorAdded", {"is_operator": True, "operator": mapping}) is None
    assert rp._operator_descriptor(
        "OperatorAdded", {"is_operator": True,
                          "operator": dict(mapping)}) == {
        "op_type": "IMPL", "inputs": ["p1"]}


def test_an_unhashable_op_type_is_a_refusal_not_a_crash():
    """A corrupt record must not abort the write — `mutations_for` is TOTAL.

    `_OPERATOR_REL_TYPES.get({"a": 1})` raises `TypeError: unhashable type`; the
    fold raises on the same payload, and a record write may not, because the
    journal append has already committed. The verdict is the fold's unknown-op
    branch: no typed edge, and the reverse `INPUT` only.
    """
    rows = _operator_records(_operator_added(
        {"op_type": {"a": 1}, "inputs": ["p1"]}))
    assert rows == [("INPUT", 0, "p1", "op1")], rows


def test_a_record_that_never_reaches_the_edge_writer_lands_no_operator_relation():
    """`PointRevised`-class records never call `_upsert_point_edges`."""
    for record_type in ("PointRevised", "DedupeRecorded", "DedupeRejected",
                        "BatchIdStamped", "PointInvalidated", "PointSuperseded"):
        record = {"type": record_type, "ts": "t", "id": "op1",
                  "point": {"id": "op1", "is_operator": True,
                            "operator": {"op_type": "IMPL", "inputs": ["p1"]}}}
        assert _operator_records(record) == [], record_type
