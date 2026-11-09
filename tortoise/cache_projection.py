"""Interface B — the cache projection: the record's STATE, loaded to hot, one way.

WHY THIS MODULE EXISTS
======================
``docs/architecture/STORAGE-ARCHITECTURE.md`` §2.0 (owner, 2026-10-08) makes
**Postgres the system of record** and **FalkorDB a derived hot cache**, and the
owner's words are *"data goes to postgres and it's loaded to hot (falkor)"*.

``tortoise/projection/`` already is a replay — of the **journal**, into
FalkorDB. That is history's fold, and it has two consequences this module
exists to fix:

1. **A rebuild could only read history.** ``FalkorProjection.rebuild_all``
   takes a ``log_dir`` and re-reads the JSONL. So *"Postgres is the record and
   the cache is loaded from it"* had **no execution path**, and the ruling's
   second half had no carrier (#7971; #7967 S3's own failure mode — *"a rebuild
   that can only read the journal rather than the record"*).
2. **The fold's output is not confined to the record.** ``apply()`` writes the
   ``content_hash``/``confidence``/embedding it recomputes — names the schema
   **refuses as authority** (#7968's ``chk_*_props_no_derived``, the EP
   carve-out). A cache built that way carries fields with **no PRIMARY claim in
   the record**, which is exactly what S3's contract clause forbids.

So this module projects the record's **state** — the layer tables ``#7968``
built — and confines its output to the record's PRIMARY field set.

THE FOUR PROPERTIES, AND WHERE EACH LIVES
=========================================
1. **One-way by construction.** Nothing here reads the cache to decide what to
   project. The plan is built entirely from the record; the only read it
   contains is the endpoint ``MATCH`` inside a relation upsert, which resolves
   a **record** coordinate. ⛔ That is why the six graph-only classes
   ``rebuild_all`` currently rescues from the cache (``_SNAPSHOT_SECTIONS``:
   ``#548`` synthetic Points, ``#990`` ``:Batch`` markers, ``#2814`` config,
   ``#4641`` onboarding state, ``#4653`` ``:GraphEventMeta``, ``#5353``
   ``:TeamMeta``) are **not carried
   here**. The issue's own words forbid exactly that — *"the cache is a
   destination, never an input"* — and the placement ruling says what they are:
   *"a field with no PRIMARY claim in the record that exists only here is a
   defect, not a shortcut."* A record-sourced rebuild **drops** them, and the
   drop is **named**, not silent — the six classes are listed right here and in
   ``06-cache-projection.md`` §0.5. It is deliberately **not counted at
   runtime**: counting them would require the pre-wipe cache read this path
   forbids, and `rebuild_from_record`'s report is about the RECORD's coverage,
   not a census of what the wipe destroyed.
2. **The cache is never authoritative.** A property is projected **iff** the
   schema declares a column for it, does not refuse it, **and** the projection
   does not declare that column not-projectable (:data:`CACHE_FORBIDDEN_COLUMNS`
   — a THIRD class, neither PRIMARY-and-projectable nor schema-refused; the
   record may hold it as authority and the cache must not materialise it).
   The property→column
   decision is **read from disk** — the schema (#7968) and the placement table
   (#7955) — never restated here, so a new column is a loud failure rather than
   a silent drop. ⛔ **A declared NARROWING, one of TWO (#10853, narrowed again
   by #10965; the second is :data:`CACHE_FORBIDDEN_COLUMNS`, stated as
   exception (5) under point 3 below):** an EPISODIC Source — EITHER identity arm,
   `_EPISODIC_SOURCE_URL_PREFIX` or `_EPISODIC_SOURCE_KIND` — never carries the
   COLUMNS in :data:`_EPISODIC_SOURCE_PROSE_COLUMNS` (`summary`, `_search_text`;
   graph property `_searchText`) onto its node **when the row holds the
   pre-#10000 derivation**, because §9.1/D1 places captured turn text in the
   record and never on a graph node. The authority for the pair is
   `STORAGE-ARCHITECTURE.md` §9.1: *"`summary`/`title` stay on the node — they
   are §4.6 metadata, not the S1 narrative"*. That sentence is why a
   post-#10000 row's lexicon `_searchText` IS projected — it is that §4.6
   metadata value, not the narrative — and why this test is on the row's
   PROVENANCE rather than on the column's NAME. ⛔ It does NOT sanction refusing
   `summary`: that is a DEPARTURE from §9.1's own sentence, taken deliberately
   and recorded at that table (#10965 — the declared set exists for the FTS
   field, and restoring a display label gains no retrieval), never something the
   authority implies.
   The decision is per ROW, from the row's own
   values (#10000's rows carry the topic lexicon there — token-shaped, not a copy of the row's prose, though a space-less CJK run arrives whole and a Thai one fragments) — see that
   table for the rule, its bound, and its costs.
3. **Rebuildable from the record alone.** The plan is a pure function of the
   record view: same view ⇒ same statements (idempotent), and the written field
   set never EXCEEDS the record view's (a superset would make the cache a second
   authority) — that direction has no class-level exception, because every
   property the declared classes below add is materialised in the VIEW-BUILDING
   path and is therefore already IN the view: :func:`node_from_row` applies the
   fold and derived classes, and :func:`apply_event_stream_folds` (reached from
   :func:`read_record_view` and ``FalkorProjection.rebuild_from_record``) applies
   the stream class. ⚠️ Two MECHANICAL adjustments in :func:`node_statement` are
   not projection classes and can make the written set NARROWER than the view:
   ``embedding`` is written in its own clause rather than in ``$props``, and a
   FALSY :data:`VERBATIM_MARKER` is popped and not re-added (reachable only from
   a row written outside the SDK's ``'true'``/``NULL`` writer, so it is named
   rather than feared). ⚠️ The five declared classes are exceptions to a
   DIFFERENT
   identity — *the view against the record's PRIMARY column set* — and they run
   in BOTH directions (the count and the classification are stated once here and
   mirrored by §17.2 of `STORAGE-ARCHITECTURE.md` and by
   :mod:`tests.test_cache_projection`'s own enumeration):
   ONE **value-filling** class does not widen the field set at all — the
   properties in :data:`FOLD_ON_CREATE_DEFAULTS`, whose DEFAULT belongs to the
   fold, not the record: a record row that is silent about a kind materializes the
   same constant the live fold would have written (#9634), for a column the
   record HAS (the placement table spells `subjectKind`/`objectKind`/`title` `P`),
   so only a silent row's VALUE is supplied. Reading it as a widening is the
   field-set/value conflation this paragraph exists to avoid. Its bound is the
   drift pin, not an exactness test: the plan is compared against the VIEW, and a
   constant the VIEW carries is on both sides of that comparison. A **second**
   declared exception is :data:`DERIVED_FROM_RECORD` (§2.6's DERIVED half,
   #9974): a property the record is FORBIDDEN to store and the cache must COMPUTE
   from the columns it does carry — `status` from a supersession carrier
   (`supersededBy`/`supersededAt`), for which `public.object` has NO column at
   all, so this one DOES widen the field set. Same bound, same pin shape; the
   divergences from the record's own `public.*_current_status` views (their
   `ELSE 'live'` branch, and their `IS NOT NULL` reading of an empty carrier) are
   measured and named at that table, as is the residue it deliberately does NOT
   derive. A **third** declared exception is :data:`EVENT_STREAM_FOLDS` (#10198):
   the same `status`, derived not from a COLUMN but from the record's own EVENT
   STREAM (`public.journal_event`), because the connector/pack lifecycle
   vocabulary is declared at RUNTIME by packs and is not reconstructible from any
   row — also a widening, for the same reason.
   Its source is ordered by `seq`, its vocabulary is imported from
   :mod:`tortoise.status_folds` (never restated here), and its bound — the
   terminal statuses the record has no carrier for — is stated at the table.
   That exception reads THREE writers of the field (`EventRecorded` connector/
   pack folds, an `EntityMutated` state op, and the CREATION record), so the
   field is covered on every writer the record can carry — with the residues the
   table names (birth-on-ON-MATCH, ordering, ambiguous names, a null clear).
   A **fourth** declared exception is :data:`_EPISODIC_SOURCE_PROSE_COLUMNS`
   (#10853, narrowed by #10965), and unlike the two WIDENING classes above it
   narrows the field set instead of widening it (the fold class neither widens nor
   narrows — it supplies a value for a column that exists): an EPISODIC Source's `summary` and `_search_text`
   columns (graph property `_searchText`) are §9.1/D1 turn prose **on a
   pre-#10000 row**, the record keeps them and the cache declines them. The drop
   is decided per ROW, from the row itself, because #10000 replaced the
   derivation: a post-#10000 `_search_text` is the session's topic lexicon —
   exactly `", ".join(topics)` — and it IS projected, because it is the only field
   the Source FTS index is declared on and dropping it would un-index a restored
   session (#3518). `summary` stays declined on every row. Costs, named here and
   at that table rather than implied:

   * the **journal fold** projects the pair the capture wrote, and the record fold
     now projects the same post-#10000 pair, so the two rebuild paths agree on
     `_searchText`; they disagree only on `summary` (declined on the record door);
   * **graph-side FTS** survives on a post-#10000 row (the lexicon is restored) and
     goes, with the prose, on a legacy one — where there is no non-prose value to
     keep;
   * **`record-reconcile`** builds its RECORD side through this same projector, so
     its truth side HOLDS `_searchText` for a post-#10000 row, and
     `_partition_cache_owned` keeps every name the record side holds — so a
     drifted `_searchText` is COMPARED and reds. `summary`, and a legacy row's
     whole pair, stay CACHE-HELD — ⛔ NOT "cache-owned": that term is §17.5's for
     the scalars the record CANNOT hold, and this pair the record DOES hold
     (`record_reconcile.RecordReconcileResult.graph_only_total` is the retraction)
     — via :func:`episodic_source_prose_properties`,
     consumed by `record_reconcile.scan_cache_view`, counted under their
     own `graph_only` bucket — visible, and not a divergence against a record that
     is correct. ⛔ The repair for a legacy node is NOT a re-capture: re-capturing
     an already-captured session id is the #1727 no-op replay, so
     ``_materialize_session_source`` — whose only caller sits behind the fresh
     capture gate — never runs and the stale pair survives (measured:
     `#10852` review, P1). THREE routes clear an existing node's pair: a retry
     of a prior FAILED capture (that arm re-runs the mint for the SAME `url`
     — `retry_failed_capture`, #2335 — so the capture's SETs overwrite the
     pair); ``session_source_prose_migration.reconcile_session_source_prose``
     (#11070, shipped via `#11152` — repairs/clears the PAIR IN PLACE from the
     record, the only NON-destructive route); and a full ``record-restore``,
     whose wipe is whole-graph (see below). Two things that read like repairs are NOT (#10852 review, P3): a
     capture under a NEW `session_id` MERGEs a DIFFERENT node and leaves the old
     one alone; and a record-sourced re-projection for a node the projection
     CREATES is a PREVENTION route — its write is additive
     (`MERGE … SET n += $props`, :func:`node_statement`) and `summary` is
     declined on this door, so a node that ALREADY carries the old pair keeps
     it. ⛔ That is the INCREMENTAL path only (`project_records` on a live graph):
     ``record-restore`` / ``rebuild_from_record`` WIPE first
     (``_wipe_all_nodes``, *"unconditional and whole-graph"*), so a FULL restore
     CLEARS the pair rather than preserving it — measured by
     ``test_the_record_restore_writes_no_story_arc_on_any_node`` arm (3), which
     creates a legacy node carrying the prose, restores, and asserts the total
     prose size is 0. Anything else needs an explicit property clear (#11070).
   A **fifth** declared exception is :data:`CACHE_FORBIDDEN_COLUMNS` (#10394),
   and it is the one that is NOT per-row: a COLUMN the record holds as authority
   and the cache must never materialise, because §9.1/D1 places the text in
   Postgres and forbids it in the graph — and the record IS the Postgres half.
   `story_arc` on `public.source` and `public.event` is the case, and it is ONE
   DECLARATION with ONE ENTRY PER TABLE (`CACHE_FORBIDDEN_COLUMNS` is keyed by
   table and consulted by `projected_columns`), so the placement rows keep their PRIMARY
   class (the record does store it) and state `not projected` beside it. ⛔ The
   distinction from the fourth exception is the point: that one decides per ROW
   from the row's provenance because a LATER build made the column legitimate
   again; this one is a stable statement about the two stores, so a row-derived
   test would answer the wrong question. Consumers are aligned rather than
   assumed: `record_reconcile` counts a legacy cache copy as `graph_only` (not a
   divergence), and `record_serve.parity_difference` EXPLAINS it (not a
   projection gap) — both pinned, because either rule, left alone, would tell an
   operator to re-project a field the projection exists to keep out.
4. **A cold region reads ``not-hot``, never ``empty``.** ``declaration_for``
   writes the known-hot set **after** the writes and as a function of what was
   materialized, so a record key the projection did not write is not claimed
   hot; with that declaration in force ``residency.apply_residency`` turns an
   ``empty`` read over it into ``degraded`` — never the ``empty`` that is
   indistinguishable from *"no such node"* (#7952, §17.2/§17.3).

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the write chokepoint.** §2.3/Interface C is ``#7951``; this is the
  third step of that path and routes no live writes.
* **Not the read path** (``#7970``, deferred by §2.0) nor **the residency
  router** (``#7953``).
* **Not a second declaration of the placement.** The table is parsed, and
  ``tests/test_cache_projection.py`` pins the parse against the schema on disk.
* **No migration-safety apparatus.** Owner scope ruling 2026-10-10: the only
  customer is the dogfood instance. No dual-write window, no backfill, no
  cutover, no rollback-parity surface appears here.
* **No public surface change.** ``FalkorProjection`` is internal; no MCP tool
  and no public ``TortoiseSDK`` method is added, removed or renamed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import record_projection as _rp
from .residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    ResidencyDeclaration,
    declare_residency,
)
from .security import validate_rel_type
from .session_source_display import SESSION_SOURCE_SUMMARY_PREFIX
from .stored_form import STORED_VECTOR_PROPS, VERBATIM_MARKER, is_verbatim

__all__ = [
    "ADDITIONAL_PRIMARY_COLUMNS",
    "CACHE_FORBIDDEN_COLUMNS",
    "GENERATED_COLUMNS",
    "DERIVED_FROM_RECORD",
    "EVENT_STREAM_FOLDS",
    "FOLD_ON_CREATE_DEFAULTS",
    "KEY_PROPERTY",
    "NODE_TABLES",
    "PACKAGED_PLACEMENT_TABLE",
    "PLACEMENT_DOC",
    "RELATION_TABLE",
    "AmbiguousRecordColumn",
    "CacheProjectionError",
    "LifecycleEvent",
    "PlacementTableUnavailable",
    "ProjectionPlan",
    "ProjectionReport",
    "RecordNode",
    "RecordRelation",
    "RecordView",
    "Statement",
    "UnknownRecordColumn",
    "UnknownRecordLabel",
    "UnprojectableRecord",
    "ambiguities",
    "apply_event_stream_folds",
    "bookkeeping_columns",
    "cache_forbidden_columns",
    "canonical_property",
    "declaration_for",
    "declaration_is_sound",
    "declared_key",
    "episodic_source_declined_properties",
    "episodic_source_prose_properties",
    "is_episodic_source_node",
    "node_from_row",
    "node_statement",
    "parse_placement_rows",
    "placement_columns",
    "placement_spellings",
    "project_records",
    "projected_columns",
    "projected_properties",
    "read_lifecycle_events",
    "read_record_view",
    "record_plan",
    "relation_from_row",
    "relation_statement",
    "stored_vector_props",
]

#: label → record table (the layer tables `#7968` built).
NODE_TABLES: dict[str, str] = {
    "Point": "point",
    "Event": "event",
    "Subject": "subject",
    "Object": "object",
    "Source": "source",
    "Session": "session",
}

#: The relations table — every edge is a row (ONTOLOGY §8 reification).
RELATION_TABLE = "relation"

#: The record's own coordinates: supplied by the schema/projection, never a node
#: property. Kept in step with `record_projection._BOOKKEEPING_COLUMNS` by the
#: suite's unclassified-column pin.
RECORD_BOOKKEEPING_COLUMNS: frozenset[str] = frozenset({
    "org_id", "graph_id", "props", "node_label", "node_key",
    "rel_type", "src_label", "src_key", "tgt_label", "tgt_key",
})

#: Coordinates that are bookkeeping on ONE table only. `public.relation.id` is
#: the row's own identity column (`bigint GENERATED ALWAYS AS IDENTITY`), not an
#: edge property — while on `public.point`/`public.event`, `id` IS a property
#: and the Event's key lives in `event_id`.
_TABLE_BOOKKEEPING: dict[str, frozenset[str]] = {
    "relation": frozenset({"id"}),
}


def bookkeeping_columns(table: str) -> frozenset[str]:
    """The columns of `table` that are coordinates, never properties."""
    return RECORD_BOOKKEEPING_COLUMNS | _TABLE_BOOKKEEPING.get(table, frozenset())

#: Columns the placement table does not spell and the schema does not refuse.
#: Four, and each is named with its reason — because the alternative is a
#: SILENT DROP, which is the failure Interface A/B both forbid.
#:
#: * `point.baseline_alpha` / `point.baseline_beta` — §2.8's narrowed carve-out
#:   exception: the author-set prior and the α/β it names are a
#:   NON-RECONSTRUCTIBLE input, therefore PRIMARY. The table spells only
#:   `baseline_source` (§2.7), and the schema adds the pair it governs.
#: * `subject.superseded_by` / `subject.superseded_at` — the Object row's
#:   lifecycle fields (§2.6), which `public.subject` carries too. The table
#:   spells them on Object only; the graph property names are Object's
#:   (`supersededBy`/`supersededAt`), which is what the fold writes.
ADDITIONAL_PRIMARY_COLUMNS: dict[str, dict[str, str]] = {
    "point": {"baseline_alpha": "baseline_alpha", "baseline_beta": "baseline_beta"},
    "subject": {"superseded_by": "supersededBy", "superseded_at": "supersededAt"},
}

#: The **fold's** `ON CREATE` constants, per label — the properties whose DEFAULT
#: is the fold's, not the record's (#9634).
#:
#: WHY THIS TABLE EXISTS. A bare write journals NO discriminator ONCE #9572
#: LANDS — that branch (`fix/9572-discriminator-clobber`) removes the fabricated
#: `subjectKind`/`objectKind` default from every write door, which is correct:
#: the record must not state a kind nobody named. This branch carries BOTH
#: halves (#9634's record side was closed by #9711, which introduced this table;
#: #9572 is the write-door side), so a bare write journals no kind at all and
#: the FOLD supplies the default below — and the divergence is reachable through
#: any record row written without the column.
#:
#: The FOLD supplies that default under `ON CREATE` —
#: `projection/entities.py`'s `_upsert_subject` writes
#: `s.subjectKind=coalesce($sk, 'other')` and `_upsert_object` writes both
#: `o.objectKind=coalesce($ok, 'other')` and `o.title=coalesce($title, '')`.
#: `public.subject.subject_kind` / `public.object.object_kind` / `title` are
#: NULLABLE, and `_props_from_row` drops a NULL column (#7971 property 3 — no
#: fabricated authority reaches the cache), so a **record-sourced** rebuild
#: materialised a `:Subject` with NO `subjectKind` at all: a property ONTOLOGY
#: §4.2 (Subject) / §4.3 (Object) lists as REQUIRED. `rebuild_all` (journal
#: replay, which runs the fold) was never affected — so live ≠ record.
#:
#: The fix is NOT to write the default back into the record (that is the
#: fabrication #9572 deletes) and NOT to invent one here: it is to apply the
#: FOLD's own `ON CREATE` constant on the way into the cache, so a record-sourced
#: node is the node the live fold would have built. These are therefore
#: **fold-derived defaults**: the record carries a kind when one was NAMED (the
#: `EntityMutated` state map does), and this table supplies the same fallback the
#: fold supplies when none was.
#:
#: ✅ THE SUBJECT HALF IS PARITY TOO (#9572). `_upsert_subject` now writes
#: `s.subjectKind=coalesce($sk, 'other')` under `ON CREATE`, with `$sk` built as
#: `ev.get("subject_kind")` — a NULL default, never `"other"`. An ABSENT key and
#: a PRESENT-and-null key therefore behave IDENTICALLY (both coalesce to
#: `'other'`), exactly as the Object clauses do, so the record row and the fold
#: agree on the null case and this table's fill reproduces the fold rather than
#: overriding it. The Subject row STAYS (a NULL record column still needs the
#: `'other'` fill to match the fold, ONTOLOGY §4.2 REQUIRED) — it is parity now,
#: not a decision of this loader's.
#:
#: ⚠️ HISTORY, kept because it is why the two halves look different in git, and
#: because the pre-#9572 shape is one edit away. The clause USED to be
#: `s.subjectKind=$sk` with the parameter `ev.get("subject_kind", "other")` — a
#: default for an ABSENT key only — so a present-null key set `$sk = null` and
#: `SET ... = NULL` REMOVED the property (`#7936`), while an absent key got
#: `'other'`. The loader then had to DECIDE the null case, which is why a
#: RESIDUAL note used to stand here; #9572 removed the split at the fold, so the
#: residual is DISCHARGED and must not be reinstated as prose.
#:
#: ⚠️ What #9572 deliberately does NOT move is the `ON MATCH` arm —
#: `s.subjectKind=coalesce($sk, s.subjectKind)` preserves a STORED kind, which
#: IS the fix: a bare re-create sends NULL and a stored `team` survives. That is
#: exactly why the parameter must never regain a fabricated default: with `$sk`
#: defaulted, "the caller named none" and "the caller named other" are the same
#: value at that arm, and the stored kind is clobbered.
#:
#: EXCLUDED, each with its reason — the fold's OTHER `ON CREATE` defaults are
#: not applied here, and the reasons are not all "it is not a constant":
#:
#: * `status` — PROJECTION-OWNED CACHE (§2.6/
#:   `record_projection.OpDispatch.never_store`); the schema has no column for it.
#:   It is NOT a constant this table may fill, because it is not a DEFAULT here:
#:   it is DERIVED from another column the record does hold, and that derivation
#:   is :data:`DERIVED_FROM_RECORD`'s job (below). Filling `'live'` from this
#:   table would *fabricate* a positive claim over a value the record carries —
#:   the one thing a fold-derived default must never do (#9634's own rule:
#:   a default stands in where the record is SILENT).
#: * `createdAt` (`coalesce($ca, $now)`) — not a constant: the default is the
#:   REBUILD INSTANT, so there is nothing for a table to pin.
#: * `Source.sourceKind` — ONTOLOGY §4.6 REQUIRED, `public.source.source_kind`
#:   NULLABLE, and `s.sourceKind=$sk` IS the same construction as Subject's…
#:   but the fold has TWO defaults for it, so there is no single constant to
#:   transcribe: `_upsert_source` passes `ev.get("sourceKind", "document")`,
#:   while `_mint_source_stub` (`projection/edges.py`) defaults to
#:   `"agentSession"` for a `session:` ref and `"document"` otherwise — its own
#:   docstring records that `session:` stubs minted as `document` were the wart
#:   #5012 removed. Defaulting a NULL row to `"document"` would therefore
#:   FABRICATE the wrong kind on every session Source, so the value is not one
#:   this table can hold; it needs the prefix rule (or a record column that
#:   states it). Tracked in #9710.
#: * `Source.is_episodic` (`s.is_episodic=$ep`, TRUE only for a `session:` ref)
#:   — the same prefix-conditional shape as `sourceKind`, and the flag
#:   `graph-scripts/backfill_is_episodic.py` reads to exclude a session Source
#:   from its one-time backfill. `source.is_episodic` is placed PRIMARY and
#:   NULLABLE, so a silent row loses it exactly like the kinds. Tracked in
#:   #9710.
#: * `Source.title = coalesce($title, $url)` and the other copies in the same
#:   clauses (`canonicalUrl`, `urlAliases=[$raw_url]`, `ingestedAt`, and
#:   `id = coalesce($id, $url)`) — not CONSTANTS: a copy of another record
#:   column, or the write instant.
#: * `Source`'s `contentHash` (`coalesce($hash, '')`), `externalId` and
#:   `version` (`CASE WHEN $stub_completion THEN NULL … END`) — constant-VALUED
#:   for a creator record, and still excluded, because that clause emits the
#:   value conditionally: a record that COMPLETES a stub gets `NULL` (the
#:   property stays absent, #5097), and `public.source` has no column that tells
#:   the two apart — so applying the constant would fabricate `version=1` /
#:   `externalId=''` on a node the live fold leaves bare (and `version` is a
#:   creator-only ordinal the record deliberately never writes). Constant is not
#:   the only test: the value must be DECIDABLE FROM THE ROW.
#:   Both residuals are tracked in #9710, not silently absorbed here.
#:
#: ⚠️ **THIS DECLARATION IS NOT TOTAL, and the bound is named rather than
#: implied.** The fold writes further `ON CREATE` values this loader does not
#: reproduce, and the biggest is `:Event`: `_upsert_event` builds a `props` dict
#: of DEFAULTS (`eventKind` `''`, `subject`/`object` `''`, `startedAt` `''`,
#: `participants` `[]`, `classificationLevel` `'internal'`, `format` `'jsonl'`,
#: and `id`) and writes all of it unconditionally with `e += $props`, so a
#: record-sourced `:Event` loses every one of them the record row does not carry
#: (seven, for an event whose optional columns are NULL) — including the
#: ONTOLOGY §4.5-REQUIRED `eventKind` when a producer omits it. Also undeclared:
#: the other `:Source` create clauses (`_materialize_connector_source`,
#: `_upsert_document`, `_mint_source_stub`) and the `_stub` adoption marker the
#: fold clears. Filed as #9721, as its own change, for two shapes this table cannot
#: express: `participants` is a LIST (a mutable default per node) and `id` is a
#: key-ish copy of `eventId`.
#:
#: So: the table plus the exclusions above are what the pin below covers. It is
#: not a census of every `ON CREATE` value in the fold, and it must not be read
#: as one.
#:
#: `tests/test_cache_projection.py` pins this table against the fold's own
#: clauses (source-parsed, as the repo does for other cross-module contracts), so
#: a change to the fold's constant reds the pin instead of silently re-opening
#: the divergence. It pins the clauses it NAMES; it is not a discovery sweep over
#: the fold (the exclusion list above is hand-maintained, and the pin says so).
FOLD_ON_CREATE_DEFAULTS: dict[str, dict[str, Any]] = {
    "Subject": {"subjectKind": "other"},
    "Object": {"objectKind": "other", "title": ""},
}

#: The CACHE-owned lifecycle properties, DERIVED from record COLUMNS — the
#: *other half* of §2.6's split, and the half the CACHE had no owner for (#9974).
#:
#: WHY THIS TABLE EXISTS. §2.6 (`02-identity-and-placement.md`) classifies
#: `Object.status` / `Subject.status` as **DERIVED (a cache)** — *"the journal
#: value is not the record"* — and both schemas refuse the name in `props`
#: (`chk_object_props_no_derived` #8179, `chk_subject_props_no_derived` #8263),
#: so the record must not store it. The record layer DOES define the fold —
#: `public.object_current_status` / `public.subject_current_status`
#: (`20261012000004_record_procedural.sql`) compute it as
#: `superseded_by IS NOT NULL OR superseded_at IS NOT NULL → 'superseded' ELSE
#: 'live'` — but the CACHE projection invoked no fold, so a **record-sourced
#: rebuild** materialized every `:Object` with no `status`. And
#: `live._terminal_excluded` reads an ABSENT status as LIVE *by design* (the
#: legacy-node rule), so a superseded Object came back LIVE and
#: `entity_identity.resolve_entity_id` resolved a name to it. That is #9974,
#: measured: superseded live → rebuild → `status` NULL → the name resolves to
#: the superseded id.
#:
#: WHY DERIVATION AND NOT A FILL. `status` is not a constant: the fold derives
#: it from the event stream (`_fold_object_superseded` → `'superseded'`;
#: `status_folds` / `_fold_object_status` → the connector and pack-declared
#: vocabularies; `EntityMutated op=restatus` → a caller's explicit lifecycle
#: write). `FOLD_ON_CREATE_DEFAULTS` above may NOT fill it, because filling
#: `'live'` where the record names a supersession would replace a carried value
#: with a fabricated one.
#:
#: WHAT THIS TABLE CAN AND CANNOT DERIVE — stated, because "a cache owns it" is
#: only true where the record carries the signal, and the residue is a decision
#: rather than an omission (#9974):
#:
#: * **DERIVED — a supersession, from EITHER PRIMARY carrier.** §2.6 makes
#:   `supersededBy` AND `supersededAt` PRIMARY, and the record's own views read
#:   them with `OR`. Both carriers are needed because the record genuinely
#:   produces rows with only one of them: an id-only (legacy §6b)
#:   `ObjectSuperseded` writes `superseded_at` ALONE and replays through the
#:   fold as `status='superseded'` with `supersededBy=''`
#:   (`test_status_projection.py::test_rebuild_all_legacy_6b_id_only_shape_supersedes`,
#:   #2164/#2193), while an `op=supersede` mutation may write `superseded_by`
#:   with no instant. Deriving from `supersededBy` alone left the id-only shape
#:   resurrected — #9974's own measured defect, still open on a shape the record
#:   still emits.
#:   ⚠️ TWO deliberate divergences from those views, both measured, so neither is
#:   a guess:
#:   (a) The views' `ELSE 'live'` branch is NOT mirrored. An absent status
#:   already reads LIVE to `live._terminal_excluded` (its documented legacy-node
#:   rule), so the branch changes no terminal decision — while WRITING `'live'`
#:   would assert a positive lifecycle claim about a row whose real status the
#:   record may carry elsewhere (a `restatus`, below) and this table cannot
#:   read. Same no-fabrication rule `FOLD_ON_CREATE_DEFAULTS` obeys.
#:   (b) A carrier must be PRESENT AND NON-EMPTY, which is narrower than the
#:   views' `IS NOT NULL` — and it is the views, not this table, that are wrong
#:   on that edge. Measured on a live engine:
#:   `update_entity(obj, supersededBy="")` leaves the node
#:   `status='live', supersededBy=''`, so `'' IS NOT NULL` makes
#:   `public.object_current_status` answer `'superseded'` for a node the graph
#:   holds LIVE. Truthiness agrees with the graph here, so it is what this table
#:   uses; `tests/test_cache_projection.py`'s
#:   `test_an_empty_carrier_is_not_a_supersession` pins the measured edge.
#: * **NOT MONOTONE — the carriers CAN be cleared, and the record writes that.**
#:   An explicit `sdk.update_entity(obj, status='live', supersededBy=None,
#:   supersededAt=None)` journals `EntityMutated op=supersede` with null state and
#:   the record mutation is `UPDATE public.object SET superseded_by = %s,
#:   superseded_at = %s` bound to `(None, None)` — measured, carriers cleared, and
#:   a rebuild then correctly derives nothing. Only the STATUS-ONLY
#:   `update_entity(status='live')` shape is a record no-op (`never_store` yields
#:   a literal `SET node_key = node_key`), so only that shape leaves an
#:   un-supersede unrecorded. Do not generalise one shape into "the record cannot
#:   express un-superseded"; it can, via the carriers.
#: * **⚠️ ONCE SOUND TO FAIL: a CARRIER SET WITHOUT THE STATUS FOLD — a
#:   LIVE-WRITER divergence, measured under and CLOSED AT THE WRITE DOOR by
#:   #10192.** A carrier present does NOT by itself imply the node's `status` is
#:   `'superseded'`. Measured on a live engine BEFORE the guard, all three
#:   ACCEPTED by `update_entity` (and by `create_object`; both doors now refuse
#:   them unless the same call supplies `status='superseded'` —
#:   `sdk._refuse_bare_lifecycle_carrier`). ⚠️ The refusal keys on the record's
#:   carrier COLUMN through `record_projection.snake`, not on a list of prop
#:   names, so it covers `superseded_by`/`superseded_at` as well as
#:   `supersededBy`/`supersededAt` — the camel-only list was inert against the
#:   snake spelling, which reaches this very column (PR #10379 review P1):
#:
#:       update_entity(obj, supersededBy='v2')                  -> status='live', supersededBy='v2'
#:       update_entity(obj, supersededAt='2026-01-01T00:00:00Z') -> status='live', supersededAt='…'
#:       update_entity(obj, supersededBy='')                    -> status='live', supersededBy=''
#:       update_entity(obj, status='superseded', supersededBy='p-v2') -> status='superseded'  (the coherent pair)
#:
#:   The record wrote the carrier (`op=supersede` is the classifier's reading of
#:   that write), so a rebuild would materialize a TERMINAL node for an entity
#:   the live graph holds LIVE — `live._terminal_excluded` drops it from recall
#:   and `entity_identity.resolve_entity_id` refuses its name. BOTH carriers were
#:   exposed; the empty-carrier case is the one this table escapes (see (b)
#:   above), and the non-empty cases were #10192's, settled on the WRITE-DOOR
#:   half (the record's fold cannot be changed: it is a view over
#:   `public.object` and cannot see the graph, and a second rule here would give
#:   the cache and the record two different folds — the defect #9974 is about).
#:   ⚠️ The refusal closes the DOOR, not the rows: a `public.object` row written
#:   before the guard still carries a bare carrier (and the record still mints
#:   one through the legacy id-only `ObjectSuperseded`, which writes the carrier
#:   PAIR coherently with `status`), so this table's derivation stays the right
#:   reading of the record — it is no longer fed a row where it disagrees with a
#:   node the tenant doors made LIVE.
#: * **NOT DERIVED FROM A COLUMN — every `restatus` value
#:   (`deprecated`/`archived`/`retracted`/`draft`, and `superseded` reached without a
#:   carrier).** `public.object` has no row-level trace and the props tail refuses
#:   the name, so an explicit `update_entity(status=…)` is invisible HERE. It is
#:   NOT invisible to the cache: `EntityMutated op=restatus` is journalled, and its
#:   `state.status` is read from the record's OWN layer-1 history
#:   (`public.journal_event.payload`) by :data:`EVENT_STREAM_FOLDS` — the same
#:   stream, the same placement, the same principle as #10198's connector fold.
#:   ⛔ That is NOT a second source of truth: it is the record's own history, and
#:   it is the same record the live fold applied (`MATCH (n:$label {id:$rid}) SET
#:   n += $state`), so a rebuild reproduces the value BY CONSTRUCTION rather than
#:   by a rule that could drift from the fold. This table therefore stays a
#:   COLUMN-only derivation; the value's home is the stream.
#:   ⚠️ Why not widen `public.*_current_status` instead: its `COMMENT` delegates
#:   the non-row-reconstructible states to the projection
#:   (`20261012000004_record_procedural.sql`), and a SQL copy of the vocabulary
#:   would give `status_folds` a second home — the one thing #2729 forbids.
#:   ⚠️ #9974 CLOSED THE OTHER HALF OF THIS BOUND, and it did so in the stream
#:   rather than here: a `restatus` to a TERMINAL status (`archived`) now folds
#:   from the record's own history, in `seq` order, UNCONDITIONALLY (the live fold
#:   applies a state op as `SET n += $state`, with no terminal guard) — and a
#:   later connector fold is then refused by the terminal guard, so the replay
#:   latches exactly where the live graph latches. See
#:   :data:`EVENT_STREAM_FOLDS` and :class:`LifecycleEvent`'s `restatus_status`.
#:   ✅ **CLOSED (#9974's BIRTH leg) — the CREATION writer is now read.**
#:   `_upsert_object`/`_upsert_subject` seed `status=$st` from the creation
#:   record's own payload (`projection/entities.py`) — DERIVED-OR-ABSENT since
#:   #9974 R1 (#10524), which removed the fold's fabricated `coalesce($st,
#:   'live')` and the door's `out["status"] = "live"` — and `sdk.create_entity`
#:   accepts a caller status — ⚠️ the SUBJECT door validates it against §4.2, the
#:   OBJECT door validates nothing (`sdk._check_endpoints`'s status-refusal loop,
#:   reached through `sdk._validate_bundle` — it says so of `:Object`/`:Event`:
#:   "do not validate, they STORE the value"; cited by SYMBOL, not by line, per
#:   #10524's own rule that a merge re-stales a line cite while a symbol cannot)
#:   — so
#:   `create_object(name, status='superseded')` writes a terminal status at
#:   birth that `never_store` keeps out of the layer tables. `ObjectRegistered` /
#:   `SubjectAdded` are now selected from the SAME stream (their `payload ->
#:   'status'`) and replayed in `seq` order, ON CREATE, so a record-sourced
#:   rebuild reproduces the value `rebuild_all` (which reads the JSONL payload)
#:   writes instead of materializing NO status → LIVE.
#:   ✅ **WHERE THE RECORD STATES NO STATUS, THE TWO ROUTES NOW AGREE — this was
#:   a DIVERGENCE when this leg was written, and #9974 R1 closed it from the
#:   other side.** At the time, `_upsert_object`/`_upsert_subject` seeded
#:   `status=coalesce($st, 'live')`, so `rebuild_all` materialised `'live'` for a
#:   silent creation record while this replay wrote nothing; that difference in
#:   the PROPERTY SET was stated here as THE PRICE. R1 (#10524) removed the
#:   fold's fabricated default — the seed is `status=$st` — so a status-less
#:   birth now leaves the property ABSENT on BOTH routes. The price is GONE, not
#:   merely re-described: neither route asserts a lifecycle fact the record never
#:   stated, and an `rebuild_all`-vs-`rebuild_from_record` equality probe no
#:   longer needs to treat `absent ≡ 'live'` for this property.
#:   ⚠️ ONE STATED BOUND rides that leg, deliberate:
#:   "the EARLIEST creation record speaks" is exact only where that record
#:   is the write that CREATED the node. `_upsert_object`/`_upsert_subject` MERGE
#:   on `id` and write `status` ON CREATE only, so for an id that already existed
#:   (a stub/mention path, `create_object(..., id=…)`, a pre-#3590 graph, or a
#:   delete-then-re-create under an EXPLICIT id — the SDK mints a fresh id on its
#:   own delete+re-create path, so that one is not reachable) the
#:   live node took the ON MATCH arm and kept its own status while this replay
#:   would otherwise write the record's.
#:   ⛔ THE DIVERGENCE RUNS IN BOTH DIRECTIONS, and only ONE of them is closed.
#:   (a) THE HARM DIRECTION — a TERMINAL the graph really DID hold, discarded by
#:   the birth arm, so the rebuild answers CURRENT. PRE-LATCH the overwrite
#:   ordering `[restatus(seq 1,'archived'), birth(seq 2,'live')]` rebuilt `live`
#:   while the graph holds `archived`, and `live._terminal_excluded` then admits a
#:   superseded entity whose name still resolves — that ordering is CLOSED, see
#:   (a-1). Reachability is not exotic: the first journaled creation record is
#:   a RE-MENTION whenever the node pre-existed (stub, explicit `id=`, pre-#3590
#:   graph, partial replay from `start_seq`), and a caller-supplied
#:   `create_object(..., status='live')` states it explicitly — so the failure
#:   does not degrade to the benign "absent reads LIVE" flavour, it actively
#:   asserts CURRENT. ⚠️ #9974 R1 NARROWED this: `sdk._entity_door_props` used to
#:   seed `status='live'` on every create, so any SDK birth could supply the
#:   value; R1 deleted that fabrication, so the value now has to be stated
#:   explicitly (or come from a pre-R1 journal). Narrower, still open.
#:   ⛔ ONE of its two orderings is CLOSED, and the other is NOT — do not read this
#:   as "the harm direction is fixed":
#:   (a-1) the OVERWRITE ordering (above) is CLOSED. The birth arm now honours
#:   the terminal latch (`_is_terminal_status`) every other in-stream fold here
#:   honours, so it can no longer overwrite a terminal the REPLAY ALREADY HOLDS.
#:   (a-2) the DELETE-THEN-RE-CREATE ordering is STILL OPEN, and the latch
#:   cannot reach it: it is the `birth_seen` earliest-only rule that discards the
#:   value, not the (now-guarded) assignment. `_upsert_object` writes `$st`
#:   ON CREATE (derived-or-absent since #9974 R1), so a re-create under an
#:   EXPLICIT id puts
#:   the LATER record's terminal in the graph while this replay answers from the
#:   FIRST incarnation — `[birth(seq 1, None), birth(seq 2, 'archived')]` and
#:   `[birth(seq 1, 'live'), birth(seq 2, 'archived')]` both rebuilt absent/`live`
#:   against a graph holding `archived` (MEASURED; exhaustive sweep over
#:   `{restatus, birth, delete}` × length ≤ 4 finds 52 harm orderings, every one of
#:   them containing a delete). The SDK mints a FRESH id on its own delete+re-create
#:   path, so this needs an explicit `id=` — narrower, not closed. See the birth
#:   arm's own STATED BOUND in :func:`apply_event_stream_folds`.
#:   (b) THE FAIL-SAFE DIRECTION — still open, and the reason the latch is not
#:   the whole bound: where the id pre-existed holding a NON-terminal status, the
#:   ON MATCH arm keeps it live while this replay writes the record's, i.e. a
#:   TERMINAL value the graph never held (`[restatus(seq 1,'live'),
#:   birth(seq 2,'archived')]`). That direction is conservative — it makes the
#:   rebuild claim LESS life, never more — so it cannot produce #9974's harm.
#:   (c) THE THIRD ORDERING, NAMED (review P2): the latch also refuses a TERMINAL
#:   **pre-birth fold** in the CREATED case — an id that did NOT pre-exist, where
#:   that fold matched no node and wrote NOTHING live, and the creation record then
#:   really is the write that made the node (`[pack-fold(seq 1,'archived'),
#:   birth(seq 2,'live')]` → replay `archived`, graph `live`; same for
#:   `deprecated`/`retracted`). Pre-latch this returned the birth value (round 6's
#:   fix). ⚠️ This is pack-declaration-conditional — no pack in `packs/` declares a
#:   terminal fold target today — and it lands in the FAIL-SAFE direction (the
#:   rebuild claims less life, never more), which is the deliberate trade: the
#:   replay cannot distinguish "the fold matched nothing" from "the graph held
#:   it", and preferring the terminal never re-opens #9974's harm.
#:   The record carries no create-vs-match signal, so the reader cannot tell (a-1),
#:   (a-2), (b) and (c) apart; this is the same class of named residue as
#:   the ordering bounds below.
#:   ⚠️ Also STATED BOUND — a null-valued `state.status` (`update_entity(obj,
#:   status=None)`) is NOT modelled: the graph applies `SET n += {status: null}`,
#:   which REMOVES the property, while this reader skips a non-string value and
#:   keeps whatever an earlier fold left. The two DISAGREE in two reachable
#:   places, and the second needs no stream fold at all (#9974 review round 11):
#:   (a) an explicit null arriving AFTER a restatus/connector fold on the same
#:   node, and (b) an explicit null after the carrier derivation —
#:   `update_entity(obj, status='superseded', supersededBy='p2')` then
#:   `update_entity(obj, status=None)` leaves the graph LIVE (the `SET n += {status:
#:   null}` removes the property, and `live._terminal_excluded` reads only
#:   `n.status`) while the record's surviving `superseded_by` still derives
#:   `superseded` through :data:`DERIVED_FROM_RECORD`, so the rebuild HIDES a name
#:   the graph resolves. (b) predates this change; only the scope of the residual
#:   is at issue here. Closing either means modelling an explicit removal as a
#:   fold output, which is its own change and is recorded on #9974 rather than
#:   assumed here.
#:
#: `(property, source properties, value)`, applied in :func:`node_from_row` only
#: where the record is SILENT about the target property — which, for a name the
#: schema refuses, is always. The value is derived when ANY declared source is
#: present and non-empty; the sources are alternatives (the record emits each
#: alone), never a preference order.
DERIVED_FROM_RECORD: dict[str, tuple[tuple[str, tuple[str, ...], Any], ...]] = {
    "Object": (("status", ("supersededBy", "supersededAt"), "superseded"),),
    "Subject": (("status", ("supersededBy", "supersededAt"), "superseded"),),
}

#: The properties the cache COMPUTES from the record's EVENT STREAM — the third
#: declared exception to "the cache is the record's rows", and the answer to
#: #10198.
#:
#: WHY A THIRD TABLE. :data:`FOLD_ON_CREATE_DEFAULTS` derives from the FOLD's
#: constants and :data:`DERIVED_FROM_RECORD` derives from the record's COLUMNS.
#: Neither can reach the connector/pack lifecycle: `_fold_object_status`
#: (``projection/entities.py``) writes `in_progress`/`completed` from
#: `status_folds.BUILTIN_CONNECTOR_FOLDS` plus every pack-declared
#: `kindDefs.<kind>.folds` — a vocabulary declared by packs at RUNTIME. No
#: `public.object` row carries it, which is why a record-sourced rebuild
#: materialised every connector-tracked Object with no `status`, why
#: `live._terminal_excluded` read those as LIVE, and why the shipped
#: ``record_reconcile`` trigger reported `status` under `graph_only` — "the cache
#: holds it, the record cannot place it" — for every tenant with connector work
#: items. A `graph_only` count that is permanently non-zero is worthless for the
#: same reason #10017 R2 found `relations_not_written` worthless: it is a
#: declared partial signal that is always on. MEASURED before this table (a real
#: Postgres, the record replayed from a live `EventAPI` journal): the live
#: `github.issue.closed` Object held `status='completed'`, the rebuilt node
#: carried no `status` at all, and the reconcile trigger's `graph_only` carried
#: one `status` per Object.
#:
#: WHY THE EVENT STREAM AND NOT THE VIEW. `public.object_current_status` folds
#: ONLY the two states the Object ROW can reconstruct (`live`/`superseded`) and
#: its own `COMMENT` delegates the rest: *"the connector work-item states
#: (in_progress/completed) are not reconstructible from the Object row and are
#: the projection's (#7969) to fold from public.journal_event"*
#: (`20261012000004_record_procedural.sql`; the same division is recorded in
#: `docs/epics/2026-10-10-7879-postgres-record/03-schema.md` §5.4). So this is
#: not a second answer to the view's question — it is the half the view declines
#: to answer, in the layer the record assigns it. Widening the VIEW instead would
#: have required a record-side declaration table (a schema decision) or a SQL
#: copy of the vocabulary, which §"Whichever it is" on #10198 forbids: the
#: vocabulary has ONE home, :mod:`tortoise.status_folds`.
#:
#: WHAT IT READS. `public.journal_event`, the record's own history, ordered by
#: `seq` — the per-graph journal position, which is exactly the order the live
#: fold applies events in. It reads THREE writers, not one (#9974):
#: `event_type = 'EventRecorded'` (the connector/pack lifecycle leg),
#: `event_type = 'EntityMutated'` rows whose payload carries a STRING
#: `state.status` (a caller's explicit lifecycle write — see
#: :data:`_LIFECYCLE_EVENTS_SQL` for why the predicate is the payload and never
#: the `op` label), and `event_type IN ('ObjectRegistered', 'SubjectAdded')` —
#: the CREATION record, whose `payload ->> 'status'` is the BIRTH value and the
#: only carrier it has (a snapshot row cannot hold the name: no column, and
#: `_split_properties` drops it). The
#: per-event payload keys (`eventKind`/`object`/`about_entities`) are the
#: projection's contract (#7969); they are read HERE, in the projection, and
#: nowhere else. The payload is unwrapped to the event ITSELF first, because the
#: record persists two envelope shapes and only one is flat (see
#: :data:`_PAYLOAD_EVENT`) — the live fold unwraps the nested one, so a reader
#: that did not would fold nothing for every conversation-mined event, silently.
#:
#: HOW IT FOLDS. The LIVE fold's semantics, imported rather than restated:
#:
#: * **the events replayed in `seq` order**, over the ones that name the Object.
#:   The connector/pack leg names it by NAME — its `object` (the event's declared
#:   target) or one of its `about_entities` (the loose list the extraction
#:   pipeline emits instead), with `about_entities` PREFERRED over
#:   `aboutEntities`, not unioned with it, exactly as the live fold reads them.
#:   ⛔ The `restatus`/state-op leg names it by ID (`payload ->> 'id'`), because a
#:   state op is what `sdk.update_entity` applies as `MATCH (n:$label {id:$rid})
#:   SET n += $state` — a name-keyed predicate would have been the wrong
#:   coordinate and would have dropped the very record this leg exists to read.
#:   The two coordinates are unioned in
#:   :data:`_LIFECYCLE_EVENTS_FOR_ONE_SQL`, not substituted for each other;
#: * **pack-declared folds first**, guarded to an Object whose `objectKind` is
#:   the DECLARING pack's namespaced kind; **then the built-in connector
#:   compatibility fold**, guarded to an Object whose kind declares NO lifecycle
#:   (a kind that declares `statuses` OWNS its state — the #2725 collision);
#: * **a terminal Object is never resurrected.** The live guard is `o.status IS
#:   NULL OR NOT (o.status IN $excluded)`, applied to EVERY `SET`, so it is a
#:   state machine rather than a last-writer pick: once a write makes the Object
#:   terminal, the later folds are refused. This replays the stream with that
#:   same guard per event (:func:`_is_terminal_status`), so a pack fold that
#:   targets a terminal word (`archived`) latches instead of being overwritten
#:   by the next event — and a supersession (`superseded`, from
#:   :data:`DERIVED_FROM_RECORD`) still wins in either temporal order. Replaying
#:   the transition rules, rather than reading the last event's value, IS what a
#:   rebuild of an event-sourced projection is (Microsoft Learn, *Event Sourcing
#:   pattern*: rehydration = *"replaying all the events in its stream"*).
#:
#: ✅ **A TERMINAL `restatus` IS honoured now (#9974).** This bullet used to
#: state the opposite — *"the record carries no signal for it"* — and that was
#: true of the LAYER tables only: the value's carrier is the record's own layer-1
#: history (`public.journal_event.payload -> 'state' -> 'status'`), which this
#: reader now folds in `seq` order. So an Object held `archived` and then
#: receiving a `github.issue.closed` keeps `archived` here, matching the live
#: fold's refusal (measured: the terminal guard latches on the replayed value).
#: ⚠️ **BOUND — a status write AFTER a supersession is unmodelled, and it points
#: ONE way (#9974 review rounds 3-5).**
#: The guard above refuses EVERY fold once the carrier-derived `current` is
#: terminal, so a later `state.status` write is ignored: a *status-only*
#: un-terminate (`update_entity(status='live')`, which leaves both carriers in
#: place) rebuilds as `superseded` where the graph reads LIVE, and a later
#: non-terminal value (`draft`, `active`) rebuilds TERMINAL where the graph reads
#: LIVE — the rebuild then HIDES a name the graph resolves, which is #9974's own
#: read consequence inverted. Ordering it is IMPLEMENTABLE, not impossible: the
#: carrier is written by a record in this same stream (`EntityMutated
#: op=supersede`, or the legacy id-only `ObjectSuperseded`) and therefore carries
#: its own `journal_event.seq`; what is missing is reading that seq, which is a
#: follow-up on #9974 and not something this table can infer. Do NOT read this as
#: "the record cannot express the order".
#: ⚠️ **BOUND — the fold's AUDIT emission is not reproduced.**
#: `_fold_object_status` emits `status_fold_unapplied` (#1934) when a declared
#: fold's explicit target is absent. That is a warning about a WRITE, not graph
#: state, so a rebuild has nothing to restore from it.
#:
#: `{label: (property, ...)}` — spelled as a table so the suite can pin its
#: scope, exactly as the other two exceptions are pinned.
EVENT_STREAM_FOLDS: dict[str, tuple[str, ...]] = {
    # BOTH families. `status` is a projection-owned cache on each — §2.6 rules on
    # `Object.status` explicitly; the Subject twin is `ONTOLOGY.md:424`
    # ("Projection-owned since #1423") and `03-schema.md:95` ("Deliberately
    # absent: `status` on both (a projection-owned cache)"). And an
    # explicit state-op write reaches `:Subject` through the same `_ENTITY_MUTATION_
    # STATE_OPS` arm as `:Object` — leaving Subject out would close #9974 for one
    # of the two labels that carry the field. #9974's own Subject evidence is the
    # `record_projection.mutations_for({… "label": "Subject", "state":
    # {"status": "archived"}})` measurement on the issue (its headline
    # reproduction is the Object), so this is a member of the ruled field, not a
    # widened scope.
    "Object": ("status",),
    "Subject": ("status",),
}


@dataclass(frozen=True)
class LifecycleEvent:
    """One record-sourced lifecycle record, reduced to what the status fold reads.

    THREE record types ride this shape, because the live fold's `status` writes
    come from FOUR places — the CREATION record, a supersession's CARRIERS (read
    by :data:`DERIVED_FROM_RECORD`, a COLUMN derivation, not a stream leg), a
    connector/pack fold, and a state op. This shape carries the three that are
    not columns; a rebuild that read fewer reproduced part of the field:

    * `EventRecorded` history — an event whose `eventKind` a
      connector or pack fold maps onto ``Object.status`` (#10198). Joined to the
      row by NAME — which is this READER's join key, not the live fold's match:
      since #3590 `_fold_object_status` issues `MATCH (o:Object {id:$n})`.
    * An explicit ``EntityMutated`` whose `state` carries a `status` (#9974) — a
      caller's own lifecycle write, which the live fold applies as
      `MATCH (n:$label {id:$rid}) SET n += $state`. ⛔ It is ANY state op, NOT
      `op='restatus'`: `classify_entity_mutation_op` labels the record by
      precedence (`supersededBy` > `name` > `status`), so
      `update_entity(obj, name='acme-2', status='archived')` journals an
      `op='rename'` CARRYING the status — and its own docstring says a consumer
      wanting every transition "must NOT filter on `op`". Targeted by ID, and it
      is the ONLY carrier the value has:
      `public.object` has no `status` column and the props tail refuses the name
      (`record_projection.OpDispatch.never_store`), so the layer-1 record
      (`public.journal_event.payload -> 'state' ->> 'status'`) is the whole
      signal. Before this field, a record-sourced rebuild dropped the value and
      `live._terminal_excluded` read the absent status as LIVE — #9974's window.
    * A CREATION record — ``ObjectRegistered`` / ``SubjectAdded`` (#9974's BIRTH
      leg). Its `payload ->> 'status'` is the caller's birth status, and it is
      the value's ONLY carrier for the same reason (no column; the snapshot's
      props tail refuses the name). Targeted by ID (the snapshot's record key),
      labelled from the EVENT TYPE (`_BIRTH_EVENT_LABELS`) because a creation
      payload has no `label` key, and applied ON CREATE only — see the replay in
      :func:`apply_event_stream_folds`.

    Deliberately NOT the event payload: a fold that carried the whole dict would
    be a second `:Event` writer, and the projection already materializes the
    `:Event` from its row. `seq` is the graph's journal position (the ordering
    the live fold applies events in) and the ONLY field that is not a graph
    property.

    ``object_name`` is the event's declared target (the record's `object`
    column); ``about_names`` is the loose list the extraction pipeline emits
    instead. Both are graph NAME keys, not ids. ⚠️ NAME-keyed is how this reader
    finds the ROWS, not how the live fold finds the NODE: since #3590
    `_fold_object_status` issues `MATCH (o:Object {id:$n})` after
    `resolve_fold_name` resolves the name to ONE id, because a name-keyed write
    would move every carrier's status — the harm class the identity change
    exists to remove. The name is the record's join key and the id is the
    graph's, and this reader does NOT resolve an ambiguous name the way the live
    fold does: `entity_identity.resolve_fold_name` folds only on exactly ONE live
    holder and folds NOTHING on `ambiguous-name`, while `by_target[name]` folds
    EVERY same-named node. ⚠️ That is a real bound, stated rather than implied
    (recorded on #9974): a name with two carriers can be folded here where the
    live fold declines. A collision across LABELS is separate, and is why the
    connector leg below is `:Object`-only. ``target_id`` is the opposite
    coordinate: the stable `id` a state op names, which is what `state`-op
    writes match on.
    """

    seq: int
    event_kind: str
    object_name: str | None = None
    about_names: tuple[str, ...] = ()
    #: ANY `EntityMutated` carrying a `state.status` — NOT "restatus only": the
    #: reader never inspects `op` (see the class docstring). Value the caller
    #: wrote, from the record's own `state` map. `None` means "this record carries no status write". ⚠️ It does
    #: NOT mean the caller cleared the field: `update_entity(obj, status=None)` is
    #: a real clear (`sdk.py`: "a null REMOVES the property … a clear is CALLER
    #: intent") and that shape is R4's declared bound, not a representable state
    #: here. A caller who wants a live value writes `status='live'`, which IS
    #: carried.
    restatus_status: str | None = None
    #: A state op **or a creation record** — the `(label, id)` the live fold
    #: matches on. `EntityMutated` sets it from `state`, and since #9974's BIRTH
    #: leg an `ObjectRegistered`/`SubjectAdded` sets it from the snapshot's own
    #: `id` (a creation record is not a state op, which is why this docstring
    #: names both — review round 8, P2). The label is carried so a state op
    #: cannot fold onto the other family's node of the same id (ids are
    #: per-label, not global).
    target_id: str | None = None
    target_label: str | None = None
    #: #9974 — the FOURTH `status` writer: the CREATION record's own payload.
    #: `ObjectRegistered` / `SubjectAdded` is a `NodeSnapshot` (`record_projection`)
    #: whose `status` reaches neither a column (there is none) nor the props tail
    #: (`_split_properties` refuses the name), so `payload ->> 'status'` on the
    #: creation record is the value's ONLY carrier — exactly the placement
    #: `EntityMutated`'s `state.status` has. `is_birth` is a separate flag because
    #: a creation record with NO status still decides the ON CREATE arm (see
    #: :func:`apply_event_stream_folds`): the live fold's `_upsert_object` writes
    #: `status=$st` ONLY on create (derived-or-absent since #9974 R1), so the
    #: Earliest creation record is the one that speaks and a later re-mention
    #: never overwrites.
    is_birth: bool = False
    birth_status: str | None = None

    def targets(self) -> tuple[tuple[str, bool], ...]:
        """``(name, is_explicit_object)``, deduplicated, declared target first.

        The live fold builds the same list: the explicit `object` is folded AND
        reported on a miss, an `about_entities` entry is folded silently, and a
        name that is both is written once.
        """
        out: list[tuple[str, bool]] = []
        seen: set[str] = set()
        if self.object_name:
            out.append((self.object_name, True))
            seen.add(self.object_name)
        for name in self.about_names:
            if isinstance(name, str) and name and name not in seen:
                out.append((name, False))
                seen.add(name)
        return tuple(out)

#: The placement table (#7955) — the field contract the schema is pinned to.
#: THE SOURCE OF TRUTH: prose is where this contract is ruled and reviewed, and
#: :data:`PACKAGED_PLACEMENT_TABLE` is GENERATED from it
#: (``tools/gen_placement_table.py``), never hand-edited.
PLACEMENT_DOC = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "epics"
    / "2026-10-10-7879-postgres-record"
    / "02-identity-and-placement.md"
)

#: The same table as PACKAGE DATA (#10663) — the runtime read.
#:
#: WHY IT EXISTS. The doc above is a `docs/` file, and **the deployed image ships
#: no `docs/` tree** (`Dockerfile.hosted` Layer 2 COPYs `tortoise/`, `packs/`,
#: `tests/sample_transcript.txt`, `product/pricing.json`,
#: `supabase/migrations/`, and nothing else) — so every verb that reads the
#: placement table refused in production with *"the placement table is not on
#: disk at /app/docs/…"*, wrapped as *"the record is unreadable"*, exit 2,
#: divergence UNKNOWN. Measured in the shipped image: `record-reconcile` could
#: not run at all (#10663).
#:
#: A contract a production verb reads is a RUNTIME DEPENDENCY, and a runtime
#: dependency must live in the package. Shipping `docs/` instead would keep a
#: prose file load-bearing at runtime and grow the image; embedding the table in
#: a `.py` literal would make a SECOND declaration, the one that goes stale. So
#: the doc stays the one declaration and this file is its generated carrier —
#: the same shape `record_projection.RECORD_MIGRATIONS` uses to keep the record's
#: schema read from disk rather than restated.
#:
#: PARITY IS PINNED, not hoped for: `tests/test_10663_placement_table_packaged.py`
#: re-derives these rows from `PLACEMENT_DOC` and reds on any difference, so the
#: two files cannot drift — and `deploy-hosted.yml`'s `packaging-smoke` asserts
#: the table RESOLVES INSIDE THE IMAGE, which is the check that would have caught
#: #10663 before it shipped.
PACKAGED_PLACEMENT_TABLE = Path(__file__).resolve().parent / "_placement_table.json"

#: The carrier's generator version — BUMP it when the emitted SHAPE changes.
#:
#: ONE value, shared: ``tools/gen_placement_table.py`` imports it and stamps it
#: into ``_generated_by``, and :func:`_read_packaged_placement_rows` REFUSES a
#: carrier that carries any other value (#10663 review, P2). It lives here, in
#: the package, rather than in ``tools/`` because the READER is the one that has
#: to enforce it: a provenance stamp written by the generator and read by nobody
#: is decoration, so a carrier produced by a different generator version — or
#: from a different doc — was accepted as authoritative.
PLACEMENT_TABLE_GENERATOR = "gen_placement_table/2"

#: section heading in the placement table → the physical table.
_PLACEMENT_SECTION_TABLE: dict[str, str] = {
    "Event": "event",
    "Source": "source",
    "Session": "session",
    "Object": "object",
    "Subject": "subject",
    "Point": "point",
    "Edges": "relation",
}

#: The physical tables a carrier row may name — the allowed `row[0]` set.
#: DERIVED from the section map, never restated: a new section must not be able
#: to leave the reader refusing a table the generator emits, and a row naming
#: anything else is a silent partial namespace (the property lands under a key
#: no accessor asks for, with the row count still matching).
_DECLARED_TABLES = frozenset(_PLACEMENT_SECTION_TABLE.values())

_SECTION_RE = re.compile(r"^## (Event|Source|Session|Object|Subject|Point) \(node\)")
_EDGES_RE = re.compile(r"^## Edges / operators")
_HEADING_RE = re.compile(r"^#{2,3} ")
_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([PDU])\s*\|")

#: `public.node_identity`'s generated key per label — parsed, not restated.
_KEY_CASE_RE = re.compile(r"WHEN\s+'(\w+)'\s+THEN\s+'([^']+)'")
_KEY_DDL = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / (
    "20261012000001_record_event_stream.sql")


class CacheProjectionError(RuntimeError):
    """Base class for every refusal on this path."""


class PlacementTableUnavailable(CacheProjectionError):
    """The placement table could not be resolved — an INSTALL or CALLER-PATH fault.

    Raised on EVERY arm that leaves the runtime without a property namespace.
    Arms 1-5 are packaging faults and none of them is a record fault (#10663);
    arm 6 is a caller-supplied path, which is neither an install nor a record
    fault (the preamble says so because a docstring that calls every arm an
    install fault would contradict the one arm whose purpose is to keep a bad
    path from being read as a broken install):

    1. **neither carrier** — no packaged table (:data:`PACKAGED_PLACEMENT_TABLE`)
       AND no source document (:data:`PLACEMENT_DOC`);
    2. **a present-but-corrupt carrier** — the packaged file exists and does not
       parse, or does not DECODE, or cannot be READ at all (EACCES / EISDIR /
       ELOOP — NOT absence: only ENOENT takes the doc fallback). Undecodable
       bytes raise `UnicodeDecodeError`, a `ValueError` and not an `OSError`, so
       they are caught here explicitly; left alone they escaped to the caller's
       "record is unreadable" wrapper, which is the misdiagnosis this type
       exists to end. The doc is deliberately NOT consulted: papering a corrupt
       carrier over with a fallback hides the packaging fault instead of
       reporting it;
    3. **a present-but-EMPTY carrier** — the packaged file parses to zero rows.
       An empty carrier is never legitimate, and accepting it would project
       every record property as unmapped, silently, in production;
    4. **a stale or malformed carrier** — written by another generator version,
       carrying a row that is not two non-empty strings or names no declared
       table, or holding a row count that differs from its own `_row_count`
       (truncated by a bad merge, or hand-edited);
    5. **the source document fails in the same three ways** — it is undecodable,
       it cannot be read (EACCES / EISDIR / ELOOP), or it parses to zero rows.
       A checkout is not exempt from the checks an image's carrier gets: the doc
       IS the declaration the carrier is generated from, and a doc that lost its
       table projected every record property as unmapped with no error at all.
       ⚠️ The doc arm is NOT at full parity: a doc has no `_row_count` and no
       per-row provenance, so a row LINE the parser cannot match is dropped
       silently (`parse_placement_rows` skips it). What catches that is
       `tests/test_record_schema_placement.py`'s ABSOLUTE count —
       `EXPECTED_TOTALS` (192 primary / 29 derived / 0 unclassified) and
       `EXPECTED_TOTAL_NAMES` (221) — NOT `test_cache_projection.py`,
       which derives from the same doc through the same regex and so shrinks with
       it (its unclassified-column pin catches a lost PRIMARY row only, via the
       migrations). Stated because an unqualified "refuses exactly like the
       carrier" is how the next reader concludes this arm is closed;
    6. **an explicitly named doc that is not on disk** — the `doc=` arm. NOT arm
       1 and NOT a broken install: arm 1 is the no-arg read ("neither
       location"), while a caller-supplied path that does not exist is a BAD
       PATH. It raises this type only so a public accessor cannot leak a class
       no out-of-module handler can name.

    It is a distinct type because the message it replaces — *"the record is
    unreadable"* — sent an operator to Postgres credentials for a file that was
    never shipped.

    It subclasses :class:`CacheProjectionError` so every existing handler keeps
    its behaviour (a structural refusal, never an availability event).
    """


class _PlacementDocAbsent(PlacementTableUnavailable):
    """INTERNAL: the source document is not on disk — the only ABSENCE arm.

    Separate from the other refusals (:class:`PlacementTableUnavailable` is
    public and stays the handler's type) so `_read_placement_rows` can tell
    "neither location" from "the doc is right there and is broken". Collapsing
    the two told an operator the table was missing while they were looking at
    it — the same false-absence that `Path.exists()` produced at the carrier
    path (#10663 review, P3), one arm over.
    """


class UnknownRecordLabel(CacheProjectionError):
    """A label with no record table — the record cannot carry it."""


class UnknownRecordColumn(CacheProjectionError):
    """A record column the projection has not classified.

    Raised rather than dropped: a column nobody placed in PRIMARY, DERIVED or
    bookkeeping is a schema change the cache projection has not been taught,
    and silently skipping it is the "cache poorer than the record" failure.
    """


class AmbiguousRecordColumn(CacheProjectionError):
    """Two placement spellings collapse to one column — refusals are explicit."""


class UnprojectableRecord(CacheProjectionError):
    """A record row that cannot be projected (a missing contract key)."""


# ════════════════════════════════════════════════════════════════════════════
# The read shape's declarations, read from disk
# ════════════════════════════════════════════════════════════════════════════


def _read_key_property() -> dict[str, str]:
    """`{label: key property}` from `public.node_identity`'s generated CASE.

    Read, never restated: a store that disagrees with the W1 contract cannot
    write the row at all, so the DDL is the contract's one home (#7955).

    ⚠️ This DELEGATES to :func:`record_projection.key_properties`, because the
    CASE can be **re-declared by a forward migration** and only a reader that
    walks every record migration in order can tell which declaration is live.
    #9881 moved `Object`/`Subject` from `name` to `id` exactly that way; a
    single-file parse kept reporting the pre-move contract, so this module would
    have MERGEd an Object by the name of a row whose `node_key` is now its id.
    """
    try:
        return _rp.key_properties()
    except _rp.RecordProjectionError as e:  # pragma: no cover - deployment guard
        raise CacheProjectionError(
            f"the record schema's key contract could not be read from the "
            f"record migrations (looked in `record_projection.RECORD_MIGRATIONS`, "
            f"first choice {_KEY_DDL}): {e}"
        ) from e


#: `{label: the property that is the label's canonical key}` (Interface E).
KEY_PROPERTY: dict[str, str] = _read_key_property()


def parse_placement_rows(text: str) -> list[tuple[str, str]]:
    """`(table, property)` parsed from the placement table's markdown.

    The ONE parser: the runtime reads the same rows from the generated
    :data:`PACKAGED_PLACEMENT_TABLE`, and `tools/gen_placement_table.py`
    generates that file through this function — so the artefact cannot describe
    a table the doc does not, in either direction.

    EVERY class is read (`P`, `D`, `U`) — the class decides whether a field is
    *authoritative*, and the schema decides that (#7968's pin), while the NAME
    is needed here to spell a column back into the graph's namespace. Reading
    the `D`/`U` rows too is what lets the suite assert that a DERIVED column is
    never projected: the name must be known to be refused.
    """
    rows: list[tuple[str, str]] = []
    section: str | None = None
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        if _EDGES_RE.match(line):
            section = "Edges"
            continue
        if _HEADING_RE.match(line):
            section = None
            continue
        if section is None:
            continue
        r = _ROW_RE.match(line)
        if not r:
            continue
        prop = r.group(1)
        if prop.endswith(".*"):
            # A grouped PREDICATE row (`mitigated_by.*`) names edge predicates,
            # not properties; the edge's own properties are spelled by the
            # `IMPL.` / `NAND.` rows.
            continue
        if "." in prop:
            prop = prop.partition(".")[2]
            for part in prop.split("/"):
                rows.append((_PLACEMENT_SECTION_TABLE[section], part.strip()))
        else:
            rows.append((_PLACEMENT_SECTION_TABLE[section], prop))
    return rows


def _placement_rows_from_doc(path: Path) -> list[tuple[str, str]]:
    """Parse `path` (an explicit doc, or :data:`PLACEMENT_DOC`).

    Refuses the states the packaged arm refuses: bytes that do not decode, a
    read that fails (EACCES / EISDIR / ELOOP), and a table that parses to ZERO
    rows are all install faults, never an empty namespace. The EMPTY guard
    landed with the carrier (#11210, closing #10663) and this arm kept the hole
    one file over, where a doc that lost its table projected every record
    property as unmapped with no error at all — which is what #11227 filed.

    NOT at full parity with the carrier, and the difference is named rather than
    implied: a doc declares no row count and carries no per-row provenance, so a
    single row LINE the parser cannot match is dropped silently. The pin that
    catches that is `tests/test_record_schema_placement.py`'s ABSOLUTE count
    (`EXPECTED_TOTALS`), not `test_cache_projection.py` — that file derives from
    the same doc through the same regex, so it shrinks with a lost row.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        # ABSENCE is its own type, so `_read_placement_rows` can turn it into
        # the "in NEITHER location" message. Any other read fault is reported
        # here as itself, naming a file the operator can see.
        raise _PlacementDocAbsent(
            f"the placement table is not on disk at {path} — the cache "
            f"projection reads the property namespace from it: {e}"
        ) from e
    except OSError as e:
        raise PlacementTableUnavailable(
            f"the placement table at {path} is unreadable "
            f"({type(e).__name__}: {e}) — a fault in the INSTALL, not in the "
            f"record"
        ) from e
    except UnicodeDecodeError as e:
        # `read_text(encoding="utf-8")` raises this — a `ValueError`, NOT an
        # `OSError` — so it bypassed every handler and reached the CLI as
        # "record is unreadable (UnicodeDecodeError)": the #10663 misdiagnosis.
        raise PlacementTableUnavailable(
            f"the placement table at {path} is unreadable "
            f"(UnicodeDecodeError: {e}) — a fault in the INSTALL, not in the "
            f"record"
        ) from e
    rows = parse_placement_rows(text)
    if not rows:
        raise PlacementTableUnavailable(
            f"the placement table at {path} parsed to ZERO rows — a table "
            f"with no rows would project every record property as unmapped, "
            f"silently. The DOC is the fault here: restore its table and "
            f"regenerate the carrier with `uv run python "
            f"tools/gen_placement_table.py`"
        )
    return rows


def _placed_rows_with_extras(
        rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The parsed rows PLUS :data:`ADDITIONAL_PRIMARY_COLUMNS`.

    Those columns are PRIMARY by decision but are spelled only in the schema, so
    they are appended here rather than in either carrier — the doc does not
    name them and the generated table must not invent them.
    """
    out = list(rows)
    for table, extra in ADDITIONAL_PRIMARY_COLUMNS.items():
        out.extend((table, prop) for prop in extra.values())
    return out


def _read_packaged_placement_rows() -> list[tuple[str, str]] | None:
    """The generated table's rows, or `None` when the package does not carry it.

    `None` is the SOURCE-CHECKOUT-without-regeneration case (a `git clean` that
    dropped the generated file): the doc is on disk there and is read instead.

    EVERY OTHER state of a PRESENT carrier is a packaging fault and is reported
    as one, never papered over with a fallback that hides it (#10663 review):

    * **unparseable** — not JSON, not an object, or bytes that do not DECODE
      (`UnicodeDecodeError` is a `ValueError`, so it needs its own arm);
    * **from another generator** — `_generated_by` is not
      :data:`PLACEMENT_TABLE_GENERATOR`, so its SHAPE is unknown here;
    * **shape-wrong** — a row that is not exactly two strings, or that names a
      table outside :data:`_DECLARED_TABLES`. The old
      ``[(str(a), str(b)) for a, b in rows]`` was the silent one: iterating a
      DICT row yields its **keys**, so ``{"table": "point", …}`` became
      ``("table", "property")`` for every row and every record column then
      projected as unmapped, with no exception anywhere. A row under an
      undeclared table is the same silence one step quieter: it parses, the
      count can still match, and the property lands under a key no accessor
      asks for;
    * **EMPTY** — zero rows. An empty namespace is never legitimate;
    * **row-count mismatch** — `_row_count` != the rows held (fewer: truncated by
      a bad merge; more: hand-edited). `!=`, not `<`.

    `_source_sha256` is deliberately NOT enforced here: it can only be checked
    against the doc, and the deployed image ships no `docs/` tree — the whole
    reason this carrier exists. It is for the parity pin (which HAS the doc) and
    for a human reading a bad carrier, and saying so is the point: a provenance
    field that looks enforced but is not is how the next reader concludes the
    carrier is validated when only its syntax is.

    The two RELATIVE pins in `tests/test_10663_placement_table_packaged.py`
    (carrier vs the same parser over the doc, generator vs the committed bytes)
    cannot see any of these: an artefact that agrees with itself keeps them —
    and `--check` — green. This function is the only thing that reads the
    SHIPPED artefact for what it is (the #1929/#9231 silent-empty class).
    """
    import json

    def _fault(reason: str) -> PlacementTableUnavailable:
        return PlacementTableUnavailable(
            f"the packaged placement table at {PACKAGED_PLACEMENT_TABLE} {reason} "
            f"— regenerate it with `uv run python tools/gen_placement_table.py`. "
            f"This is a fault in the INSTALL, not in the record."
        )

    # NOT `Path.exists()`. It swallows ELOOP/EBADF and reports a carrier that IS
    # there as ABSENT — which does not merely lose the message, it takes the
    # DOC FALLBACK, so in an image the operator is told the table is not on disk
    # and told to regenerate a file that is on disk. Only ENOENT is the benign
    # source-checkout case; every other read error (ELOOP, EACCES, ENOTDIR, …)
    # is a fault in the INSTALL and is reported as one.
    try:
        text = PACKAGED_PLACEMENT_TABLE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise _fault(f"is unreadable ({type(e).__name__}: {e})") from e
    except UnicodeDecodeError as e:
        # NOT an `OSError` (it is a `ValueError`), so a bare `except OSError`
        # let it escape to the CLI's "record is unreadable" wrapper — a
        # packaging fault reported as a record fault, which is the exact
        # misdiagnosis #10663 exists to end.
        raise _fault(f"is unreadable (UnicodeDecodeError: {e})") from e

    try:
        payload = json.loads(text)
    except ValueError as e:
        raise _fault(f"is unreadable ({type(e).__name__}: {e})") from e
    if not isinstance(payload, dict):
        raise _fault(
            f"is unreadable (its top level is {type(payload).__name__}, not an "
            f"object)")

    # PROVENANCE FIRST: it decides what the rest of the file is even supposed to
    # look like, so a shape check running before it would report a version skew
    # as a malformed row.
    if payload.get("_generated_by") != PLACEMENT_TABLE_GENERATOR:
        raise _fault(
            f"was generated by {payload.get('_generated_by')!r}, not by "
            f"{PLACEMENT_TABLE_GENERATOR!r} — it is STALE against the reader")

    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise _fault(
            f"is unreadable (`rows` is {type(rows).__name__}, not a list)")
    parsed: list[tuple[str, str]] = []
    for index, row in enumerate(rows):
        if (not isinstance(row, list) or len(row) != 2
                or not all(isinstance(part, str) and part for part in row)):
            raise _fault(
                f"has a MALFORMED row at index {index} ({row!r}) — every row "
                f"must be a two-element list of non-empty strings")
        if row[0] not in _DECLARED_TABLES:
            # A table name nothing reads is a SILENT partial namespace: the row
            # parses, the count can still match, and the property lands under a
            # key no accessor asks for. `parse_placement_rows` cannot produce
            # one (it maps through _PLACEMENT_SECTION_TABLE), so a carrier is
            # strictly weaker without this check.
            raise _fault(
                f"has a row naming {row[0]!r}, which is not one of the declared "
                f"tables {sorted(_DECLARED_TABLES)} at index {index} — its "
                f"SHAPE is unknown here")
        parsed.append((row[0], row[1]))

    if not parsed:
        raise _fault(
            "is EMPTY (it parsed to zero rows) — an empty table would project "
            "every record property as unmapped, silently; if the doc itself "
            "parsed empty, the doc is the fault")

    declared_count = payload.get("_row_count")
    if declared_count != len(parsed):
        raise _fault(
            f"declares `_row_count` = {declared_count!r} but holds {len(parsed)} "
            f"rows — it is TRUNCATED or was hand-edited")

    return parsed


def _read_placement_rows(doc: Path | None = None) -> list[tuple[str, str]]:
    """`(table, property)` for every row the placement table declares.

    THE PACKAGED TABLE IS PREFERRED (#10663). That is the read the deployed
    image performs, so it is also the read every test performs — a path only the
    image takes is a path nothing exercises, which is exactly how a docs/ file
    became a production verb's dependency with CI green.

    `doc=` (a fixture, or a caller reading a specific revision) still parses
    markdown directly. With neither carrier present the refusal is
    :class:`PlacementTableUnavailable` and names BOTH locations.
    """
    if doc is not None:
        try:
            return _placed_rows_with_extras(_placement_rows_from_doc(doc))
        except _PlacementDocAbsent as e:
            # The private absence type is for INSPECTION here, never for escape:
            # a caller of a public accessor must be able to NAME what it catches,
            # and the no-arg form of this same function raises the public type
            # for the identical condition.
            raise PlacementTableUnavailable(str(e)) from e
    packaged = _read_packaged_placement_rows()
    if packaged is not None:
        return _placed_rows_with_extras(packaged)
    try:
        return _placed_rows_with_extras(_placement_rows_from_doc(PLACEMENT_DOC))
    except _PlacementDocAbsent as e:
        raise PlacementTableUnavailable(
            f"the cache projection's placement table is in NEITHER location — "
            f"this is a PACKAGING fault, not a record fault: no packaged table "
            f"at {PACKAGED_PLACEMENT_TABLE} and no source document at "
            f"{PLACEMENT_DOC} ({e}). A checkout regenerates the carrier with "
            f"`uv run python tools/gen_placement_table.py`; an image must ship "
            f"`tortoise/_placement_table.json` as package data (#10663)."
        ) from e


#: NOTE on caching: the parsers below read FILES, and every accessor is memoized.
#: That is not premature — `_props_from_row` runs PER RECORD ROW, so an uncached
#: parse would read the placement doc once per row (a 45k-node rebuild: 45k file
#: reads). The cache is keyed on the path, so a test pointing at a fixture doc
#: gets its own entry.
@lru_cache(maxsize=8)
def placement_spellings(doc: Path | None = None) -> dict[str, dict[str, tuple[str, ...]]]:
    """`{table: {column: (spelling, ...)}}`, sorted — the raw namespace.

    ⚠️ **More than one spelling can collapse to one column, and on this schema
    five DO** — FOUR on `public.source` (`session_id`, `event_id`, `source_path`,
    `document_kind`) and ONE on `public.event` (`session_id`). Each appears in
    the placement table BOTH snake- and camel-cased, because the graph's
    document writer emits the snake forms while the session-capture writer mints
    the camel forms (the declaration `projection/entities.py::_SOURCE_NODE_PROPS`
    says so in its own comment). The record's `snake()` naming is therefore
    **not injective** over the graph's namespace: a single record column cannot
    distinguish the two,
    and `canonical_property` chooses deterministically. Reported, not hidden —
    a record-sourced cache cannot be byte-identical to a journal-sourced one
    for those five fields until one spelling wins (#7955/#7968).
    """
    out: dict[str, dict[str, list[str]]] = {}
    for table, prop in _read_placement_rows(doc):
        out.setdefault(table, {}).setdefault(_rp.snake(prop), []).append(prop)
    return {t: {c: tuple(sorted(set(v))) for c, v in cols.items()}
            for t, cols in out.items()}


def _prefer(spellings: Sequence[str]) -> str:
    """The canonical spelling for one column — deterministic, and pinned.

    camelCase wins over the bare column name when both exist: the graph's
    property namespace is camelCase-first (the fold's own clauses write
    `pointKind`, `contentHash`, `canonicalUrl`, …), and the snake duplicates in
    the table are the document writer's raw payload leaking through a passthrough.
    A snake-only column keeps its own name (`is_episodic`, `subject_kind`, …).
    """
    camel = [s for s in spellings if s != _rp.snake(s)]
    return camel[0] if camel else spellings[0]


@lru_cache(maxsize=8)
def placement_columns(doc: Path | None = None) -> dict[str, dict[str, str]]:
    """`{table: {column: the canonical graph property name}}`."""
    return {t: {c: _prefer(v) for c, v in cols.items()}
            for t, cols in placement_spellings(doc).items()}


def canonical_property(table: str, column: str) -> str:
    """The graph property name a record column projects to."""
    cols = placement_columns().get(table, {})
    if column in cols:
        return cols[column]
    extra = ADDITIONAL_PRIMARY_COLUMNS.get(table, {})
    if column in extra:
        return extra[column]
    if column in RECORD_BOOKKEEPING_COLUMNS:
        return column
    raise UnknownRecordColumn(
        f"{table}.{column} is neither a placed property nor record bookkeeping "
        f"— the cache projection has not been taught this column"
    )


@lru_cache(maxsize=32)
def projected_columns(table: str) -> frozenset[str]:
    """The record columns that carry AUTHORITY — i.e. that get projected.

    The set is decided by the SCHEMA, not by the placement table's class column:
    a column exists **iff** the placement table demands one (#7968's two-way
    pin), and a name the schema REFUSES as a `props` key is DERIVED/VOLATILE by
    that refusal. So:

        projected = schema columns  −  refused  −  bookkeeping  −  not-projectable

    with the placement table supplying only the SPELLING. That is what makes
    the eight `U` rows resolve correctly without a second copy of §2.7: the
    five that become PRIMARY have a column and are not refused (`updatedAt`,
    `baseline_source`, `temporal_reviewed`, `IMPL.confidence`, `IMPL.weight`),
    and the three that become DERIVED are refused by name (`version`,
    `inherited_at`, `baseline_set` — all in `chk_point_props_no_derived`).

    ⛔ The last subtraction is `CACHE_FORBIDDEN_COLUMNS` and it is a THIRD
    category, not a fourth spelling of `refused`: those columns stay authoritative
    in the record and are simply not projectable into the cache (§9.1/D1's
    narrative is the case, `#10394` door 2). Folding them into `refused` would
    say the record must not store them, which is false — and would red the
    record's own `props`-tail CHECK.
    """
    from .record_projection import table_columns

    columns = table_columns().get(table, frozenset())
    refused = _refused(table)
    forbidden = cache_forbidden_columns(table)
    placed = placement_columns().get(table, {})
    extra = ADDITIONAL_PRIMARY_COLUMNS.get(table, {})
    return frozenset(
        c for c in columns
        if c not in bookkeeping_columns(table) and c not in refused
        and c not in forbidden
        and (c in placed or c in extra)
    )


def projected_properties(table: str) -> frozenset[str]:
    """The graph property names the projection writes for one table."""
    return frozenset(canonical_property(table, c) for c in projected_columns(table))


def ambiguities() -> dict[str, tuple[str, ...]]:
    """Columns the placement table spells more than one way — sorted columns."""
    return {
        f"{table}.{column}": spellings
        for table, cols in placement_spellings().items()
        for column, spellings in cols.items()
        if len(spellings) > 1
    }


@lru_cache(maxsize=32)
def _refused(table: str) -> frozenset[str]:
    """The names the schema (or the projection) refuses as a `props` key."""
    from .record_projection import refused_props_names

    return frozenset(refused_props_names().get(table, ()))


#: Columns the RECORD holds **authoritatively** and the CACHE must NEVER
#: materialise: `{table: {column}}`.
#:
#: ⛔ THIS IS NOT `_refused`, AND THE DIFFERENCE IS THE WHOLE POINT. `_refused`
#: (`record_projection.refused_props_names`) is derived from the schema's own
#: CHECK constraints and means *"the record must not store this name as
#: authority"*. These columns ARE authority in the record — §9.1/D1 puts the S1
#: narrative in **Postgres** and forbids it in the **graph**, and the record is
#: the Postgres half. Authority in the source store is not projectability into
#: this cache, and a projection with no vocabulary for that distinction
#: re-materialises the prose on every RESTORE — which is `#10394` door 2, and it
#: undoes `#10000` (the write deletions) at once: a write deletion that the next
#: routine recovery reverses is not a deletion. ⚠️ `#10017`'s purge is NOT the
#: lane this undoes (#11325): its single destructive statement deletes legacy
#: episodic turn `:Point`s, never `:Source`/`:Event`, and names no `story_arc` —
#: so this class's targeted repair is a graph-side `REMOVE` that no instrument
#: ships, or the full restore's whole-graph wipe (itself refused pre-wipe on a
#: production server graph name, `#2944`'s L2 guard). ⚠️ BOTH are destructive, and
#: the WIPE cannot be row-scoped — `_wipe_all_nodes` is `MATCH (n) DETACH DELETE n`,
#: so it cannot be restricted to the rows whose prose the record still carries: a
#: graph holding ANY row whose PROSE neither `public.{source,event}.story_arc` nor
#: `public.session_narrative.narrative` carries must NOT be restored at all — copy
#: that prose out first, because the wipe destroys the only copy
#: (STORAGE-ARCHITECTURE.md §17.2, `#11325`).
#:
#: So an entry here is a statement about the CACHE, not about the record: the
#: placement table keeps the row's class (`P` — the record does store it) and
#: its note says `not projected` with the reason, so a reader cannot take this
#: for an omission and `#7968`'s two-way pin (PRIMARY ⇒ column) still holds.
#:
#: ⛔ The fix is HERE — at the seam — and deliberately not a purge pass after
#: the restore: a sweep would still rent the RAM for the duration of the write
#: and would have to run on every restore forever. Stop the write.
#:
#: Pinned per table by `tests/test_9555_narrative_cache_rebuild.py::
#: test_the_record_restore_writes_no_story_arc_on_any_node`, whose restore arm
#: runs over a cache that still holds a legacy copy (`sum(size(n.story_arc))`
#: must end at 0).
#: Columns POSTGRES computes, so a record→cache read must skip them (#11642).
#: ⛔ IMPORTED, never re-listed: `record_projection.GENERATED_COLUMNS` is the ONE
#: declaration of "the projection must never bind this", and a second copy here
#: is how the writer and the reader start disagreeing about the same column.
#: The failure it prevents is not cosmetic: without this entry
#: `_props_from_row` reaches its final `UnknownRecordColumn` raise, and
#: `read_record_view`/`record_serve.read_node` both do `SELECT *` — so EVERY
#: record row read would raise on the first row after the migration applied.
from .record_projection import GENERATED_COLUMNS as GENERATED_COLUMNS  # noqa: PLC0414
_GENERATED_COLUMNS = GENERATED_COLUMNS

CACHE_FORBIDDEN_COLUMNS: dict[str, frozenset[str]] = {
    # §9.1/D1 — the S1 narrative, on BOTH anchors the commit path could reach
    # (`public.source.story_arc`, `public.event.story_arc`; the `:Event` half
    # was measured in `#10389`'s review and is why this is not a
    # `:Source`-only entry). Its ONE home is `public.session_narrative`, which
    # `record_text.serve_from_record` reads and this module never touches — so
    # nothing a reader sees is lost by denying the projection.
    "source": frozenset({"story_arc"}),
    "event": frozenset({"story_arc"}),
}


def cache_forbidden_columns(table: str) -> frozenset[str]:
    """The COLUMN names of `table` that are authoritative and NOT projectable.

    ⚠️ Column names, not graph property names. The two coincide for `story_arc`
    (which the placement table spells once) and do NOT in general — callers that
    compare against graph-property keys must widen the set through
    :func:`placement_spellings`, which is what the `props`-tail filter in
    :func:`_props_from_row` does (#10394 review).
    """
    return CACHE_FORBIDDEN_COLUMNS.get(table, frozenset())


# ════════════════════════════════════════════════════════════════════════════
# The record read shape
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RecordNode:
    """One record row, as the cache must materialize it."""

    label: str
    key: str
    props: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordRelation:
    """One `public.relation` row, as an edge.

    Endpoints are addressed by `(label, key)` — Interface E's contract — never
    by a graph-resident id (`#2489`/`#5086`).
    """

    rel_type: str
    src_label: str
    src_key: str
    tgt_label: str
    tgt_key: str
    idx: int = 0
    props: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordView:
    """The record, read: the cache projection's ONLY input."""

    nodes: tuple[RecordNode, ...] = ()
    relations: tuple[RecordRelation, ...] = ()
    #: The record's `EventRecorded` stream, as the lifecycle fold's inputs
    #: (#10198). It rides the VIEW rather than only being consumed inside
    #: `read_record_view` because a rebuild is a pure function of the view: a
    #: caller that assembles a view without a connection would otherwise get a
    #: cache with no connector lifecycle, i.e. the defect this field closes.
    #: Empty is a legitimate value — a graph with no event stream folds nothing,
    #: which is exactly the pre-#10198 behaviour.
    lifecycle_events: tuple[LifecycleEvent, ...] = ()

    @property
    def record_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple((n.label, n.key) for n in self.nodes)


@dataclass(frozen=True)
class Statement:
    """One parameterized Cypher statement."""

    cypher: str
    params: Mapping[str, Any]


def _scalar(value: Any) -> Any:
    """A value as the graph stores it. `jsonb` reads back as list/dict already."""
    if isinstance(value, (list, dict)):
        return value
    return value


#: ⛔ §9.1/D1 AT THE PROJECTION DOOR (#10853, narrowed by #10965) — the columns an
#: EPISODIC (`session:`) Source may **not** carry onto its node **when the row
#: holds the PRE-#10000 derivation**.
#:
#: WHY THIS IS HERE AND NOT ONLY IN THE CAPTURE. A pre-#10000 capture derived BOTH
#: columns from the transcript's first substantive utterance
#: (`_session_source_metadata`, then `_source_search_text(summary=…)` — the
#: capture path has no title) and wrote them on the session `:Source`
#: (`s.summary=$sum`). #10000's ruling (§9.1/D1) is that captured turn text lives
#: in the RECORD, not on a graph node. This table is the OTHER door: every
#: record-sourced READ and rebuild goes through :func:`node_from_row` —
#: `tortoise record-restore` and `rebuild_from_record` MATERIALISE a node with it,
#: and `record_serve.read_node` answers the graph-engine-down read with
#: the same map *because its contract is that the two cannot drift*. So a row
#: written by a build that still placed the sentence would re-materialise the
#: prose **whatever build is reading it** — a one-way divergence (`#10853`). The
#: rule cannot be a one-off sweep either: a session nobody re-captures keeps its
#: row, so the decision belongs at the door.
#:
#: ⭐ WHAT #10000 CHANGED, AND WHY THE PAIR IS NO LONGER ONE RULE. The capture now
#: derives the session's TOPIC LEXICON — the most frequent content words of the
#: (already redacted) transcript — and writes `summary` = a display DESCRIPTOR
#: over it and `_searchText` = `", ".join(topics)`, both from the SAME list in ONE
#: statement (`sdk._materialize_session_source`). So on a post-#10000 row
#: `_search_text` is **not a CHOSEN utterance**: in a word-delimited script it is the lexicon — TOKEN-shaped, so
#: a space-less CJK run arrives WHOLE, i.e. that member IS the turn's prose verbatim; a Thai one FRAGMENTS at its combining
#: (the residual in `sdk._session_source_metadata`) — and it is the ONLY field the Source
#: FTS index is declared on (`_FULLTEXT_INDEX_LABEL_FIELDS`), i.e. #3518's findability.
#: Dropping it by column NAME on such a row silently UN-INDEXES the session on the recovery door (#10965).
#:
#: ⛔ SO THE DROP IS DECIDED PER ROW, FROM THE ROW — and the test is EXACT, never a
#: shape heuristic: the value IS `", ".join(topics)`, the join the capture computes
#: from the very column that survives, **and** `summary` carries that join behind
#: `_SESSION_SOURCE_SUMMARY_PREFIX`. The join alone is not enough: a legacy
#: utterance that IS a comma-space list satisfies it word for word (#10852 review,
#: P2), so the display column is the WITNESS that the derivation is the post-#10000
#: one. A prefix/suffix/"looks like a word list" test alone would re-index a legacy
#: sentence whose opening words happened to match — and a join-only test DOES
#: re-index one whose whole body does. See
#: :func:`_episodic_source_search_text_is_the_lexicon`.
#:
#: ⛔ `summary` STAYS DECLINED ON EVERY ROW, and that is the stated residual
#: (#10965), not an oversight: it is the same lexicon BEHIND
#: `_SESSION_SOURCE_SUMMARY_PREFIX`, and restoring it would put the whole
#: descriptor on the node for no retrieval gain (the Source FTS index is declared
#: on `_searchText`). The column is therefore READ AS THE WITNESS and never
#: projected — its home is the leaf module `tortoise/session_source_display.py`,
#: which imports nothing, precisely so this door does not import the capture (the
#: layering inversion this module refuses). Cost, named: a record-restored (or
#: served) session `:Source` carries no `summary` while a live one does.
#:
#: ⚠️ WHAT THIS DELIBERATELY DOES NOT TOUCH. `topics` is the capture's word lexicon (its most frequent content
#: words) — in a word-delimited script not a CHOSEN transcript slice, though a space-less (CJK) run arrives
#: as ONE member verbatim (the residual in `sdk._session_source_metadata`) — and it survives; every
#: non-episodic Source keeps both columns untouched — a document's `summary` is its own metadata and its
#: `_searchText` is the shared title+summary+topics rule, so a guard that fired on every Source would
#: un-index every document in the cache. The drop is a **projection** decision: the row is not mutated, so
#: the record stays the authority for the value.
#:
#: ⚠️ THE THIRD COST, and it is an OBSERVATION instrument, where a permanent
#: non-clean reads as noise: `python -m tortoise record-reconcile` builds its
#: RECORD side through :func:`read_record_view` → `node_from_row`, so the truth
#: side omits what this door declines while the CACHE side may hold it. Left as a
#: `field_mismatch` that is the class the reconciler's own docstring grades P1
#: (*"a signal indistinguishable from noise is not observability"*), so
#: :func:`episodic_source_prose_properties` and
#: `record_reconcile.scan_cache_view` mark the pair CACHE-HELD for an episodic
#: Source and count it under `graph_only` — visible, and not a divergence against
#: a record that is correct. ⭐ And the partition stays provenance-correct without
#: a second predicate: `_partition_cache_owned` KEEPS every name the RECORD side
#: holds, so on a post-#10000 row — where the record door now projects
#: `_searchText` — the value is COMPARED (a drifted `_searchText` reds), while
#: `summary`, and a legacy row's whole pair, stay cache-held (≠ "cache-owned",
#: §17.5's term for what the record cannot hold at all).
_EPISODIC_SOURCE_PROSE_COLUMNS: frozenset[str] = frozenset({
    "summary", "_search_text",
})


#: The row-signals that make a Source EPISODIC, and they are TWO identity
#: shapes — never a flag. This mirrors the repo's existing declaration of the
#: same question rather than inventing a second one: `sdk.py` excludes document
#: sources from its same-document review pool with exactly these two arms, and
#: `session_source_migration._LEGACY_CANDIDATES` is the second arm's definition
#: on the graph side. Both are cited there as *"the two session signals, both
#: narrow enough to BE the identity"*.
#:
#: * `url LIKE 'session:%'` — the capture's own URL shape (`session:{id}`), the
#:   prefix `_mint_source_stub` mints an episodic stub with, and the prefix
#:   `derive_session_source_url` (file_indexer) lands on, so a migrated node
#:   keeps it;
#: * `source_kind = 'agentSession'` **on a separator-free bare-filename url** —
#:   the pre-#4005 identity was `os.path.basename(ref.path)` (literally
#:   `session.md`), and `_LEGACY_CANDIDATES` is its definition.
#:
#: ⛔ NEITHER FLAG IS A SIGNAL, and that is measured, not a preference — the
#: three terms an earlier round of this change used all fire on DOCUMENTS, i.e.
#: the mirror of the defect this table closes. `sdk.py` records the same lesson
#: and its cost: keying on the flags *"silently disabled this exclusion on the
#: corpus #9914 targets"*.
#:
#: * `source_kind = 'agentSession'` ALONE is not episodic: `file_indexer`
#:   classifies a transcript FILE from its frontmatter (`sessionId` present →
#:   `agent_session` → `CLASSIFIER_TO_SOURCE_KIND` → `'agentSession'`, pinned at
#:   §6.2/I26) and writes it on a per-FILE `corpus://` permalink, so a transcript
#:   document IS an `agentSession` Source. That is why the legacy arm ALSO
#:   requires a separator-free url.
#: * `is_episodic = TRUE` is not episodic either: the capture commit stamps it on
#:   every EXTERNAL source of `payload.sources` (`hosted_api`, step 4), and
#:   `graph-scripts/backfill_is_episodic.py` stamps it on every Source reachable
#:   from a session point.
#: * `session_id` is not episodic: it is a legitimate DOCUMENT coordinate
#:   (`EventAPI.add_document` takes one; `tortoise/ingest.py` passes a
#:   transcript's `sessionId` frontmatter into it).
#:
#: A guard keyed on any of the three dropped a real document's own `summary` —
#: the exact outcome `_EPISODIC_SOURCE_PROSE_COLUMNS` forbids (*"a guard that
#: fired on every Source would un-index every document in the cache"*). All
#: three were caught by review before this landed and each has a NEGATIVE leg.
_EPISODIC_SOURCE_URL_PREFIX = "session:"
_EPISODIC_SOURCE_KIND = "agentSession"


@lru_cache(maxsize=1)
def _episodic_source_kind_property() -> str:
    """The KIND's property spelling, DERIVED — and resolved at CALL time.

    Derived for the same reason the prose set is: `is_episodic_source_node` reads
    graph properties, and a hand-typed `sourceKind` would stop matching the row
    predicate the day the placement re-spells it — a permanent false
    `field_mismatches` no test would red.

    ⛔ **NOT a module-level constant** (#10853 review P1). `canonical_property`
    reaches the placement TABLE ON DISK (`docs/epics/…/02-identity-and-placement.md`),
    which a wheel does not ship — so evaluating it at import made
    `import tortoise.cache_projection` fail outright on an installed
    distribution, and with it `tortoise.record_reconcile`, `tortoise.legacy_ids`
    and the `record-reconcile` / `record-restore` verbs THIS change names as the
    repair. Every other placement read in this module already resolves at call
    time (`projected_columns`, `_props_from_row`); these follow that rule.
    """
    return canonical_property("source", "source_kind")


#: Distinguishes "the caller did not decode the tail" from an EMPTY tail.
_UNRESOLVED_TAIL: Any = object()


def _tail_payload(raw: Any) -> Any:
    """The row's `props` tail DECODED, in whatever shape the row holds it.

    ONLY `None` and the EMPTY STRING answer `{}` — the two spellings of ABSENT.
    ⛔ `''` is handled explicitly: `props` is `jsonb NOT NULL DEFAULT '{}'`, so the
    common shapes are a mapping or `None`, but the pre-existing read accepted a
    STRING tail and wrote `json.loads(tail) if tail else {}` — i.e. it treated the
    empty string as an ABSENT tail on purpose. `json.loads('')` RAISES, so keeping
    that case is what stops the projection from turning an empty tail into a
    `JSONDecodeError` (pinned by
    :func:`test_the_DROP_SET_itself_is_exactly_the_declared_pair`, whose row
    populates every column).

    ⛔ IT DOES NOT COERCE, AND THE ABSENCE TEST IS NOT FALSINESS (#10867 review,
    second pass). `props` is `jsonb NOT NULL DEFAULT '{}'` with no CHECK that it is
    an OBJECT, so a corrupt row can carry a scalar or an array. Answering those
    verbatim is what lets the WRITER path (:func:`_props_from_row`) refuse them
    loudly — and a `not raw` test defeated exactly the shapes it says it covers:
    a DECODED `[]`/`0`/`False` is FALSY, so it would have been coerced to `{}` and
    projected clean while the same row as TEXT (`'[]'`, `'0'`) was refused. That
    split is decided by the SHAPE the tail arrives in — JSON TEXT or already
    DECODED — never by the row, and BOTH shapes are reachable here: the record
    layer reads through `psycopg2`, whose `jsonb` typecaster answers Python
    objects, while JSON TEXT is what a `::text` cast, a pre-serialised tail or a
    fixture hands over. So the test is IDENTITY against the two absent spellings,
    never truthiness.

    ⛔ DECLARED RESIDUAL — TWO DEGENERATE SCALARS STILL SPLIT ON THE TAIL'S SHAPE.
    PostgreSQL's `jsonb` maps JSON `null` and JSON `""` onto the SAME Python
    values as the absent spellings above (`null` → `None`, `""` → `''`), so a
    DECODED tail cannot be told apart from an absent one — those two project as
    absence while the identical row as TEXT (`'null'`, `'""'`) is refused below.
    That asymmetry is not closable at this
    operator: the decoded values ARE the mandated lenient spellings, and
    widening the lenient set to admit `'null'`/`'""'` as well would re-spell
    ABSENCE rather than detect it. It is declared rather than fixed because it
    carries NO content: neither a JSON `null` nor a JSON empty string has keys,
    so no tail property is lost either way (`test_a_MALFORMED_props_tail_is_
    REFUSED_not_dropped_in_silence` pins the split so it cannot be read as
    parity).
    """
    if raw is None or raw == "":
        return {}
    return json.loads(raw) if isinstance(raw, str) else raw


def _decoded_tail(raw: Any) -> Mapping[str, Any]:
    """The row's `props` tail as a MAPPING — the IDENTITY read's view.

    A `None`/empty tail and anything that is not an object answer `{}`, because
    this feeds the row predicate, which must answer for every shape a row can
    hold. That leniency is deliberately NOT shared with :func:`_props_from_row`:
    the writer path decodes through :func:`_tail_payload` and RAISES on a
    non-object, so a malformed tail is refused where it would otherwise be
    projected (and silently dropped).

    ⛔ IT MUST NOT RAISE, AND A DECODED JSON STRING IS NOT JSON TEXT (#10867
    review, closing pass). `_tail_payload` routes any `str` through `json.loads`,
    which is right for a JSON-TEXT tail — where a `jsonb` string arrives as its
    JSON SOURCE (`'"hello"'`). A DECODED tail (what `psycopg2`'s `jsonb`
    typecaster answers) hands the same column back as the Python `str` `'hello'`,
    which is not JSON text at all: `json.loads` raised `JSONDecodeError` out of
    the ROW PREDICATE, so `_is_episodic_source_row` — the function whose whole
    contract is to ANSWER for every shape a row can hold — raised on the DECODED
    shape and answered on the JSON-TEXT one. Unparseable non-objects answer
    `{}` here (the DOCUMENT arm, which is
    this module's declared safe asymmetry: a missed legacy session keeps its
    prose, a false positive strips a document's metadata). The WRITER path is
    untouched and stays LOUD — `_props_from_row` decodes through
    :func:`_tail_payload` directly, so `props='{not json'` still raises."""
    try:
        tail = _tail_payload(raw)
    except (ValueError, TypeError):
        # Unparseable, i.e. not JSON text — a decoded string tail, which a
        # decoding read spells as a bare `str`. NOT `CacheProjectionError` (a
        # RuntimeError) and NOT the writer path, which must keep raising.
        return {}
    return tail if isinstance(tail, Mapping) else {}


def _row_identity_view(row: Mapping[str, Any],
                       tail: Mapping[str, Any]) -> Mapping[str, Any]:
    """The row's identity AS THE NODE WILL CARRY IT: the columns + `props` tail.

    ⛔ THE TAIL CARRIES IDENTITY, NOT ONLY PROSE (#10867 review). The tail's
    property names reach the node UNMAPPED — they are not routed through the
    placement table — although a column value overwrites the same coordinate and
    an EPISODIC Source's declined names are popped (see :func:`_props_from_row`,
    which applies the whole rule). So a row whose `source_kind` column is NULL
    while the tail carries `sourceKind='agentSession'` produces an EPISODIC
    node — `is_episodic_source_node` says so, because it reads the MERGED map.
    A predicate that read the COLUMNS alone therefore answered "document" for a
    node the reconciler classifies as a captured session: the prose reached the
    node through the column door (which never fired) — R1 ("from any row shape")
    failing OPEN, and the same hazard the declined-name tail filter exists for,
    defeated from the other side.

    ⛔ AND NOTHING DOWNSTREAM REDACTS IT, which is why the failure was SILENT and
    why it has to be caught HERE. `record_reconcile` builds its RECORD side
    through this same projector, so the re-projected record carries the prose
    too; `keep` includes ``record_props`` — that module says a name the record
    side carries "is always compared" (`record_reconcile.py:449-451`, `:540`) —
    so the pair compares EQUAL and the reconciler reports neither a field
    mismatch nor a `graph_only` occurrence. It is not a divergence the reconciler
    hid under another bucket: it is one it structurally CANNOT see, because both
    sides are wrong the same way. The harm is the prose itself, sitting on a node
    §9.1/D1 says must not carry it.

    A COLUMN VALUE WINS over the tail's spelling of the same coordinate, which
    preserves the asymmetry :func:`_is_episodic_source_row` declares: an
    AMBIGUOUS row is read as a DOCUMENT, so it keeps its prose (the pre-existing
    defect) rather than stripping a document's own metadata.

    ⛔ THE KIND IS READ IN THE CANONICAL SPELLING ONLY, AND THAT IS DELIBERATE
    — it is NOT the same rule as :func:`episodic_source_declined_properties`,
    which covers BOTH spellings (`sourceKind` and the COLUMN's `source_kind`)
    because the `props` tail is an open set. (#10962 review.) The two sets are
    asymmetric because their FAILURE DIRECTIONS are opposite, and this function
    is the one that moves the dangerous way:

    * popping a name that is not there costs nothing, so the decline set can
      afford to be generous — it is also why it is the safe place to be wrong;
    * answering EPISODIC here strips a row's metadata and un-indexes it, which
      is the false positive this module refuses everywhere else.

    And a tail-carried `source_kind` CANNOT make the node episodic anyway:
    `_props_from_row` seeds the tail verbatim, so the node gets a literal
    `source_kind` property while :func:`is_episodic_source_node` reads
    `sourceKind` — the node is a DOCUMENT by the graph's own vocabulary. Reading
    the snake spelling here would therefore make this view answer EPISODIC for a
    node the projector and the node predicate both call a document: the same
    row-predicate/node-predicate divergence this function exists to close, only
    mirrored. (The shape is unreachable through the writer regardless —
    `record_projection._split_properties` classifies `source_kind` as a COLUMN —
    so this is an invariant on hand-built rows, declared rather than assumed.)

    ⛔ AND THAT RULE IS THE *NODE MAP's* RULE TOO — `_props_from_row` seeds `out`
    from the tail and lets the column loop overwrite it, for this reason (#10867
    review ISSUE 1). The rule is stated here once and applied in both places on
    purpose: it is the same collision, and resolving it twice is how the two
    functions came to disagree.

    `None` is SILENCE, and nothing else is: a column that carries a value (an
    empty string included) wins the coordinate, because `_props_from_row` writes
    it. An empty key is refused downstream anyway (`node_from_row`'s
    `UnprojectableRecord`, and the schema's own CHECK), so the two agree on every
    shape that can become a node.

    The row itself is NEVER mutated — a FRESH mapping is returned, on every
    branch. ⛔ The empty-tail branch used to `return row`, so `is` identity held
    and a caller mutating the "view" mutated the RECORD ROW — a false guarantee
    on the one object R3 leans on to prove the record is untouched (#10867 review
    P3). One shallow copy on a branch the live path does not take is the whole
    cost.
    """
    if not tail:
        return dict(row)
    merged: dict[str, Any] = {k: v for k, v in row.items() if k != "props"}
    kind_property = _episodic_source_kind_property()
    if merged.get("source_kind") is None and kind_property in tail:
        merged["source_kind"] = tail[kind_property]
    if merged.get("url") is None and "url" in tail:
        merged["url"] = tail["url"]
    return merged


def _is_episodic_source_row(row: Mapping[str, Any],
                            tail: Any = _UNRESOLVED_TAIL) -> bool:
    """Is this `public.source` row a captured session's Source?

    The two identity arms are declared at :data:`_EPISODIC_SOURCE_URL_PREFIX`,
    together with the three flag-based terms that look plausible and are NOT
    signals. A NULL column is silence, never a negative, and a row that neither
    arm names is a DOCUMENT — the safe answer: a missed legacy session keeps its
    prose on a node (the pre-existing defect), while a false positive silently
    strips a document's own metadata and un-indexes it.

    ⚠️ BOUNDED RESIDUAL, stated rather than implied: arm 2 reads
    `public.source.source_kind` HERE and `sourceKind` on a CACHE node
    (:func:`is_episodic_source_node`), so the two agree only while the node still
    carries the property. Where it does not, the record side declines the pair
    while the reconciler still places it, so the pair is counted as a field
    mismatch on top of whatever else that node is missing — a visible RED
    (`field_mismatches` non-empty, `graph_only` empty) instead of the healthy
    empty mismatches + 2 `graph_only` names, never a silent loss, and not
    reachable through today's
    writers
    (the legacy population is *defined* by `sourceKind` on the node —
    `session_source_migration._LEGACY_CANDIDATES` — and a record-sourced rebuild
    writes `sourceKind` from the column). Fail-closed by direction, and left
    named.

    The identity is read through :func:`_row_identity_view`, so the `props` tail
    participates: a tail-carried `sourceKind`/`url` is an identity the NODE will
    carry, and a predicate that ignored it disagreed with the reconciler on that
    row shape (see the view's docstring).
    """
    if tail is _UNRESOLVED_TAIL:
        tail = _decoded_tail(row.get("props"))
    view = _row_identity_view(row, tail) if tail else row
    url = view.get("url")
    if isinstance(url, str) and url.startswith(_EPISODIC_SOURCE_URL_PREFIX):
        return True
    return (view.get("source_kind") == _EPISODIC_SOURCE_KIND
            and isinstance(url, str) and "://" not in url and "/" not in url)


def _episodic_source_search_text_is_the_lexicon(row: Mapping[str, Any]) -> bool:
    """Is this row's `_search_text` the post-#10000 LEXICON derivation (#10965)?

    The ONE mechanical question that separates the two derivations of the session
    `:Source`'s pair, asked of the ROW rather than of the build that wrote it:
    #10000's capture writes `_searchText = _source_search_text(topics=topics)` and
    `summary = _SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics)`, both from the
    SAME list in ONE statement. So the derivation is present iff the stored text
    IS the join — compared as a STRING, exactly — **and** the display column
    carries that join behind the declared prefix.

    ⛔ TWO TERMS, NOT ONE (#10852 review, P2). The join alone is NOT the
    derivation: a pre-#10000 capture wrote `summary` as the transcript's first
    substantive utterance and derived `_searchText` FROM it, so on a transcript
    whose utterance is itself a comma-space list
    (`"milk, eggs, bread, cheese, apples, oranges"`) the legacy row satisfies
    `_search_text == ", ".join(topics)` with its PROSE, word for word. `summary`
    is what tells the two apart — the post-#10000 value carries
    `SESSION_SOURCE_SUMMARY_PREFIX`, which a prose utterance does not. Without
    that second term this predicate re-indexes the sentence it exists to decline,
    and nothing reds (the cost `:data:`_EPISODIC_SOURCE_PROSE_COLUMNS`` names).

    ⛔ EXACT EQUALITY, never a shape test. `startswith`, "contains no sentence",
    "few words", or a case-folded compare would accept a legacy row's sentence
    whose opening words happen to match the join, and re-index it — the §9.1/D1
    prose this module declines, smuggled back by a heuristic. The near-miss shapes
    are pinned in `tests/test_10853_session_source_prose.py`.

    ⛔ FAIL-CLOSED ON EVERY UNCERTAIN SHAPE. A `topics` column that is not a list
    of non-empty strings (NULL, a JSON string, a list with a hole, empty) cannot
    prove the derivation; neither can a `summary` that is absent, empty, or
    missing the declared prefix. Each such row is treated as legacy and the pair
    stays declined. The cost of a false negative is a session that stays
    unfindable on the recovery door until it is re-captured (visible, and named at
    the table); the cost of a false POSITIVE is turn prose on a graph node, which
    nothing reds — so the polarity is not a preference. ⚠️ The prefix's own home
    is `tortoise/session_source_display.py`, a leaf module: the projection must
    not import the capture for it (the layering inversion the table refuses).
    """
    search_text = row.get("_search_text")
    if not isinstance(search_text, str) or not search_text:
        return False
    topics = row.get("topics")
    if not isinstance(topics, (list, tuple)) or not topics:
        return False
    tokens = [t for t in topics if isinstance(t, str) and t]
    if len(tokens) != len(topics):
        return False
    joined = ", ".join(tokens)
    if search_text != joined:
        return False
    # ⛔ THE WITNESS — `summary` is DECLINED on the node below, and this is why it
    # is still read: on the post-#10000 row it carries the lexicon behind the
    # display prefix, which is what a legacy prose row cannot carry.
    summary = row.get("summary")
    if not isinstance(summary, str):
        return False
    return summary == f"{SESSION_SOURCE_SUMMARY_PREFIX}{joined}"


def is_episodic_source_node(props: Mapping[str, Any]) -> bool:
    """The same two identity arms, read off a CACHE node (graph property names).

    `_is_episodic_source_row` answers for a RECORD ROW (column names); this
    answers for a node's property map, and it exists because the reconciler
    compares node properties: it must classify the cache side's own key without
    re-implementing the predicate. Two arms, never a flag — see
    :data:`_EPISODIC_SOURCE_URL_PREFIX`.
    """
    url = props.get("url")
    if isinstance(url, str) and url.startswith(_EPISODIC_SOURCE_URL_PREFIX):
        return True
    return (props.get(_episodic_source_kind_property()) == _EPISODIC_SOURCE_KIND
            and isinstance(url, str) and "://" not in url and "/" not in url)


@lru_cache(maxsize=1)
def episodic_source_prose_properties() -> frozenset[str]:
    """The narrowing in the CACHE's vocabulary — the GRAPH property names.

    What :func:`tortoise.record_reconcile.scan_cache_view` must treat as
    cache-held for an episodic Source, so the reconciler reports it under
    `graph_only` instead of counting it as a `field_mismatch` against a record
    that is correct. ⛔ The term is CACHE-HELD, never "cache-owned": the record
    holds this pair (post-#10000 it even projects `_searchText`), so the repair
    is a record-sourced clear (#11070), not nothing.

    Derived, never typed: `canonical_property` is the one declaration of a
    column's property name, so a hand-written `"_searchText"` here could drift
    from it and the reconciler would then exclude a name no writer produces (a
    silent no-match). Cached, and resolved at CALL time — see
    :func:`_episodic_source_kind_property` for why a module-level evaluation is
    forbidden here.

    ⭐ It stays the FULL pair even though `_searchText` is now projected for a
    post-#10000 row (#10965): the exclusion is what keeps a LEGACY row's prose
    from being counted as divergence, and on a post-#10000 row it costs nothing —
    `_partition_cache_owned` keeps every name the RECORD side holds, so the
    projected `_searchText` is compared there regardless of this set.
    """
    return frozenset(canonical_property("source", column)
                     for column in _EPISODIC_SOURCE_PROSE_COLUMNS)


def episodic_source_declined_properties() -> frozenset[str]:
    """EVERY spelling the declared decline covers — the one home for the set.

    Two spellings of the same datum reach a cache node: the graph property name
    (:func:`episodic_source_prose_properties`, ``summary`` / ``_searchText``)
    and the raw COLUMN spelling (``_EPISODIC_SOURCE_PROSE_COLUMNS``:
    ``_search_text``), which the ``props`` tail is an open set over — see
    :func:`_props_from_row`'s open-set door, where the tail can carry the snake
    spelling because ``_search_text`` IS a real column.

    ⛔ ONE declaration, because the decline is read by THREE consumers and a
    second list is how they drift. Two need BOTH spellings and take this
    function: :func:`_props_from_row`'s open-set door (what it pops — the tail
    is an open set, so the snake spelling really can appear there) and
    `record_serve.parity_difference` (what it EXPLAINS instead of reddening —
    #10867 review ISSUE 1). The third, `record_reconcile.scan_cache_view`,
    compares two GRAPH-vocabulary name sets, so it takes
    :func:`episodic_source_prose_properties` — the subset — deliberately: a
    snake name is not in either side's vocabulary there, and subtracting it from
    a graph-name set would suggest a comparison this module does not make.

    What must NOT happen is the drift #10867 caught: before this function
    existed, `_props_from_row` spelled the union inline and the parity
    instrument spelled nothing at all, so it reported a legacy session node's
    own prose as an unexplained projection gap — a sanctioned decline and a real
    defect as the same red.

    Derived, resolved at CALL time for the same reasons as
    :func:`episodic_source_prose_properties`. ⛔ NOT `lru_cache`d like that
    sibling, deliberately: this composes two frozensets, so a call is free, and a
    cache would be state every consumer has to clear — the suite clears the two
    REAL caches by calling `.cache_clear()`, and a decorator copied onto this one
    to match the sentence that used to be here would only add an attribute to
    remember.
    """
    return episodic_source_prose_properties() | _EPISODIC_SOURCE_PROSE_COLUMNS


def _props_from_row(table: str, row: Mapping[str, Any], *,
                    skip: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Project a record row's columns + `props` tail into graph properties.

    FIVE destinations, and no sixth. The first three were the contract before
    §9.1/D1; the fourth is #10853's declared NARROWING and the fifth is
    #10394's declared NON-PROJECTABILITY. Both of the last two are destinations
    rather than absences ON PURPOSE — a name either one refuses must be nameable
    here, or the next reader takes "no further destination" as proof that a
    property cannot be lost on this door:

    * a column the placement table spells → that property, under its name —
      **unless** the row is an EPISODIC Source and the column is in
      :data:`_EPISODIC_SOURCE_PROSE_COLUMNS`, which is the fourth destination
      below (`episodic_source_declined_properties()` is its declared set);
    * a column the schema REFUSES (the EP carve-out, the projection caches) →
      dropped, by name — never stored as authority (S3's contract clause);
    * a column `CACHE_FORBIDDEN_COLUMNS` names → dropped, by name, and for the
      OPPOSITE reason to the line above: it IS authority in the record and the
      cache is the one place it must not land (§9.1/D1, `#10394` door 2);
    * the `props` tail → the open-set surface, unmapped. ⚠️ It is not written
      "verbatim" any more, and the order matters: the tail is seeded **first**
      and a column value **overwrites** it (the row-ordering collision resolved
      once — see the seed comment below). TWO name sets are filtered out of that
      seed: for an EPISODIC Source the declined names, in both spellings, and
      the not-projectable set in every spelling the placement table declares
      (#10394 — the tail is an open set keyed by GRAPH names, so a column-name
      deny alone would leave the back door open);
    * an EPISODIC Source's §9.1/D1 turn prose (`summary`, `_search_text` /
      `_searchText`) → **declined**, whatever build wrote the row, from the
      column door AND from the tail door;
    * anything else → :class:`UnknownRecordColumn`, loudly.
    """
    placed = placement_columns().get(table, {})
    refused = _refused(table)
    forbidden = cache_forbidden_columns(table)
    projected = projected_columns(table)
    # The tail is decoded ONCE: it feeds the identity read below AND the verbatim
    # seed of `out`, and decoding is what decides whether the identity participates.
    # ⛔ A NON-OBJECT TAIL IS REFUSED HERE, NOT SWALLOWED (#10867 review P3).
    # `props` is `jsonb NOT NULL DEFAULT '{}'` with NO CHECK that it is an object,
    # so a corrupt row can carry a scalar or an array; `_decoded_tail` answers
    # `{}` for those by design (it also feeds the predicate, which must answer for
    # every shape), and seeding `out` from that would drop the tail's whole
    # content with no signal — the silent loss this module refuses by name
    # everywhere else. The pre-#10867 block this replaces was gated on `if tail:`,
    # so the class split by TRUTHINESS rather than by shape: a TRUTHY non-object
    # still raised `TypeError` out of the tail block — `set(tail)` for a
    # non-iterable scalar (`7`, `3.5`, `True`), `dict(tail)` for an iterable of
    # the wrong shape (`[1, 2]`) — loud, and kept loud here in this module's own
    # error type, while a FALSY one (`[]`, `0`,
    # `False`, and the text/decoded spelling of each) was skipped entirely and
    # projected CLEAN. Both are refused now, so for the falsy half this is NEW
    # loudness, not preserved loudness.
    tail = _tail_payload(row.get("props"))
    if not isinstance(tail, Mapping):
        raise CacheProjectionError(
            f"{table}.props is a {type(tail).__name__}, not a JSON object — the "
            f"column is `jsonb NOT NULL DEFAULT '{{}}'` with no CHECK, so a row "
            f"can carry a scalar or an array; refusing to drop its content "
            f"silently"
        )
    # §9.1/D1 (#10853): resolved ONCE per row, from the row's identity AS THE
    # NODE WILL CARRY IT — the columns AND the `props` tail — and from the tail
    # already decoded above, never a second decode.
    # ⛔ ONE assignment. The #10852 forward-merge left main's pre-#10867 form
    # (`_is_episodic_source_row(row)`, which re-decoded `props` itself) beside
    # this branch's tail-aware one, so the second statement silently replaced the
    # first and the pair of comments contradicted each other about what the read
    # is FROM. The two were measured to agree on every tail shape — a Mapping
    # tail reaches both paths identically and `_decoded_tail` falls back to `{}`
    # only for shapes already refused above — so the duplicate was dead, not
    # wrong; it is removed because a reader deciding what the predicate reads
    # must not have two answers.
    episodic_source = (table == "source"
                       and _is_episodic_source_row(row, tail))
    # ⛔ PROVENANCE, PER ROW (#10965) — never the column's NAME. On a post-#10000 row `_search_text` is the topic
    # lexicon (exactly `", ".join(topics)`) — not a CHOSEN utterance, though a space-less CJK run arrives whole (the
    # residual in `sdk._session_source_metadata`) — and it is the ONLY field the Source FTS index is declared on, so
    # dropping it un-indexes a restored session. The proof is TWO-TERM (#10852
    # review, P2): the join equality AND a `summary` that is that same join behind
    # `SESSION_SOURCE_SUMMARY_PREFIX`, because a legacy PROSE value can itself BE
    # the join (a list-shaped utterance) while a legacy row's two columns are
    # literally equal. `summary` is NOT relaxed: the door READS it as the WITNESS
    # and never projects it — it now classifies the descriptor exactly, and still
    # declines it, because restoring a display label gains no retrieval (the index
    # reads `_searchText`) and widening the projected field set is its own
    # decision. The table above states that residual and its cost.
    prose_columns = _EPISODIC_SOURCE_PROSE_COLUMNS
    if episodic_source and _episodic_source_search_text_is_the_lexicon(row):
        prose_columns = prose_columns - {"_search_text"}
    # ⛔ THE TAIL IS SEEDED FIRST, AND A COLUMN VALUE WINS (#10867 review ISSUE
    # 1). Ordering here is not cosmetic: `props` is declared LAST in every
    # record table (`CREATE TABLE public.source` … `props jsonb NOT NULL DEFAULT
    # '{}' :189), and Postgres yields `SELECT *` columns in DDL order
    # (`pg_attribute.attnum` — "ordinary columns are numbered from 1 up"), so on
    # a REAL row the tail used to be iterated last and `out.update(dict(tail))`
    # made it the WINNER — of every coordinate it carried, not just the identity.
    # The winner was therefore the row's MAPPING ORDER (`sorted()` in a test
    # fixture puts `props` early, `SELECT *` puts it last), while
    # `_row_identity_view` declares the opposite rule — *"a COLUMN VALUE WINS
    # over the tail's spelling of the same coordinate"*. On a row whose
    # `source_kind` COLUMN and `sourceKind` tail disagreed, the row predicate
    # read the column (document) and the node carried the tail (agentSession):
    # R1 failed OPEN on a node `is_episodic_source_node` itself calls a session,
    # with the turn prose on it — and nothing downstream caught it, because the
    # reconciler re-projects the record through this same projector, so both
    # sides carry the prose and compare EQUAL (`record_reconcile.py:449-451`).
    # Seeding `out` from the tail and letting the column loop
    # overwrite it resolves the collision ONCE, in the direction both functions
    # already declare: the record's COLUMN is the authority for a column-backed
    # property (the writer routes it there — `record_projection._split_properties`
    # — so the tail never legitimately carries one), and the tail stays the
    # open-set surface for the names no column spells.
    out: dict[str, Any] = {}
    if tail:
        refused_tail = sorted(set(tail) & refused)
        if refused_tail:
            raise CacheProjectionError(
                f"{table}.props carries DERIVED/VOLATILE name(s) "
                f"{refused_tail} — the record must not hold them as "
                f"authority, so the cache must not either (the "
                f"schema's own CHECK refuses this row)"
            )
        # ⛔ THE TAIL DOOR IS CLOSED FOR A NOT-PROJECTABLE COLUMN TOO (#10394).
        # `props` is copied VERBATIM below, so a legacy row that carried the
        # name there would reach the graph by the back door while the column
        # door stayed shut. Not reachable through today's writer
        # (`record_projection` classifies `story_arc` as a COLUMN, so it never
        # reaches the tail) — filtered rather than assumed, exactly as the
        # episodic pair is one block below (the tail's declined names are popped
        # in the same seed).
        # ⚠️ BOTH SPELLINGS. `forbidden` holds RECORD COLUMN names and the tail
        # is keyed by GRAPH PROPERTY names, which coincide only for a column the
        # placement table spells one way. Five columns in this projection are
        # AMBIGUOUS (`ambiguities()`: `event.session_id` →
        # `sessionId`/`session_id`, …), so filtering on the column name alone
        # would let a forbidden AMBIGUOUS column walk through the tail while the
        # column door stayed shut — a deny that stops denying without reddening.
        # Both spellings, derived from the same table the column door reads.
        tail_denied = forbidden | {
            spelled for column in forbidden
            for spelled in placement_spellings().get(table, {}).get(column, ())
        }
        out.update({k: v for k, v in dict(tail).items()
                    if k not in tail_denied})
        # ⛔ THE TAIL'S DECLINED NAMES ARE POPPED **BEFORE** THE COLUMN LOOP.
        # Popping them AFTER it removed the coordinate the COLUMN had just
        # legitimately written (#10962 review, P2): a post-#10000 row whose
        # caller-controlled tail also carries the canonical `_searchText` lost
        # the lexicon, i.e. the restored session was left un-indexed — the
        # un-indexing this whole narrowing exists to prevent. Measured at the
        # parent: tail `{"_searchText": "SMUGGLED"}` → `_searchText is None`,
        # with no tail the same row keeps `', '.join(topics)`.
        #
        # Popping here makes the two rules agree, which is what the docstrings
        # already claimed and the code did not do: a COLUMN value WINS the
        # coordinate (`_row_identity_view`'s rule, applied to the node map), and
        # the tail's spelling of a declined name never reaches the node because
        # it was never seeded.
        #
        # ⭐ THE TAIL DOOR STAYS UNCONDITIONAL — it is NOT narrowed by the
        # per-row provenance test (#10965): nothing DERIVES this pair into the
        # tail (the writer classifies both names as COLUMNS), so a tail key is by
        # construction unclassified prose the row smuggled round the column door.
        # ⚠️ BOTH spellings: the tail is an OPEN set keyed by whatever the payload
        # said, so a `_search_text` tail key is the snake spelling of the same
        # datum and must go too.
        # ⛔ AND IT UNDOES ONLY WHAT THE TAIL ADDED, now literally: it is scoped
        # to the tail's own keys and runs before anything else touches `out`, so
        # it cannot subsume the column-loop guard BELOW it (deleting that guard
        # still reds — measured, 14 legs; deleting THIS pop reds 3).
        if episodic_source:
            for name in set(tail) & episodic_source_declined_properties():
                out.pop(name, None)
    for column, value in row.items():
        if column == "props":
            continue
        if column in bookkeeping_columns(table) or column in skip:
            continue
        if episodic_source and column in prose_columns:
            # ⛔ §9.1/D1: a session `:Source` never carries turn prose, whatever
            # build wrote the row — decided by the row's own provenance, not by
            # the column's name (#10965). See `_EPISODIC_SOURCE_PROSE_COLUMNS` for
            # the rule, its pre-existing-capture cost, and its bound.
            continue
        if column in refused:
            # The EP carve-out and the projection-owned caches. Dropped BY NAME
            # rather than by the CHECK's later refusal (S3's contract clause).
            continue
        if column in forbidden:
            # Authoritative in the RECORD, forbidden in the CACHE (§9.1/D1: text
            # in Postgres, not in the graph). ⛔ **DEFENSIVE, not the operative
            # deny, and the distinction is measured rather than assumed**:
            # `projected_columns` already subtracts the same set, so deleting
            # these lines reds NOTHING (measured by review: the seam suites stay
            # green, whose counts depend on which files are selected — so the
            # property is the claim here, not a number) — `story_arc` is a PLACED
            # column, so it reaches the `placed` fallback below either way. It is
            # kept because that fallback means *"DERIVED/VOLATILE"*, a DIFFERENT
            # statement about the same absence: without this line an auditor
            # reading a `story_arc` drop is told the column is derived, which is
            # false (§9.1/D1 makes it authority the record keeps). The operative
            # denies are `projected_columns` for the column and the TAIL filter
            # ABOVE (in the seed) for the open set — and the tail filter IS
            # load-bearing
            # (deleting it reds `test_the_record_restore_writes_no_story_arc…`).
            continue
        if column in projected:
            if value is not None:
                out[canonical_property(table, column)] = _scalar(value)
            continue
        if column in placed:
            # A placed column the schema does NOT authorise: DERIVED/VOLATILE.
            continue
        if column in _GENERATED_COLUMNS:
            # #11642 — a GENERATED column is DERIVED, and Postgres owns it.
            # `public.point/source/event.embedding_vec` is
            # `GENERATED ALWAYS AS (record_embedding_vector(embedding)) STORED`,
            # so it is not authority the cache may hold: the graph keeps
            # `embedding`, and a projected copy of the ANN projection would be a
            # second, unreconciled truth about the same vector. It is skipped
            # BEFORE the unknown-column raise below rather than added to the
            # placement table, because the placement table describes what the
            # RECORD stores and what the CACHE rebuilds — and nothing rebuilds
            # this: Postgres recomputes it from `embedding` on every write.
            continue
        raise UnknownRecordColumn(
            f"{table}.{column} is not classified by the placement table, not "
            f"refused by the schema, and not record bookkeeping — refusing to "
            f"drop it silently"
        )
    return out


def node_from_row(label: str, row: Mapping[str, Any]) -> RecordNode:
    """One record row → one `RecordNode`, keyed by the label's contract key."""
    table = NODE_TABLES.get(label)
    if table is None:
        raise UnknownRecordLabel(f"{label!r} has no record table")
    key_prop = KEY_PROPERTY[label]
    key_column = _rp.snake(key_prop)
    raw = row.get(key_column)
    if raw is None or not isinstance(raw, str) or not raw:
        raise UnprojectableRecord(
            f"{label}: no contract key at {table}.{key_column} — the schema's "
            f"own CHECK would refuse the row, and a NULL key cannot be merged"
        )
    props = _props_from_row(table, row)
    # The key column is a graph property too (`id`, `url`, `eventId` — the
    # contract's key columns; `name` stopped being one for `Object`/`Subject`
    # in #9881), so it is projected under its own name as well as being the
    # MERGE key.
    props.setdefault(key_prop, raw)
    # The fold's `ON CREATE` constants, applied where the record is SILENT
    # (#9634). `None` counts as silence, not as a value: it is a FALLBACK, so a
    # value the record states — a named kind, from the column or from the
    # `props` tail, which `_props_from_row` seeds FIRST and then lets a column
    # value overwrite (and, for an EPISODIC Source, pops the declined names out
    # of — :func:`_props_from_row`'s own docstring states the rule in full) — is
    # never replaced.
    #
    # On the OBJECT side null-as-silence IS the fold's own rule: both clauses
    # COALESCE (`coalesce($ok, 'other')`, `coalesce($title, '')`), so an
    # explicit null and an absent column behave identically there.
    #
    # On the SUBJECT side it is parity TOO (#9572):
    # `s.subjectKind=coalesce($sk, 'other')` with `$sk` NULL-defaulted, so a
    # present-null key and an absent column both take the fold's `'other'` — the
    # same null-as-silence rule as Object, and the record row cannot distinguish
    # them either. (Before #9572 the SUBJECT clause did not coalesce, so this
    # fill WAS the loader's decision for the null case; that residual is
    # discharged, see the note where the table is declared.)
    for prop, default in FOLD_ON_CREATE_DEFAULTS.get(label, {}).items():
        if props.get(prop) is None:
            props[prop] = default
    # §2.6's DERIVED half (#9974): the cache COMPUTES a projection-owned
    # property from the columns the record does carry. Applied AFTER the
    # defaults and only where the record is silent about the target, so a record
    # that ever states the value wins over the derivation. `_props_from_row`
    # cannot state it for `status` (the schema refuses the name), which is why
    # this is the only path that can put a lifecycle value on a rebuilt node.
    #
    # ANY declared source suffices, and it must be non-empty — the sources are
    # alternatives (the record emits each alone: an id-only legacy supersession
    # carries `supersededAt` and NOT `supersededBy`), never a preference order.
    # A single-source reading left the id-only shape underived and therefore
    # LIVE on rebuild, which is #9974 itself. Truthiness (not the record views'
    # `IS NOT NULL`) is deliberate: an empty carrier is measured to coexist with
    # a LIVE node; see DERIVED_FROM_RECORD's bound.
    for prop, sources, value in DERIVED_FROM_RECORD.get(label, ()):
        if props.get(prop) is None and any(props.get(src) for src in sources):
            props[prop] = value
    return RecordNode(label=label, key=raw, props=props)


def relation_from_row(row: Mapping[str, Any]) -> RecordRelation:
    """One `public.relation` row → one `RecordRelation`."""
    rel_type = row.get("rel_type")
    if not isinstance(rel_type, str) or not rel_type:
        raise UnprojectableRecord("relation: no rel_type (the edge predicate)")
    src_label = row.get("src_label")
    tgt_label = row.get("tgt_label")
    if src_label not in NODE_TABLES or tgt_label not in NODE_TABLES:
        raise UnprojectableRecord(
            f"relation: endpoint labels {src_label!r}/{tgt_label!r} are not "
            f"record labels {sorted(NODE_TABLES)}"
        )
    src_key, tgt_key = row.get("src_key"), row.get("tgt_key")
    if not src_key or not tgt_key:
        raise UnprojectableRecord("relation: an endpoint key is empty")
    idx_raw = row.get("idx") or 0
    try:
        idx = int(idx_raw)
    except (TypeError, ValueError) as e:
        raise UnprojectableRecord(f"relation: idx {idx_raw!r} is not an int") from e
    props = _props_from_row(
        RELATION_TABLE, row,
        skip=frozenset({"rel_type", "src_label", "src_key", "tgt_label",
                        "tgt_key", "idx"}),
    )
    return RecordRelation(rel_type=rel_type, src_label=src_label, src_key=src_key,
                          tgt_label=tgt_label, tgt_key=tgt_key, idx=idx,
                          props=props)


# ════════════════════════════════════════════════════════════════════════════
# The EVENT-STREAM fold — the connector / pack lifecycle (#10198)
# ════════════════════════════════════════════════════════════════════════════

#: The event ITSELF, out of a `public.journal_event.payload`. TWO shapes are
#: persisted, and the live fold unwraps one of them:
#:
#: * **flat** — `eventKind` / `object` / `about_entities` at the top level, what
#:   `EventAPI._emit` journals; and
#: * **nested** — `{"version": "1.0", "type": "EventRecorded", "event": {…},
#:   "createdAt": …}`, what `ConversationMiner._emit_event` builds and writes to
#:   the record (`mining.py`, #5475). `_journal_row` strips the envelope keys
#:   (`record_projection.ENVELOPE_KEYS` = type/ts/event_id/initiated_by/
#:   projection_version/seq), so `version`, `event` and `createdAt` SURVIVE and
#:   `payload ->> 'eventKind'` is NULL for every mined event.
#:
#: The live fold reads both — `inner = event.get("event", event)`
#: (`projection/entities.py::apply`) — so a record-side reader that read only the
#: top level would fold NOTHING for a mined event, in silence (review P2).
#: `jsonb_typeof` is what makes the unwrap exact: `payload -> 'event'` yields JSON
#: `null` (not SQL NULL) for a missing key, which `COALESCE` would keep.
_PAYLOAD_EVENT = ("COALESCE(CASE WHEN jsonb_typeof(payload -> 'event') = 'object' "
                  "THEN payload -> 'event' ELSE NULL END, payload)")

#: #9974 — the label each CREATION record types: the reader's half of the
#: creation shape, and the reason the birth leg cannot write a `:Object` status
#: onto a `:Subject` with the same id (ids are per-label).
#: ⛔ IT IS A COPY, NOT THE ONE HOME, and it must not be described as one.
#: It mirrors TWO tables and owes both of them:
#: * **the journal fold's home**, `projection._ENTITY_CREATION_EVENT_LABEL` — the
#:   map the graph fold's creation shapes are declared in; and
#: * **the record layer's own literal**, `record_projection.RECORD_MUTATIONS`
#:   (`ObjectRegistered` / `SubjectAdded` are `NodeSnapshot`s of the `object` /
#:   `subject` tables). ⚠️ That table does NOT read this one: it is written out
#:   again at the record door, so "the map `record_projection` materialises a
#:   snapshot from" would be FALSE — the record layer has its own copy and
#:   nothing in it consults the fold's map.
#: This table is both of them RESTRICTED to the labels that carry a §2.6
#: projection-owned status (:data:`EVENT_STREAM_FOLDS`: `Object`, `Subject`).
#: `EventRecorded` is deliberately absent: `public.event` carries an
#: `event_status` COLUMN and its CHECK refuses only `confidence`, so `:Event`.
#: `status` is record-held rather than a projection-owned cache (and its record
#: key is `event_id`, not `id`).
#: The copy is therefore only as good as the pin that binds it to BOTH homes —
#: `tests/test_cache_projection.py::
#: test_the_birth_event_labels_are_the_one_home_creation_map`, which asserts this
#: table against the fold's map AND against `RECORD_MUTATIONS`' snapshot labels.
#: Without that pin, a creation type added or renamed in either home and not here
#: would leave this table naming the old types, the birth leg would select NOTHING
#: for the new one, and a born-terminal node would rebuild with NO status — which
#: `live._terminal_excluded` reads as LIVE. That is #9974's harm, silently, with
#: every other pin green.
_BIRTH_EVENT_LABELS = {"ObjectRegistered": "Object", "SubjectAdded": "Subject"}

#: The record's own event stream, reduced to the fold's inputs. `seq` orders it.
#: ⚠️ The payload keys are the projection's own contract (#7969) — this is the
#: projection reading them, which is where `status_folds` says the fold lives.
_LIFECYCLE_EVENTS_SQL = (
    "SELECT seq, event_type, "
    f"       {_PAYLOAD_EVENT} ->> 'eventKind' AS event_kind, "
    f"       {_PAYLOAD_EVENT} ->> 'object'    AS object_name, "
    f"       {_PAYLOAD_EVENT} -> 'about_entities' AS about_entities, "
    f"       {_PAYLOAD_EVENT} -> 'aboutEntities'  AS about_entities_camel, "
    # #9974: the OTHER `status` writer. `EntityMutated op=restatus` is a
    # caller's explicit lifecycle write; its value reaches the record ONLY here
    # (`OpDispatch.never_store` refuses the name for the layer tables, so the
    # mutation is a no-op UPDATE), which is why the record-sourced rebuild used
    # to drop it and read the Object back LIVE. Read from `payload -> 'state'`,
    # the map the live fold applies verbatim as `SET n += $state`.
    f"       {_PAYLOAD_EVENT} ->> 'label'     AS label, "
    f"       {_PAYLOAD_EVENT} ->> 'id'        AS node_id, "
    f"       {_PAYLOAD_EVENT} -> 'state' ->> 'status' AS restatus_status "
    # #9974: the FOURTH writer — the CREATION record's own `status`. It is a
    # `NodeSnapshot` whose value the layer table cannot hold (no column, and
    # `_split_properties` refuses the name in `props`), so this is its only
    # carrier. Without it, `create_object(name, status='superseded')` rebuilt
    # from the record with NO status — which `live._terminal_excluded` reads as
    # LIVE, i.e. a terminal Object answered as current after a rebuild.
    # ⛔ THE `jsonb_typeof` GUARD IS NOT DECORATION (review round 2 P2, round 3
    # NOT-FIXED — this is the line that made it real): `payload ->> 'status'` in
    # Postgres returns TEXT for a JSON NUMBER too, so a bare `->>` folds `'5'` for
    # `create_object(name, status=5)` — accepted by the object door (it validates
    # nothing) and stored by the live fold as the INT 5. The state-op sister leg
    # above guards exactly this way, so the two agree on what a legal lifecycle
    # value is: a non-string birth status folds as "the record states none"
    # instead of inventing its text spelling (pinned DB-free by
    # `tests/test_cache_projection.py::
    # test_the_birth_column_guards_a_non_string_status`).
    f"       , CASE WHEN jsonb_typeof({_PAYLOAD_EVENT} -> 'status') = 'string' "
    f"              THEN {_PAYLOAD_EVENT} ->> 'status' END AS birth_status "
    "FROM public.journal_event "
    "WHERE org_id = %s AND graph_id = %s "
    "  AND (event_type = 'EventRecorded' "
    "       OR (event_type = 'EntityMutated' "
    # ⛔ NOT `payload ->> 'op' = 'restatus'`. `_ENTITY_MUTATION_STATE_OPS` is
    # `{rename, restatus, revise, supersede}` and the live fold applies EVERY one
    # of them as `SET n += $state`; `classify_entity_mutation_op` labels the
    # record by precedence (`supersededBy` > `name` > `status`), so
    # `update_entity(obj, name='acme-2', status='archived')` journals
    # `op='rename'` CARRYING `state.status='archived'`. Filtering on the label
    # dropped that value — and `never_store` means it exists in no layer table —
    # so the rebuild answered LIVE for an archived node. The predicate is the
    # PAYLOAD, exactly as `projection/__init__.py::classify_entity_mutation_op`
    # says: a consumer wanting every status transition must read `state["status"]`
    # and must NOT filter on `op`.
    # ⛔ NO `<> ''` HERE, deliberately (#9974 review round 7, P1 — the first
    # draft added one and it CREATED the divergence it claimed to avoid).
    # `update_entity(obj, status='')` is accepted on `:Object` and the graph
    # STORES the empty string, where `_is_terminal_status('')` is false — i.e. the
    # graph reads LIVE. Excluding it at the reader then keeps an earlier terminal
    # fold, so the rebuilt cache HIDES a name the graph resolves: the same
    # hide-a-live-name direction, produced by the "repair". Folding `''` as the
    # value it is reproduces the graph exactly.
    # ⚠️ THIS CLOSES THE `EntityMutated` GROUP ONLY — the outer
    # `AND (…` is closed by the birth clause's tail below. It used to close
    # BOTH (it ended `'string'))`), and appending a third `OR (…)` leg outside
    # the tenant group left the whole statement UNPARSEABLE (`syntax error at or
    # near ")"`) — caught by review on #9974's birth leg, not by the suite,
    # because `tests/test_cache_projection.py::_FakeConn` matches this SQL by
    # substring and never parses it. `tests/test_cache_projection.py`
    # ::test_the_lifecycle_statement_is_parenthesised_as_declared pins the
    # balance, since a DB-free fake structurally cannot.
    "           AND jsonb_typeof(payload -> 'state' -> 'status') = 'string')"
    # #9974: the CREATION records. Selected from the same stream, in the same
    # `seq` order, for the same reason as the two legs above — the value they
    # carry is real graph state (`_upsert_object`'s ON CREATE `status=$st`,
    # derived-or-absent since #9974 R1) and no layer row can place it. ⛔ The
    # label is decided by the EVENT TYPE, not by a payload field: these records
    # predate the
    # `eventKind`/`label` keys, and inventing one here would let a birth record
    # impersonate a connector fold.
    "       OR (event_type IN ('ObjectRegistered', 'SubjectAdded')))"
)

#: The same statement narrowed to the events that could name ONE Object. The
#: `@>` containment test reads an ARRAY and a bare STRING the same way
#: (a scalar contains itself), which is why it is used instead of `?` — the
#: pipeline emits `about_entities` as a list, but a hand-written event may spell
#: it as one string, and the live fold normalises that case rather than
#: dropping it.
_LIFECYCLE_EVENTS_FOR_ONE_SQL = (
    _LIFECYCLE_EVENTS_SQL
    + f" AND (({_PAYLOAD_EVENT} ->> 'object') = %s"
      f" OR ({_PAYLOAD_EVENT} -> 'about_entities') @> to_jsonb(%s::text)"
      f" OR ({_PAYLOAD_EVENT} -> 'aboutEntities')  @> to_jsonb(%s::text)"
      # #9974: a `restatus` names its target by ID, not by name, so the
      # single-row narrowing has to look at the second coordinate as well — a
      # name-only predicate would filter out the very record this widening
      # exists to read, and the read door would answer a status the rebuild
      # does not (the two-door disagreement #10198's own leg pins).
      # ⚠️ The CREATION records join the same id coordinate (#9974's birth leg).
      # ⛔ THE UNWRAPPED EXPRESSION — `({_PAYLOAD_EVENT} ->> 'id')`, the SAME
      # expression the select list uses for `node_id`. Review round 2 (P2)
      # measured the reason: `record_projection._nested_node_carrier` accepts an
      # `event`-nested carrier for ANY `path=None` snapshot, so an
      # `event`-nested `ObjectRegistered` IS materialised by the record and IS
      # folded by the full statement — while a raw `payload ->> 'id'` (NULL for a
      # nested payload) silently EXCLUDED it from the single-row narrowing, the
      # two-door split this whole module exists to prevent. Today's producers
      # (`_create_entity`, `_journal_entity_mutation`) are flat, which is why the
      # raw spelling looked right; it is the ACCEPTED nested shape that decides.
      # `tests/test_cache_projection.py::_ID_CLAUSE_RE` reads the KEY out of
      # this statement (`'id'`, tolerant of the COALESCE), so the FAKE follows
      # whichever key is here rather than assuming one. ⚠️ The KEY is all it
      # follows: the COALESCE-unwrapped **expression** is pinned separately —
      # textually — by
      # `tests/test_cache_projection.py::test_the_narrowing_id_expression_matches_the_selects_node_id`
      # (review round 8, P2: reverting to the raw `payload ->> 'id'` here leaves
      # `test_a_nested_creation_record_is_read_by_the_id_narrowing` GREEN, because
      # the fake compares its own pre-unwrapped `node_id` under whatever key the
      # regex found). Do not delete that textual pin on the strength of the fake.
      f" OR (event_type IN ('EntityMutated', 'ObjectRegistered', 'SubjectAdded')"
      f" AND ({_PAYLOAD_EVENT} ->> 'id') = %s))"
)


def _as_names(value: Any) -> tuple[str, ...]:
    """`about_entities`/`aboutEntities` as a tuple of names, whatever shape.

    The live fold normalises the same three shapes (`inner.get("about_entities")
    or inner.get("aboutEntities") or []`, then a bare string becomes a one-item
    list); a rebuild that only handled the list form would silently drop every
    hand-written event.
    """
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(v for v in value if isinstance(v, str) and v)
    return ()


def _lifecycle_events(cur: Any, *, org_id: str, graph_id: str,
                      target: str | None = None,
                      target_id: str | None = None) -> tuple[LifecycleEvent, ...]:
    """The record's lifecycle rows as fold inputs, ordered by `seq`.

    `target` narrows by NAME (the connector/pack fold's coordinate), `target_id`
    by the stable id (the coordinate a state op **and, since #9974's birth leg, a
    creation record** names). Both may be given;
    they are OR-ed by the SQL, and a caller that holds both must pass both or the
    single-row fold disagrees with the rebuild (#9974 — a `restatus` names its
    target by id only).
    """
    if target is None and target_id is None:
        rows = _rows(cur, _LIFECYCLE_EVENTS_SQL + " ORDER BY seq",
                     (org_id, graph_id))
    else:
        rows = _rows(cur, _LIFECYCLE_EVENTS_FOR_ONE_SQL + " ORDER BY seq",
                     (org_id, graph_id, target, target, target, target_id))
    out: list[LifecycleEvent] = []
    for row in rows:
        try:
            seq = int(row.get("seq"))
        except (TypeError, ValueError):
            continue
        restatus = row.get("restatus_status")
        event_type = row.get("event_type")
        birth_label = _BIRTH_EVENT_LABELS.get(event_type) if isinstance(
            event_type, str) else None
        if birth_label is not None:
            # #9974: the creation record. Keyed by the stable id (the record's
            # own `NodeSnapshot` key, `id`), and labelled by the EVENT TYPE —
            # the payload has no `label` to read, and a guessed one would let a
            # birth record fold onto the other family's node of the same id.
            # ⚠️ `''` IS carried, exactly as in the state-op arm below: the
            # graph stores it and `_is_terminal_status('')` is false. Only a
            # NON-STRING (an absent or null payload `status`) is `None`, and
            # `None` here still MEANS something — it is the creation record that
            # decides the ON CREATE arm, so it must be carried either way.
            birth_id = row.get("node_id")
            if not isinstance(birth_id, str) or not birth_id:
                continue
            birth = row.get("birth_status")
            out.append(LifecycleEvent(
                seq=seq,
                event_kind="",
                is_birth=True,
                birth_status=birth if isinstance(birth, str) else None,
                target_id=birth_id,
                target_label=birth_label,
            ))
            continue
        if event_type == "EntityMutated":
            # #9974: the caller's explicit lifecycle write. It carries NO
            # `eventKind`, so the connector/pack branch below cannot see it and
            # this arm is the only reader. Any STRING folds — `''` included,
            # because the graph STORES `''` and `_is_terminal_status('')` is
            # false, so skipping it would keep an earlier terminal fold and hide
            # a name the graph resolves (review round 7). Only a NON-STRING is
            # skipped, and that shape is R4's declared bound.
            node_id = row.get("node_id")
            # ⚠️ `''` IS a value here: the graph stores it (`status=''` is an
            # accepted write) and `_is_terminal_status('')` is false, so a reader
            # that skipped it would keep an earlier terminal fold and hide a name
            # the graph resolves. Only a NON-STRING is skipped (R4's bound).
            if not isinstance(restatus, str):
                continue
            if not isinstance(node_id, str) or not node_id:
                continue
            label = row.get("label")
            out.append(LifecycleEvent(
                seq=seq,
                # No `eventKind`: a state op is not an event, and synthesizing
                # one would let it match a `status_folds` key it never declared.
                event_kind="",
                restatus_status=restatus,
                target_id=node_id,
                target_label=label if isinstance(label, str) else None,
            ))
            continue
        kind = row.get("event_kind")
        if not isinstance(kind, str) or not kind:
            # An Event with no kind cannot fold (the live fold gates on
            # `if _wk and ...`), and `record_projection` already journaled the
            # row that produced this one.
            continue
        out.append(LifecycleEvent(
            seq=seq,
            event_kind=kind,
            object_name=row.get("object_name") or None,
            # PREFER, do not UNION: the live fold is
            # `inner.get("about_entities") or inner.get("aboutEntities") or []`
            # (`entities.py::apply`), so a payload carrying both spellings with
            # different contents folds only the snake_case list there. A rebuild
            # that concatenated would fold Objects the live graph never touched
            # (review P2, same reader).
            about_names=(_as_names(row.get("about_entities"))
                         or _as_names(row.get("about_entities_camel"))),
        ))
    out.sort(key=lambda e: e.seq)
    return tuple(out)


def read_lifecycle_events(conn: Any, *, org_id: str, graph_id: str,
                          target: str | None = None,
                          target_id: str | None = None) -> tuple[LifecycleEvent, ...]:
    """The record's event stream as fold inputs, for one tenant scope.

    The CONNECTION-scoped entry point to :func:`_lifecycle_events`, for a door
    that holds a connection rather than a cursor (``record_serve.read_node``).
    ``target`` narrows to the events that could name one Object, so a
    single-row read folds only the events that could POSSIBLY name it.

    ⚠️ What that buys is bounded, and it is not "avoids a scan": the predicate
    is on `payload` expressions, while the table's only index is
    `idx_journal_event_node (org_id, graph_id, node_label, node_key)`
    (`20261012000001_record_event_stream.sql:141`) — keyed on the event's OWN
    subject, not on the Objects it names. The narrowing therefore filters rows
    AFTER the scope scan; it narrows the Python fold, not the read. Worth
    saying plainly because the cheaper-sounding claim invites the next reader
    to raise the single-row `LIMIT` or drop the predicate believing either is
    an index win.
    """
    cur = _rp.scope_connection(conn, org_id)
    return _lifecycle_events(cur, org_id=org_id, graph_id=graph_id,
                             target=target, target_id=target_id)


def _lifecycle_index():
    """The pack-declared fold surface, or an EMPTY index — never raising.

    ⛔ Deliberately the PROJECTION's own accessor (`entities._status_fold_index`)
    rather than a second registry lookup here: it carries the last-good
    declared-kind spellings through a registry outage (#2729 review P2), so the
    built-in connector fold cannot re-label a lifecycle-owning kind on the path
    a rebuild takes. A rebuild must fold by the SAME index a live write folds
    by, which means asking the same function rather than re-deriving one.
    """
    from .projection.entities import _status_fold_index

    return _status_fold_index()


def _is_terminal_status(value: Any) -> bool:
    """Is this an Object status the live fold's guard refuses to overwrite?

    The ONE reading of `commit_ops.OBJECT_TERMINAL_STATUSES` on this path —
    the live guard's `$excluded` (`_terminal_object_statuses()` in
    `projection/entities.py`) is the same set, so the replay below cannot drift
    from it. Case-folded, matching what this module already did for the record's
    own `superseded` carrier (the live vocabulary is lowercase, and a
    case-variant is treated as terminal here rather than resurrected).
    """
    from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES

    return isinstance(value, str) and value.lower() in OBJECT_TERMINAL_STATUSES


def apply_event_stream_folds(
    nodes: Iterable[RecordNode],
    events: Iterable[LifecycleEvent],
    *,
    index: Any = None,
) -> tuple[RecordNode, ...]:
    """Derive :data:`EVENT_STREAM_FOLDS`' properties from the record's events.

    A PURE function of (record rows, record event stream) — no graph read, no
    constant of its own. The vocabulary comes from :mod:`tortoise.status_folds`
    (the ONE home, #2729) and the ordering comes from the record's `seq`, so a
    rebuilt cache folds exactly the words the live fold writes, in the order it
    wrote them.

    **A sequential REPLAY, not a last-writer pick.** The live fold is a state
    machine: every one of its `SET o.status` statements carries the guard
    `AND (o.status IS NULL OR NOT (o.status IN $excluded))`
    (`projection/entities.py::_fold_object_status`, both halves), so once an
    event folds an Object terminal, EVERY later fold is refused. The same guard
    is applied here, per event, in `seq` order — a rebuild re-runs the
    transition rules rather than reading the last event's value, which is what
    rehydration means (Microsoft Learn, *Event Sourcing pattern*: *"Applications
    derive the current state of an entity by replaying all the events in its
    stream. This process is known as rehydration."*). Without it, a pack fold
    that targets a terminal word (`archived`) followed by any later fold
    resurrected a terminal Object — the #2164/#3309 defect, re-opened on the
    record path (review P2).

    Applied where a VIEW becomes properties: :func:`read_record_view` (so every
    connection-based consumer — reconciliation, `record-restore`, the CLI — sees
    it) and `FalkorProjection.rebuild_from_record` (so a view a CALLER built
    without a connection folds too). Both applications are idempotent: the
    replay is a pure function of (row props, event stream), so re-running it
    over an already-folded node reproduces the same value.

    ``nodes`` is returned unchanged when the view holds no event or no row the
    fold could touch — the common case, and the reason a rebuild of a
    connector-less tenant does no extra work.
    """
    nodes_t = tuple(nodes)
    props_by_label = {label: props for label, props in EVENT_STREAM_FOLDS.items()
                      if props}
    if not props_by_label:
        return nodes_t
    by_target: dict[str, list[LifecycleEvent]] = {}
    by_id: dict[str, list[LifecycleEvent]] = {}
    for event in sorted(events, key=lambda e: e.seq):
        for name, _explicit in event.targets():
            by_target.setdefault(name, []).append(event)
        if event.target_id:
            by_id.setdefault(event.target_id, []).append(event)
    if not by_target and not by_id:
        return nodes_t

    if index is None:
        index = _lifecycle_index()
    from tortoise.status_folds import builtin_fold_status

    out: list[RecordNode] = []
    for node in nodes_t:
        derived = props_by_label.get(node.label)
        if not derived:
            out.append(node)
            continue
        current = node.props.get("status")
        name = node.props.get("name")
        events_for = by_target.get(name) if isinstance(name, str) else None
        # #9974: the id-keyed half — an explicit `restatus` names its target by the
        # stable id (`MATCH (n:$label {id:$rid}) SET n += $state`), and so does a
        # CREATION record (its snapshot key is the `id`) — not by name —
        # so the two coordinates are merged into ONE `seq`-ordered stream. That
        # single stream is what makes this a replay of the live sequence rather
        # than two independent rules whose relative order is a guess; nothing else
        # in the live fold is order-free.
        by_id_for = (
            # ⛔ `target_label` must EQUAL the node's label; `None` is NOT a
            # wildcard. The live fold refuses a label-less state op outright
            # (`_fold_entity_mutation`: `state-op-miss`, returns 0 for a label that
            # is not canonical), so folding it here would let the rebuild write a
            # value the graph refused — and onto the other family's node whenever
            # the ids collide (ids are per-label, not global).
            tuple(e for e in by_id.get(node.key, ())
                  if e.target_label == node.label)
            if isinstance(node.key, str) else ()
        )
        if not events_for and not by_id_for:
            out.append(node)
            continue
        evs = sorted(list(events_for or ()) + list(by_id_for),
                     key=lambda e: e.seq)
        # The record can carry ONE terminal value of its own (`superseded`, from
        # DERIVED_FROM_RECORD's carriers), and a supersession beats any stream
        # event in either temporal order — so refusing to fold here is exact for
        # that value rather than an approximation of a timeline the carrier does
        # not date.
        # The carrier is the row's FINAL state (derived from `superseded_by`/
        # `superseded_at` as they stand NOW), so it is not a seed the stream may
        # replay on top of: a `restatus` earlier in `seq` would overwrite it and
        # the rebuild would answer LIVE for an entity the graph holds superseded.
        # A stream `supersede` needs no exception — it carries
        # `state.status='superseded'`, the same value the carrier supplied.
        # ⛔ ORDER-BLIND, and stated rather than hidden: `LifecycleEvent` carries
        # no `ts` and `superseded_at` has no `seq`, so a `restatus` that comes
        # AFTER the supersession cannot be placed behind it. A STATUS-ONLY
        # un-terminate (`update_entity(status='live')`) therefore rebuilds as
        # `superseded` while the graph reads LIVE — because that write does NOT
        # clear the carriers (it is `SET n += {status}$props`), it is a record
        # no-op, and `DERIVED_FROM_RECORD` still derives from the retained pair.
        # That divergence PRE-EXISTS this change (#10189) and is recorded on
        # #9974; a temporal guess here would manufacture a rule the record cannot
        # support.
        if _is_terminal_status(current):
            out.append(node)
            continue
        kind = node.props.get("objectKind")
        kind_key = None if kind is None else str(kind).lower()
        # `objectKind` absent => the built-in fold applies (`o.objectKind IS
        # NULL` in the clone's guard); a declared lifecycle kind owns its state
        # and suppresses it (#2725).
        owns = bool(kind_key) and index.owns_state(kind_key)
        # ⛔ THE CONNECTOR/PACK LEG IS `:Object` ONLY. `_fold_object_status` emits
        # `MATCH (o:Object {id:$n})` and resolves `about_entities` against
        # `"Object"`, so the live fold can NEVER write a lifecycle word onto a
        # `:Subject` — while this leg is keyed on a NAME, and names collide across
        # labels (an `:Object` and a `:Subject` both called `acme` is ordinary).
        # Without this gate, adding `Subject` to EVENT_STREAM_FOLDS fabricated
        # `:Subject.status='completed'` from a `github.issue.closed` that named
        # the Object — a value the live fold cannot produce, written by every
        # `rebuild_from_record` AND answered by the read door. ⚠️ Not
        # "invisible to `record_reconcile`": that verb's CACHE side reads raw
        # graph properties (`scan_cache_view`), so a record-side-only value is
        # counted (`field_mismatches`), not hidden. The gate's job is to prevent
        # the divergence, not to hide it — and reconcile cannot tell a FABRICATED
        # value from a correct one once both sides agree on it. The `restatus`
        # leg needs no such gate: it is id-keyed and already label-matched,
        # which is exactly how the live state op matches.
        object_side = node.label == "Object"

        # The replay: `evs` is in `seq` order, and EVERY connector/pack write is
        # guarded the way the live `SET` is — `AND (o.status IS NULL OR NOT
        # (o.status IN $excluded))`. So a terminal word written by one event
        # latches: the later events are refused instead of resurrecting it.
        status: str | None = current
        birth_seen = False
        for event in evs:
            if event.is_birth:
                # #9974 — the CREATION record. `_upsert_object` /
                # `_upsert_subject` write `status` **ON CREATE** and never on
                # MATCH, so only the EARLIEST creation record for this id may
                # speak: #3590 journals another `ObjectRegistered` on EVERY
                # create, and a re-mention must not rewrite a stored status.
                # ⛔ THE ASSIGNMENT IS UNCONDITIONAL *EXCEPT FOR THE TERMINAL
                # LATCH*, and that is the round-6 (P2) fix — for the NORMAL case,
                # where this record is the write that CREATED the node (see the
                # stated bound below for the cases where it is not). There, any
                # fold this replay applied BEFORE the creation record folded
                # NOTHING in the live graph, because the node did not exist yet
                # (`entity_identity.resolve_fold_name` never mints, and an
                # `about_entities` miss is the exempt `unfolded-mention`, so the
                # event is legal and journaled).
                # MEASURED before it: `venture:disbursement` naming `T1` at
                # seq 1 (no node), then `create_object("T1",
                # objectKind="venture:tranche", status="archived")` at seq 2 →
                # the live graph holds `archived` (TERMINAL) while the replay
                # answered `released` (a pack fold from the pre-birth event), and
                # `status is None` — the old gate — refused the birth value
                # because the pre-birth fold had already made it non-None. So the
                # rebuilt Object read as CURRENT and its name resolved: #9974's
                # own harm, one ordering case over.
                # ⚠️ Where a creation record states no status the property is left
                # ABSENT, because absent already reads LIVE and materializing
                # `'live'` would fabricate a lifecycle claim the record never
                # made (pinned: `test_a_silent_birth_record_
                # derives_no_status_rather_than_live`). This is now the SAME rule
                # the live fold applies — `_upsert_object`/`_upsert_subject` seed
                # `status=$st`, derived-or-absent since #9974 R1 (#10524) — so the
                # two routes agree here rather than diverging.
                # ⚠️ STATED BOUND (round 7, P2 — the rule above holds for the
                # CREATED case only, and the record carries NO create-vs-match
                # signal, so it cannot be tightened here): "the earliest creation
                # record" is exact only where that record is the write that
                # CREATED the node. Where the id ALREADY existed (a stub/mention
                # path, an explicit `id=`, a pre-#3590 graph, or a
                # delete-then-re-create) the live fold took a DIFFERENT arm than
                # this replay assumes, and the divergence runs in BOTH directions:
                #   (a) the node existed at that earlier event, so a pre-birth
                #   `restatus`/pack fold the live graph DID apply can be
                #   DISCARDED by this arm. ⚠️ ONE of its orderings is now CLOSED
                #   by the terminal latch below, and the other is NOT — do not
                #   read this as "closed":
                #   (a-1) `restatus → silent birth` (`[restatus(seq 1,
                #   'archived'), birth(seq 2, None)]`) is CLOSED. MEASURED at this
                #   head: it rebuilds `archived`, because `if not
                #   _is_terminal_status(status)` refuses to let the silent birth
                #   clear a terminal the replay already holds (pre-latch it
                #   rebuilt with NO status, which reads LIVE).
                #   (a-2) `delete-then-re-create` is STILL OPEN —
                #   `[birth(None), …, birth('archived')]` rebuilds absent while the
                #   graph holds `archived`, because the value is discarded by the
                #   earliest-only rule, which the latch cannot reach.
                #   (b) the ON MATCH arm keeps the node's own status while this
                #   replay writes the record's — note that a re-CREATE under an
                #   explicit id takes ON CREATE, so there the graph holds the
                #   LATER record's value and this arm answers from the FIRST
                #   incarnation.
                # ⛔ THE TERMINAL LATCH (review P1, rounds 6-7): a creation record
                # is a RE-MENTION as soon as the id pre-existed (a name stub, an
                # explicit `id=`, a pre-#3590 graph, a partial/segment replay from
                # `start_seq`), and then the live fold takes ON MATCH, which writes
                # NO status — so a TERMINAL value an earlier `restatus` put on the
                # node SURVIVES in the graph. Writing the birth value here
                # instead RESURRECTS the node: the replay answers `live`, which
                # `live._terminal_excluded` admits as CURRENT, and
                # `entity_identity.resolve_entity_id` resolves its name. That is
                # #9974's own harm, re-opened by the leg added to close it — and
                # measured on this very tree:
                #     [restatus(seq 5, 'archived'), birth(seq 9, 'live')]
                #       → rebuilt `live`; the graph holds `archived`.
                # So the birth arm honours the SAME latch every other in-stream
                # fold in this function honours (`_is_terminal_status`): a terminal
                # the replay already holds is not overwritten by a later birth.
                # ⚠️ The non-terminal case is NOT affected — the round-6 fix
                # above still applies there, because a non-terminal pre-birth fold
                # (`released`) genuinely folded nothing in the live graph (the node
                # did not exist), and the creation record is the write that made
                # it. `birth_seen` is still set by the EARLIEST creation record
                # whether or not it was allowed to speak, so a later re-mention can
                # never overwrite either.
                # ⛔ BUT A **TERMINAL** PRE-BIRTH FOLD IS NOW REFUSED, and that is a
                # deliberate trade, not an oversight (review P2): for an id that did
                # NOT pre-exist, such a fold matched no node and wrote nothing live,
                # so round 6's rule says the creation record must win — yet the
                # replay cannot tell that case from "the graph already held this
                # terminal" (the record carries no create-vs-match signal). Given
                # the choice, it prefers the TERMINAL, because that lands in the
                # fail-safe direction (the rebuild claims LESS life) while the
                # alternative re-opens #9974's harm (the rebuild asserts CURRENT).
                # MEASURED: `[pack-fold(seq 1,'archived'), birth(seq 2,'live')]`
                # → replay `archived`, graph `live`. Pack-declaration-conditional
                # today (no pack in `packs/` declares a terminal fold target);
                # named as residue (c) at the top of this module.
                if not birth_seen:
                    birth_seen = True
                    if not _is_terminal_status(status):
                        status = event.birth_status
                continue
            # #9974: a `restatus` is the caller's explicit write, applied the way
            # the live fold applies it — `SET n += $state`, unguarded, so it can
            # move a terminal node and it beats any earlier in-stream fold. It
            # carries no `eventKind`, so it can never match a connector/pack key.
            if event.target_id is not None:
                status = event.restatus_status
                continue
            if not object_side:
                continue
            if kind_key:
                for target_kind, fold_status in index.folds_for_event(
                        event.event_kind):
                    if kind_key in index.kind_forms_for(target_kind) \
                            and not _is_terminal_status(status):
                        status = fold_status
            if event.object_name == name:
                builtin = builtin_fold_status(event.event_kind)
                if builtin is not None and not owns \
                        and not _is_terminal_status(status):
                    status = builtin
        if status is None or status == current:
            out.append(node)
            continue
        props = dict(node.props)
        props["status"] = status
        out.append(RecordNode(label=node.label, key=node.key, props=props))
    return tuple(out)


def read_record_view(conn: Any, *, org_id: str, graph_id: str,
                     labels: Iterable[str] = tuple(NODE_TABLES)) -> RecordView:
    """Read the record's node and relation state for ONE scope.

    The scope predicate is not optional: a reader that forgot it would project
    every tenant into this graph. Rows come back `dict`-shaped (a `RealDict`
    cursor, or a plain cursor's `description`).

    THE TENANT SCOPE (R6) IS INSTALLED HERE, not left to the caller (#9600
    review cycle 2, P1). `replay()` and this function share ONE DSN
    (``TORTOISE_RECORD_DSN``), and the tenancy policies read a GUC only
    `replay()` used to set — so ARMED under the app-role DSN this PR mandates, this
    reader would have read ZERO rows from every record table (a missing GUC is
    "no rows", never an error), reported every cache key
    ``extra_in_cache`` with ``missing_in_cache = 0`` — the chokepoint-violation
    / second-writer signature, not a projection lag — and exited ``3`` on a
    coherent pair. That is the
    counterfactual, not a measurement: the DSN is unset on the hosted
    deployment (R1), and no schedule drives the verb, so it had not been
    observed in production. The same
    ``scope_connection`` the write door uses sets the GUC from the very
    ``org_id`` this reader is about to filter on — one value, so the predicate
    and the policies cannot disagree — and refuses a connection whose role
    bypasses RLS, which a read-only door needs at least as much as a writer.

    ``labels`` is DECLARED `Iterable`, so it is materialised ONCE here, before
    anything iterates it (#10258 review, P2). The event-stream fold is gated on
    membership in this set, and a generator caller — the canonical
    `(l for l in …)` form — would have been drained by the node loop, leaving
    the gate `False`, `lifecycle_events` empty, and every connector-tracked
    Object back to the #10198 defect (an absent `status` reads LIVE) with no
    raise, no log and no signal.
    """
    labels_t = tuple(labels)
    nodes: list[RecordNode] = []
    relations: list[RecordRelation] = []
    cur = _rp.scope_connection(conn, org_id)
    for label in labels_t:
        table = NODE_TABLES[label]
        # #10017 R2: the tier is a COLUMN, not a reader's opinion. A turn row is
        # `record_only` — §9.1 keeps raw turns outside the graph — and the cache
        # must not materialise it, or `rebuild_from_record` would re-create the
        # turn nodes the journal rebuild correctly skips: two rebuild routes,
        # two different graphs. An "except turns" predicate here would be
        # inherited by nobody; the next export or sibling projection reading
        # this table would node-ify the row again.
        # ⚠️ Spelled `= 'graph'`, NOT the `<> 'record_only'` a reader might
        # prefer while there are only two tiers: the migration declares the
        # partial index `WHERE cache_tier = 'graph'`, and PostgreSQL uses a
        # partial index only when the query's predicate IMPLIES the index
        # predicate. Measured on postgres:16 over one tenant's rows —
        # `= 'graph'` plans as `Index Scan using idx_point_graph_tier` with no
        # filter, `<> 'record_only'` as `idx_point_scope` plus `Filter:
        # (cache_tier <> 'record_only')`, i.e. every row of the record's
        # highest-volume table read and thrown away. A third tier added later
        # must therefore be a place someone LOOKS, which is the point.
        tier = " AND cache_tier = 'graph'" if table == "point" else ""
        for row in _rows(cur, f"SELECT * FROM public.{table} "
                              "WHERE org_id = %s AND graph_id = %s" + tier,
                         (org_id, graph_id)):
            nodes.append(node_from_row(label, row))
    # …and its EDGES go with it (#10017 R2 review): a CONTAINS edge to a
    # `record_only` turn is planned against a node this view deliberately does
    # not project, so `rebuild_from_record` records it in
    # `relations_not_written` — which makes the operator-facing "declared
    # partial" signal permanently non-empty for every tenant with tiered turns,
    # and therefore worthless. The endpoint subquery is label-agnostic on
    # purpose: `point` is the only tiered table, and a key that is not a point
    # simply never matches it.
    # ⚠️ `<> 'graph'` is the COMPLEMENT and it is deliberately unindexed: a
    # partial index is only used where the predicate implies it, so this scans
    # `idx_point_scope` + filter. Acceptable here and nowhere else — this is the
    # rebuild path, and the node read above keeps the `= 'graph'` spelling that
    # its index can serve.
    record_only = ("SELECT node_key FROM public.point "
                   "WHERE org_id = %s AND graph_id = %s "
                   "AND cache_tier <> 'graph'")
    # ⚠️ The exclusion is scoped to `Point` ENDPOINTS: `node_identity` is keyed
    # `(org_id, graph_id, node_label, node_key)`, so another label may legally
    # carry the same key STRING as a record-only point — an unscoped `NOT IN`
    # would drop that edge from the view AND from `relations_not_written`
    # (whose denominator excludes it): a silent edge loss with no signal.
    for row in _rows(cur, f"SELECT * FROM public.{RELATION_TABLE} "
                          "WHERE org_id = %s AND graph_id = %s "
                          f"AND NOT (src_label = 'Point' AND src_key IN ({record_only})) "
                          f"AND NOT (tgt_label = 'Point' AND tgt_key IN ({record_only}))",
                     (org_id, graph_id,
                      org_id, graph_id, org_id, graph_id)):
        relations.append(relation_from_row(row))
    # #10198: the record's EVENT STREAM is the other half of the `status` cache
    # — the connector/pack lifecycle, which no row carries (see
    # EVENT_STREAM_FOLDS). It is read here, where the record's history is
    # already being read, and CARRIED on the view so a caller that re-projects
    # this view folds by the same events rather than by nothing.
    events: tuple[LifecycleEvent, ...] = ()
    # ⛔ FOLD MEMBERSHIP, not a hard-coded "Object" (#9974 review round 3, P1).
    # `labels` is a caller-chosen subset, so a `read_record_view(labels=
    # ("Subject",))` — a supported shape — skipped the stream entirely, the view's
    # `:Subject` carried no status, and a `rebuild_from_record` over that view
    # materialized it LIVE while `read_node(:Subject)` answered the folded value:
    # the two-answer split, on the label this change added.
    if any(_label in EVENT_STREAM_FOLDS for _label in labels_t):
        events = _lifecycle_events(cur, org_id=org_id, graph_id=graph_id)
    return RecordView(nodes=apply_event_stream_folds(nodes, events),
                      relations=tuple(relations), lifecycle_events=events)


def _rows(cur: Any, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    fetched = cur.fetchall()
    if not fetched:
        return []
    if isinstance(fetched[0], Mapping):
        return fetched
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in fetched]


# ════════════════════════════════════════════════════════════════════════════
# The materialization
# ════════════════════════════════════════════════════════════════════════════


def stored_vector_props() -> frozenset[str]:
    """The property names this projection writes through the store's width.

    Re-exported from :mod:`tortoise.stored_form` so a caller can ask the
    PROJECTION what it narrows without importing the low-level seam, and so
    :func:`node_statement` and ``tortoise.reconciliation`` cannot disagree
    about which properties the cache holds at a different width (#8615).
    """
    return STORED_VECTOR_PROPS


def node_statement(node: RecordNode) -> Statement:
    """Materialize ONE node — `MERGE` by the contract key, `SET` the record.

    The vector properties are written as their own clauses because the graph
    stores a VECTOR (`vecf32`) while a caller-owned one is stored verbatim
    (`Point.embedding_verbatim`, both PRIMARY `#5004`). WHICH properties those
    are, and when the narrowing applies, is read from
    :mod:`tortoise.stored_form` — the one spelling this writer shares with the
    record↔cache check (#8615), so a widened or narrowed writer cannot leave
    the check comparing a form nobody writes.
    """
    key_prop = KEY_PROPERTY[node.label]
    props = dict(node.props)
    verbatim = is_verbatim(props)
    props.pop(VERBATIM_MARKER, None)
    cypher = f"MERGE (n:{node.label} {{{key_prop}:$key}}) SET n += $props"
    params: dict[str, Any] = {"key": node.key, "props": props}
    for prop in sorted(STORED_VECTOR_PROPS):
        vector = props.pop(prop, None)
        if vector is None:
            continue
        form = f"${prop}" if verbatim else f"vecf32(${prop})"
        cypher += f", n.{prop} = {form}"
        params[prop] = vector
    if verbatim:
        # The marker is PRIMARY and the graph writes it as its own clause
        # (`projection/entities.py`), so it is carried in BOTH branches — with
        # and without a vector. Dropping it here would make a record-sourced
        # cache POORER than the record, and the marker is the authority on the
        # storage FORM: a caller-owned float64 vector would lose its flag and
        # the next journaled re-emit would narrow it through `vecf32` (#5004).
        props[VERBATIM_MARKER] = True
    return Statement(cypher=cypher, params=params)


def relation_statement(rel: RecordRelation) -> Statement:
    """Materialize ONE edge, resolving both endpoints by `(label, key)`.

    The predicate is interpolated into the query STRUCTURE
    (`MERGE (src)-[r:TYPE]->(tgt)`), where parameterization is impossible — so
    it is allowlisted exactly as every other interpolation site in the tree
    (`projection/__init__.py`, `sdk.py`, `mcp_server.py`) does. `record_plan`
    builds this before the wipe, so a non-allowlisted predicate refuses the
    whole projection up front rather than raising mid-materialization.
    """
    try:
        validate_rel_type(rel.rel_type)
    except ValueError as e:
        raise UnprojectableRecord(
            f"relation: rel_type {rel.rel_type!r} is not an allowlisted edge "
            f"predicate — refusing to interpolate an unvalidated identifier "
            f"into Cypher"
        ) from e
    src_kp = KEY_PROPERTY[rel.src_label]
    tgt_kp = KEY_PROPERTY[rel.tgt_label]
    cypher = (
        f"MATCH (src:{rel.src_label} {{{src_kp}:$src_key}}), "
        f"(tgt:{rel.tgt_label} {{{tgt_kp}:$tgt_key}}) "
        f"MERGE (src)-[r:{rel.rel_type} {{idx:$idx}}]->(tgt) SET r += $props"
    )
    return Statement(cypher=cypher, params={
        "src_key": rel.src_key, "tgt_key": rel.tgt_key, "idx": rel.idx,
        "props": dict(rel.props),
    })


@dataclass(frozen=True)
class ProjectionPlan:
    """The statements a record view projects to — built before anything runs.

    Materializing the whole plan first is what makes "the projection cannot
    half-apply a view it refuses" true: `node_from_row`/`relation_from_row`
    raise while the plan is being built, so a refusal leaves the cache untouched
    (the same shape `record_projection.replay_plan` uses for Interface A).
    """

    statements: tuple[Statement, ...]
    nodes: tuple[RecordNode, ...]
    relations: tuple[RecordRelation, ...]

    def __iter__(self):
        return iter(self.statements)

    def __len__(self) -> int:
        return len(self.statements)

    def node_props(self) -> dict[tuple[str, str], Mapping[str, Any]]:
        """`{(label, key): props}` — the field set the cache would carry."""
        return {(n.label, n.key): n.props for n in self.nodes}

    def node_keys(self) -> set[tuple[str, str]]:
        return {(n.label, n.key) for n in self.nodes}


def record_plan(nodes: Iterable[RecordNode],
                relations: Iterable[RecordRelation] = ()) -> ProjectionPlan:
    """The projection plan for a record view — a PURE function of the record.

    Nodes first, then relations: an edge's endpoints are `MATCH`ed, so a
    relation written before its node would find nothing.
    """
    nodes_t = tuple(nodes)
    rels_t = tuple(relations)
    statements = [node_statement(n) for n in nodes_t]
    statements.extend(relation_statement(r) for r in rels_t)
    return ProjectionPlan(statements=tuple(statements), nodes=nodes_t,
                          relations=rels_t)


@dataclass(frozen=True)
class ProjectionReport:
    """What a projection did — COUNTS, and the declaration it wrote."""

    nodes_written: int = 0
    relations_written: int = 0
    coverage: str = COVERAGE_COMPLETE
    declaration_keys: int = 0
    scope: str = ""

    def render(self) -> str:
        return (
            f"cache projection: {self.nodes_written} node(s), "
            f"{self.relations_written} relation(s) written; coverage="
            f"{self.coverage} over {self.declaration_keys} declared key(s)"
            + (f" (scope {self.scope})" if self.scope else "")
        )


def project_records(g: Any, plan: ProjectionPlan) -> int:
    """Execute a plan against a graph handle. Returns the statements run.

    ⛔ **No read here decides what to project.** Every statement is a `MERGE`
    derived from the record; the `MATCH` inside a relation statement resolves a
    RECORD coordinate, not a cache fact.
    """
    for stmt in plan.statements:
        g.query(stmt.cypher, params=dict(stmt.params))
    return len(plan.statements)


# ════════════════════════════════════════════════════════════════════════════
# The declaration — the known-hot set, derived from what was materialized
# ════════════════════════════════════════════════════════════════════════════


def _declared_key(scope: str, key: str) -> str:
    return f"{scope}:{key}" if scope else key


def declared_key(label: str, key: str, *, scope: str = "") -> str:
    """The declared coordinate for ONE record node — ``label:key``, scoped.

    ⛔ **The LABEL is part of the coordinate, never discarded.** ``KEY_PROPERTY``
    keys ``Object``, ``Subject``, ``Point`` and ``Session`` all on ``id`` (#9881
    moved ``Object``/``Subject`` off ``name``, which is a mutable natural key —
    two live entities may share it), so a bare key string is **not unique
    across labels**: a key-only declaration collapses ``(Object, "acme")`` and
    ``(Subject, "acme")`` into ONE hot key, and a projection that wrote only one
    of them then declares ``complete`` — the fail-OPEN polarity this module
    forbids (a miss on the omitted label stays ``empty`` instead of becoming
    ``not-hot``/``degraded``).

    It is the coordinate a reader must pass to
    ``residency.mark_residency(..., key=...)``, so the writer and the reader
    speak the same vocabulary rather than agreeing only by accident.
    """
    return _declared_key(scope, f"{label}:{key}")


def declaration_for(record_keys: Iterable[tuple[str, str]],
                    materialized_keys: Iterable[tuple[str, str]], *,
                    scope: str = "") -> ResidencyDeclaration:
    """The known-hot set, from the RECORD's keys and what the projection wrote.

    `complete` **iff every record key it read was materialized** — coverage is
    a fact about the projection's own work, never a constant. Otherwise
    `partial`, enumerating exactly the keys that WERE written.

    ⚠️ A `partial` declaration **claims only what it enumerates** (#7952's
    reader), so an unenumerated node is a residency MISS — `not-hot`, never an
    absence. That is the polarity this function exists to feed.
    """
    record = {declared_key(label, key, scope=scope)
              for label, key in record_keys}
    written = {declared_key(label, key, scope=scope)
               for label, key in materialized_keys}
    if record and written >= record:
        return ResidencyDeclaration(coverage=COVERAGE_COMPLETE,
                                    source="cache-projection")
    return ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                hot_keys=frozenset(written),
                                source="cache-projection")


def declaration_is_sound(record_keys: Iterable[tuple[str, str]],
                         materialized_keys: Iterable[tuple[str, str]],
                         declaration: ResidencyDeclaration, *,
                         scope: str = "") -> bool:
    """Does `declaration` claim only what was actually materialized?

    The check that makes "a projection cannot declare a key hot it did not
    write" enforceable rather than aspirational — a `complete` declaration over
    a partial write is the mutation this refuses.
    """
    if declaration.partial:
        return True
    written = {declared_key(label, key, scope=scope)
               for label, key in materialized_keys}
    record = {declared_key(label, key, scope=scope)
              for label, key in record_keys}
    return written >= record


def write_declaration(g: Any, declaration: ResidencyDeclaration) -> None:
    """Write the known-hot set — ONE declaration format (reused from #7952)."""
    declare_residency(g, declaration)
