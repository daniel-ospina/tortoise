---
title: "The record projection — journal → Postgres, total and idempotent (#7969)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-12
updated: 2026-10-22
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7969, issue #9553, epic #7879, interface A, record projection, Postgres record, idempotent replay"
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
2. **A declared refusal is not a skip — but a refusal is only correct when the
   record genuinely cannot represent the operation.** `EntityMutated op=rename`
   is *known* and *refused* with a written reason (`RefusedJournalOperation`).
   `PointsMerged` used to sit beside it and was **wrong to**: the graph fold's
   whole mutation for a merge is one `DETACH DELETE` per `merge_ids` entry
   (`projection/entities.py::_EntityHandlers._delete`; `keep_id` is read nowhere
   in `tortoise/` outside the record module), so the record *can* represent it —
   and since `#9892` it does, emitting the same identity+node delete pair
   `EntityMutated op=delete` emits, whose `ON DELETE CASCADE` removes the
   loser's incident relations exactly as `DETACH` does. The refusal had also
   never fired for the only producer (`api.py::merge_points` emits no `op`), so
   the gap was misclassified as a generic fold failure while both merged Points
   stayed in `public.point`.
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
   key is the label's canonical key (`Object`/`Subject`→`id` since #9881, `Source`→`url`,
   `Point`/`Session`→`id`, `Event`→`event_id`). The projection joins the node row
   by `id` and takes its `node_key`; an endpoint with no row lands **no relation
   at all** — the fold's own `"absent"` verdict, not a phantom identity the hard
   delete can never reach.
8. **The caller owns the transaction.** `replay_plan()` materializes, `replay()`
   executes on the caller's connection and **does not commit or roll back**: §3's
   one transaction spans the journal *append* (the chokepoint, `#7951`, not this
   module) and this projection, so only the caller can own its boundary.

### 0.4 Verification

