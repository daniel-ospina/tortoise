---
title: "The cache projection — the record loaded to hot (FalkorDB), one way (#7971)"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-13
updated: 2026-10-13
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "issue #7971, epic #7879, interface B, cache projection, record read shape, known-hot set, Postgres record, FalkorDB cache"
---

# The cache projection — the record loaded to hot, one way

**Issue:** **#7971** (P0-blocker, lane `c6-durability`) — epic **#7879**.
**Ruling:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) —
**Postgres is the record; FalkorDB is a derived hot cache**, loaded from the record.
**Interfaces:** **B** (Postgres → FalkorDB, the declared hot set) and the
projection half of **C** (the write path). **Test-design consumers:** `#7967`
surfaces **S3**, **S9**.
**Inputs consumed, not re-derived:** the schema (`#7968`), the record projection
(`#7969`, `tortoise/record_projection.py`), the id contract + placement table
(`#7955`), and the completeness marker (`#7952`, `tortoise/residency.py`).
**Scope ruling:** owner, 2026-10-10 — **there are no existing customers.** No
dual-write window, no backfill, no cutover, no rollback-parity surface is
designed or claimed here.

---

## 0. Plan

### 0.1 Confirmed problem

`tortoise/projection/` **is** a replay of the journal, into FalkorDB. That makes
it the derived cache's fold **over history**, and there are two consequences:

1. **A rebuild reads history, not the record.** `FalkorProjection.rebuild_all`
   takes a `log_dir` and re-reads the JSONL journal. So *"Postgres is the record
   and the cache is loaded from it"* had **no execution path**: the ruling's
   second half had no carrier (`#7971`, `#7967` S3's own failure mode — *"a
   rebuild that can only read the journal rather than the record"*).
2. **The fold's output is not confined to the record.** `apply()` writes
   `content_hash`, `confidence` and the embedding it recomputes — fields the
   schema **refuses as authority** (the EP carve-out, `#7968`'s
   `chk_*_props_no_derived`). A cache built that way carries fields with **no
   PRIMARY claim in the record**, which is exactly what S3's contract clause
   forbids.

### 0.2 Alternatives considered, and why they lose

| approach | verdict |
|---|---|
| **A — `rebuild_all`'s source becomes `public.journal_event`; replay the same events through `apply()`** (the smallest diff; the test-design's machinery line describes it) | **Rejected.** It is a **second reading of history** — the record's own `journal_event` rows re-entered through the same fold — so the record's *state* tables become redundant for the cache and a bug in the state projection is invisible in the cache. Worse, it **fails S3's contract structurally**: `apply()` writes the recomputed `content_hash`/`confidence`/embedding, which have no PRIMARY claim. The contract would then have to be scoped away — i.e. the clause that makes this unit's verification non-vacuous would be the one dropped. |
| **B — a hand-written second declaration of the property set** | **Rejected**, same reason `#7969` rejected its alternative A: the second declaration is the one that goes stale. The property↔column pairing is read from the placement table (`#7955`), which `#7968`'s schema is already pinned to. |
| **C — project the record's **state**, output confined to the record's PRIMARY field set, one way; the declaration is derived from what was materialized** ✅ | **Chosen.** Nodes are `MERGE`d by the label's canonical key (`#7955` Interface E) and `SET` from the record's own columns; relations are `MERGE`d with `(label, key)` endpoints resolved from `node_identity`'s contract. Nothing on the path reads the cache. |
| **D — reuse `projection/entities.py`'s `_upsert_point_props`** | **Rejected.** It is event-shaped and open-set (`SET n += $props` over a journal payload), so it would carry the derived fields and the journal-only ones straight through — including the six graph-only classes `rebuild_all` currently rescues from the cache (`#548`/`#990`/`#2814`/`#4641`/`#4653`/`#5353`), which §0.4 addresses head-on. |

### 0.3 Chosen approach

`tortoise/cache_projection.py`, plus one **source seam** on `FalkorProjection`.
The fold's machinery that is reused: the guarded graph handle, the destructive
wipe (`_wipe_all_nodes`), the index creation (`_ensure_indexes`), and the
build-graph-name assertion. What is new is the **source**: record rows instead
of journal events.

1. **The record read shape.** `NODE_TABLES` (label → table), `KEY_PROPERTY`
   (label → the Interface E key property, **parsed from `public.node_identity`'s
   generated `key_property` CASE**), `PLACEMENT_COLUMNS` (table → `{column:
   property}`, **parsed from the placement table**), and `node_from_row()` /
   `relation_from_row()` which turn a record row into `(key, {property: value})`.
   The `props` tail keeps its verbatim property names; a column is mapped back
   through the placement table, so the graph's property namespace is preserved
   (`pointKind`, not `point_kind`).
2. **The materialization.** `project_records()` emits, per node,
   `MERGE (n:{label} {{{key}: $key}}) SET n += $props`, and per relation,
   `MATCH` both endpoints by `(label, key)` then `MERGE (a)-[r:{rel_type} {idx:
   $idx}]->(b) SET r += $props`. **It issues no read that decides what to
   project** — the only reads are the endpoint `MATCH`es, which resolve a
   *record* coordinate, never a cache fact.
