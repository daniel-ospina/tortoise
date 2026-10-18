"""Interface A — the record projection: journal → Postgres, total and idempotent.

WHY THIS MODULE EXISTS
======================
`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) makes
**Postgres the system of record for every layer** and FalkorDB a **derived hot
cache**. §3 states the invariant that makes that safe:

    derived tables  =  replay(the journal)

`tortoise/projection/` already *is* a replay of the journal — into FalkorDB.
It is therefore the **derived cache's** fold, and a cache rebuilt from a cache
proves nothing. This module is the **record's own** fold (Interface A, epic
`#7879` item 3, issue **#7969**): the same journal, replayed into Postgres.

The issue states four properties, and each is a property of this module rather
than an aspiration:

1. **Every journal event type has a defined Postgres mutation.** Every record
   type in :data:`RECORD_MUTATIONS` carries one — a node materialization, a
   targeted patch, a delete, a relation upsert, or an explicit
   :class:`NoRecordEffect` **with a written reason**.
2. **Idempotent** — a second replay of the same journal produces the same
   record. Every mutation is written so that re-applying it is a no-op: node
   upserts are `ON CONFLICT … DO UPDATE SET` (same values ⇒ same row), patches
   are `UPDATE … SET` to absolute values, deletes are `DELETE`. The journal
   position is *also* recorded (`public.journal_event`, keyed on
   `(org_id, graph_id, seq)`) so the record can say which records it consumed.
3. **Total** — an event with no handler is a **failure, not a skip**. This is
   `#5285`'s rule, applied to the record's destination. `#3585`'s non-folded
   collector made the *graph* fold fail closed; this module is the record's
   equivalent, and it fails at the same place for the same reason: **two
   equally incomplete projections compare equal**, so a fold that silently
   skips an event manufactures a vacuous green.
4. **A field is PRIMARY iff the journal carries it** (§3's per-write test).
   The projection is where that rule becomes executable, so the property→column
   decision is **read from the schema on disk**, never restated here: a property
   with a column is written, a property the schema refuses (the EP-scalar
   carve-out) is **never** written, and a property with neither a column nor a
   carve-out lands in the table's declared `props` tail. A DERIVED value can
   therefore not become authoritative through this door — the schema's
   `chk_*_props_no_derived` CHECK is the backstop, and this module does not rely
   on it being the first line.

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the write chokepoint.** §2.3/Interface C is `#7951` — one live path that
  appends the journal, applies the record projection and applies the cache
  projection. This module is the *second* step of that path; it does not route
  live writes.
* **Not the cache projection.** Record → FalkorDB is `#7971` (Interface B).
* **Not a second declaration of the placement table.** `#7968`'s
  `tests/test_record_schema_placement.py` pins §17.5's table to the SQL; this
  module consumes the SQL.
* **No migration-safety apparatus.** Owner scope ruling 2026-10-10: the only
  customer is the dogfood instance. No dual-write, backfill, cutover or
  rollback-parity surface appears here, and none should be added.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "JOURNAL_EVENT_TYPES",
    "RECORD_MUTATIONS",
    "THE_RECORD_TABLES",
    "RecordProjectionError",
    "RefusedJournalOperation",
    "ReplayReport",
    "UnknownJournalRecordType",
    "mutations_for",
    "refused_props_names",
    "replay",
    "replay_plan",
    "snake",
    "table_columns",
    "to_dollar_placeholders",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

RECORD_MIGRATIONS: tuple[str, ...] = (
    "20261012000001_record_event_stream.sql",
    "20261012000002_record_semantic.sql",
    "20261012000003_record_epistemic.sql",
    "20261012000004_record_procedural.sql",
    # A FORWARD migration re-declaring a refusal. Migrations are append-only
    # history, so a later refusal (`subject.props` gains `status`, #8263)
    # arrives as `ALTER TABLE … ADD CONSTRAINT` — and the schema read below
    # must see it, or the record projection would emit a props tail the
    # constraint refuses and hand a caller a crash instead of a drop.
    "20261014000001_record_subject_props_no_derived.sql",
    # A FORWARD migration adding COLUMNS (`ALTER TABLE public.point ADD COLUMN`,
    # #2782's money value). Same reason: without it `table_columns()` reports
    # the pre-ALTER schema, so `_split_properties` would leave an amount in the
    # `props` tail and `cache_projection` would not project it.
    "20261018000001_record_point_money_value_2782.sql",
)

#: The record's own tables (the procedural layer is views over these).
THE_RECORD_TABLES: tuple[str, ...] = (
    "node_identity",
    "journal_event",
    "source",
    "event",
    "session",
    "object",
    "subject",
    "point",
    "relation",
)

#: The per-layer node tables, and the canonical key column each one pins
#: `node_key` to (W1/Interface E: `(label, key)`, never a bare id).
NODE_TABLES: dict[str, tuple[str, str]] = {
    # table: (node_label, key column)
    "source": ("Source", "url"),
    "event": ("Event", "event_id"),
    "session": ("Session", "id"),
    "object": ("Object", "name"),
    "subject": ("Subject", "name"),
    "point": ("Point", "id"),
}

#: node label → the record table that materializes it (the inverse of
#: :data:`NODE_TABLES`). An edge endpoint names its label, and the relation's
#: `(label, key)` FK is into `node_identity`, so the endpoint's key must be the
#: label's CANONICAL key (`Object`/`Subject`→`name`, `Source`→`url`,
#: `Point`/`Session`→`id`, `Event`→`event_id`), resolved from the projection's
#: own node row — never the raw journal id.
_LABEL_TABLES: dict[str, str] = {
    label: table for table, (label, _key_column) in NODE_TABLES.items()
}

#: The columns an `EntityMutated` record's identity (`payload["id"]`) is matched
#: against, per node table. The graph fold matches the label's OR-set identity
#: (`_ENTITY_ID_PROP` + `_CANONICAL_ENTITY_SECONDARY_ID_PROPS`), NOT the table's
#: `node_key` column: a Point/Session/Event keys `node_key` on its id/eventId,
#: but an Object/Subject keys `node_key` on `name` (its record identity is the
#: `id` column) and a Source keys `node_key` on `url` while its record identity
#: is usually `id`. Matching `node_key` against the record's `id` silently
#: touches 0 rows.
_ENTITY_MATCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "point": ("node_key",),
    "session": ("node_key",),
    "event": ("node_key",),
    "object": ("id",),
    "subject": ("id",),
    "source": ("id", "node_key"),
}

#: ``_emit_event`` owns these envelope keys; they are never node properties.
ENVELOPE_KEYS: frozenset[str] = frozenset({
    "type", "ts", "event_id", "initiated_by", "projection_version", "seq",
})

#: The journal's record-type vocabulary. Sourced from the graph fold's own
#: dispatch (`tortoise/projection/`) and the producers' emit sites, and pinned
#: by `tests/test_record_projection.py` against both. A type that appears in a
#: producer but not here is exactly the silent-skip defect totality forbids.
JOURNAL_EVENT_TYPES: frozenset[str] = frozenset({
    "PointAdded",
    "OperatorAdded",
    "PointPromoted",
    "OperatorPromoted",
    "PointRevised",
    "PointRetracted",
    "PointSuperseded",
    "PointInvalidated",
    "PointsMerged",
    "OperatorAnnotated",
    "ConfidenceChanged",
    "EdgeMessageChanged",
    "EntityMutated",
    "ObjectRegistered",
    "ObjectSuperseded",
    "SubjectAdded",
    "EventRecorded",
    "SourceCreated",
    "SourceVersioned",
    "DocumentCreated",
    "SessionRecorded",
    "SessionDeleted",
    "EntityLinked",
    "DirectEdgeCreated",
    "DirectEdgeRepoint",
    "BatchIdStamped",
    "DedupeRecorded",
    "DedupeRejected",
    "EntityBindingRefused",
    "IngestStarted",
    "CalibrationRecorded",
})


class RecordProjectionError(Exception):
    """Base class — a record projection failure is loud, never a warning."""


class UnknownJournalRecordType(RecordProjectionError):
    """Interface A's *total* clause: an event with no handler is a FAILURE.

    Raised for a record type this projection does not know at all. `#5285`
    asserts `rebuild == live` for the graph; the record's half is that a
    replay which cannot account for every record it consumed cannot claim to
    be the record.
    """


class RefusedJournalOperation(RecordProjectionError):
    """A record this projection knows but will not silently materialize.

    Distinct from :class:`UnknownJournalRecordType` on purpose: a **declared**
    refusal (e.g. an `EntityMutated` op no writer produces yet) is a different
    fact from an **undeclared** type, and collapsing the two would hide which
    one a new producer tripped.
    """


# ════════════════════════════════════════════════════════════════════════════
# The mutation vocabulary. One small algebra, declared per record type.
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Node:
    """A node table, its canonical label, and the column holding the node key."""

    table: str
    label: str
    key_column: str


@dataclass(frozen=True)
class NodeSnapshot:
    """Materialize (upsert) a node row from a property dict.

    ``path`` names the sub-dict that carries the node's properties (`"point"`
    for the snapshot-carrying record types); ``None`` means the record's own
    flat payload *is* the property dict. ``key_from`` names the payload field
    that holds the canonical key when it is not itself inside the snapshot.
    """

    node: Node
    path: str | None
    key_from: str
    drop: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NodePatch:
    """Apply absolute values to an EXISTING row (never creates one).

    A patch on a row the record does not hold is a 0-row no-op — the same
    verdict the graph fold reaches for a revision of an unknown id, and the
    reason a patch must not be an upsert: registering a node from a *mutation*
    record would materialize a row the journal never created.
    """

    node: Node
    key_from: str
    literals: tuple[tuple[str, Any], ...] = ()
    #: Property names taken verbatim from the flat payload. ``None`` means
    #: "every flat payload key that is not record-only" (the `PointRevised`
    #: extras / the `annotator_*` dims); an empty set means "none".
    from_payload: frozenset[str] | None = None
    #: ``(column, payload key)`` renames when the journal's key differs.
    renames: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NodeDelete:
    """Remove an existing row (a re-delete is a 0-row no-op).

    ``companion_deletes`` names rows the same record removes beside the node
    itself — the record's half of the graph's ``DETACH DELETE``. The fold's
    ``SessionDeleted`` removes the session's agentSession ``:Source`` stub
    (``url = session:{id}``) in the SAME step, so the record must too or the
    note claiming it would be describing behaviour the code does not
    implement. The template's ``{key}`` is the deleted node's key.
    """

    node: Node
    key_from: str
    companion_deletes: tuple[tuple[Node, str], ...] = ()


@dataclass(frozen=True)
class RelationUpsert:
    """One row per edge, per `ONTOLOGY.md` §8's reification rule."""

    rel_type_from: str = "edge_type"
    src_from: str = "src"
    tgt_from: str = "tgt"
    attrs: tuple[str, ...] = ("direction", "confidence", "weight", "label",
                              "batch_id", "source_version")


