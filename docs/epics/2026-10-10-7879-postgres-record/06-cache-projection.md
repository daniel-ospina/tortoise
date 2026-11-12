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
| **D — reuse `projection/entities.py`'s `_upsert_point_props`** | **Rejected.** It is event-shaped and open-set (`SET n += $props` over a journal payload), so it would carry the derived fields and the journal-only ones straight through — including the eight graph-only classes `rebuild_all` currently rescues from the cache (`#548`/`#990`/`#3947`/`#2814`/`#4641`/`#4653`/`#5353`/`#9752`), which §0.4 addresses head-on. |

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

`rebuild_all` **reads the cache before wiping it** to preserve eight graph-only
classes that no journal record carries (`_SNAPSHOT_SECTIONS`): `#548` synthetic
Points, `#990` `:Batch` markers, `#3947` `:Session` containers and their
`:CONTAINS` edges, `#2814` config, `#4641` onboarding state,
`#4653` `:GraphEventMeta`, `#5353` `:TeamMeta`, and `#9752` `:PackSelection`
(the pack-set marker — one node per graph whose PRESENCE says the set was
deliberately configured, so dropping it re-admits an admin-removed pack and
resets the `revision` a client holds). The issue's own words forbid exactly that on this
path — *"the cache is a destination, never an input. Nothing in this path may
read the cache to decide what to project"* — and the placement ruling says what
they are: *"a field with no PRIMARY claim in the record that exists only here is
a defect, not a shortcut."*

So **this projection does not carry them.** The consequence is named, not
hidden and not silent: a record-sourced rebuild **drops** them, and the eight
classes are listed here (and in the module docstring). It is deliberately **not
counted at runtime** — a count would need the same pre-wipe cache read this
path forbids, and `rebuild_from_record`'s report describes the RECORD's
coverage (`record_nodes`/`record_relations`/`coverage`/`declared_keys`), not a
census of what the wipe destroyed. ⚠️ Since #9556 the report ALSO carries
`nodes_written`/`relations_written`, which are **measured off the cache AFTER the
write** (so a caller can tell a landed restore from a wipe that put nothing
back). That is not the read §0.5 forbids and it reverses nothing here: it runs
after the projection has finished and it decides nothing about what is
projected — it only refuses to report a plan's length as a result. ⚠️ The same
distinction covers the two §9 reporting reads that DO happen BEFORE the wipe —
`record_restore._target_nodes_before` (how many restorable nodes the target held,
so a mistyped `--cache-graph` is visible) and the projection's own L2 name
guard. Neither feeds the projection, neither decides what is projected, and
neither lands in the report as a census of the eight dropped classes; they are
pre-flight facts about the TARGET, exactly like the wipe guard itself. The classes are the next unit's work (journal/record
completeness, the `#4240`/`#5089` root). Shipping a `rebuild_all`-shape snapshot
on this path would import a **cache read** into the one path the ruling requires
to have none.

### 0.5b A vector is not carried onto a label no query path serves (`#11747`)

The vector clause is the one place this projection writes a property with a
STORAGE FORM rather than a value (`n.embedding = vecf32($embedding)`), and it was
guarded by nothing: whatever label the record row carried got its vector. But
the cache is loaded to be READ, and `run_vector_query` can only read the labels
`tortoise.security.VALID_ENTITY_TYPES` translates to — so a vector on any other
record label is paid for and unreadable, which is `#5090` DoD row 2's whole
subject.

`:Session` was the one such label, and the door was the RECORD's:
`public.session` is the only record table whose `chk_*_props_no_derived` refuses
NOTHING (`NOT (props ?| ARRAY[]::text[])`, an empty array) and it has no
`embedding` column, so a vector could arrive through the row's open-set `props`
tail. It is now refused at the writer (`cache_projection.UNSERVED_VECTOR_LABELS`,
derived as `NODE_TABLES` minus the served set — never a list) and the refusal is
**reported**, because this projection does not drop a name it cannot name.
⛔ Not an index decision: which
labels get a VECTOR INDEX is §14.3 **V3** (`#11901`), and nothing here creates
one. ⛔ Nor a narrowing of the 2026-11-10 ruling: `:Object` and `:Subject` are
served, keep their embeddings, and are untouched.

**Where the drop is REPORTED — on every surface that must not read as clean**
(`#11747`'s review, finding 1: `Statement.dropped_vectors` only, which no
production reader touched):

