---
title: "The record projection — journal → Postgres, total and idempotent (#7969)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-12
updated: 2026-10-12
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7969, epic #7879, interface A, record projection, Postgres record, idempotent replay"
---

# The record projection — journal → Postgres, total and idempotent

**Issue:** **#7969** (P0-blocker, lane `c6-durability`) — epic **#7879**, scope item 3.
**Ruling:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) and §3
(*"Writes go to the truth first, then project"*).
**Interface:** **A** — journal → Postgres (workstream plan, `#7879` comment `6097743519` §2.1).
**Inputs consumed, not re-derived:** **W1** the canonical id contract and **W2** the
field-level placement table (both `#7955`, PR **#8138**), and **W4** the schema
(`#7968`, PR **#8179**). This document is the third step: what those three look
like as a **replay**.
**Test-design consumers:** `#7967` surfaces **S1**, **S9**.
**Scope ruling:** owner, 2026-10-10 — **there are no existing customers.** No
migration-safety apparatus is designed or claimed here.

---

## 0. Plan — the shape, and why

### 0.1 Confirmed problem

`tortoise/projection/` is already a replay of the journal — **into FalkorDB**.
That makes it the *derived cache's* fold, and a cache rebuilt from a cache proves
nothing about the ruling. The record's own fold did not exist, so
*"Postgres is the system of record"* had no mechanism: `#7968` built the
destination and nothing wrote to it.

Interface A states four properties, and the failure each one prevents:

| clause | the failure it prevents |
|---|---|
| every record type has a defined mutation | a record type the journal carries and the record **silently skips** |
| idempotent | a second replay **double-applies** (S1's stated failure mode) |
| total — no handler is a **failure** | two equally incomplete projections compare equal: a **vacuous green** (`#3585`'s finding, and `#5285`'s rule) |
| a field is PRIMARY **iff the journal carries it** | a DERIVED value stored as authority — the carve-out's whole point (§2.0/§17.5) |

### 0.2 Alternatives considered, and why they lose

| approach | verdict |
|---|---|
| **A — hand-write a second fold over 216 property names** (`projection/entities.py`-shaped, but Postgres-targeted) | **Rejected.** It is a second declaration of §17.5, and the second declaration is the one that goes stale. `#7968`'s own schema↔placement pin exists because that failure already happened once. |
| **B — implement the fold in plpgsql inside a new migration, Python only ingests** | **Rejected for this increment.** It puts the mutation set where the placement table is not, and it makes every future layer a migration. The record is a *destination*; the fold belongs to the code that owns the vocabulary. |
| **C — a declarative mutation algebra + a schema-driven engine** ✅ | **Chosen.** One small union (`NodeSnapshot`/`NodePatch`/`NodeDelete`/`RelationUpsert`/`OpDispatch`/`NoRecordEffect`), one registry entry per record type, and the property→column decision read from `#7968`'s migrations **on disk**. Totality becomes a property of the registry; placement conformance becomes a property of the engine, not of 216 hand-written clauses. |
| **D — reuse the graph fold's pure point index (`_apply_one`)** | **Rejected.** It yields Points only — `_NO_POINT_FOLD` documents that Source/Session/Object/Subject and every edge have no representation in it — and it is not a whole-journal fold. Half a record is worse than a declared one. |

### 0.3 Chosen approach

`tortoise/record_projection.py` — stdlib-only, no DB driver, no third-party import:

1. **`RECORD_MUTATIONS`** — one declared mutation per journal record type. A type
   with no entry raises `UnknownJournalRecordType` **before any statement runs**
   (`replay_plan` materializes the whole journal first), so a replay either
   materializes the journal or leaves the record untouched.
2. **A declared refusal is not a skip.** `PointsMerged` and
   `EntityMutated op=rename` are *known* and *refused* with a written reason
   (`RefusedJournalOperation`) — an approximation would silently re-point edges
   the journal does not describe.
3. **`NoRecordEffect` is a decision with a reason**, never a hole: `IngestStarted`
   and `CalibrationRecorded` are audit-only, and `ConfidenceChanged` is the EP
   carve-out in executable form — the record's correct mutation for a recomputed
   belief is *none*, and that is now stated where a reviewer can check it.
4. **The schema is read, not restated.** `table_columns()` / `refused_props_names()`
   parse the four migrations. A property with a column is written; a property the
   schema refuses (the carve-out) is dropped **by name**; anything else lands in
   the table's declared `props` tail.
5. **Idempotence by construction.** Node writes are
   `INSERT … ON CONFLICT (org_id, graph_id, node_key) DO UPDATE SET`;
   patches write absolute values; deletes are `DELETE`; the journal position is
   `ON CONFLICT (org_id, graph_id, seq) DO NOTHING`.
6. **`public.node_identity` first, always** — the `(label, key)` FK parent, which
   is what makes Interface E enforceable rather than conventional (S6).
7. **Edge endpoints are RESOLVED, never minted.** An `EntityLinked` endpoint is
   addressed by the journal `id` (the fold's own `MATCH (t:{label} {id:$tid})`),
   but `relation.src_key`/`tgt_key` are composite FKs into `node_identity`, whose
   key is the label's canonical key (`Object`/`Subject`→`name`, `Source`→`url`,
   `Point`/`Session`→`id`, `Event`→`event_id`). The projection joins the node row
   by `id` and takes its `node_key`; an endpoint with no row lands **no relation
   at all** — the fold's own `"absent"` verdict, not a phantom identity the hard
   delete can never reach.
8. **The caller owns the transaction.** `replay_plan()` materializes, `replay()`
   executes on the caller's connection and **does not commit or roll back**: §3's
   one transaction spans the journal *append* (the chokepoint, `#7951`, not this
   module) and this projection, so only the caller can own its boundary.

### 0.4 Verification

* `tests/test_record_projection.py` — 66 hermetic tests (no DB): totality against
  the graph fold's own dispatch vocabulary, the producers' own emit sites
  (including the `_*_RECORD_TYPE` constants — so a NEW producer reds the suite),
  and `log.py`'s torn-tail-harmless classification; the
  carve-out proven by the projection's **output** rather than by its intent;
  every PRIMARY property storable (the "record poorer than the history" failure);
  idempotence by statement shape; the `(label, key)` refusals; and the edge
  endpoints resolved to the label's canonical key (an `id != name` Object link
  and an absent endpoint, both against the real producer shape).
* `supabase/tests/pglite/validate.mjs` — **the DB half of S1, on a real
  Postgres.** A committed 39-record journal fixture covering the whole declared
  vocabulary is replayed against the migrated schema **twice**, in one
  transaction per replay, and the tables are compared byte for byte; the EP
  scalars the journal carries are asserted absent from the stored rows; an
  `EntityLinked` to an `id != name` Object is asserted to resolve to the
  `name`-keyed row and to disappear with it on hard delete; and an
  undeclared record type is asserted to **fail** the replay.
* `python tools/ci_selection.py --integrity` — the new test is registered where
  its inputs select (`core`), so a projection-only or migration-only change runs it.
* `uv run python tools/surface_manifest.py check` — no MCP tool and no public SDK
  method moved (this is a new internal module behind the chokepoint).

### 0.5 Out of scope (explicitly)

* **The write chokepoint** (Interface C, `#7951`) — this module is the *second*
  step of that path, not the path. It does not route live writes.
* **The cache projection** (Interface B, `#7971`) — record → FalkorDB.
* **The read path** (`#7970`, deferred by §2.0).
* **Normalising the ~9,700 legacy ids** (`#7955` second half).

---

## 1. The registry, by layer

| layer | record types | mutation |
|---|---|---|
| event stream | `SourceCreated`, `DocumentCreated`, `SourceVersioned`, `EventRecorded`, `SessionRecorded`, `SessionDeleted` | node upsert / delete on `source` · `event` · `session` |
| semantic | `ObjectRegistered`, `ObjectSuperseded`, `SubjectAdded` | node upsert / patch on `object` · `subject` |
| epistemic | `PointAdded`, `OperatorAdded`, `PointPromoted`, `OperatorPromoted`, `PointRevised`, `PointRetracted`, `PointSuperseded`, `PointInvalidated`, `OperatorAnnotated`, `DedupeRecorded`, `DedupeRejected`, `EntityBindingRefused`, `BatchIdStamped` | node upsert / patch on `point` |
| epistemic | `EntityMutated` | `OpDispatch` on `op` — patch / delete / **declared refusal** (`rename`) |
| epistemic | `ConfidenceChanged` | **no effect** — the EP carve-out |
| edges | `EntityLinked`, `DirectEdgeCreated`, `DirectEdgeRepoint` | `relation` upsert with `(label, key)` composite FKs |
| epistemic | `PointsMerged` | **declared refusal** |
| bookkeeping | `IngestStarted`, `CalibrationRecorded` | **no effect**, with a reason |

## 2. Field placement — the schema is the contract

`#7968` pins `§17.5`'s placement table to the SQL, in both directions, and its own
test asserts the resolved totals (216 properties → 187 PRIMARY / 29 DERIVED-VOLATILE / 0 U).
This module therefore reads the SQL rather than the table: they cannot disagree,
because `#7968`'s pin already fails if they do.

`tests/test_record_projection.py` closes the loop in the other direction: it
imports `#7968`'s parser (not a second one), builds a record carrying **every**
PRIMARY and **every** DERIVED name of each section, and asserts that every PRIMARY
name is storable and no DERIVED name is.

**The one name the schema's own CHECKs do not cover**, so the projection carries
the refusal itself — plus the name that used to be a schema gap and is now
closed at both layers:

* **`Subject.status`** — once a schema gap (`public.object`'s props CHECK
  refused `status`; `public.subject`'s did not, although §2.6 classifies both as
  the projection-owned lifecycle cache). Filed as **#8263** and now **CLOSED**:
  forward migration `20261014000001_record_subject_props_no_derived` re-declares
  `chk_subject_props_no_derived` to refuse `embedding` **and** `status`, and the
  projection still carries the refusal on its own side too —
  `OpDispatch.never_store` for the `EntityMutated` mutation path, the
  `refused_props_names()` schema read for the snapshot path — so it DROPS the
  name rather than emitting a props tail the CHECK would refuse. `Point.status`
  is deliberately NOT in that set: a Point's lifecycle IS journaled and
  `point.status` is a PRIMARY column.
* **The retired `DocumentCreated` props** (`content`, `doc_status`, `objectKind`)
  — §9.5 Q3 retired them and `_upsert_document` scrubs them, so storing them would
  leave the record holding state the node removed.

## 3. What this document does not claim

* **It does not re-open §2.0.** Postgres-as-record is the owner's ruling; this is
  its replay mechanism.
* **It does not claim the record is complete.** Two record types are declared and
  **refused** (§1), and the **edge set derived from point properties**
  (`extractedFrom` / `references` / `about*` / `mitigated_by` as `relation` rows)
  is not materialized by this increment — those properties are recorded on the
  point row today, and the `relation` table's structural family is a follow-up
  inside `#7969`/`#7951`. Every one of those is a **loud failure or a written
  declaration**, never a silent skip.
* **It does not approve `#7879`'s scope.** Scope and Plan carry human gates.
* **It adds no migration-safety surface** (owner scope ruling, 2026-10-10).
* **It changes no public surface.** No MCP tool and no public SDK method is added,
  removed or renamed.