@dataclass(frozen=True)
class RelationDelete:
    """Remove a relation row (the `delete_only` leg of a repoint)."""

    rel_type_from: str = "edge_type"
    src_from: str = "src"
    tgt_from: str = "tgt"


@dataclass(frozen=True)
class NoRecordEffect:
    """A record type that legitimately produces NO record row.

    ``reason`` is mandatory and is the whole point: a declared no-op is a
    decision a reviewer can check, while an undeclared one is the silent skip
    Interface A forbids. Every entry here must be justified by the *record's*
    semantics — not by "we did not get to it".
    """

    reason: str


@dataclass(frozen=True)
class OpDispatch:
    """`EntityMutated` — one record type with an ``op`` discriminator.

    `#3299` chose one record type over one-per-label×operation; the replay
    dispatches on ``op``. Ops not produced by any writer are declared and
    REFUSED rather than guessed (`#3377`/`#4769`).

    ``never_store`` is the record's own half of the §2.6 split: `Object.status`
    and `Subject.status` are **projection-owned caches** (the fold's docstring),
    and BOTH are refused by the schema's props CHECK (`Object` from #8179,
    `Subject` from the forward migration 20261014000001/#8263) — so the
    projection must not make either authoritative through the props tail. This
    set is the mutation path's second line; the snapshot path is covered by
    `refused_props_names()`, which reads the same CHECKs from disk.
    `Point.status` is NOT in this set: a Point's lifecycle IS journaled
    (`PointRetracted`/`…Superseded`) and `point.status` is a PRIMARY column.
    """

    table_for_label: Mapping[str, str]
    patch_ops: frozenset[str]
    delete_ops: frozenset[str]
    refused_ops: Mapping[str, str]
    never_store: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Mutation:
    """The declared mutation for one record type (a documented union)."""

    effect: (
        NodeSnapshot | NodePatch | NodeDelete
        | RelationUpsert | RelationDelete | OpDispatch | NoRecordEffect
    )
    note: str = ""