* the STATEMENT (`Statement.dropped_vectors`) and the VIEW
  (`RecordNode.dropped_vectors`, so a record read cannot advertise a vector the
  cache refuses);
* the PLAN — `ProjectionPlan.dropped_vectors()`, read off the statements, so a
  directly-built `RecordNode` is counted too;
* the RUN — `rebuild_from_record` carries the plan's drop beside its write
  counts as TWO report keys of different shapes: `dropped_vectors`, an `int`
  TOTAL, and `dropped_vector_labels`, the sorted label list (the projection
  returns them at `tortoise/projection/__init__.py`'s report dict, and
  `record_restore.RestoreOutcome` types them `int` / `tuple[str, …]`). It also
  logs a warning **after the write**, and `RestoreOutcome` prints the pair on
  the operator's line and in `as_dict()`. A rebuild that DELETES vectors a
  previous cache held must not read the same as one that lost nothing, and no
  re-run can bring them back: the record row is the only carrier and it is what
  the drop was applied to. ⚠️ The log must describe a run that RAN — it sits
  BELOW the destructive/L2 guards, because a refused rebuild that logged a drop
  over an untouched cache is the same false signal in the other direction
  (`#11747` review, finding 2); and
* ⚠️ **`ProjectionReport.dropped_vectors` / `dropped_vector_labels` are
  CALLER-FILLABLE, and no code path constructs a `ProjectionReport` today**
  (`#11747` review, finding 3: this section used to claim the plan *feeds*
  them, and nothing does). The dataclass is a rendering surface a caller may
  fill; the operator-facing pin is the RUN's report →
  `record_restore.RestoreOutcome`, which renders and serialises the drop under
  its own tests. Stated as such so a later reader does not mistake an
  unpopulated field for a wired one.