* `tests/test_record_projection.py` — 96 hermetic tests (no DB), 68 of them test
  functions at #9553 (`--collect-only -q` counts the parametrised cases too): totality against
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
| epistemic | `PointsMerged` | `NodesDelete` on `point` — one hard delete per `merge_ids` entry (the graph fold's `DETACH DELETE`; edges go with it, `keep_id` is not written because no unit reads it) |
| bookkeeping | `IngestStarted`, `CalibrationRecorded` | **no effect**, with a reason |

## 2. Field placement — the schema is the contract

`#7968` pins `§17.5`'s placement table to the SQL, in both directions, and its own
test asserts the resolved totals (221 properties → 192 PRIMARY / 29 DERIVED-VOLATILE / 0 U
— 216 names at the `#7955` pass, plus `#2782`'s five PRIMARY money rows, annotated rather
than folded into that pass's numbers).
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
  **Updated by #9553 (R5 class 2):** the ONE edge family that rode the record's
  own envelope — the capture's `(:Session)-[:CONTAINS]->(:Point)` link, carried
  as `contains_session` on `PointAdded`/`OperatorAdded` — now lands a `relation`
  row (`record_projection._ENVELOPE_RELATIONS`), ordered after the Point's
  `node_identity` because both endpoints are composite FKs into it. The endpoint
  gate is `projection._writable_container_id` (the fold's own), mirrored
  value-for-value by `record_projection._writable_endpoint_id`: a blank / NUL /
  lone-surrogate id drops the row on BOTH routes, so the two rebuilds cannot
  disagree about it. The blank case is measured, not stylistic — an indexed
  graph cannot match a whitespace-only key (FalkorDB equality filter, **#9599**),
  so the fold would mint a duplicate `:Session` and land no edge while Postgres
  would have held the row.
  **Updated by #9553 (R5 class 1, `extractedFrom`):** the first family of the
  point-PROPERTY set now lands too — one `relation` row per ref
  (`record_projection._POINT_PROPERTY_RELATIONS`), because that is the fan-out
  the fold's `_link_source` performs (many-to-many, `#3263`). Three details make
  it parity rather than a plausible copy: the target resolves to the SAME Source
  the fold picks — `resolve_source_key`'s canonical identity, matched on `url`,
  on the **canonical spelling**, **or** on `canonical_url` (THREE candidates,
  the same triple `_source_key_resolution` binds for the row key: the graph's
  step 2 adopts by EITHER spelling, and a pre-`#9553` row filed under the
  canonical spelling carries `canonical_url IS NULL` — a two-arm rule leaves
  exactly that population invisible and a variant then files a SECOND row),
  PREFERRING the canonical match and ordered by `node_key` in **byte** order
  (`ORDER BY node_key COLLATE "C"`), which is that resolver's own order (it
  queries `canonicalUrl` first and returns there; the raw spelling is the
  adopt-on-touch fallback) and its own tiebreak (`ORDER BY s.url LIMIT 1`, whose
  comparator is byte order — a bare `ORDER BY`'s LOCALE collation is not the
  graph's answer), and never via the
  `id` fallback `_ENTITY_MATCH_COLUMNS` supplies for `EntityLinked` — which also
  required the projection to DERIVE `source.canonical_url`, since no payload may
  carry it (`projection/entities.py::_EntityHandlers._SOURCE_HANDLED` lists
  `canonicalUrl` as writer-derived); the ref container and the per-member gate
  mirror `_link_source`'s (a dict fans out to nothing, `#8171`); and the `#5256`
  read-version anchor rides `relation.source_version` — a PRIMARY column the
  cache projection maps back to `r.sourceVersion` — from the Point's own
  `sourceVersionTransit` carrier, all-or-nothing and raw-ref keyed, exactly as
  `_point_source_transit` decides. That anchor is written ON CREATE, as
  `_anchor_on_create` does: the INSERT carries it and the conflict path does not
  touch the column at all, so a replayed edge neither advances nor fills a
  recorded `source_version` on either route (every other relation attribute
  stays latest-record-wins — edge state, not a recorded read version).
  **Three residuals, none an unmatched-URL case.** (a) The Source's record must
  precede the Point's (the live order: the capture materialises the session
  Source first, `sdk.py::_materialize_session_source`), else the insert finds no
  `source` row and lands nothing — the `absent` verdict every relation gives.
  (b) A ref naming a Source the record never saw — `_link_source` MINTS an
  **unjournaled** stub for one (`_mint_source_stub`, **#5024**) — has no
  `(Source, key)` identity for `relation.tgt_key`'s composite FK at all, so the
  record lands nothing by design: minting a node from an edge directive would
  materialise a Source no record created. Same absent verdict, same reason, as
  the CONTAINS envelope family (**#9594**).
  (c) **Which RECORDS carry the family, where the fold's own routes disagree.**
  `apply()` materialises a Point from four arms and three reach
  `_upsert_point_edges` — `PointAdded`/`OperatorAdded`, `PointPromoted`,
  `OperatorPromoted` (neither promote payload wrapper drops `extractedFrom`) —
  while `rebuild_all`'s pass 2 re-derives the family for the two creation types
  only. `_POINT_PROPERTY_RELATIONS` declares all four and the record follows
  `apply()`, the chronological engine `rebuild`/`recover_from_log` run: declining
  the promote rows would make the record POORER than the graph for exactly the
  #2256 population whose promote IS the operator's only record. A full rebuild
  that re-derives no promote edge is the divergence that follows, and it sits on
  the pass-2 side.
  The other class-1 families are still **not**
  materialized, each for its own reason: `about*` needs `_create_about_edges`'
  live name→label cascade (and `EntityLinked` already carries it whenever the
  capture resolved it); `TAGGED` has no record `tag` node for
  `relation.tgt_key` to key on.
  **Updated by #9553 (R5 class 3, operator topology):** `mitigated_by` — and the
  `IMPL`/`NAND`/`hasPart` edge and reverse `INPUT` it is emitted WITH — now land
  as `relation` rows (`record_projection._operator_relation_statements`, declared
  by `_OPERATOR_RELATION_RECORDS` for the same four `apply()` arms). The carrier
  is the Point's own `{op_type, inputs}` descriptor, which is what
  `edges.py::_create_edges` reads; the record mirrors it VALUE FOR VALUE — the
  `op_type`→predicate map (four composition spellings → `hasPart`), the reverse
  `INPUT`, and the two mitigation gates (`is_non_operator_payload` AND
  `has_usable_mitigation_strength`, restated because the module loads STANDALONE
  and pinned value-for-value). Three parity details: the endpoints are resolved
  across the **four** epistemic labels (#7902 — an operator input may be a
  `Subject`/`Object`/`Event`, so `relation.src_label` comes from the record's own
  node rows rather than being assumed `Point`, which is why these rows are built
  by a statement whose LABELS are subselect columns); each label resolves to its
  CANONICAL key (`Point`/`Event` → `node_key`, `Subject`/`Object` → `id` since #9881),
  because `relation.src_key` is a `node_identity` FK and the raw journal id for a
  Subject would address a phantom identity; and the conflict path is
  `DO NOTHING`, not `DO UPDATE` — the fold writes `{idx}` on the typed/reverse
  edges and nothing at all on `mitigated_by`, so there is no attribute to
  correct. ⛔ The input gate is `_writable_id`'s (`_writable_epistemic_id`), NOT
  the blank-refusing `_writable_endpoint_id` the CONTAINS/extractedFrom families
  use, and the difference is deliberate: those two address an endpoint with an
  INLINE indexed property match, where FalkorDB's equality filter misses a
  whitespace-only key (**#9599**), while `_create_edges` probes an operator input
  with a label-disjunctive `WHERE` — a SCAN — and lands the edges for a blank key
  (measured through the fold). Reusing the stricter rule here would make the
  record poorer than the graph on the very edge this change carries. Residuals, stated: an input the fold STUBS (a short id that does not
  resolve) lands no row, because a stub has no record row to resolve from — the
  same `absent` verdict as every other relation — and a snapshot whose `operator`
  key is ABSENT on a promote arm lands none, exactly as
  `_promotion_point_with_operator` synthesizes only for `OperatorPromoted`; a
  snapshot whose key is PRESENT but holds `None` (or any non-`dict`) lands none
  too, because the fold's synthesis gate is `"operator" in p` and `_create_edges`
  refuses a non-`dict` — the record follows both, so it can never be richer than
  the graph on a corrupt record. One stated BOUND, and the record is the POORER
  side of it: the fold matches `s` to EVERY node holding an input id and MERGEs
  an edge pair per node, while the record files one row under the first label in
  `EPISTEMIC_LABELS`. Ids are per-node ULIDs, so a duplicated id needs a
  hand-written graph; the row is absent-but-one rather than wrong.
  The remaining R5 classes are still **not** materialized and are named
  where the producer lives: the rest of the point-property set (`about*`,
  `TAGGED`) and the structural predicates whose `DirectEdgeCreated` fold works
  but has **no producer**.
  > **Updated #9553 (R5 class 4).** The structural predicates now HAVE their
  > producer: the edge lane's two mints (`projection/edges.py::create_edge` and
  > `create_about_edge`, plus `create_edge`'s three typed wrappers) journal §4.4's
  > flat `DirectEdgeCreated` — `{src, tgt, edge_type, source_label,
  > target_label}` — through `TortoiseSDK._record_structural_edge`, which the
  > projection reaches via `_bind_structural_edge_recorder` (`_get_proj`, beside
  > `_bind_journal`, and only when a journal exists). Nothing new is invented: the
  > record type, the pass-2b fold and the `RelationUpsert` all predate it; the
  > labels are additive for the RECORD (`relation.src_label`/`tgt_label` are bound
  > values and each label resolves through its own key column) and ignored by the
  > fold. TWO gates sit in front of the emission, both load-bearing: a REPLAY never
  > journals (`projection.replay_window_open()` — `apply()`/`rebuild_all` re-wire
  > about links through this very mint on the SDK's BOUND projection, so without it
  > a recovery write would append to the append-only journal and write back the
  > endpoints it resolved at replay time), and an endpoint the fold cannot address
  > (`Source`; a non-`id`-valued node) is declined rather than half-journaled.
  >
  > **Measured.** PGlite (`supabase/tests/pglite/validate.mjs`) is the record
  > route's CI gate: `PointAdded` + `SubjectAdded` + the descriptor ⇒ exactly one
  > `relation` row `Point/p1 -> Subject/team-x` (the Subject filed by NAME), and
  > idempotent on re-replay. On a local throwaway Postgres + FalkorDB the whole
  > family set was measured 1:1:1 across live / journal-rebuild / record-rebuild
  > (`ownedBy`, `dependsOn`, `hasPart`, `wasDerivedFrom`, `references`, bare
  > `aboutObject`); `relation` goes from 0 rows for each to one. A
  > `capture_session` leaves `aboutObject`/`extractedFrom` short by the captured
  > turns — R4's pinned `point.search_keys` refusal, not an edge defect (the Point
  > never reaches the record, so its edges have no endpoint).
  >
  > **What is and is not carried.** `RECORD_CARRIED_STRUCTURAL_RELS` is the
  > lane's WHOLE write set (23 predicates, asserted at import), because the
  > producer journals each one when its endpoints are addressable — including
  > `aboutDocument`/`aboutSource`, which ARE journaled between epistemic endpoints
  > (measured; `create_edge` type-checks only `aboutDocument`, `create_about_edge`
  > type-checks neither). The per-WRITE decline is the `:Source` endpoint: the fold
  > sweeps `EPISTEMIC_LABELS` on `p.id` and a `:Source` is url-keyed, so
  > `SOURCE_ENDPOINT_CANONICAL_RELS` (`aboutDocument`, `aboutSource`) and a
  > `:Source`-anchored `references` (`link_source_to_entity`) are declined and
  > REPORTED through the package's one skip reporter (`projection._log_identity_skip`,
  > `absent_is_ordinary=False, drops_record=True` — so the skip is COUNTED, not just
  > logged); making a `:Source` addressable is a FOLD change — **#9834**. A
  > self-edge is skipped (the fold's `E2E-11.6`) and warned once per relation at the
  > recorder. The one
  > family with no RECORD carrier is `TAGGED` (no record `tag` table to key
  > `relation.tgt_key` on — a schema decision); a JOURNAL rebuild DOES restore it
  > from the Point's `tags` property (`_sync_tags`, measured), and it is silent:
  > `_sync_tags` reaches neither the lane nor this producer.
  >
  > ⚠️ **Supersede.** The `DirectEdgeCreated` fold's superseded-endpoint skip is gated on what the supersede DID
  > to that edge (`_supersede_took_this_edge`), because each of its three shapes was a live ≠ rebuild divergence
  > until the gate learned it: (1) DIRECTION — leg 2a (`IMPL`/`NAND`) moves both ways and always journals a
  > repoint, while leg 2b moves only the OUTGOING side, so an incoming edge must be replayed; (2) ORDER — an edge
  > minted at an already-superseded id was never moved by the supersede, so the gate compares the creation's
  > journal seq with the supersede's and replays it; (3) THE DELETE-LEG'S BLIND SPOT — a replayed post-supersede
  > creation is exempted from the 2b repoint delete-leg (which cleans pass-2's resurrection and cannot tell the
  > two apart) by `(src, edge_type, RESOLVED target node)`, while the successor-side transfer still runs. Residuals:
  > (a) **CLOSED by #9836** for every ADDRESSABLE target — `wasDerivedFrom`/`aboutAction` are MOVED live by 2b but
  > are not derivable, so 2b used to journal no repoint and the journal route lost them after a supersede. They now
  > carry a descriptor too (the ID-ADDRESSED family: the target is addressed by the node's own identity property —
  > `id`, or `eventId` for an `Event` — with the label the transfer observed riding `target_label`), and both routes
  > resolve `(label, identity)`, so `live == journal-rebuild` for every shape whose target the fold can NAME.
  > ⛔ TWO boundaries remain, both stated. (i) **A target with no addressable identity** — a `:Source` (url-keyed,
  > outside `RECORD_ADDRESSABLE_EDGE_LABELS`) or an `Event` the journal never gave an `eventId` — gets NO descriptor
  > (it is never half-journaled), so live still MOVES that edge while **both** rebuild routes hold nothing; making
  > `:Source` addressable is a FOLD change, **#9834**, and the producer's own skip is pinned by
  > `tests/test_9836_id_addressed_repoint.py::test_the_descriptor_is_NOT_emitted_when_the_target_cannot_be_addressed`.
  > (ii) The PRE-FIX JOURNAL: a supersede journaled before #9836
  > carries no descriptor for these two rels, so on that journal the record keeps the row at the pre-supersede point
  > while live moved the edge to the successor (a rebuild does not repair historical graphs).
  > (b) **CLOSED for the derivable set by #10191**, and closed for the ID-ADDRESSED family by #9836 — the record
  > applies a 2b descriptor as a MOVE (`DELETE` at the superseded point + `INSERT` at the
  > hop `superseded_by` names — one hop per descriptor, so a chain reaches the terminal row as each hop runs), so the row follows the edge. A shape the producer DECLINES at the mint (`_record_addressable`) holds nothing at all — no
  > snapshot, no row — and the `:Source`-targeted edge is one of those, not the only one: the same gate also
  > declines any endpoint whose value is not that node's own `id` — a `:Source` value, an `Event` the journal
  > never gave an `eventId`, AND an `Event` whose `id` differs from its `eventId` (the guarded-meeting shape,
  > where `id` stays the shared candidate): the MOVE descriptor addresses an Event by `eventId`
  > (`ID_ADDRESSED_TARGET_ID_PROPS`), while this CREATION gate and the fold's `p.id IN $ids` creation probe
  > still speak `id`, so the mint declines the pre-supersede row for that shape. A
  > non-`:Source` edge of these shapes lands nothing at CREATION for the same reason — the MOVE descriptor
  > still follows live when it runs.
  >
  > ⚠️ One uncarried writer is OUTSIDE this lane and stays open:
  > `sdk.backfill_about_entities` mints about edges through `_create_about_edges` →
  > `wire_about_link` (a bare MERGE) with no record and no `aboutEntities` snapshot
  > prop, so neither carrier restores them — **#9833**.
  >
  > ⚠️ **One RECORD-side residual the producer cannot reach — `idx` (#9844), measured.** A
  > `DirectEdgeCreated` descriptor carries no `idx`, so the record defaults it to `0`, while
  > `relation`'s conflict key includes `idx` and an operator's own `hasPart`/`IMPL`/`NAND` rows
  > carry `idx = enumerate(inputs)`. Mint the edge an operator's record already carries at
  > `idx >= 1` (`create_operator("composedOf", a, [b])`, then
  > `create_edge("hasPart", op, b)`) and the LIVE graph is unchanged — the `MERGE` is
  > property-agnostic, so it dedups onto the `idx=1` edge and mints no new edge — while the
  > record route computes `(op, b, hasPart, 0)` beside the existing `(op, b, hasPart, 1)`: two
  > rows for one edge, the record RICHER than the graph. The producer cannot fix it (the mint
  > takes no property parameter, §9.6, and cannot read the `idx` the existing edge carries), so
  > the fix is a record-layer rule with its own test — insert only when the
  > `(org, graph, rel_type, src, tgt)` triple is absent, for this family only — filed as
  > **#9844**.
* **It does not approve `#7879`'s scope.** Scope and Plan carry human gates.
* **It adds no migration-safety surface** (owner scope ruling, 2026-10-10).
* **It changes no public surface.** No MCP tool and no public SDK method is added,
  removed or renamed.