#: **The registry.** Every member of :data:`JOURNAL_EVENT_TYPES` has an entry;
#: `tests/test_record_projection.py` asserts that in both directions, so a new
#: producer fails this suite rather than disappearing at replay time.
RECORD_MUTATIONS: dict[str, Mutation] = {
    # ── Epistemic layer: Points and operator Points ──────────────────────────
    # The `…Added` / `…Promoted` / dedup record types all carry a FULL point
    # snapshot (`payload["point"]`), which is what the graph fold indexes too
    # (`_apply_one`'s `points[p["id"]] = p`), so the record projects the same
    # dict the fold does. `id` rides the envelope, not the snapshot.
    "PointAdded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
        note="full snapshot under `point`; identity keys are record-only and "
             "are dropped by `drop`",
    ),
    "OperatorAdded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "PointPromoted": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "OperatorPromoted": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "DedupeRecorded": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
        note="#784 — the payload is the point snapshot the audit inspected",
    ),
    "DedupeRejected": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    "EntityBindingRefused": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    # #902/A10: batch_id (+ content_hash / mitigates_operator_id /
    # mitigation_strength) restore, replayed from the batch snapshot.
    "BatchIdStamped": Mutation(
        NodeSnapshot(Node("point", "Point", "id"), path="point", key_from="id"),
    ),
    # ⚠️ `new_content` is the journal's own name for the revised `content`; it
    # is NOT a node property of that name, so it is renamed rather than written
    # into `props`. The fold writes `p["content"] = new_content`.
    "PointRevised": Mutation(
        NodePatch(
            Node("point", "Point", "id"),
            key_from="id",
            renames=(("content", "new_content"),),
        ),
        note="extras ride the flat payload verbatim (an `annotator_*` key here "
             "is a node property of that exact name, never aliased)",
    ),
    "OperatorAnnotated": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id"),
        note="#3689 — the JSONL line carries the canonical `annotator_*` names; "
             "the `:GraphEvent` payload's SHORT aliases never reach this door",
    ),
    "PointRetracted": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("status", "retracted"),),
                  from_payload=frozenset()),
        note="tombstone — the node is KEPT (the hard delete is EntityMutated)",
    ),
    "PointSuperseded": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("status", "superseded"), ("outdated", True)),
                  # ⚠️ NOT `valid_from`: the producer emits the SUCCESSOR's window
                  # start under that name (#4021) and the fold writes it
                  # verbatim as the PREDECESSOR's `validTo` (`valid_to`), never
                  # as the predecessor's own `validFrom`. The old point's
                  # `validFrom` already rides `PointAdded`; writing the
                  # successor's start here would invert/rewrite the window.
                  from_payload=frozenset({"valid_to", "expired_at",
                                          "valid_to_precision"}),
                  renames=(("superseded_by", "new_id"),)),
        note="§17.5 names this record as the carrier of `validTo`/`expiredAt`/"
             "`outdated`; the fold replays them verbatim "
             "(`SET n.status='superseded', n.outdated=true, n.validTo=$vt, "
             "n.expiredAt=$ea`) — and does NOT touch `validFrom`, which is why "
             "the payload's `valid_from` (the successor's start) is not stored",
    ),
    "PointInvalidated": Mutation(
        NodePatch(Node("point", "Point", "id"), key_from="id",
                  literals=(("outdated", True),),
                  from_payload=frozenset({"valid_to", "expired_at",
                                          "valid_to_precision"}),
                  renames=(("superseded_by", "corrected_by"),)),
        note="#2488 — `outdated` flag + CORRECTS; no status change. §17.5's "
             "carrier for `validTo`/`expiredAt` — the fold replays both "
             "verbatim, so the record's columns carry them too",
    ),
    # ⚠️ THE EP-SCALAR CARVE-OUT, in its executable form: `confidence` is
    # DERIVED (§2.0/§17.5 — recomputed from the structure, "never stored as
    # authority"), and the record has no column for it. The record's mutation
    # for this record type is therefore, correctly, *none* — and it is declared
    # rather than dropped, so the day a writer starts using it for something
    # else the decision is visible.
    "ConfidenceChanged": Mutation(
        NoRecordEffect(
            "EP carve-out (STORAGE-ARCHITECTURE §2.0/§17.5): confidence is "
            "recomputed from the structure and is never stored as authority. "
            "`public.point` has no `confidence` column and its props CHECK "
            "refuses the name, so the record's correct mutation is none. The "
            "`outdated=true` terminal leg rides PointInvalidated's flag, and "
            "`_graph_id`/`_nid`/`reason` are replay bookkeeping (DERIVED)."
        ),
    ),
    "EdgeMessageChanged": Mutation(
        NoRecordEffect(
            "EP carve-out, the EDGE half of `ConfidenceChanged`'s (STORAGE-"
            "ARCHITECTURE §2.0/§17.5): `msg_alpha`/`msg_beta`/`back_msg_*` are "
            "EP runtime message parameters, recomputed by the next "
            "propagation from the structure and never stored as authority — "
            "`public.relation` has no such column and no props tail entry for "
            "them, so the record's correct mutation is none. The record is "
            "GRAPH-REPLAY state only: it makes a FalkorDB rebuild faithful "
            "(#5380, the edge half of #2884), and `derived = replay(journal)` "
            "holds without the RECORD becoming their owner."
        ),
    ),
    "PointsMerged": Mutation(
        OpDispatch(
            table_for_label={"Point": "point"},
            patch_ops=frozenset(),
            delete_ops=frozenset({"merge"}),
            refused_ops={
                "merge": "#331 merge is not materialized yet: the record's "
                         "merge is survivor-update + loser-delete + edge "
                         "re-point, and no unit owns that shape (the journal "
                         "carries `keep_id`/`merge_ids`/`corrects`, not the "
                         "post-merge edge set). Declared and REFUSED rather "
                         "than approximated.",
            },
        ),
        note="refused, not skipped — an approximate merge would silently "
             "re-point edges the journal does not describe",
    ),
    "EntityMutated": Mutation(
        OpDispatch(
            table_for_label={
                "Point": "point",
                "Object": "object",
                "Subject": "subject",
                "Event": "event",
                "Source": "source",
                "Session": "session",
            },
            patch_ops=frozenset({"revise", "restatus", "supersede"}),
            delete_ops=frozenset({"delete"}),
            refused_ops={
                "rename": "#3377/#4769 — the fold supports `rename` but no "
                          "writer produces it, deliberately: a name-bearing "
                          "write withholds its record because journalling it "
                          "drops a legacy name-keyed ObjectSuperseded on "
                          "replay. On the record side a rename MOVES the "
                          "canonical key (Object/Subject key = `name`), so it "
                          "is a key migration, not a patch.",
            },
            never_store={
                "object": frozenset({"status"}),
                "subject": frozenset({"status"}),
            },
        ),
        note="#3299/#3300/#3311/#3312/#1423 — one record type, dispatched on "
             "`op`; `state` carries absolute values the graph STORED",
    ),
    # ── Semantic layer ───────────────────────────────────────────────────────
    "ObjectRegistered": Mutation(
        NodeSnapshot(Node("object", "Object", "name"), path=None, key_from="name"),
    ),
    "ObjectSuperseded": Mutation(
        NodePatch(
            Node("object", "Object", "name"),
            key_from="name",
            from_payload=frozenset(),
            renames=(("superseded_by", "supersedes_by"),
                     ("superseded_at", "ts")),
        ),
        note="#1350 — `supersededBy` is journal-carried verbatim (PRIMARY); "
             "`Object.status` is NOT written (projection-owned cache, §2.6) "
             "and `supersededAt` comes from the record's own envelope `ts`",
    ),
    "SubjectAdded": Mutation(
        NodeSnapshot(Node("subject", "Subject", "name"), path=None, key_from="name"),
    ),
    # ── The event stream, materialized (the episodic layer) ──────────────────
    "EventRecorded": Mutation(
        NodeSnapshot(Node("event", "Event", "event_id"), path=None,
                     key_from="eventId"),
        note="the key column is `event_id`; the Event is named by the payload's "
             "`eventId`/`id` (the fold's MERGE key), NEVER the `_emit_event` "
             "envelope `event_id` — that is a per-record ULID, record-only",
    ),
    "SourceCreated": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url"),
    ),
    "DocumentCreated": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url",
                     drop=frozenset({"content", "doc_status", "docStatus",
                                     "objectKind", "object_kind"})),
        note="§9.3/§9.5 Q3 — a document is a `:Source`; the retired "
             "`content`/`doc_status`/`objectKind` props have no column AND are "
             "scrubbed by the graph fold (`_upsert_document`), so storing them "
             "would leave the record holding state the node removed",
    ),
    "SourceVersioned": Mutation(
        NodeSnapshot(Node("source", "Source", "url"), path=None, key_from="url",
                     drop=frozenset({"previousContentHash", "previousValidTo"})),
        note="#5024/T6 — the prior version's hash and window are RECORD-ONLY "
             "keys (`_SOURCE_HANDLED` keeps them off the node; the single "
             "`:Source` row holds only the current version)",
    ),
    "SessionRecorded": Mutation(
        NodeSnapshot(Node("session", "Session", "id"), path=None, key_from="id"),
        note="four records per capture, merged onto one row — see the "
             "first-writer-wins / last-writer-wins split below",
    ),
    "SessionDeleted": Mutation(
        NodeDelete(
            Node("session", "Session", "id"), key_from="id",
            companion_deletes=(
                (Node("source", "Source", "url"), "session:{key}"),
            ),
        ),
        note="#7951 — the hard-delete carrier; the session's agentSession "
             "`:Source` stub is a `Source` row keyed `session:{id}` and is "
             "removed with it (the fold's `remove_source=True`)",
    ),
    # ── Edges ────────────────────────────────────────────────────────────────
    "EntityLinked": Mutation(
        RelationUpsert(),
        note="#3664 — flat logical endpoints; `edge_type` and both labels are "
             "validated against a frozen vocabulary by the graph fold. The "
             "journal addressing id is the GRAPH id, while `relation`'s FK key "
             "is the label's canonical key, so the endpoint is resolved from "
             "the projection's own node row (an absent endpoint lands no "
             "edge — the fold's `absent` verdict)",
    ),
    "DirectEdgeCreated": Mutation(
        RelationUpsert(),
        note="A10/#1048 — §4.4's canonical flat descriptor, shared with "
             "DirectEdgeRepoint",
    ),
    "DirectEdgeRepoint": Mutation(
        RelationUpsert(),
        note="A10 pass-2b — same flat descriptor; a `delete_only` leg removes "
             "the old edge and is carried by the record's own key",
    ),
    # ── Bookkeeping: declared no-ops, with the reason each one is one ────────
    "IngestStarted": Mutation(
        NoRecordEffect(
            "audit-only run marker (`_NO_PROJECTION_FOLD`): no graph effect, "
            "and the journal position itself is already in "
            "`public.journal_event`, which is the record of what happened"
        ),
    ),
    "CalibrationRecorded": Mutation(
        NoRecordEffect(
            ":Meta milestone marker (audit) — `_NO_PROJECTION_FOLD`; the value "
            "it records is a calibration constant recomputed from the corpus, "
            "not a node property"
        ),
    ),
}