⚠️ **And it is DECLARED to the parity rule.** The drop happens in the row→property
map, so the record ANSWER no longer carries the vector and a LEGACY cache node
that still holds one is an asymmetry `record_serve.parity_difference` must
explain (the `#11747` review's one design decision, taken here in the open):
`cache_projection.unserved_vector_declined_properties` is its fifth enumerated
explanation, in both directions, so such a pair reads CACHE-HELD rather than as a
projection gap whose only sanctioned repair is a re-projection that can no longer
add the field. The alternative — moving the drop off the read door — was
rejected: it would restore the "live ≠ rebuild" divergence the drop removes.

### 0.6 Verification

* `tests/test_cache_projection.py` — hermetic, no DB:
  * **the read shape is total and lossless**: for every PRIMARY property in the
    placement table, `node_from_row` yields it **under its own name**; for every
    DERIVED/VOLATILE name, it yields nothing — the S3 contract, executable.
    ⚠️ **One declared exception, and it is a THIRD class rather than a gap in
    the rule (`#10394`):** a column can be PRIMARY in the record — authority the
    record must store — and **not projectable** into the cache, because the
    target store is where §9.1/D1 says the value must not live at all.
    `cache_projection.CACHE_FORBIDDEN_COLUMNS` names them (`source.story_arc`,
    `event.story_arc`: the S1 narrative, whose home for readers is
    `public.session_narrative`). The two tests carry the gate rather than a
    restatement: the set of PRIMARY rows that do not project must **equal** the
    declared set, so any OTHER column that stops projecting reds.
  * **the keys are the contract's**, read from `node_identity`'s DDL rather
    than restated.
  * **one-way by construction**: the materializer's statement stream contains
    no read that could decide what to project — every statement is a `MERGE`,
    asserted over the plan's cypher (the endpoint `MATCH` inside a relation
    upsert is the one read, and it resolves a RECORD coordinate).
  * **rebuild == record VIEW**: re-projecting the plan writes exactly the record
    **VIEW's** key set and field set — **no extra node**, and no field the view
    does not carry — asserted over the plan's `Statement.params['props']`, not
    over the input model. ⛔ "the VIEW's", not "the record's COLUMNS": the one
    widening name is `status` (classes (2)/(3)), which has NO column — and reading
    "no extra field" against the column names would have an implementer DELETE it,
    leaving a superseded Object that answers LIVE (#9974). ⚠️ Class (1) must not
    be read as widening: `objectKind`/`subjectKind`/`title` ARE projected columns
    (`object_kind`, `subject_kind`, `title`), the fold only supplies a VALUE where
    the row is silent — the field-set/VALUE conflation §17.2 exists to stop. Note
    `_props_from_row` SKIPS a `None` column, so a sparse row's plan is a strict
    SUBSET of the projected properties, and a column-name comparison reports a
    projected name as "extra" purely on spelling (`objectKind` vs `object_kind`). ⚠️ The
    assertion reads the VIEW, so it cannot see any class that is materialised in
    the VIEW-BUILDING path; the bound on those is a drift pin, not this
    assertion. ⛔ **The census is ONE, stated in full at
    `STORAGE-ARCHITECTURE.md` §17.2 and in `cache_projection`'s module docstring
    point 3; this section only names what this assertion is blind to.** The five
    declared classes, by DIRECTION: ONE **value-filling**
    (`cache_projection.FOLD_ON_CREATE_DEFAULTS`, #9634 — `subjectKind`/`objectKind`
    → `'other'`, `Object.title` → `''`) supplies a constant where the record row
    is SILENT; those columns are `P` in the placement table, so it adds a VALUE
    for a field the record HAS and does **not** widen the field set, and reading
    it as a widening is the field-set/value conflation §17.2 exists to avoid;
    TWO **widening** (`cache_projection.DERIVED_FROM_RECORD`, #9974, and
    `cache_projection.EVENT_STREAM_FOLDS`, #10198 — both mint `status`, which has
    no record column); TWO **narrowing**
    (`cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS`, #10853, per ROW, and
    `cache_projection.CACHE_FORBIDDEN_COLUMNS`, #10394, per TABLE). The members
    the record cannot decide are #9710.
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
* **The eight graph-only classes** (§0.5) — dropped (and named), not carried.
* **Normalising the ~9,700 legacy ids** (`#7955` §1.4, W1b).
* **No migration-safety surface** (owner scope ruling, 2026-10-10).
* **No public surface change.**

## §9.1/D1 at the projection door — an EPISODIC Source carries no turn prose (#10853), except the space-less-run residual named below

**A declared narrowing** of the field set — one of TWO — in the opposite
direction from the TWO widening exceptions the module declares
(`DERIVED_FROM_RECORD`, `EVENT_STREAM_FOLDS`). ⛔ `FOLD_ON_CREATE_DEFAULTS` is
NOT a third widening: it fills a VALUE into a column the record already has
(`subject_kind` on `Subject`, `object_kind`/`title` on `Object` — all three in
their own table's `projected_columns`; the fold's KEYS are PROPERTY names like
`subjectKind` while the columns are the row's snake_case spellings — the two
namespaces §17.2 keeps apart), so
the plan's FIELD SET is unchanged by it. §17.2 of `STORAGE-ARCHITECTURE.md` and
`cache_projection`'s module-docstring point 3 state the full census BY DIRECTION
— one value-filling, two widening, two narrowing — and this line previously
contradicted it (#10394 review). The other narrowing is
`cache_projection.CACHE_FORBIDDEN_COLUMNS` (`#10394`), which is **per TABLE
rather than per row** and is stated at the end of this section
(`:source`/`:event` `story_arc`):
`public.source.summary` and `public.source._search_text` are §9.1/D1 turn prose
when the Source is a captured session **and the row holds the pre-#10000
derivation**, so `cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS` declines to
project them onto the node. The record keeps the values (the row is not mutated,
and they are retrievable by a column read — `record_serve.read_node`,
`record-text`; there is no record-side text INDEX), and `topics` — the capture's
word lexicon — still projects.

⭐ **The decision is per ROW, from the row's own values (#10965).** #10000
replaced the derivation: the capture now writes `summary` = a display DESCRIPTOR
over the session's topic lexicon and `_searchText` = `", ".join(topics)`, both
from the SAME list in one statement. So on a post-#10000 row `_search_text` is not
a CHOSEN utterance — it is the token lexicon (a §4.6 metadata value, not the S1 narrative, per `STORAGE-ARCHITECTURE.md` §9.1; in a word-delimited script; a space-less CJK run can reach it whole, i.e. the field can BE the turn's prose verbatim, while a Thai run fragments at its combining marks, the residual named in `sdk._session_source_metadata`, #10852) — and it is the only field the Source FTS index is
declared on, which makes dropping it an UN-INDEXING of every restored session on
the recovery door rather than a §9.1/D1 measurement. `_search_text` is therefore
projected when the row proves the derivation, and declined otherwise — and the
proof is TWO terms, never a shape test and never the join alone: the stored text
IS `", ".join(topics)`, **and** `summary` is that same join behind
`SESSION_SOURCE_SUMMARY_PREFIX`. ⛔ The join by itself is not enough, and this is
measured rather than reasoned: a pre-#10000 capture wrote `summary` = the
transcript's first substantive utterance and derived `_searchText` FROM it, so on
a transcript whose utterance is itself a comma-space list (`"milk, eggs, bread,
cheese, apples, oranges"`) the legacy row matches the join word for word, and a
join-only test re-indexed its prose with nothing red (#10852 review, P2). The
display column is the WITNESS that the derivation is the post-#10000 one; it is
READ and never projected. Every row that cannot prove both terms — including one
whose `summary` is absent, empty, or unprefixed — is declined (fail-closed: a
session that stays un-indexed on this door until one of the clearing routes below
re-derives it — visible and named here, against prose on a node, which nothing
reds). The prefix has ONE home,
`tortoise/session_source_display.py` (a leaf module that imports nothing): the
projection may not import the capture to obtain it, and a second literal is how
two doors come to disagree about what a derived row looks like. `summary` itself
stays declined on every row: restoring it would put the whole descriptor on the
node for no retrieval gain, since the Source FTS index is declared on
`_searchText` — a stated residual, not an oversight.

Why it lives here rather than only in the capture: every record-sourced read and
rebuild goes through `cache_projection.node_from_row` — `tortoise record-restore`
and `rebuild_from_record` materialise a node with it, and
`record_serve.read_node` answers with the same map because its contract is
that a served node and a rebuilt node cannot drift. So a row written by a build
that still placed the sentence re-creates the prose **whatever build is reading
it** — the one-way divergence `#10853` measured.

### Which rows are EPISODIC — two identity arms, and three flags that are NOT signals

`_is_episodic_source_row` mirrors the repo's existing declaration of the same
question (`sdk.py`'s document-source exclusion, with
`session_source_migration._LEGACY_CANDIDATES` as the second arm's definition):

* `url LIKE 'session:%'` — the capture's URL shape, and the prefix the stub
  minter and `derive_session_source_url` both land on;
* `source_kind='agentSession'` **on a separator-free bare-filename url** — the
  pre-#4005 identity (`os.path.basename(ref.path)`, literally `session.md`).

⛔ Three plausible terms are **not** signals, because each fires on real
DOCUMENTS — this is measured, three times, by review on the change itself, and
each has a NEGATIVE leg in the suite:

| term | why it is not an identity |
|---|---|
| `session_id` | a legitimate DOCUMENT coordinate: `EventAPI.add_document` takes one, and `tortoise/ingest.py` passes a transcript's `sessionId` frontmatter into it |
| `source_kind='agentSession'` alone | `file_indexer` classifies a transcript FILE and writes that kind on a per-FILE `corpus://` permalink (`CLASSIFIER_TO_SOURCE_KIND`, pinned §6.2/I26) |
| `is_episodic=TRUE` | the capture commit stamps it on every EXTERNAL source of `payload.sources`, and `backfill_is_episodic.py` on every Source reachable from a session point |

`sdk.py` records the same lesson and its cost: keying on the flags *"silently
disabled this exclusion on the corpus #9914 targets"*. Here the cost is worse —
the prose guard would strip a document's own frontmatter summary and its
title-derived `_searchText`, silently un-indexing it in the cache.

#### The identity is read AS THE NODE WILL CARRY IT — the `props` tail included

`props` is written to the node with its property names **unmapped** (the tail is
seeded first and a column value overwrites the same coordinate; an episodic
Source's declined names are popped), so it can carry identity, not only
prose: a row whose `source_kind` column is NULL while the tail holds
`sourceKind='agentSession'` produces a node `is_episodic_source_node` calls a
captured session. A predicate reading the **columns alone** therefore answered
*"document"* for a node the reconciler classified as a session — R1 (*"from any
row shape"*) failing **open**, with nothing red: the column door never fired,
the prose landed. Nothing reddened — not because the reconciler filed it under
some other heading, but because it **cannot see** it: it builds its record side
through this same projector, so the re-projected record carries the prose too,
`keep` includes `record_props` (`record_reconcile.py:449-451`, `:540`), and the
pair compares **equal**. That is the same hazard the `props`-tail filter below
exists for, defeated from the other side (#10867 review).

So `_is_episodic_source_row` reads a **merged view** (columns + decoded tail,
`_row_identity_view`), which is what makes it agree with
`is_episodic_source_node` on every shape — the agreement legs pin it. Two
bounds, both deliberate:

* **a COLUMN value wins** the same coordinate, preserving the asymmetry above:
  an ambiguous row is read as a DOCUMENT and keeps its prose (the pre-existing
  defect) rather than stripping a document's metadata. ⛔ **That rule is the NODE
  MAP's rule too**, and it has to be the *same* rule: `_props_from_row` seeds
  the map from the tail and lets the column loop overwrite it. Resolving the
  collision only in the identity read left the node map's winner to the row's
  MAPPING ORDER, and the DDL declares `props` **last**, so on a real `SELECT *`
  row the TAIL won `sourceKind` while the predicate read the COLUMN — the two
  functions disagreed and R1 failed open exactly on the conflicting shape
  (#10867 review ISSUE 1). `_source_row`'s `sorted()` keys put `props` BEFORE the
  identity columns it can collide with (`props`, `source_kind`, `title`, `url` —
  indices 27, 33, 39, 42 of 45), so every agreement leg ran on the one ordering real rows
  do not have; the pin now
  has a legs pair that builds the row in `SELECT *` order and one that builds the
  same record both ways and requires the SAME node map;
* **only the KIND can travel *for a row that can become a node***, because `url` is the
  label's CONTRACT KEY — `node_from_row` refuses a NULL one (`UnprojectableRecord`, and
  the schema's own CHECK `chk_source_node_key_is_contract_key` — `url IS NOT NULL AND
  node_key = url`), so the tail's `url` is **unreachable**, never **absent**. ⚠️ The
  bound is REACHABILITY, not the predicate: `_row_identity_view` does fill a NULL `url`
  from the tail, so on the predicate alone `url=None` + `props={"url": "session:abc"}`
  **is** episodic and the tail decides the arm. Pinned by
  `test_a_tail_only_url_DOES_decide_the_arm_but_never_reaches_a_node`
  (`tests/test_10853_session_source_prose.py`) so a later audit cannot read this bullet
  as "the predicate cannot answer that way" and skip the shape.

### Costs, named rather than implied

* the **journal fold** projects the pair the capture wrote and the record fold now
  projects the same post-#10000 pair, so the two rebuild paths agree on
  `_searchText`; they disagree only on `summary` (declined on the record door);
* graph-side FTS survives on a post-#10000 row — the lexicon is restored — and
  goes, with the prose, on a legacy one, where there is no non-prose value to
  keep. A legacy session therefore stays un-indexed on the recovery door, and
  ⛔ **a RE-CAPTURE does NOT repair it** — this is the claim `#10852` measured
  as FALSE and retracted from the PR's other artifacts, so it is stated here in
  the corrected form: re-capturing an already-captured session id is the
  `#2104`/`#1727` no-op replay, so `_materialize_session_source` — whose only
  production caller sits behind the fresh-capture gate — **never runs** and the
  stale pair survives (pinned by
  `tests/test_10000_source_prose.py::test_a_re_capture_does_not_heal_a_pre_fix_node`).
  THREE routes DO clear it: (1) a retry of a prior FAILED capture — that arm
  re-runs the mint for the SAME node (`url = session:<id>`), so the capture's
  `SET`s overwrite the pair (route 1); (2)
  `session_source_prose_migration.reconcile_session_source_prose` (`#11070`,
  shipped via `#11152`), which repairs or CLEARS the PAIR IN PLACE from the
  record **without a wipe** — the only NON-destructive route, and it leaves the
  node's other properties untouched; and (3) a full `record-restore`, whose
  `_wipe_all_nodes` is unconditional and whole-graph. Two routes that read like
  repairs are NOT (`#10852` review, P3): a capture under a NEW `session_id`
  MERGEs a DIFFERENT node and leaves the old one alone, and an INCREMENTAL
  record-sourced re-projection is a **prevention** route, not a clearing one
  (see the next bullet);
* `python -m tortoise record-reconcile` builds its RECORD side through the same
  projector, so its truth side HOLDS `_searchText` for a post-#10000 row — and
  the cache-held partition keeps every name the record side holds, so a drifted
  `_searchText` is compared and reds. `summary`, and a legacy row's whole pair,
  are CACHE-HELD for an episodic Source (not "cache-owned": the record holds
  them, and §17.5's term is for the scalars it cannot)
  (`cache_projection.episodic_source_prose_properties()`) and counted under their
  own `graph_only` bucket: visible, and not a divergence against a record that is
  correct. The graph side of a legacy node is repaired by a record-sourced
  re-projection — **for a node the
  projection CREATES**: the INCREMENTAL write is additive (`MERGE … SET n += $props`), and
  `summary` is declined on this door, so a node that ALREADY carries the pair
  keeps it across an incremental projection — it produces a prose-free node
  only where none existed, which PREVENTS the defect rather than clearing it.
  ⛔ But `record-restore` / `rebuild_from_record` are NOT that path: they WIPE
  the graph first (`_wipe_all_nodes`, *unconditional and whole-graph*), so a
  FULL restore CLEARS an existing node's pair instead of preserving it —
  measured by `test_the_record_restore_writes_no_story_arc_on_any_node` arm (3).

### The pin

`tests/test_10853_session_source_prose.py`, the `#10853` arms: the drop; the
narrowness of the drop; the reconciler counting the declined pair as `graph_only`
(not divergence) while a DOCUMENT with a differing summary is still counted field
by field; ONE LEG PER ARM; the FOUR negative legs (a plain-document baseline,
then one per term that must not be a signal: a transcript document carrying a
`session_id`, a `corpus://` permalink stamped `agentSession`, a document stamped
`is_episodic`); the row/node predicate-agreement legs; the `props`-tail leg; and
the record row that must not be mutated. Mutation-checked, one at a time: each
identity arm, EACH OF THE TWO DOORS (the column loop and the `props` tail — the
tail filter undoes only the keys the tail added, so neither door subsumes the
other), the label gate, the call-time resolution of the placement spellings, and
each of the three flags re-added — every one reds at least the leg that names it,
plus the row/node agreement shape that exists to catch exactly that drift.

and, in the same file, the SIX `#10965`/`#10852` arms of the per-row decision:
`test_a_post_10000_session_row_keeps_the_field_its_fts_index_is_declared_on` (the
restored `_searchText` is the lexicon, and the index really is declared on it);
`test_the_drop_is_decided_by_the_rows_own_provenance_not_by_the_column_name`
(four near-miss shapes that only LOOK like the derivation are still dropped, plus
the legacy control); `test_a_list_shaped_legacy_utterance_is_not_mistaken_for_the_lexicon`
(the JOIN ALONE is not the derivation — a legacy utterance that is itself a
comma-space list satisfies it word for word, and only the display prefix tells the
two apart; it reds with the witness removed, while every other legacy leg stays
green); `test_the_witness_fails_closed_on_every_unprovable_summary` (absent,
empty, unprefixed, prefixed-plus-more → all declined, with the live derivation's
own row kept as the control); `test_the_display_summary_stays_declined_and_the_residual_is_named`
(the stated residual, pinned so it cannot become an accident); and
`test_the_reconciler_compares_the_restored_lexicon_on_a_post_10000_row` (a restored
`_searchText` that drifts from the record REDS instead of hiding in `graph_only`).

The `#10852` prose-retraction arms live in `tests/test_10000_source_prose.py`:
`test_the_write_site_does_not_claim_a_re_capture_heals_a_pre_fix_node` (the write
site's comment) and `test_a_re_capture_does_not_heal_a_pre_fix_node` (the stale
pair SURVIVES a replay); and this section is itself guarded, in both directions,
by `tests/test_10000_cache_projection_doc_pin.py` — the doc is the fourth
artifact to have carried the retracted premise, and its guard is DB-free and
registered in `tier1` because a docs-only edit runs `tier1` only.

### The other narrowing — a column can be authoritative and NOT projectable (`#10394`)

`cache_projection.CACHE_FORBIDDEN_COLUMNS` is the second declared narrowing, and the reason it is a
separate mechanism rather than another entry in the per-row set above is that it answers a different
question: **is there a column the RECORD must hold and the CACHE must never materialise?**

`story_arc` on `public.source` and `public.event` is that column. §9.1/D1 puts the S1 narrative in
Postgres and forbids it in the graph — and the record IS the Postgres half — so the placement rows keep
their **PRIMARY** class (the record does store it, and `#7968`'s two-way pin keeps reading that class)
and state **`not projected`** beside it with the reason. The narrative's one home for readers is
`public.session_narrative`, which `record_text.serve_from_record` reads and the projection never
consults; nothing a reader sees is lost by the deny.

The entry is keyed by TABLE because the statement is about the two stores, not about a row's provenance:
a row-derived test (the shape `_EPISODIC_SOURCE_PROSE_COLUMNS` uses) would answer the wrong question
here. The placement class came first and the projection could not express it, which is exactly how the
RECORD → CACHE restore re-materialised the prose on every rebuild and undid `#10000`'s write deletions,
non-durably. ⚠️ `#10017`'s purge is NOT the lane that was undone (`#11325`): its single destructive
statement deletes legacy episodic turn `:Point`s, never `:Source`/`:Event`, and names no `story_arc` — so
this class's targeted repair is a graph-side `REMOVE n.story_arc` that no instrument ships, or the full
restore's whole-graph wipe on either ANCHOR (⚠️ refused PRE-WIPE on a production SERVER graph name by
`#2944`'s L2 guard; ⚠️ BOTH are destructive and the WIPE cannot be row-scoped — `_wipe_all_nodes` is
`MATCH (n) DETACH DELETE n`, so a graph holding ANY row whose PROSE neither
`public.{source,event}.story_arc` nor `public.session_narrative.narrative` carries must NOT be
restored at all: copy that prose out first, `STORAGE-ARCHITECTURE.md` §17.2, `#11325`) — ⚠️ **and on a `:Point` a clear is available in place,**
**subject to the same per-row test** (clear only where the record still HOLDS the text for that session —
`public.session_narrative.narrative`; ⛔ NOT the point's own `props` tail, which the clear's OWN record
write consumes: `point` has no `story_arc` column, so the projection tails the key and the patch merges
right-hand (`props = props || '{"story_arc": null}'`), OVERWRITING the prose — a row whose only record
home is the tail loses the last copy the record's KEYED VIEW holds (the append-only `journal_event` payload
still carries the pre-clear prose, but using it needs a `rebuild_all` that can reach the graph); where the
narrative row does not carry it the node's
copy is the only one no shipped route can put back (the `rebuild_all` that would put it back is refused
PRE-WIPE on a production name):
`update_point(<id>, story_arc=None)` clears the property and keeps the node (measured; the `None` passes
`_refuse_unpaired_narrative` and `SET n += $props` removes a `None`-valued key — but JOURNAL-bound only: the
`PointRevised` fold has no `story_arc` clause and the creation fold's open-set passthrough drops a `None`, so a
`rebuild_all` over a journal whose creation carried the prose RESTORES it, `#10429` — ⚠️ on a graph a rebuild can
REACH: `rebuild_all` asks the SAME L2 name guard as the restore (`_assert_test_graph`, refused PRE-WIPE on every
production name), so on a production server graph the replay is refused and the clear IS durable there, and it is
defeated only on a `test_`/`tortoise_test` graph or an embedded `path=` DB — while the RECORD→CACHE
rebuild replays the stored null and keeps it clear; and on EITHER anchor `update_entity(…, story_arc=None)` is
refused as server-managed, the stored value untouched). **The seam is the fix, and a post-restore sweep is not an alternative**: a purge pass would
rent the RAM for the duration of the write and would have to run on every restore forever.

Three consumers align on the same constant rather than each re-deciding it, and all three are pinned:

* `cache_projection.projected_columns` / `_props_from_row` — the deny itself, on the COLUMN door and on
  the open-set `props` tail (copied verbatim, so a legacy row could otherwise use the back door);
* `record_reconcile` — a legacy cache node still carrying the prose partitions as **`graph_only`** (one
  property the projection declines to write), never as `field_mismatches` against a record that is
  correct;
* `record_serve.parity_difference` — the same pair is **`explained_missing`**, never an
  `unexplained_missing` projection gap. ⛔ Both of the latter two matter because each rule's only
  sanctioned repair for a gap is a re-projection, i.e. an instruction to re-open this door.

Pins: `tests/test_9555_narrative_cache_rebuild.py::test_the_record_restore_writes_no_story_arc_on_any_node`
(the seam, both anchors, the tail, and a real restore over a cache that still holds a legacy copy —
`sum(size(n.story_arc))` ends at 0), its sibling
`test_the_reconciler_reads_a_pre_purge_cache_copy_as_graph_only_not_divergence`, the parity arm in
`tests/test_record_serve.py`, and the third-category equality gate in `tests/test_cache_projection.py`
which makes any OTHER silent stop-projection red.
