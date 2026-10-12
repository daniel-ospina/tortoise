"""The record projection's contract, made executable (#7969, epic #7879).

WHAT THIS PINS. Interface A — *"journal → Postgres, replayable and idempotent"* —
is four claims, and each one is a test here rather than a sentence in a design
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

WHY THE PLACEMENT TABLE IS IMPORTED, NOT RE-DECLARED. `#7968`'s
`tests/test_record_schema_placement.py` already parses §17.5 and resolves its
eight rows (`RESOLUTIONS`); a second parser here would be a second declaration
of the placement, and the second declaration is the one that goes stale.

NO DATABASE IS NEEDED. The DB-level half of S1 is the PGlite harness, whose
subject is the same statement set this module emits.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

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
    "EntityBindingRefused": {"id": "p1", "point": dict(_SNAPSHOT)},
    "BatchIdStamped": {"id": "p1", "point": dict(_SNAPSHOT)},
    "PointRevised": {"id": "p1", "new_content": "c2"},
    "OperatorAnnotated": {"id": "p1", "annotator_bias": 0.5},
    "PointRetracted": {"id": "p1"},
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
    "ObjectRegistered": {"id": "o1", "name": "n1", "objectKind": "document"},
    "ObjectSuperseded": {"id": "o1", "name": "n1", "supersedes_by": "o2"},
    "SubjectAdded": {"id": "s1", "name": "sub1", "subjectKind": "other"},
    "EventRecorded": {"id": "e1", "eventId": "e1", "eventKind": "x"},
    "SourceCreated": {"id": "u1", "url": "u1", "sourceKind": "web"},
    "SourceVersioned": {"id": "u1", "url": "u1", "contentHash": "h2",
                        "previousContentHash": "h1", "previousValidTo": "t0"},
    "DocumentCreated": {"id": "u1", "url": "u1"},
    "SessionRecorded": {"id": "sess1", "turn_count": 2, "is_episodic": True},
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
        "EntityMutated", "PointsMerged",
    }


@pytest.mark.parametrize("record_type", sorted(_MINIMAL_RECORDS))
def test_each_declared_type_materializes_or_declares_a_no_effect(record_type):
    record = {"type": record_type, "ts": "2026-10-12T00:00:00Z",
              **_MINIMAL_RECORDS[record_type]}
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    effect = rp.RECORD_MUTATIONS[record_type].effect
    if isinstance(effect, rp.NoRecordEffect):
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

    with pytest.raises(rp.RefusedJournalOperation) as excinfo:
        rp.mutations_for({**base, "op": "rename", "name": "n2"},
                         org_id="o", graph_id="g")
    assert "#3377" in str(excinfo.value)

    with pytest.raises(rp.RecordProjectionError):
        rp.mutations_for({**base, "op": "levitate"}, org_id="o", graph_id="g")


def test_points_merged_is_refused_not_approximated():
    with pytest.raises(rp.RefusedJournalOperation) as excinfo:
        rp.mutations_for(
            {"type": "PointsMerged", "ts": "T", "op": "merge", "keep_id": "p1",
             "merge_ids": ["p2"]},
            org_id="o", graph_id="g",
        )
    assert "REFUSED" in str(excinfo.value)


# ════════════════════════════════════════════════════════════════════════════
# 2. IDEMPOTENCE — structural
# ════════════════════════════════════════════════════════════════════════════


def _plan_for_everything() -> list[rp.Statement]:
    records = [
        {"type": t, "ts": "2026-10-12T00:00:00Z", **payload}
        for t, payload in _MINIMAL_RECORDS.items()
    ]
    records.append({"type": "EntityMutated", "ts": "T", "id": "p1",
                    "label": "Point", "op": "restatus",
                    "state": {"status": "live"}})
    return rp.replay_plan(records, org_id="o", graph_id="g")


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
    S5. `Object.props` refuses the name by CHECK; `Subject.props` does NOT
    (filed as a schema gap in the PR body), so the projection carries the
    refusal on its own side rather than relying on the backstop.

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
    assert identity.sql.startswith("DELETE FROM public.node_identity")
    assert "node_key IN (SELECT node_key FROM public.point" in identity.sql
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
    assert insert.sql.startswith("INSERT INTO public.relation"), insert.sql
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
    """
    found: set[str] = set()
    for path in sorted((REPO_ROOT / "tortoise").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(
            r'(?:_emit_event|_emit)\(\s*"([A-Z][A-Za-z0-9_]*)"', text))
        constants = dict(re.findall(
            r'([A-Za-z_][A-Za-z0-9_]*_RECORD_TYPE)\s*=\s*"([A-Z][A-Za-z0-9_]*)"',
            text))
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