#: Asserted at import time so a registry edit cannot silently lose a type.
assert set(RECORD_MUTATIONS) == set(JOURNAL_EVENT_TYPES), (
    "RECORD_MUTATIONS and JOURNAL_EVENT_TYPES must agree in both directions"
)


# ════════════════════════════════════════════════════════════════════════════
# The schema is read from disk, never restated. (§3: "a field is PRIMARY iff
# the journal carries it" — the projection is where that becomes executable.)
# ════════════════════════════════════════════════════════════════════════════

_COLUMN_RE = re.compile(r'^\s*("[a-z_][a-z0-9_]*"|[a-z_][a-z0-9_]*)\s+'
                        r'(?:text|jsonb|integer|boolean|bigint|double precision|'
                        r'timestamptz)\b', re.M)
_DERIVED_CHECK_RE = re.compile(
    r"chk_[a-z_]+_props_no_derived\s+CHECK\s*\(\s*NOT\s*\(\s*props\s*\?\|"
    r"\s*ARRAY\[(.*?)\]\s*::text\[\]\s*\)\s*\)",
    re.S,
)
_TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS public\.([a-z_]+)\s*\((.*?)\n\);\s*\n", re.S
)
#: The SAME refusal carried by a FORWARD migration: `ALTER TABLE public.<t> …
#: ADD CONSTRAINT chk_<t>_props_no_derived CHECK (…)`. A landed migration is
#: never edited (append-only history), so a later refusal arrives this way —
#: and the schema read must see it, or the projection would emit a props tail
#: the constraint refuses (#8263). `[^;]*?` keeps the match inside one
#: statement (DROP CONSTRAINT …, ADD CONSTRAINT … is a single ALTER).
_ALTER_DERIVED_CHECK_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?public\.([a-z_]+)\b"
    r"[^;]*?ADD\s+CONSTRAINT\s+chk_[a-z_]+_props_no_derived\s+CHECK\s*\("
    r"\s*NOT\s*\(\s*props\s*\?\|"
    r"\s*ARRAY\[(.*?)\]\s*::text\[\]\s*\)\s*\)",
    re.S,
)
#: A FORWARD migration that ADDS a COLUMN: `ALTER TABLE public.<t> ADD COLUMN
#: [IF NOT EXISTS] <col> <type>, …;`. Append-only history means a landed
#: `CREATE TABLE` can only ever GAIN a column this way (#2782: the money value),
#: so a schema read that understands only `CREATE TABLE` reports the record
#: poorer than it is — `_split_properties` would push the value into the `props`
#: tail and `cache_projection` would drop it as unplaced, while §17.5's pin
#: reports the PRIMARY property as MISSING COLUMN.
_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?public\.([a-z_]+)\b([^;]*?);", re.S
)
_ADDED_COLUMN_RE = re.compile(
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([a-z_][a-z0-9_]*)\"?",
    re.I,
)


def _migrations_dir() -> Path:
    return REPO_ROOT / "supabase" / "migrations"


def _read_migrations(migrations_dir: Path | None = None) -> str:
    base = migrations_dir or _migrations_dir()
    return "\n".join(
        (base / name).read_text(encoding="utf-8")
        for name in RECORD_MIGRATIONS
        if (base / name).exists()
    )


def _parse_record_tables(sql: str) -> dict[str, frozenset[str]]:
    tables: dict[str, frozenset[str]] = {}
    for name, body in _TABLE_RE.findall(sql):
        if name not in THE_RECORD_TABLES:
            continue
        columns = {m.group(1).strip('"') for m in _COLUMN_RE.finditer(body)}
        # `props` is spelled `props jsonb NOT NULL DEFAULT …` — matched above.
        tables[name] = frozenset(columns)
    return tables


def _parse_added_columns(sql: str) -> dict[str, frozenset[str]]:
    """`{table: {column}}` from forward `ALTER TABLE … ADD COLUMN` migrations.

    The same forward-migration rule `refused_props_names()` follows for a
    re-declared refusal (#8263), applied to the column set: a landed migration
    is never edited, so a column added after the fact arrives as an ALTER and
    the schema read must see it (#2782's five money columns).
    """
    added: dict[str, set[str]] = {}
    for table, body in _ALTER_ADD_COLUMN_RE.findall(sql):
        if table not in THE_RECORD_TABLES:
            continue
        for column in _ADDED_COLUMN_RE.findall(body):
            added.setdefault(table, set()).add(column)
    return {table: frozenset(cols) for table, cols in added.items()}


def table_columns(migrations_dir: Path | None = None) -> dict[str, frozenset[str]]:
    """`{table: {column}}` for the record's tables, parsed from the migrations.

    Parsed rather than restated: a hand-copied column list is a second
    declaration of the schema, and the second declaration is the one that goes
    stale. `#7968`'s own pin does the same thing for the placement table.

    A `CREATE TABLE` is the base; a FORWARD `ALTER TABLE … ADD COLUMN` is
    unioned over it, because append-only history means a landed table can only
    gain a column that way (#2782, the same rule as `refused_props_names`).
    """
    sql = _read_migrations(migrations_dir)
    tables = _parse_record_tables(sql)
    for table, added in _parse_added_columns(sql).items():
        if table in tables:
            tables[table] = tables[table] | added
    missing = [t for t in THE_RECORD_TABLES if t not in tables]
    if missing:
        raise RecordProjectionError(
            "the record schema is not on disk — no CREATE TABLE found for "
            f"{missing}. The record projection targets `#7968`'s migrations "
            f"under {_migrations_dir()}."
        )
    return tables