3. **`rebuild_from_record()`** — assert the destructive token, wipe, project,
   then **declare the known-hot set**. Counts only.
4. **The declaration is derived from the projection.** `declare_coverage()`
   writes `complete` **iff every record key it read was materialized**, and
   otherwise `partial` **enumerating the keys it did materialize**. It reuses
   `residency.declare_residency`, so the marker keeps ONE declaration format
   (`#7952`) — this unit supplies the authoritative writer, it does not
   re-implement the classifier.
5. **The seam.** `FalkorProjection.rebuild_from_record(view, ...)` is the
   record-sourced sibling of `rebuild_all(log_dir, ...)`: same wipe, same
   guards, different source. `rebuild_all` is unchanged.

### 0.4 The hot/not-hot split, stated exactly

* The hot set is **what the projection materialized, and nothing else.** The
  declaration is written *after* the writes and is a function of the record's
  keys, never of the cache's contents.
* **A key the record holds but the projection did not materialize is NOT
  declared hot.** With that declaration in force, a read for that key is
  `not-hot` (`residency.mark_residency`), and `apply_residency` turns an
  `empty` status into **`degraded`**, never `empty` — so a cold region cannot
  read as *"no such node"*.
* **A cache-only node is a defect, not a shortcut.** §0.5 says why they exist
  today and what this unit does about them.

### 0.5 What is deliberately NOT carried — and it is a report, not a silent skip

`rebuild_all` **reads the cache before wiping it** to preserve six graph-only
classes that no journal record carries (`_SNAPSHOT_SECTIONS`): `#548` synthetic
Points, `#990` `:Batch` markers, `#2814` config, `#4641` onboarding state,
`#4653` `:GraphEventMeta`, and `#5353` `:TeamMeta`. The issue's own words forbid exactly that on this
path — *"the cache is a destination, never an input. Nothing in this path may
read the cache to decide what to project"* — and the placement ruling says what
they are: *"a field with no PRIMARY claim in the record that exists only here is
a defect, not a shortcut."*

So **this projection does not carry them.** The consequence is named, not
hidden and not silent: a record-sourced rebuild **drops** them, and the six
classes are listed here (and in the module docstring). It is deliberately **not
counted at runtime** — a count would need the same pre-wipe cache read this
path forbids, and `rebuild_from_record`'s report describes the RECORD's coverage
(`nodes_written`/`record_nodes`/`coverage`/`declared_keys`), not a census of what
the wipe destroyed. The classes are the next unit's work (journal/record
completeness, the `#4240`/`#5089` root). Shipping a `rebuild_all`-shape snapshot
on this path would import a **cache read** into the one path the ruling requires
to have none.

### 0.6 Verification

* `tests/test_cache_projection.py` — hermetic, no DB:
  * **the read shape is total and lossless**: for every PRIMARY property in the
    placement table, `node_from_row` yields it **under its own name**; for every
    DERIVED/VOLATILE name, it yields nothing — the S3 contract, executable.
  * **the keys are the contract's**, read from `node_identity`'s DDL rather
    than restated.
  * **one-way by construction**: the materializer's statement stream contains
    no read that could decide what to project — every statement is a `MERGE`,
    asserted over the plan's cypher (the endpoint `MATCH` inside a relation
    upsert is the one read, and it resolves a RECORD coordinate).
  * **rebuild == record**: re-projecting the plan writes exactly the record's
    key set and field set — **no extra field, no extra node** — asserted over
    the plan's `Statement.params['props']`, not over the input model.
  * **idempotent**: projecting the same view twice yields the same statement
    multiset.
  * **not-hot, never empty**: an unmaterialized record key is `not-hot` and an
    `empty` read over it becomes **`degraded`**, never `empty`; the declared
    identity is `(label, key)`, so two nodes that share a key string
    (`Object 'acme'` / `Subject 'acme'`) do not collapse into one hot key; and a
    projection that declared coverage it did not write fails the test.
* `tests/test_cache_projection_live.py` — the S9 shape, on a real FalkorDB
  (skipped when no engine is reachable, so it never reds a DB-free lane):
  project → **destroy the cache** → re-project from the record alone → assert
  equality of nodes, keys and fields.
* `python tools/ci_selection.py --integrity` — the new tests are registered
  where their inputs select.
* `uv run python tools/surface_manifest.py check` and `tools/surface-guard.py`
  — **no MCP tool and no public `TortoiseSDK` method moved.** `FalkorProjection`
  is internal; the new module is behind it.

### 0.7 Out of scope (explicitly)

* **The write chokepoint** (Interface C, `#7951`) — this module is the *third*
  step of that path and does not route live writes.
* **The read path** (`#7970`, deferred by §2.0) and the **residency router**
  (`#7953`).
* **The six graph-only classes** (§0.5) — dropped (and named), not carried.
* **Normalising the ~9,700 legacy ids** (`#7955` §1.4, W1b).
* **No migration-safety surface** (owner scope ruling, 2026-10-10).
* **No public surface change.**
