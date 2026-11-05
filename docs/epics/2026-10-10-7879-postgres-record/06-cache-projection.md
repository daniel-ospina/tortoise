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
neither lands in the report as a census of the six dropped classes; they are
pre-flight facts about the TARGET, exactly like the wipe guard itself. The classes are the next unit's work (journal/record
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
    the plan's `Statement.params['props']`, not over the input model. ⚠️ **One
    declared exception (#9634):** `cache_projection.FOLD_ON_CREATE_DEFAULTS`
    applies the FOLD's `ON CREATE` constants (`subjectKind`/`objectKind` →
    `'other'`, `Object.title` → `''`) where the record row is silent, so a
    record-sourced node equals the node the live fold would build. Because the
    assertion reads the VIEW, it cannot see this class — the drift pin in
    `tests/test_cache_projection.py` is the bound, and the members the record
    cannot decide are #9710.
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

## §9.1/D1 at the projection door — an EPISODIC Source carries no turn prose (#10853)

**A declared narrowing** of the field set — the only one — in the opposite
direction from the three widening exceptions the module names
(`FOLD_ON_CREATE_DEFAULTS`, `DERIVED_FROM_RECORD`, `EVENT_STREAM_FOLDS`):
`public.source.summary` and `public.source._search_text` are §9.1/D1 turn prose
when the Source is a captured session, so
`cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS` declines to project them onto
the node. The record keeps the values (the row is not mutated), and `topics` —
the capture's word lexicon — still projects.

⛔ **No in-tree verb reads the declined pair back** (#10867 review, P2).
`record_serve.read_node` returns *this* projector's map — the narrowed one — and
`record-text` reads `public.session_narrative`, a different table holding the
session-level summary, never `public.source.summary`. The only read is a direct
`SELECT summary, _search_text FROM public.source WHERE url = …`, and there is no
record-side text INDEX. Stated in the negative because the opposite was written
here first and it is the load-bearing half of "safe to decline".

Why it lives here rather than only in the capture: every record-sourced read and
rebuild goes through `cache_projection.node_from_row` — `tortoise record-restore`
and `rebuild_from_record` materialise a node with it, and
`record_serve.read_node` answers with the same map because its contract is
that a served node and a rebuilt node cannot drift. So a row written by a build
that still placed the key either re-creates the prose **whatever build is reading
it** or makes the served answer disagree with the rebuild — the one-way
divergence #10853 measured. Sweeping the existing rows instead would decay: at
this head the capture still writes the value, so the next capture of the same
session writes it straight back.

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

`props` is written to the node **verbatim**, so it can carry identity, not only
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
  identity columns it can collide with (`source_kind`, `title`, `url` — indices
  27, 33, 39, 42 of 45), so every agreement leg ran on the one ordering real rows
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

### Three costs, named rather than implied

* the **journal fold** still projects the value until #10000's capture-side change
  lands, so the two rebuild paths disagree on that one column until then — in the
  ruling's direction, and it does **not** converge on its own (see the obligation
  below);
* graph-side FTS over a session Source's own utterances goes with the prose. The
  value survives in the record (retrievable by a column read; there is no
  record-side text index), and #10000 replaces the capture's summary with a topic
  lexicon;
* `python -m tortoise record-reconcile` builds its RECORD side through the same
  projector, so its truth side omits what the cache holds. **The population is not
  "legacy"**: at this head the capture still writes and journals the pair, so
  every session this build captures would report `field_mismatches` forever — the
  "signal indistinguishable from noise" the reconciler's own docstring grades P1.
  So `scan_cache_view` marks the pair CACHE-OWNED for an episodic Source
  (`cache_projection.episodic_source_prose_properties()`) and the run counts it
  under its own `graph_only` bucket: visible, and not a divergence against a
  record that is correct. The graph side is repaired by a record-sourced
  re-projection (`record-restore` / `rebuild_from_record`, which re-materialise
  through this projector) — **for a node the projection CREATES**. The write is
  additive (`MERGE … SET n += $props`, `cache_projection.node_statement`), so a
  node that ALREADY carries the pair keeps it across a re-projection; the
  narrowing stops the pair being written, it does not clear an existing one.
  Both verbs are also **test-graph only**: `rebuild_from_record` runs
  `_assert_test_graph`, which refuses every production graph name
  (`org_<org_id>`), so neither is a production repair path at all. Clearing a
  populated cache's stale pair needs an explicit property clear, and the sweep is
  #10000's capture change.

### Obligation: `#10852` DELETES this rule, it does not converge with it