def refused_props_names(migrations_dir: Path | None = None) -> dict[str, frozenset[str]]:
    """`{table: {name refused as a props key}}` — the carve-out, from the SQL.

    These are the names the schema itself refuses: the EP scalars (§17.5) and
    the projection-owned caches. The record must not store them as authority,
    and the projection drops them **by name** rather than relying on the CHECK
    to catch it later.
    """
    sql = _read_migrations(migrations_dir)
    out: dict[str, frozenset[str]] = {}
    # Bound each table to ITS OWN `CREATE TABLE … );` body (`_TABLE_RE`, the
    # same regex `table_columns` uses). Splitting on the CREATE headers alone
    # would run the LAST table's block to the end of the concatenated SQL, so a
    # later forward `ALTER` (#8263) would be misread as belonging to whichever
    # table happened to be last.
    for table, body in _TABLE_RE.findall(sql):
        if table not in THE_RECORD_TABLES:
            continue
        names: set[str] = set()
        for arr in _DERIVED_CHECK_RE.findall(body):
            names |= {n.strip().strip("'") for n in arr.split(",") if n.strip()}
        out[table] = frozenset(n for n in names if n)
    # Forward migrations re-declare a refusal rather than rewrite the CREATE
    # TABLE (append-only history). Apply them over the parsed tables — the set
    # only ever GROWS, so an ALTER that names fewer keys cannot silently
    # re-open a name an earlier migration closed.
    for table, arr in _ALTER_DERIVED_CHECK_RE.findall(sql):
        if table not in out:
            continue
        extra = frozenset(n.strip().strip("'") for n in arr.split(",") if n.strip())
        out[table] = out[table] | extra
    return out


def snake(prop: str) -> str:
    """camelCase → snake_case, preserving leading underscores.

    The SAME rule `#7968`'s schema↔placement pin uses, and preserving the
    leading underscore matters for the same reason: `_nid`/`_graph_id` are
    replay bookkeeping (DERIVED), while `graph_id` is the record's tenancy
    column — collapsing the underscore would let a DERIVED name look satisfied.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", prop).lower()


#: Journal keys that name the record itself rather than a node property: the
#: `_emit_event` envelope, the snapshot carrier, and the JSONL-only link/batch
#: markers. They are never written as columns and never fall into `props`.
#:
#: ⚠️ Deliberately NARROW. `id`, `name`, `label`, `key`, `op` and `state` are
#: NOT here even though `EntityMutated`/`EntityLinked` treat them as record
#: coordinates: for a `Point` snapshot `label` is a real PRIMARY property
#: (operator label), for `Object`/`Subject` `name` is written as the key, and
#: for `Event` `id` mirrors `eventId` (#122). Those two dispatch paths read
#: their coordinates directly, so a global drop would silently lose a PRIMARY
#: field from the layer rows — exactly the "record poorer than the history"
#: failure Interface A forbids.
_RECORD_ONLY_KEYS: frozenset[str] = frozenset({
    "type", "ts", "event_id", "initiated_by", "projection_version", "seq",
    "point", "contains_session", "keep_id", "merge_ids", "corrects",
    # #5097 review P2: the capture path's STUB-COMPLETION discriminator. It
    # selects `_upsert_source`'s write behaviour (suppresses the ON CREATE
    # creator-only defaults and admits the capture replay props), is listed in
    # `_META_KEYS` so it never reaches a `:Source` node, and `create_source` /
    # `_sanitize_props` / MCP refuse it as a caller key. The RECORD layer is the
    # third home and it was missing: a `SourceCreated` snapshot is a
    # `path=None` NodeSnapshot, so the record's flat payload IS the property
    # dict, and the key fell into the `source.props` jsonb tail — which
    # `cache_projection` copies back onto the node verbatim. Same class as the
    # sibling `contains_session` above.
    "_stub_completion",
    # Creating-record embedding identity (a DESCRIPTION of the record, never a
    # node property — the fold's `_POINT_HANDLED` drops them too).
    "embedding_model", "embedding_revision", "embedding_text_hash",
    "embedding_preserved",
})


# ════════════════════════════════════════════════════════════════════════════
# Statement emission
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Statement:
    """One parameterized SQL statement plus its parameters."""

    sql: str
    params: tuple[Any, ...]

    def render(self) -> str:
        """A readable one-liner for logs and hover-less debugging."""
        return f"{self.sql} -- {self.params!r}"


def _jsonb_or_scalar(column: str, value: Any) -> Any:
    """`jsonb` columns take serialized JSON; everything else passes through.

    The caller supplies the value; this module does not coerce, because a
    journal carrying a string where the record's column is `boolean` is a
    journal/record disagreement and coercing it here would hide it.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


#: The record's own bookkeeping columns — supplied by the projection, never by
#: a journal payload. A payload property whose `snake()` form collides with one
#: of these must land in the `props` tail: routing it to the column either
#: emitted that column TWICE (`INSERT … (…, props, props)` — Postgres refuses
#: `column "props" specified more than once`) or silently rewrote the row's
#: identity (`node_key` via a `NodePatch`). `props` is itself the open-set tail,
#: so a node property literally named `props` nests inside it, preserving the
#: graph's property instead of dropping it.
_BOOKKEEPING_COLUMNS: frozenset[str] = frozenset({
    "org_id", "graph_id", "props",
    "node_label", "node_key",
    "rel_type", "src_label", "src_key", "tgt_label", "tgt_key", "idx",
})


