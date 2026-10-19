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
   path is only visible here (one that fires reds both layers). A caller-level
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


def _everything_records() -> list[dict[str, Any]]:
    """One record per declared type — every type but the declared refusal
    `PointsMerged`, which has its own leg — plus the `delete_only` leg of a
    repoint. Five of the 30 carry `NoRecordEffect` (`EntityBindingRefused`,
    `ConfidenceChanged`, `EdgeMessageChanged`, `IngestStarted`,
    `CalibrationRecorded`): they contribute only their `journal_event` position
    row, which `test_each_declared_type_materializes_or_declares_a_no_effect`
    is the leg for.

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
    # assertions below stay GREEN for either drop — measured. Drop the OUTER
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

    `PointAdded` from `TortoiseSDK.capture_session` carries NO envelope `id` —
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
#: pattern that only matched the canonical spelling would not skip it — the
#: mention/binding count check below would fire on a legitimate site, which is a
#: false red with a message that blames the wrong thing.
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
        # leads every layer INSERT; a swap here is a cross-tenant row that no
        # parameter VALUE check can see. Asserting the ORDER (rather than
        # filtering whichever of the two leads) keeps a reordered list from
        # silently falling out of the check.
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
      resolves. A swap in one of those subqueries resolves another tenant's
      rows while every VALUE is still one of the two the check looks for, so
      only a positional, per-site rule can see it.
    * **THE COLUMN LIST OF AN INSERT.** A layer INSERT binds the pair by column
      position, not by `= %s`, so a swapped `(graph_id, org_id, …)` is a
      cross-tenant row that no `= %s` site and no parameter-value check can
      see — which is why the column order is asserted separately.

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
    statements binds a tenant coordinate, so a fallback at the fold misfiles all
    73 of them, while a leg that builds one record only ever sees the statements
    that record produces.

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


def test_this_module_has_no_ambient_tenant_READER():
    """The "no ambient tenant" rule, executable rather than prose.

    A docstring cannot fail a build, and a statement-level leg cannot see a read
    that never fires on any fixture path. Measured: a DORMANT env read
    (`if False: org_id = os.environ["TORTOISE_RECORD_ORG_ID"] or org_id`) leaves
    116 of 117 green and only this scan reds it.

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
    _t[0]`) leaves all 117 green, because the carrier is unset on every fixture
    path — so even a fold that would honour it is invisible to both layers. That is the shape #9192 names, and
    it is closed by the production owner of the carrier (#9188), not here.
    Also not covered, deliberately: an obfuscated read (`getattr(os,
    "environ")`, `__import__("os")`) and a module-level constant applied in a
    function BODY (`org_id = org_id or _DEFAULT_TENANT[0]`, signature still
    required) — measured, that body-local shape leaves all 117 green too. A
    signature default is NOT in either gap; the refusal leg catches it.
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
    every other leg green and reddened only this one (116 of 117), which is what
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