`#10852` replaces the capture's `summary` with a TOPIC LEXICON descriptor and
derives `_searchText` from `topics` — §4.4 metadata, not §9.1/D1 prose, which is
what `docs/architecture/STORAGE-ARCHITECTURE.md:527` rules (*"`summary`/`title`
stay on the node"*). The narrowing above is keyed on the ROW's IDENTITY, so it
cannot tell that provenance from the prose: it would decline both, on the
record→cache door only (the live journal fold keeps them), and the reconciler
subtraction would keep that out of the mismatch count. So this rule is correct
for a pre-fix row and **wrong** for a post-`#10852` one, and it must not outlive
it — `#10852` deletes `_EPISODIC_SOURCE_PROSE_COLUMNS`, both guard doors in
`_props_from_row`, `episodic_source_prose_properties`,
**`episodic_source_declined_properties`**, `is_episodic_source_node`, the
`scan_cache_view` subtraction, and **`record_serve.parity_difference`'s decline**,
and adds a leg that a lexicon `summary` + a topics-derived `_searchText` SURVIVE
the door with the rebuilt session Source still Source-FTS-indexed. Recorded as a
follow-up obligation on #10853.

**SEVEN touch points, not five — and two of them are callers rather than
enumerations** (#10867 review, ISSUE 2). An obligation that named only the
projection's own names was a trap in both directions:

* `episodic_source_declined_properties` (`cache_projection.py:1567`) is a
  **caller** of `episodic_source_prose_properties`, so deleting the latter alone
  leaves `NameError` on the `props` tail for every episodic row;
* `record_serve.parity_difference` (`record_serve.py:1137-1151`) imports **both**
  `episodic_source_declined_properties` and `is_episodic_source_node` — the
  tree's SECOND record↔cache comparison instrument. A partial deletion is an
  `ImportError` on every `Source` comparison, loud today only because three legs
  the obligation did not list exist; and the other direction is worse — if
  `is_episodic_source_node` survives, `parity_difference` keeps explaining the
  pair away for every session `Source` after `#10852`, so a REAL projection gap
  on a lexicon `summary`/`_searchText` reddens as "explained". That is the
  "declared asymmetry and a real gap as the same signal" class this PR grades
  P1, and it is precisely how a rule outlives its own correctness.

So the check is not "were the names in this list deleted" but **"does any
reference survive"**: after the deletion, re-run
`grep -rn 'episodic_source_prose_properties\|episodic_source_declined_properties\|_EPISODIC_SOURCE_PROSE_COLUMNS\|is_episodic_source_node' tortoise/ tests/`
and expect **no** hits outside the new lexicon-SURVIVES legs (#10867 review
ISSUE 2, from the external pass: removal guides converge on *enumerate every
reference — including test, documentation and CI callers — then re-run the
search to prove zero*, because a hand-kept list is what goes stale).

### The pin

`tests/test_10853_session_source_prose.py`: the drop; the narrowness of the drop;
**the DROP SET itself** (every `public.source` column non-NULL, and
`set(projected_properties("source")) - set(node.props)` is EXACTLY
`{"summary", "_searchText"}` — the leg that reds when the set is WIDENED, which
nothing else did until #10867 review round 9: adding `title` left every leg and
the whole 443-test neighbourhood green while the door stripped a record-held
property);
the reconciler counting the declined pair as `graph_only` (not divergence) while a
DOCUMENT with a differing summary is still counted field by field;
**`tests/test_record_serve.py` — the SECOND instrument** (the served-read parity
check, `record_serve.parity_difference`; it is in the `#10852` obligation above,
so it is pinned here rather than only in the epic prose):
`test_parity_explains_the_episodic_source_decline_instead_of_reddening` (the
declined pair is EXPLAINED, and the explained set is drawn from the projection's
own declaration rather than a list the test curates — so a name added to the
decline needs no test edit), `test_parity_reddens_on_the_same_names_for_a_document_source`
(the negative direction: a `corpus://` document of kind `agentSession` that
carries a differing `summary`/`_searchText` still REDDENS, i.e. the decline is
scoped to the identity arms and cannot explain away a real gap), and
`test_parity_explains_only_the_declined_names_on_a_session_source` (scoped by
NAME too: the identity opens the decline, it does not blanket the comparison);
ONE LEG PER ARM; the FOUR negative legs (a plain-document baseline, then one per
term that must not be a signal: a transcript document carrying a `session_id`, a
`corpus://` permalink stamped `agentSession`, a document stamped `is_episodic`);
the row/node predicate-agreement legs, including the tail-carried identity, and
TWO legs for the ORDER the row arrives in (`_row_in_select_star_order`: the tail
is the last column in the DDL, so a real row iterates it last — the ordering in
which the tail used to win the node map while the predicate read the column, the
collision review #10867 raised as ISSUE 1), plus the falsy edge that proves the
rule is ONE rule (`None` is the only silence — an empty `url` column is a value,
and the identity view no longer fills it from the tail); the
`props`-tail legs (prose AND the `sourceKind` identity, plus the negative that a
tail-carried kind alone does not strip a `corpus://` document); and the record row
that must not be mutated. Mutation-checked, one at a time: each identity arm, EACH
OF THE TWO DOORS (the column loop and the `props` tail — the tail filter undoes
only the keys the tail added, so neither door subsumes the other), the tail read in
the predicate (column-only reds **3** legs — the two identity-view legs and the
`tail-only url decides the arm` leg that `916f32741` added), WIDENING THE DROP SET (`+"title"` reds the
drop-set leg and only it), the column-wins seeding (writing the tail last again
reds the `SELECT *`-order leg and the order-independence leg, and nothing else),
the label gate,
the call-time resolution of the placement spellings, and each of the three flags
re-added — every one reds at least the leg that names it, plus the row/node
agreement shape that exists to catch exactly that drift.

The parity half is mutation-checked the same way, in `record_serve.py`'s
decline — measured at `a7f5b6a58`, one mutation at a time, `-k parity`:
DROPPING the explanation (`declined = frozenset()`) reds **2**
(`test_parity_explains_the_episodic_source_decline_instead_of_reddening`,
`test_parity_explains_only_the_declined_names_on_a_session_source`), and
DROPPING THE IDENTITY GATE (explain for every `Source`) reds **1**
(`test_parity_reddens_on_the_same_names_for_a_document_source`) — the direction
that matters, because it is the one that would silence a real document gap.
