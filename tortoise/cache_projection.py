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
   schema declares a column for it and does not refuse it. The property→column
   decision is **read from disk** — the schema (#7968) and the placement table
   (#7955) — never restated here, so a new column is a loud failure rather than
   a silent drop.
3. **Rebuildable from the record alone.** The plan is a pure function of the
   record view: same view ⇒ same statements (idempotent), and the written field
   set equals the record view's field set exactly (never a superset). ⚠️ One
   declared, named exception: the properties in
   :data:`FOLD_ON_CREATE_DEFAULTS`, whose DEFAULT belongs to the fold, not the
   record — a record row that is silent about a kind materializes the same
   constant the live fold would have written (#9634). Named honestly, including
   its cost: the exactness test (:mod:`tests.test_cache_projection`) compares the
   plan against the VIEW, so it cannot see a property the VIEW carries — the
   bound on the exception is the drift pin, not that test. A **second** declared
   exception is :data:`DERIVED_FROM_RECORD` (§2.6's DERIVED half, #9974): a
   property the record is FORBIDDEN to store and the cache must COMPUTE from the
   columns it does carry — `status` from a supersession carrier
   (`supersededBy`/`supersededAt`). Same bound, same pin shape; the divergences
   from the record's own `public.*_current_status` views (their `ELSE 'live'`
   branch, and their `IS NOT NULL` reading of an empty carrier) are measured and
   named at that table, as is the residue it deliberately does NOT derive. A
   **third** declared exception is :data:`EVENT_STREAM_FOLDS` (#10198): the same
   `status`, derived not from a COLUMN but from the record's own EVENT STREAM
   (`public.journal_event`), because the connector/pack lifecycle vocabulary is
   declared at RUNTIME by packs and is not reconstructible from any row. Its
   source is ordered by `seq`, its vocabulary is imported from
   :mod:`tortoise.status_folds` (never restated here), and its bound — the
   terminal statuses the record has no carrier for — is stated at the table.
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
from .stored_form import STORED_VECTOR_PROPS, VERBATIM_MARKER, is_verbatim

__all__ = [
    "ADDITIONAL_PRIMARY_COLUMNS",
    "DERIVED_FROM_RECORD",
    "EVENT_STREAM_FOLDS",
    "FOLD_ON_CREATE_DEFAULTS",
    "KEY_PROPERTY",
    "NODE_TABLES",
    "RELATION_TABLE",
    "AmbiguousRecordColumn",
    "CacheProjectionError",
    "LifecycleEvent",
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
    "canonical_property",
    "declaration_for",
    "declaration_is_sound",
    "declared_key",
    "node_from_row",
    "node_statement",
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
#: * `status` (`'live'` on Subject/Object) — PROJECTION-OWNED CACHE (§2.6/
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
#:   ⛔ **STATED BOUND — the BIRTH status is a third writer this table does not
#:   read (#9974 review round 3).** `_upsert_object`/`_upsert_subject` seed
#:   `status=coalesce($st, 'live')` from the creation record's own payload
#:   (`projection/entities.py`), and `sdk.create_entity` accepts a caller status —
#:   ⚠️ the SUBJECT door validates it against §4.2, the OBJECT door validates
#:   nothing (`sdk.py:4923`, `:23474-23479`: `:Object`/`:Event` "do not validate,
#:   they STORE the value") — so `create_object(name, status='superseded')` writes a
#:   terminal status at birth that `never_store` keeps out of the layer tables and
#:   these two legs do not select (`ObjectRegistered`/`SubjectAdded` are not
#:   `EventRecorded`). A record-sourced rebuild materializes it with NO status →
#:   LIVE, while `rebuild_all` (which reads the JSONL payload) writes
#:   `superseded`. This change therefore reproduces the STATE-OP writer, not every
#:   writer of the field; the birth leg is a follow-up on #9974. Do not read
#:   `LifecycleEvent`'s docstring as claiming the field is fully covered.
#:   ⚠️ Also STATED BOUND — a null-valued `state.status` (`update_entity(obj,
#:   status=None)`) is NOT modelled: the graph applies `SET n += {status: null}`,
#:   which REMOVES the property, while this reader skips a non-string value and
#:   keeps whatever an earlier fold left. The two disagree only when an explicit
#:   null follows a restatus/connector fold on the same node; closing it means
#:   modelling an explicit removal as a fold output, which is its own change and
#:   is recorded on #9974 rather than assumed here.
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
#: WHAT IT READS. `public.journal_event`, the record's own history, restricted to
#: `event_type = 'EventRecorded'` and ordered by `seq` — the per-graph journal
#: position, which is exactly the order the live fold applies events in. The
#: per-event payload keys (`eventKind`/`object`/`about_entities`) are the
#: projection's contract (#7969); they are read HERE, in the projection, and
#: nowhere else. The payload is unwrapped to the event ITSELF first, because the
#: record persists two envelope shapes and only one is flat (see
#: :data:`_PAYLOAD_EVENT`) — the live fold unwraps the nested one, so a reader
#: that did not would fold nothing for every conversation-mined event, silently.
#:
#: HOW IT FOLDS. The LIVE fold's semantics, imported rather than restated:
#:
#: * **the events replayed in `seq` order**, over the ones that name the Object —
#:   its `object` (the event's declared target) or one of its `about_entities`
#:   (the loose list the extraction pipeline emits instead). `about_entities` is
#:   PREFERRED over `aboutEntities`, not unioned with it, exactly as the live
#:   fold reads them;
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
    # `Object.status` explicitly; the Subject twin is `ONTOLOGY.md:421`
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

    TWO record types ride this shape, because the live fold's `status` writes come
    from two places and a rebuild that read one of them reproduced half the
    field:

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
    #: Any state op — the `(label, id)` the live fold matches on. The label is
    #: carried so a state op cannot fold onto the other family's node of the same
    #: id (ids are per-label, not global).
    target_id: str | None = None
    target_label: str | None = None

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
_PLACEMENT_DOC = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "epics"
    / "2026-10-10-7879-postgres-record"
    / "02-identity-and-placement.md"
)

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


def _read_placement_rows(doc: Path | None = None) -> list[tuple[str, str]]:
    """`(table, property)` for every row the placement table declares.

    EVERY class is read (`P`, `D`, `U`) — the class decides whether a field is
    *authoritative*, and the schema decides that (#7968's pin), while the NAME
    is needed here to spell a column back into the graph's namespace. Reading
    the `D`/`U` rows too is what lets the suite assert that a DERIVED column is
    never projected: the name must be known to be refused.
    """
    path = doc or _PLACEMENT_DOC
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CacheProjectionError(
            f"the placement table is not on disk at {path} — the cache "
            f"projection reads the property namespace from it: {e}"
        ) from e
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
    for table, extra in ADDITIONAL_PRIMARY_COLUMNS.items():
        rows.extend((table, prop) for prop in extra.values())
    return rows


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

        projected = schema columns  −  refused  −  bookkeeping

    with the placement table supplying only the SPELLING. That is what makes
    the eight `U` rows resolve correctly without a second copy of §2.7: the
    five that become PRIMARY have a column and are not refused (`updatedAt`,
    `baseline_source`, `temporal_reviewed`, `IMPL.confidence`, `IMPL.weight`),
    and the three that become DERIVED are refused by name (`version`,
    `inherited_at`, `baseline_set` — all in `chk_point_props_no_derived`).
    """
    from .record_projection import table_columns

    columns = table_columns().get(table, frozenset())
    refused = _refused(table)
    placed = placement_columns().get(table, {})
    extra = ADDITIONAL_PRIMARY_COLUMNS.get(table, {})
    return frozenset(
        c for c in columns
        if c not in bookkeeping_columns(table) and c not in refused
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


def _props_from_row(table: str, row: Mapping[str, Any], *,
                    skip: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Project a record row's columns + `props` tail into graph properties.

    Three destinations, and no fourth:

    * a column the placement table spells → that property, under its name;
    * a column the schema REFUSES (the EP carve-out, the projection caches) →
      dropped, by name — never stored as authority (S3's contract clause);
    * the `props` tail → verbatim, unmapped (the open-set surface);
    * anything else → :class:`UnknownRecordColumn`, loudly.
    """
    placed = placement_columns().get(table, {})
    refused = _refused(table)
    projected = projected_columns(table)
    out: dict[str, Any] = {}
    for column, value in row.items():
        if column == "props":
            tail = value
            if isinstance(tail, str):
                tail = json.loads(tail) if tail else {}
            if tail:
                refused_tail = sorted(set(tail) & refused)
                if refused_tail:
                    raise CacheProjectionError(
                        f"{table}.props carries DERIVED/VOLATILE name(s) "
                        f"{refused_tail} — the record must not hold them as "
                        f"authority, so the cache must not either (the "
                        f"schema's own CHECK refuses this row)"
                    )
                out.update(dict(tail))
            continue
        if column in bookkeeping_columns(table) or column in skip:
            continue
        if column in refused:
            # The EP carve-out and the projection-owned caches. Dropped BY NAME
            # rather than by the CHECK's later refusal (S3's contract clause).
            continue
        if column in projected:
            if value is not None:
                out[canonical_property(table, column)] = _scalar(value)
            continue
        if column in placed:
            # A placed column the schema does NOT authorise: DERIVED/VOLATILE.
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
    # `props` tail, which `_props_from_row` keeps verbatim — is never replaced.
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
    "           AND jsonb_typeof(payload -> 'state' -> 'status') = 'string'))"
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
      f" OR (event_type = 'EntityMutated' AND payload ->> 'id' = %s))"
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
    by the stable id (the state-op coordinate). Both may be given;
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
        if row.get("event_type") == "EntityMutated":
            # #9974: the caller's explicit lifecycle write. It carries NO
            # `eventKind`, so the connector/pack branch below cannot see it and
            # this arm is the only reader. `status` must be a non-empty string
            # to be a value the caller wrote — an empty/absent one is not a
            # status and must not fold as one.
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
        # stable id (`MATCH (n:$label {id:$rid}) SET n += $state`), not by name —
        # so the two coordinates are merged into ONE `seq`-ordered stream. That
        # single stream is what makes this a replay of the live sequence rather
        # than two independent rules whose relative order is a guess; nothing else
        # in the live fold is order-free.
        restatus_for = (
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
        if not events_for and not restatus_for:
            out.append(node)
            continue
        evs = sorted(list(events_for or ()) + list(restatus_for),
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
        # `rebuild_from_record` AND answered by the read door, and INVISIBLE to
        # `record_reconcile` because both sides read this same fold. The
        # `restatus` leg needs no such gate: it is id-keyed and already
        # label-matched, which is exactly how the live state op matches.
        object_side = node.label == "Object"

        # The replay: `evs` is in `seq` order, and EVERY connector/pack write is
        # guarded the way the live `SET` is — `AND (o.status IS NULL OR NOT
        # (o.status IN $excluded))`. So a terminal word written by one event
        # latches: the later events are refused instead of resurrecting it.
        status: str | None = current
        for event in evs:
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
