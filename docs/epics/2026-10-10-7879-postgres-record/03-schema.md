---
title: "The Postgres schema — the four ontology layers as relational structure (#7968)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-12
updated: 2026-10-12
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7968, epic #7879, interface A, Postgres schema, EP-scalar carve-out, canonical id contract"
---

# The Postgres schema — the four ontology layers as relational structure

**Issue:** **#7968** (P0-blocker, lane `c6-durability`) — epic **#7879**, scope item 1.
**Ruling:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) — **Postgres is the system of record for every layer; FalkorDB is a derived hot cache.**
**Inputs consumed, not re-derived:** **W1** the canonical id contract and **W2** the field-level placement table (both `#7955`, landed in PR **#8138**; the table is `docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md`, §17.5's own pointer).
**Interface:** **A** — journal → Postgres (workstream plan `/tmp/plan-storage.md` §2.1; `#7879` comment `6097743519`).
**Test-design consumers:** `#7967` surfaces **S1**, **S6**, **S7**.
**Scope ruling:** owner, 2026-10-10 — **there are no existing customers.** No migration-safety apparatus is designed or claimed here.

---

## 0. Plan — the shape, and why

### 0.1 Confirmed problem

The epic's central claim is *"Postgres is the record"*, and the record has **no representation of the ontology at all**. Measured at the base of this branch: **24 `CREATE TABLE`s across `supabase/migrations/`, zero ontology nouns** (`#7967` §1–2, re-verified — the only post-rename reading is `org_id`, see §5.2). A two-store design whose record cannot hold a `Point` is not a design; it is a proposal.

The schema cannot be written from the ontology alone, and it cannot be written from the code alone:

* **The ontology** says what the layers *are* (`ONTOLOGY.md` §1–§8) but not which store owns each field — that is the §17.5 question;
* **the code** says what is *written* (216 measured property names) but not what is *authoritative*.

W2 answers the second with the deciding test (journal-carried ⇒ PRIMARY), and W1 fixes the key every layer must agree on. This document is the third step: what those two decisions look like as tables.

**The failure this prevents, named concretely.** A record that stores `ep_alpha` as a column makes a **recomputed belief authoritative** — and then two writers disagree about it, which is precisely the "derived cache that is also written to directly is not derived" defect (§17.1). A record whose foreign key is a **bare id** re-creates `#2489`: a rebuilt edge binds its sibling by a graph-resident id and **resolves to nothing, in silence**.

### 0.2 Alternatives considered, and why they lose

| Approach | Verdict |
|---|---|
| **A — one `node` table** (`(label, key)` PK + a `props jsonb`), i.e. EAV | **Rejected.** It makes every FK trivial and loses the thing the issue asks for: *the four layers as relational structure*. It also makes the placement table unenforceable — with one blob, a DERIVED value is a JSON key, and nothing can refuse it. |
| **B — column-per-property, relation endpoints as per-target nullable FKs** (`tgt_point_key`, `tgt_source_key`, … with an exactly-one CHECK) | **Rejected.** It gives real FKs but re-states the target set in **every** relation row, and the `(label, key)` pair becomes implicit in a `CASE`. Adding a label means editing every relation-bearing CHECK. |
| **C — an identity-registry table + column-per-property** ✅ | **Chosen.** `public.node_identity (org_id, graph_id, node_label, node_key)` is the single FK target, so `relation`'s two endpoints are **real composite foreign keys**; `key_property` is **generated from the label**, so a store that disagrees with W1 cannot write the row; and each layer table pins `node_key` to its own key column with a CHECK. Cost: one registry row per node. |
| **D — columns only where the code queries, everything else in `props`** | **Rejected.** "Queried in the graph today" is not the record's placement test. The placement table's class is. |

**A `props jsonb` column is kept on every layer table — as a *named tail*, not as an escape.** The measured 216 names are a **floor** (the open-set `SET n += $props` passthrough is unbounded, W2 §2.3–2.6), so a purely closed schema would reject the tail. The tail is bounded by a **CHECK that refuses every known DERIVED/VOLATILE name by name** — so the passthrough cannot become the back door the carve-out exists to close.

### 0.3 Chosen approach

Split **by layer**, **event stream first** (the plan's instruction), one migration per layer:

| migration | layer | tables |
|---|---|---|
| `20261012000001_record_event_stream.sql` | event stream | `node_identity`, `journal_event`, `source`, `event`, `session` |
| `20261012000002_record_semantic.sql` | semantic | `object`, `subject` |
| `20261012000003_record_epistemic.sql` | epistemic | `point`, `relation` |
| `20261012000004_record_procedural.sql` | procedural | **views only** — `record_event_stream`, `object_current_status`, `subject_current_status` |

**Why the event stream is first, and what "event stream" means here — the trap in the name.** W2 warns explicitly that the ontology uses *"journal/event stream"* for **the reconstruction source** while calling the `Event`/`Source`/`Session` group the Episodic **layer**. The issue's wording (*"the journal's own shape"*) means the *journal*; the placement table's grouping means the *layer*. **Both are built here, and they are labelled, because a lane that picks one reading silently builds half a layer**: `journal_event` is the journal (append-only, the projection's input and idempotency anchor), and `source`/`event`/`session` are the Episodic entities it materializes.

**Why the procedural layer is a view, not a table.** The procedural layer owns **no property name of its own** (W2 §2.6): its fields are `Object.status` (a **cache** — `ONTOLOGY.md` §2/§4.3) and the PRIMARY halves `supersededBy`/`supersededAt`, which live on the semantic rows. A table here would make a cache authoritative. Capstone **S5** asks for exactly the opposite, so the layer is the fold.

### 0.4 Verification

* `tests/test_record_schema_placement.py` — pins **schema ↔ placement table** in both directions, derived from the table itself (§5.1 states the mutation).
* `supabase/tests/20261012000001_record_schema.sql` — the pgTAP half (S1/S6/S7): the PKs, the composite FKs, the RLS boundary, the append-only journal, and that each carve-out **CHECK actually refuses** a derived value.
* `npm --prefix supabase/tests/pglite run validate` — all migrations apply on a fresh database and every suite passes (Postgres is available locally via PGlite, so **the SQL half is verified**, not asserted).
* `uv run python tools/surface_manifest.py check` — proves no MCP tool or public SDK method moved.

### 0.5 Out of scope (explicitly)

* **Normalising the ~9,700 legacy ids** — W1b, a separate unit (§4).
* **The projection** (journal → record) — `#7969`.
* **The cache projection** (record → FalkorDB) — `#7971`.
* **The vector representation and index shape** — `#5090` (§4.3).
* **No migration-safety surface**: dual-write · backfill · cutover · rollback parity · customer comms.

---

## 1. The schema, per layer

### 1.1 Event stream (100 names: 94 PRIMARY · 6 DERIVED/VOLATILE)

* **`journal_event`** — the journal's own shape: `(org_id, graph_id, seq)`, `event_type`, `node_label`/`node_key` (the event's subject, **deliberately without a foreign key** — the journal precedes the node it creates), `recorded_at`, `payload jsonb`. **`seq` is the idempotency key**: applying a position twice is a no-op, which is what makes Interface A's *idempotent* and *total* claims checkable rather than aspirational. **Append-only is enforced, not documented**: a `BEFORE UPDATE OR DELETE` trigger refuses both.
* **`source`** (44 P / 5 D) — `url` is the key (`node_key = url`).
* **`event`** (29 P / 1 D) — `event_id` is the key (`node_key = event_id`); `id` mirrors it (dual of `eventId`, `#122`).
* **`session`** (21 P) — `id` is the key. Beyond W1's five-type contract, which governs the five ontology entity types; `Session`'s key is its MERGE key `id`, and it is registered in `node_identity` with that key so its relations are FK-checkable like any other.

### 1.2 Semantic (23 names: 20 PRIMARY · 3 DERIVED/VOLATILE)

* **`object`** (16 P / 2 D) — `name` is the key.
* **`subject`** (4 P / 1 D) — `name` is the key.
* **Deliberately absent**: `status` on both (a projection-owned cache) and `embedding` on both.

### 1.3 Epistemic (93 names: 73 PRIMARY · 20 DERIVED/VOLATILE after §2.7)

* **`point`** (58 P / 16 D after resolution) — `id` is the key.
* **`relation`** — **one row per edge**, per `ONTOLOGY.md` §8's reification rule: the operator is always a `:Point {is_operator:true}`, so `IMPL`/`NAND`/`INPUT` are rows whose source is that operator, alongside `about*`, `extractedFrom`, `references`, `mitigated_by`, `CONTAINS` and the responsibility edges. Edge attributes are columns: `idx`, `direction`, `confidence`, `weight`, `label`, `batch_id`, `source_version`.
* **`relation.confidence` is the CALLER'S RECORDED EDGE ATTRIBUTE, and `point.confidence` deliberately does not exist.** §2.7 #7/#8 resolves the collision **by home, not by polarity** — the edge attribute is an input (PRIMARY), the propagated belief is DERIVED. Collapsing the two onto one column is the defect this separation prevents.

### 1.4 Procedural (0 own property names)

Views only, over the record's own PRIMARY columns:

* `object_current_status` / `subject_current_status` — `status` folded from `superseded_by`/`superseded_at`;
* `record_event_stream` — the journal in relational form (envelope keys `type`/`ts`/`event_id` + the payload verbatim).

All three are `security_invoker = true`, so the caller's RLS applies at the seam — a view is not a tenancy hole.

---

## 2. The `(label, key)` foreign key

W1 fixes the key **by label** — `:Point → id`, `:Event → eventId`, `:Subject`/`:Object → name`, `:Source → url` — and states the rule that follows: **a foreign key is `(label, key)`, never a bare id**, because the same string used as a `:Subject` and as a `:Source` is two different nodes.

Three enforcements, and each closes a different hole:

1. **`node_identity.key_property` is `GENERATED ALWAYS AS (CASE node_label …) STORED`** — the contract is in the database, so a caller cannot write a row that disagrees with W1 without changing the schema.
2. **Each layer table pins its own key** — `chk_source_node_key_is_contract_key CHECK (url IS NOT NULL AND node_key = url)`, `chk_point_node_key_is_contract_key CHECK (id IS NOT NULL AND node_key = id)`, and the same for `event`/`session`/`object`/`subject`. The `IS NOT NULL` guard is load-bearing: a bare `node_key = url` is SQL NULL (not FALSE) when `url` is NULL, so the constraint would be satisfied and the key would resolve to nothing in silence (#8179 review P1). A label and its key are **one unit** (W1 §3's warning): a retargeted label with a stale key resolves to nothing.
3. **`relation` carries `src_label`/`src_key` and `tgt_label`/`tgt_key`, each a composite FK into `node_identity`.** This is what makes the contract a *constraint* rather than a convention — and it is why the registry table exists at all: SQL cannot reference two tables from one column, so without it *"the FK is `(label, key)`"* would be unenforceable by construction.

**The stable-name replay keys are NOT re-declared here.** `stub_key` + `STRUCTURAL_REL_LABELS` (`tortoise/projection/edges.py`) stay the single home for *replay* resolution, pinned by `tests/test_identity_contract.py`. This schema stores the resolved endpoints; it does not become a second key map.

---

## 3. The EP-scalar carve-out

`ep_alpha`, `ep_beta`, `ep_dirty`, `ep_dirty_at`, `confidence`, `c_cal`, `posterior_alpha`, `posterior_beta`, `lastDreamedAt`, `baseline_set`, `content_hash`, `inherited_at`, `version` (Point), the four BP `msg_*`/`back_msg_*` slots, and the replay bookkeeping `_nid`/`_graph_id`/`reason` are **DERIVED/VOLATILE** — recomputed from the structure, never stored as authority. Every one is **absent as a column** and **refused by name in `props`**.

The carve-out is enforced in three places, because "the schema must say so where a maintainer will read it" is the issue's actual requirement:

* a `COMMENT ON TABLE`/`COMMENT ON COLUMN` on `point`, `relation` and the procedural views;
* the `chk_*_props_no_derived` CHECKs (a write that persists one **fails**, loudly);
* `tests/test_record_schema_placement.py`, which derives the absence from the placement table.

### 3.1 The `baseline_*` narrowing (the #7955 correction, carried)

§17.5 as first written listed `baseline_set`/`baseline_source` together as unconditionally DERIVED. **That is wrong for the first and half-wrong for the second**, and this schema carries the correction:

* **`baseline_source` is PRIMARY when `'set-by-author'`** — the `set_point_baseline` default, a **non-reconstructible human/agent input**. The carve-out's own test is *"recomputed from the structure"*, and an author-stated prior is not recomputed from anything; it **is** the input. §2.0 does not reach it, so applying §3's per-write test is applying the ruling, not overriding it.
* **It is narrowed, not widened**: only the author-set token is storable (`chk_point_baseline_author_set`), so the recomputable values (`system-default`, `inherited-from-source`) can never enter the record. The alpha/beta the token names ride alongside it (`baseline_alpha`, `baseline_beta`), present-or-absent together (`chk_point_baseline_complete`).
* **`baseline_set` stays DERIVED and has no column** — and it is *not* derivable today (token-less legacy `baseline_set=true` rows predate the `#2199` token family, and `ep.py::_write_node` writes `ep_alpha` for every point, so `ep_alpha IS NOT NULL` over-counts). The fix is to journal the author-set baseline, which is `#7969`'s journal-completeness work, not a schema column.

---

## 4. What was deliberately left out, and why

| left out | why |
|---|---|
| **The ~9,700 legacy ids** (`8,700` `<hex>-<hex>`, `1,000` `p-NNNNNNN`, ~`30` `EV-*`) | W1b — a separate unit, **P2 per the workstream plan / P1-severe per `#7955`'s own correction comment** (recorded, not silently resolved). **The rule that makes the later migration safe**: the mapping is **total and single-valued** (a legacy id is already the node's unique key — a rename, never a merge), the rewrite is **a journal event, not a raw `SET`** (so `replay(journal)` reproduces it and `rebuild == live` still holds), and it is cheap **only while the dogfood instance is the only data**. An id that matches no minted shape is **OPAQUE AND VALID** — nothing here validates ids at read time. |
| **Ontology fields the 2026-10-10 pass did not measure** — `Object.goalState`, `Object.validFrom`/`validTo`/`expiredAt`, `Subject.validFrom`/`validTo`/`expiredAt`/`updatedAt`, `Source.expiredAt`, `Point.`-family spec-only fields | Placing them here would **invent** a placement the table did not decide. The table is a **floor**, so the honest move is to name them (`#7969` re-measures as it builds the projection) rather than to guess a class. **One exception was taken**, because a *recorded* rule decides it: `Subject.supersededBy`/`supersededAt` (`#1423`, landed after the measurement) are classified by §2.6's own test exactly as the Object twins are — journal-carried verbatim ⇒ **PRIMARY**. `Subject.status` is *not* added: §2.6 and `ONTOLOGY.md` §4.2 both name it a cache. |
| **The vector column type** | `embedding` is PRIMARY for `Source`/`Event`/`Point`, so it has a column — as `jsonb`. The `pgvector` type and the index shape are `#5090`'s open decision (§12.1c); the column exists so the placement invariant holds, and an `ALTER … USING` cast is loss-free when that lands. |
| **A `rel_type` CHECK (closed predicate vocabulary)** | A closed CHECK refuses a legitimate predicate the moment the vocabulary grows; the vocabulary's enforcement home is the write path (`ONTOLOGY.md` §3.9). The record declares the vocabulary it knows (in the migration comment, pinned by the test) and does not police it. |
| **A validation gate on id shape** | It would refuse live rows for a spelling the current code never minted (W1 §1.4). |

---

## 5. Findings — contradictions recorded, not silently "fixed"

**5.1 The task brief's aggregate counts disagree with §17.5, and the disagreement is exactly the three review corrections.** The brief states the placement table as **216 → 190 PRIMARY / 26 DERIVED-VOLATILE**. Parsing the table and applying §2.7 yields **216 → 187 / 29 / 0**, which is also what `STORAGE-ARCHITECTURE.md` §17.5 and `02-identity-and-placement.md` §2.7 both state. The difference is precisely the *three measured classes corrected in review* (`Object.status` is a projection-owned cache; `Object.embedding`/`Subject.embedding` are recomputed from the carried `name`) — `185 + 5 = 190` and `23 + 3 = 26` is the count **before** those three corrections, and `182 + 5 = 187` / `26 + 3 = 29` is the count after. **The table is treated as authoritative**; the test asserts 187/29/0 so any future change to either the document or the SQL turns it red.

**5.2 The plan's tenancy column name is stale.** The workstream plan and `#7967` say *"`team_id` on every row"*. The live schema is **`org_id`** — `20260915000001_tenancy_team_to_org.sql` renamed `teams → organizations`, `team_id → org_id` and the GUC to `app.current_org_id`, and `#7967`'s own re-measurement (unqualified *"one COLUMN is named `source`"*) is consistent with that. This schema uses **`org_id`** and the `app.current_org_id` GUC. The plan's sentence is a naming drift from the same rename, not a different decision.

**5.3 `#7967`'s S6 row still lists a retired replay key.** The test-design map and `/tmp/plan-storage.md` §2.5 name the stable-name replay keys as `url` / `name` / `coalesce(title, name)`. **`coalesce(title, name)` is not a current replay key** — W1 §1.3 already recorded the correction (`#2489` moved `aboutDocument` to `url` when a document became a `:Source`; a title-only target now resolves to **nothing**). This document consumes W1's corrected statement; the design doc is not the authority to re-open.

**5.4 The connector work-item states are not reconstructible from the Object row.** `Object.status`'s cache includes `in_progress`/`completed` from connector work-item events (`ONTOLOGY.md` §4.3), and those are **not** derivable from the Object row's PRIMARY columns. The record *can* reconstruct them — the journal is now in the record (`journal_event`) — but the per-event payload keys are the projection's contract, and guessing one returns `NULL` in silence. The view therefore claims exactly the two states the record can reconstruct (`live`/`superseded`), and the extension is **named in the view's comment** rather than silently dropped.

**5.5 `confidence` is a documented doc-vs-code disagreement, and this schema does not resolve it.** `ONTOLOGY.md` §2 says confidence *"is derived at read time … never stored independently"*, while `_upsert_point_props` persists it from the payload (`n.confidence = coalesce($cf, n.confidence)`) and it sits in `_POINT_HANDLED`, not `_POINT_DENY`. The record does **not** store it — that is the carve-out — which makes the record more correct than the live graph, and the live-path defect stays where W2 §2.9 filed it (`#4240`/`#5089`).

**5.6 The append-only journal must still permit a whole-tenant erasure.** An unconditional immutability trigger makes account deletion impossible, and `20261001000006_account_deletions.sql` exists. The journal is append-only as a **record of what happened**, not as a legal barrier, so the trigger refuses UPDATE always and refuses DELETE unless the session sets `app.record_journal_purge = 'on'` — the one path a real deletion request takes. A PostgREST table DELETE cannot set a session GUC, so the flag is set by the SECURITY DEFINER RPC `public.purge_org_record` (migration `20261012000005`, service_role-only EXECUTE), which sets it transaction-locally and deletes the org; `purge_org_control_plane` calls it for the org row. The pgTAP suite asserts **both** halves (refused without the flag, permitted through the RPC's cascade) and that `authenticated` cannot execute the RPC.

---

## 6. The two tests, and the mutation

### 6.1 `tests/test_record_schema_placement.py` — schema ↔ placement table

Reads the placement table, applies §2.7's resolutions, reads the `CREATE TABLE` blocks from the four migrations, and asserts **PARSE** in both directions: every PRIMARY property has a column, every DERIVED/VOLATILE property has none. It also pins the resolved totals (216/187/29/0), the carve-out names, the predicate vocabulary, the `(label, key)` FK shape, and that every table has RLS enabled with both policies.

**The mutation that makes it fail** (both directions verified on disk, not only in memory):

* add `confidence double precision,` to `public.point` → `DERIVED/VOLATILE AS AUTHORITY: point.confidence — Point.confidence is D (§17.5 carve-out)`;
* remove `content text,` from `public.point` → `MISSING COLUMN: point.content — Point.content is PRIMARY`.

The dotted BP edge props (`IMPL.msg_*` / `IMPL.back_msg_*`) are DERIVED too, so the checker expands an `Edges` row to its leaf name in BOTH classes — without that, `i_m_p_l.back_msg_beta` could never match a column and a derived edge value could be stored as authority undetected (#8179 review P2).

`test_mutation_adding_a_derived_column_fails` / `test_mutation_removing_a_primary_column_fails` perform those mutations **in memory** on every run, so the checker's own polarity is pinned even if nobody edits the SQL.

### 6.2 `supabase/tests/20261012000001_record_schema.sql` — the SQL half

Runs on PGlite. Asserts the nine tables exist and the procedural layer is **not** one of them; the RLS boundary (a cross-tenant read returns **nothing**, and `authenticated` cannot write); the composite FKs (a relation to a non-existent node is refused); the `node_key` contract CHECKs; that each carve-out CHECK **fires**; and that the journal refuses UPDATE and DELETE.

**Postgres availability: the SQL half IS verified.** `npm --prefix supabase/tests/pglite run validate` applies every migration on a fresh database and runs every suite — including this one — and passes. PGlite is WASM Postgres and is the repo's established no-Docker harness (`supabase/tests/pglite/validate.mjs`). What it does **not** verify: row-level behaviour against a live FalkorDB, and the projection (there is none yet — `#7969`).

---

## 7. What this document does not claim

* **It does not re-open §2.0.** Postgres-as-record is the owner's ruling; this is its first physical consequence.
* **It does not normalise the legacy ids** (§4).
* **It does not approve `#7879`'s scope.** Scope and Plan carry human gates.
* **It adds no migration-safety surface** — the owner's 2026-10-10 scope ruling excludes the whole class.
* **It changes no public surface.** No MCP tool and no public SDK method is added, removed or renamed; `tools/surface_manifest.py check` stays OK. The schema is a new data plane behind the control plane (`public.graphs` is the seam, not an extension point).