def _split_properties(
    table: str,
    props: Mapping[str, Any],
    *,
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
    drop: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a journal property dict into `(columns, props_tail)`.

    Three destinations, and no fourth:
      * a property with a column is written to that column — UNLESS the
        column is the record's own bookkeeping (`_BOOKKEEPING_COLUMNS`),
        which a payload may never write;
      * a property the schema refuses as a props key is **dropped** (it is
        DERIVED/VOLATILE — recomputed from the structure, never authority);
      * everything else lands in the declared `props` tail.

    An explicit ``drop`` set carries the RECORD-ONLY keys — journal keys that
    describe the record rather than the node (`previousContentHash`,
    `previousValidTo`, a snapshot's identity keys).
    """
    cols = columns[table]
    refused_here = refused.get(table, frozenset())
    col_values: dict[str, Any] = {}
    tail: dict[str, Any] = {}
    for prop, value in props.items():
        if prop in drop:
            continue
        column = snake(prop)
        if column in _BOOKKEEPING_COLUMNS:
            # A payload may never write the record's own coordinates. The
            # property is preserved as DATA in the tail instead — dropping it
            # would make the record poorer than the graph, which is the
            # failure Interface A forbids.
            tail[prop] = value
        elif column in cols:
            col_values[column] = value
        elif prop in refused_here or column in refused_here:
            # DERIVED/VOLATILE — the carve-out. Dropping is the correct
            # mutation; storing it would make a recomputed value authority.
            continue
        else:
            tail[prop] = value
    return col_values, tail


def _node_table(node: Node, columns: Mapping[str, frozenset[str]]) -> str:
    if node.table not in columns:
        raise RecordProjectionError(
            f"unknown record table {node.table!r} — not one of {sorted(columns)}"
        )
    return node.table


def _key_value(node: Node, payload: Mapping[str, Any], key_from: str) -> str:
    """The node key, and the schema's own pin that it is the label's key.

    A record whose key column is absent cannot be projected: the schema's
    `chk_*_node_key_is_contract_key` would refuse the write anyway, and a
    `node_key = <NULL>` comparison is SQL NULL (not FALSE), so the failure
    would be silent. Fail here instead, where the record is known.
    """
    value = payload.get(key_from)
    if value is None and key_from in ("event_id", "eventId"):
        # The Event's canonical key is the payload's `eventId` (the fold's
        # MERGE key is `inner.get("id") or inner.get("eventId")`); the
        # envelope `event_id` is a per-record ULID and must never be used.
        value = payload.get("eventId") or payload.get("id")
    if not isinstance(value, str) or not value:
        raise RecordProjectionError(
            f"record has no usable {key_from!r} key for {node.label} "
            f"(got {value!r}) — a row whose node_key is not the label's key "
            "resolves to nothing, in silence"
        )
    return value


def _identity_statement(org_id: str, graph_id: str, node: Node, key: str) -> Statement:
    """The `(label, key)` FK parent. Written first, always.

    `public.node_identity` is why a relation's endpoints are real composite
    foreign keys (S6). Every node write is preceded by its identity row, and
    the insert is `DO NOTHING` on conflict so a re-replay is a no-op.
    """
    return Statement(
        "INSERT INTO public.node_identity "
        "(org_id, graph_id, node_label, node_key) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (org_id, graph_id, node_label, node_key) DO NOTHING",
        (org_id, graph_id, node.label, key),
    )


def _upsert_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    col_values: Mapping[str, Any],
    tail: Mapping[str, Any],
) -> Statement:
    columns = ["org_id", "graph_id", "node_label", "node_key", *col_values, "props"]
    params: list[Any] = [
        org_id, graph_id, node.label, key,
        *(_jsonb_or_scalar(c, v) for c, v in col_values.items()),
        json.dumps(tail),
    ]
    placeholders = ", ".join(["%s"] * len(columns))
    # IDEMPOTENCE, stated once: re-applying the same record writes the same
    # absolute values onto the same row. A second replay of the journal
    # therefore produces the same record (Interface A's second clause).
    # FIDELITY: the `props` tail is MERGED, not replaced — the graph fold
    # applies an open-set tail with `SET n += $props` (entities.py), so a
    # partial carrier (a second `SessionRecorded`, a `SourceVersioned`) that
    # omitted a prop an earlier record landed would otherwise drop it from the
    # record while the graph preserved it.
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in col_values)
    if updates:
        updates += ", "
    updates += f"props = {node.table}.props || EXCLUDED.props"
    sql = (
        f"INSERT INTO public.{node.table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (org_id, graph_id, node_key) DO UPDATE SET {updates}"
    )
    return Statement(sql, tuple(params))


def _patch_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    col_values: Mapping[str, Any],
    tail: Mapping[str, Any],
    match: tuple[str, ...] = ("node_key",),
) -> Statement:
    sets: list[str] = []
    params: list[Any] = []
    for column, value in col_values.items():
        sets.append(f"{column} = %s")
        params.append(_jsonb_or_scalar(column, value))
    if tail:
        sets.append("props = props || %s::jsonb")
        params.append(json.dumps(tail))
    if not sets:
        # A patch with nothing to write is a 0-row no-op by construction; make
        # it a real statement so the caller still sees the record was consumed.
        sets.append("node_key = node_key")
    params.extend([org_id, graph_id, *([key] * len(match))])
    predicate = " OR ".join(f"{c} = %s" for c in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    sql = (
        f"UPDATE public.{node.table} SET {', '.join(sets)} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate}"
    )
    return Statement(sql, tuple(params))


def _delete_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    match: tuple[str, ...] = ("node_key",),
) -> Statement:
    predicate = " OR ".join(f"{c} = %s" for c in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    return Statement(
        f"DELETE FROM public.{node.table} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate}",
        (org_id, graph_id, *([key] * len(match))),
    )


def _identity_delete_statement(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    match: tuple[str, ...] = ("node_key",),
) -> Statement:
    """The FK parent's removal — the record's half of the graph's `DETACH DELETE`.

    `node_identity` is the composite-FK parent of BOTH the node row and every
    incident `relation` row, and the schema declares both FKs `ON DELETE
    CASCADE` for exactly this purpose. A bare node `DELETE` therefore left the
    identity row and each incident edge alive — the record kept edges to a node
    it had removed, while the graph's `MATCH … DETACH DELETE n` (the fold's
    `_delete_entity_by_id`) dropped the node AND its edges.

    An Object/Subject delete matches on the `id` column, not `node_key`
    (Object/Subject key `node_key` on `name`), so the identity row's key is
    resolved from the node row — which still exists at this point, because this
    statement MUST run before `_delete_statement`. The subquery reads the node
    table, so the cascade then removes that row and every incident relation.
    """
    predicate = " OR ".join(f"{c} = %s" for c in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    return Statement(
        "DELETE FROM public.node_identity "
        "WHERE org_id = %s AND graph_id = %s AND node_label = %s "
        f"AND node_key IN (SELECT node_key FROM public.{node.table} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate})",
        (org_id, graph_id, node.label, org_id, graph_id, *([key] * len(match))),
    )


def _delete_statements(
    org_id: str,
    graph_id: str,
    node: Node,
    key: str,
    match: tuple[str, ...] = ("node_key",),
) -> list[Statement]:
    """A hard delete: the identity parent FIRST (so the subquery sees the node
    row), then the node row. The identity delete cascades the incident edges,
    matching the graph's `DETACH DELETE`; the explicit node delete is the
    child-first form and a no-op once the cascade has run."""
    return [
        _identity_delete_statement(org_id, graph_id, node, key, match),
        _delete_statement(org_id, graph_id, node, key, match),
    ]


def _relation_coordinates(
    rel: RelationUpsert | RelationDelete,
    payload: Mapping[str, Any],
) -> tuple[str, str, str, str, str, Any]:
    """The `(rel_type, src_label, src, tgt_label, tgt, idx)` of an edge record."""
    rel_type = payload.get(rel.rel_type_from) or payload.get("type_of_edge")
    src = payload.get(rel.src_from) or payload.get("source_id")
    tgt = payload.get(rel.tgt_from) or payload.get("target_id")
    if not isinstance(rel_type, str) or not rel_type:
        raise RecordProjectionError(
            f"edge record has no usable edge type (got {rel_type!r}) — an "
            "edge with no predicate is not a relation"
        )
    src_label = payload.get("source_label") or payload.get("src_label") or "Point"
    tgt_label = payload.get("target_label") or payload.get("tgt_label") or "Point"
    if not isinstance(src, str) or not isinstance(tgt, str) or not src or not tgt:
        raise RecordProjectionError(
            f"edge record has no usable endpoints (src={src!r}, tgt={tgt!r})"
        )
    return rel_type, str(src_label), src, str(tgt_label), tgt, payload.get("idx", 0)


def _endpoint_resolution(
    org_id: str,
    graph_id: str,
    label: str,
    journal_id: str,
    alias: str,
) -> tuple[str, list[Any]]:
    """The node row an edge endpoint addresses, keyed by the label's key column.

    The graph fold addresses an `EntityLinked` endpoint by **`id`** —
    `MATCH (t:{label} {id:$tid})` in
    `projection/entities.py::_fold_entity_linked_reason` — and that is what the
    producers emit (`sdk.py::link_entity` passes `o.id`;
    `subject_binding._resolve_target` returns `n.id`). But the RECORD's
    canonical key is `Point`/`Session`→`id`, `Event`→`event_id`,
    `Source`→`url`, `Object`/`Subject`→`name`
    (`node_identity.key_property`, migration `20261012000001`), and
    `relation.src_key`/`tgt_key` are composite FKs into `node_identity`.

    Writing the raw journal id straight through therefore attached the edge to
    a PHANTOM identity (`Object/o1` where the row is keyed `release-1.0`), which
    the hard-delete cascade could never reach — the record kept an edge to a
    node it had removed. This resolves `node_key` from the projection's own node
    row, matching the journal id with the SAME predicate the hard-delete
    cascade uses (:data:`_ENTITY_MATCH_COLUMNS`), and the caller joins on the
    result: an endpoint with no row produces **no relation at all**, which is
    the fold's own `"absent"` verdict rather than a minted phantom.
    """
    table = _LABEL_TABLES.get(label)
    if table is None:
        raise RecordProjectionError(
            f"edge endpoint names label {label!r}, which is not a record node "
            f"label ({sorted(_LABEL_TABLES)})"
        )
    match = _ENTITY_MATCH_COLUMNS.get(table, ("node_key",))
    predicate = " OR ".join(f"{column} = %s" for column in match)
    if len(match) > 1:
        predicate = f"({predicate})"
    return (
        f"(SELECT node_key FROM public.{table} "
        f"WHERE org_id = %s AND graph_id = %s AND {predicate}) {alias}",
        [org_id, graph_id, *([journal_id] * len(match))],
    )


def _relation_upsert_statement(
    org_id: str,
    graph_id: str,
    rel: RelationUpsert,
    payload: Mapping[str, Any],
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
) -> list[Statement]:
    rel_type, src_label, src, tgt_label, tgt, idx = _relation_coordinates(rel, payload)
    col_values, tail = _split_properties(
        "relation", {k: payload[k] for k in rel.attrs if k in payload},
        columns=columns, refused=refused,
    )
    # The endpoints are RESOLVED, never minted: the fold's `MATCH … MERGE`
    # creates no endpoints, and an infinite source of phantom `node_identity`
    # rows is exactly what let a link survive its object's hard delete. The
    # `FROM` join yields ZERO rows when either endpoint is absent, so the whole
    # insert is a no-op — the fold's `"absent"` verdict, not a dropped edge
    # that still materializes a key.
    src_select, src_params = _endpoint_resolution(
        org_id, graph_id, src_label, src, "src")
    tgt_select, tgt_params = _endpoint_resolution(
        org_id, graph_id, tgt_label, tgt, "tgt")
    columns_ = ["org_id", "graph_id", "rel_type", "src_label", "src_key",
                "tgt_label", "tgt_key", "idx", *col_values, "props"]
    select_list = ", ".join([
        "%s", "%s", "%s", "%s", "src.node_key", "%s", "tgt.node_key", "%s",
        *(["%s"] * len(col_values)), "%s",
    ])
    params: list[Any] = [
        org_id, graph_id, rel_type, src_label, tgt_label, idx,
        *(_jsonb_or_scalar(c, v) for c, v in col_values.items()), json.dumps(tail),
        *src_params, *tgt_params,
    ]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*col_values, "props"])
    return [Statement(
        f"INSERT INTO public.relation ({', '.join(columns_)}) "
        f"SELECT {select_list} FROM {src_select}, {tgt_select} "
        "ON CONFLICT (org_id, graph_id, rel_type, src_label, src_key, "
        f"tgt_label, tgt_key, idx) DO UPDATE SET {updates}",
        tuple(params),
    )]


def _relation_delete_statement(
    org_id: str,
    graph_id: str,
    rel: RelationUpsert | RelationDelete,
    payload: Mapping[str, Any],
) -> Statement:
    """Remove one relation row — the `delete_only` leg of a `DirectEdgeRepoint`.

    A `delete_only` descriptor (#2489, the supersede no-self-edge guard)
    describes the edge the graph DELETES: the OLD point (`src`) and the
    successor point its edge terminated at (`tgt`). BOTH endpoints are
    `:Point`, so the descriptor's `target_label` — which names the STRUCTURAL
    rel family, not the edge's actual target — is deliberately ignored here;
    using it would target a nonexistent `(Point, Source, …)` row (and the
    upsert path additionally minted a phantom `node_identity` for it). No
    identity row is minted: a delete materializes nothing.
    """
    rel_type, _src_label, src, _tgt_label, tgt, idx = _relation_coordinates(
        rel, payload)
    return Statement(
        "DELETE FROM public.relation WHERE org_id = %s AND graph_id = %s "
        "AND rel_type = %s AND src_label = 'Point' AND src_key = %s "
        "AND tgt_label = 'Point' AND tgt_key = %s AND idx = %s",
        (org_id, graph_id, rel_type, src, tgt, idx),
    )


def _op_dispatch_statements(
    org_id: str,
    graph_id: str,
    dispatch: OpDispatch,
    payload: Mapping[str, Any],
    columns: Mapping[str, frozenset[str]],
    refused: Mapping[str, frozenset[str]],
) -> list[Statement]:
    op = payload.get("op")
    label = payload.get("label")
    if not isinstance(op, str):
        raise RecordProjectionError("EntityMutated record carries no `op`")
    if op in dispatch.refused_ops:
        raise RefusedJournalOperation(f"EntityMutated op={op!r}: {dispatch.refused_ops[op]}")
    table = dispatch.table_for_label.get(str(label))
    if table is None:
        raise RecordProjectionError(
            f"EntityMutated names label {label!r}, which is not a record node "
            f"label ({sorted(dispatch.table_for_label)})"
        )
    node = Node(table, str(label), NODE_TABLES[table][1])
    state = payload.get("state")
    state = state if isinstance(state, Mapping) else {}
    # The record's identity is the journal `id` — the SAME key the graph fold
    # matches (`_ENTITY_ID_PROP`), NOT the table's `node_key` column. For
    # Object/Subject `node_key` is `name` and for Source it is `url`, so a
    # `node_key = id` predicate silently touches 0 rows and the state op is
    # lost. `state.name`/`state.url` are a defensive fallback for a record that
    # omits `id`.
    key = (payload.get("id") or payload.get("key")
           or state.get("name") or state.get("url"))
    if not isinstance(key, str) or not key:
        raise RecordProjectionError(f"EntityMutated op={op!r} carries no key")
    match = _ENTITY_MATCH_COLUMNS.get(table, ("node_key",))
    if op in dispatch.delete_ops:
        return _delete_statements(org_id, graph_id, node, key, match=match)
    if op in dispatch.patch_ops:
        col_values, tail = _split_properties(
            table, {k: v for k, v in state.items() if k != "name"},
            columns=columns, refused=refused,
            drop=dispatch.never_store.get(table, frozenset()),
        )
        return [_patch_statement(org_id, graph_id, node, key, col_values, tail,
                                 match=match)]
    raise RecordProjectionError(
        f"EntityMutated op={op!r} is neither patched nor deleted nor declared "
        "refused — an undeclared op is the silent-skip defect"
    )


def mutations_for(
    record: Mapping[str, Any],
    *,
    org_id: str,
    graph_id: str,
    columns: Mapping[str, frozenset[str]] | None = None,
    refused: Mapping[str, frozenset[str]] | None = None,
) -> list[Statement]:
    """The Postgres mutations for ONE journal record. **Total by construction.**

    Raises :class:`UnknownJournalRecordType` for a record type the registry
    does not declare, and :class:`RefusedJournalOperation` for a declared
    refusal. Both are failures — neither is a skip.
    """
    columns = columns if columns is not None else table_columns()
    refused = refused if refused is not None else refused_props_names()
    record_type = record.get("type")
    mutation = RECORD_MUTATIONS.get(record_type) if isinstance(record_type, str) else None
    if mutation is None:
        raise UnknownJournalRecordType(
            f"{record_type!r} has no declared record mutation — Interface A is "
            "TOTAL: an event with no handler is a failure, not a skip (#5285's "
            "rule applied to the record). Declare it in RECORD_MUTATIONS."
        )
    effect = mutation.effect
    if isinstance(effect, NoRecordEffect):
        return []
    if isinstance(effect, OpDispatch):
        return _op_dispatch_statements(org_id, graph_id, effect, record,
                                       columns, refused)
    if isinstance(effect, RelationUpsert):
        if record.get("delete_only") is True:
            # #2489/#7969: a `delete_only` descriptor is the graph's pass-2b
            # delete of a resurrected phantom edge. An UPSERT here re-created
            # the edge the graph removed; a DELETE is the faithful mutation.
            return [_relation_delete_statement(org_id, graph_id, effect, record)]
        return _relation_upsert_statement(org_id, graph_id, effect, record,
                                          columns, refused)
    if isinstance(effect, RelationDelete):
        return [_relation_delete_statement(org_id, graph_id, effect, record)]
    if isinstance(effect, NodeSnapshot):
        node = effect.node
        _node_table(node, columns)
        key = _key_value(node, record, effect.key_from)
        props: dict[str, Any] = {}
        if effect.path is None:
            props = {k: v for k, v in record.items() if k not in _RECORD_ONLY_KEYS}
            props.setdefault(effect.key_from, key)
        else:
            snapshot = record.get(effect.path)
            if not isinstance(snapshot, Mapping):
                raise RecordProjectionError(
                    f"{record_type!r} carries no `{effect.path}` snapshot to "
                    "project — registering from a record that does not carry "
                    "the node would materialize a row the journal never wrote"
                )
            props = {k: v for k, v in snapshot.items() if k not in _RECORD_ONLY_KEYS}
            props[effect.key_from] = key
        col_values, tail = _split_properties(node.table, props, columns=columns,
                                            refused=refused, drop=effect.drop)
        if node.key_column not in col_values:
            col_values[node.key_column] = key
        return [
            _identity_statement(org_id, graph_id, node, key),
            _upsert_statement(org_id, graph_id, node, key, col_values, tail),
        ]
    if isinstance(effect, NodePatch):
        node = effect.node
        key = _key_value(node, record, effect.key_from)
        props: dict[str, Any] = dict(effect.literals)
        if effect.from_payload:
            props.update({k: v for k, v in record.items()
                          if k not in _RECORD_ONLY_KEYS and k in effect.from_payload})
        elif effect.from_payload is None:
            # `PointRevised` / `OperatorAnnotated`: the extras ride the flat
            # payload VERBATIM, so every non-record key is a node property —
            # except a key that is the rename's own source (`new_content`
            # names the revised `content`; it is not a property of its own).
            renamed_sources = {src for _, src in effect.renames}
            props.update({k: v for k, v in record.items()
                          if k not in _RECORD_ONLY_KEYS
                          and k not in renamed_sources})
        for column, source_key in effect.renames:
            if source_key in record:
                props[column] = record[source_key]
        col_values, tail = _split_properties(node.table, props, columns=columns,
                                            refused=refused)
        return [_patch_statement(org_id, graph_id, node, key, col_values, tail)]
    if isinstance(effect, NodeDelete):
        node = effect.node
        key = _key_value(node, record, effect.key_from)
        statements = _delete_statements(org_id, graph_id, node, key)
        for companion, template in effect.companion_deletes:
            statements += _delete_statements(
                org_id, graph_id, companion, template.format(key=key)
            )
        return statements
    raise RecordProjectionError(
        f"{record_type!r} declares an effect this engine does not implement: "
        f"{type(effect).__name__}"
    )


# ════════════════════════════════════════════════════════════════════════════
# The journal-position record, and the replay driver
# ════════════════════════════════════════════════════════════════════════════


def _journal_row(record: Mapping[str, Any], seq: int, org_id: str,
                 graph_id: str) -> Statement:
    """The record of WHAT the record consumed — `public.journal_event`.

    Keyed on `(org_id, graph_id, seq)`, which the schema calls *"the
    idempotency key: applying a position twice is a no-op"*. `ON CONFLICT DO
    NOTHING` because the table is append-only by TRIGGER: an update would be
    refused, and correctly so — a consumed position is not revisable.
    """
    return Statement(
        "INSERT INTO public.journal_event "
        "(org_id, graph_id, seq, event_type, payload, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())) "
        "ON CONFLICT (org_id, graph_id, seq) DO NOTHING",
        (
            org_id, graph_id, seq, record.get("type"),
            json.dumps({k: v for k, v in record.items() if k not in ENVELOPE_KEYS}),
            record.get("ts"),
        ),
    )


@dataclass
class ReplayReport:
    """What a replay consumed. A report, never a substitute for the failure."""

    events: int = 0
    statements: int = 0
    no_effects: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (f"replayed {self.events} journal records → {self.statements} "
                f"record mutations ({len(self.no_effects)} declared no-op)")


def replay(records: Sequence[Mapping[str, Any]], conn: Any, *, org_id: str,
           graph_id: str) -> ReplayReport:
    """Replay a journal into the record, on the CALLER's transaction.

    `§3`: *"One transaction covers the journal and its projection — no dual
    write between them."* The transaction that invariant names spans the journal
    APPEND and this projection, and this module does not perform the append —
    the chokepoint (`#7951`, Interface C) does. Only the caller can therefore
    own the boundary: a `commit()` here would commit the projection ALONE and
    leave the append and the record in two transactions, which is the dual
    write §3 forbids. `replay()` executes the plan on `conn` and neither commits
    nor rolls back; the caller commits on success and rolls back on failure, and
    an exception propagates with the caller's transaction still open.

    It is the companion of :func:`replay_plan`, not an alternative to it: the
    chokepoint may use either, because the transaction boundary lives on the
    caller's side in both.

    IDEMPOTENCE. Every emitted mutation is absolute: an upsert writes the same
    values onto the same row, a patch writes the same values, a delete finds
    nothing, and the journal position is `DO NOTHING`. A second replay of the
    same journal therefore produces the same record — and the assertion the
    caller should make is exactly `#5285`'s, moved to the record: **replay
    twice and compare the tables, not the code paths.**

    TOTALITY. The whole journal is materialized into statements *before* the
    first one executes, so an undeclared record type fails the replay with the
    transaction untouched rather than leaving a half-applied record.
    """
    plan = replay_plan(records, org_id=org_id, graph_id=graph_id)
    cursor = conn.cursor()
    for statement in plan:
        cursor.execute(statement.sql, statement.params)
    report = ReplayReport(events=len(records), statements=0)
    for statement in plan:
        if _JOURNAL_INSERT_PREFIX in statement.sql:
            continue
        report.statements += 1
    return report


#: Distinguishes the journal-position write from the record's own mutations,
#: so a report can count what the projection produced.
_JOURNAL_INSERT_PREFIX = "INSERT INTO public.journal_event"


def replay_plan(records: Sequence[Mapping[str, Any]], *, org_id: str,
                graph_id: str) -> list[Statement]:
    """Every statement a full replay emits, in order, WITHOUT executing any.

    The totality gate lives here: an undeclared record type raises before a
    single statement has run, so a replay either materializes the whole journal
    or leaves the record exactly as it found it. It is also the seam the PGlite
    harness (`supabase/tests/pglite/validate.mjs`) and the tests use to look at
    the *whole* statement set rather than one record at a time.
    """
    columns = table_columns()
    refused = refused_props_names()
    plan: list[Statement] = []
    for seq, record in enumerate(records, start=1):
        plan.extend(mutations_for(record, org_id=org_id, graph_id=graph_id,
                                  columns=columns, refused=refused))
        plan.append(_journal_row(record, seq, org_id, graph_id))
    return plan


def to_dollar_placeholders(statement: Statement) -> tuple[str, list[Any]]:
    """Rewrite DB-API `%s` placeholders to Postgres `$n`.

    The engine emits DB-API (`%s`) parameters because that is what the Python
    driver on the write path takes. A harness that speaks the *native* protocol
    (PGlite, `psql`) needs `$n`, and this is the one converter for both — a
    second hand-written copy is how the two drift.
    """
    parts = statement.sql.split("%s")
    if len(parts) - 1 != len(statement.params):
        raise RecordProjectionError(
            f"statement has {len(parts) - 1} placeholders for "
            f"{len(statement.params)} parameters: {statement.sql}"
        )
    out = [parts[0]]
    for index, tail in enumerate(parts[1:], start=1):
        out.append(f"${index}")
        out.append(tail)
    return "".join(out), list(statement.params)
