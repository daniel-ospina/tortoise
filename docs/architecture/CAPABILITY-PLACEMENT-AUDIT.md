---
title: "Capability-first placement — the census: every stored shape, its readers, and whether a graph operation needs it (#13083)"
type: architecture
domain: platform
doc_status: draft
created: 2026-11-17
aboutSubjects: Tortoise memory graph, epistemic-team
aboutObjects: "#13083, #13082, #10017, #10070, #10000, #10429, #13094, #11758, #9025, #12458, #13024, STORAGE-ARCHITECTURE.md §9.1"
ownedBy: epistemic-team
related:
  - issue: 13082
    note: "The roadmap this is slice P0-a of. Phase 1/2 (migrating the Cypher readers) is explicitly NOT this document."
  - issue: 13083
    note: "The issue this answers. It moves what nothing reads; it does not rewrite what something does."
  - doc: docs/architecture/STORAGE-ARCHITECTURE.md
    note: "§9.1 (D1) is the precedent AND the mechanism; §3's per-write test is this document's vocabulary."
  - doc: docs/evidence/13083-capability-census/README.md
    note: "The measurement this document's byte columns come from, with its instrument and its limits."
---

# The capability census

**The rule this document applies** (owner ruling 2026-11-16, `#13082`): *anything no graph query reads
is Postgres-only by default, and staying in the graph is something a shape must justify.*

**The question asked of every shape, and the only question:**

> Does a **graph-capability operation** read it — variable-length path traversal · pattern matching over
> unknown adjacency · the fulltext index · EP state (a belief walk) · the vector index?
>
> **Not** "does a Cypher statement happen to mention it". A point lookup, a filtered listing, an
> aggregate and an ordered range scan are **SQL-shaped**; they are executed by FalkorDB today and they
> would execute anywhere. A row kept in RAM for a SQL-shaped reader is paying **584× the price of disk**
> (`#13082`: $73/GB-month resident vs $0.125/GB) for a latency a Postgres index also buys.

**Why the audit is the lever, and not a preference.** The field sizes graph memory for the **working
set** — the data and indexes the production query mix actually touches — not for whole-graph residency,
and the acceptance criterion is a **sustained cache-hit ratio** (Neo4j publishes `>98%`,
`dbms.page_cache.hit_ratio`, with heap and OS headroom reserved). A shape no graph operation reads is
not a *slow* member of the working set: it is **not in it at all**, and its only effect is to dilute the
ratio the budget is sized against. See §8 for what was taken from outside and what was checked in-repo.

**Method — the two censuses that worked, repeated.** `#12458` classified every `:GraphEvent` reader at
`file:line`; `#13024` proved the allocator unreachable with a call-site census. This document does the
same for **stored shapes**, and it states its population and its commands so a later head can re-derive
every number rather than trust it.

---

## 1. The population — enumerated from the WRITERS

The population is not the ontology's wish list; it is what a writer **actually puts on a node**. Two
writers reach the graph, and both were read:

1. the **live tenant write path** (`sdk.*` + `projection/entities.py`'s folds), and
2. the **record → cache projection** (`cache_projection.py`, `NODE_TABLES` → 6 labels), which is what a
   rebuild materialises.

The inventory below is the union. `d` = the label's record table in `cache_projection.NODE_TABLES`
(so a `record_only` row-tier is *mechanically expressible* for it); `—` = graph-only bookkeeping with **no
record table** (relevant in §5.2).

| # | shape (label) | written by | record table | node count in the measured corpus |
|---|---|---|---|---|
| 1 | `:Point` (claims, operators, turns) | `sdk.create_point`/`update_point`, `projection/entities._upsert_point_props`, `projection/propagation` | `public.point` | 66 (60 turn points + claims) |
| 2 | `:Source` (documents, transcripts) | `sdk._create_source`, `_upsert_source`, `_upsert_document`, `sdk._materialize_session_source` | `public.source` | 2 |
| 3 | `:Event` (lifecycle, AgentSession) | `sdk.create_event`, `_upsert_event`, `hosted_api` commit path | `public.event` | 3 |
| 4 | `:Session` | `sdk._write_session_and_turns`, `projection/entities.py:3871`, `hosted_api:16304` | `public.session` | 5 |
| 5 | `:Object` | `sdk.create_object`/`create_entity`, `_upsert_object` | `public.object` | 2 |
| 6 | `:Subject` | `sdk.create_subject`, `_upsert_subject` | `public.subject` | 2 |
| 7 | `:GraphEvent` (change feed) | `event_store.append_event` (`sdk._emit_event`) | **—** | 19 |
| 8 | `:GraphEventMeta` (seq allocator) | `event_store.next_seq` | — | 1 |
| 9 | `:EpMeta` (EP version) | `sdk._advance_ep_version` | — | 1 |
| 10 | `:Meta` (residency declaration) | `residency.py:284` | — | 0 (arm not exercised) |
| 11 | `:WhySnapshot` | `why_snapshot.py:484` | — | 0 (arm not exercised) |
| 12 | `:Tag` | `sdk` tag sync (`:21727`) | — | 0 (arm not exercised) |
| 13 | `:CommitRecord` (idempotency) | `commit_idempotency.py:45` | — | 0 (not exercised) |
| 14 | `:DeliveryReceipt` (webhook) | `webhook_delivery.py:487` | — | 0 (not exercised) |
| 15 | `:Batch`, `:PipelineStage`, `:PackInstall`, `:PackSelection` | `mining.py`, `pipeline.py`, `pack_state.py`, `hosted_api:32965` | — | 0 (not exercised) |

Rows 10–15 are declared **not exercised by the instrument** rather than counted as zero-cost — a shape
the corpus does not create is a shape this document has *not* measured, and saying so is the difference
between a census and a guess.

### 1.1 The instrument

`docs/evidence/13083-capability-census/probe_shape_bytes.py` — a fixed, synthetic corpus (deterministic
ids, texts and 384-dim vectors), seeded through the **production chokepoints** (`sdk._write_session_and_turns`,
`sdk._capture_session`, the `sdk.create_*` doors, and `cache_projection.record_plan` for the projection
arm). Bytes are the server's own `INFO memory → used_memory` (the instrument §9.1's own measurement uses,
`docs/evidence/10000-turns-bytes/README.md`) plus per-property `sum(size(n.<prop>))` summed in Python
because `size()` refuses a scalar and `toString()` refuses a list.

**Its limits are quoted, not discovered later** — `used_memory` includes block/index matrices, so a small
corpus over-states the marginal per-node cost; `sum(size())` **excludes** the per-property attribute-block
entry and is therefore a **lower bound**, a fact §5.1 turns into a number.

---

## 2. The reader census — every reader at `file:line`, by capability

Each row is a **reader**, not a mention. The capability column is what the reader *needs*; a reader that
needs none of the five is SQL-shaped and is marked **none**.

### 2.1 `:Point`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/projection/propagation.py:81` | `MATCH (n:Point {id:$id})-[r:IMPL]-(m:Point)` — EP propagation over belief edges | **EP state + adjacency** |
| `tortoise/projection/propagation.py:86` | `-[r:NAND]-(m:Point)` — the same walk over contradictions | **EP state + adjacency** |
| `tortoise/sdk.py:38073` | `WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL` — the EP baseline set | **EP state** |
| `tortoise/sdk.py:15659` | `MATCH (p:Point {id:$id})-[:extractedFrom]->` — provenance walk to the Source anchor | **traversal** |
| `tortoise/mcp_server.py:11158` / `:11161` | `MATCH (new:Point {id:$n})-[r]->(t)` / `(t)-[r]->(new:Point)` — edge discovery | **adjacency** |
| `tortoise/projection/edges.py:1888` | `(p:Point {id:$pid})-[:TAGGED]->(t:Tag)` | **traversal** |
| `tortoise/sdk.py:21978` | `(n:Point {id:$id})-[:TAGGED]->()` | **traversal** |
| `tortoise/search_engine.py:6297` | `(op)-[r2:IMPL\|hasPart]-(other:Point)` — the operator-expansion leg | **adjacency** |
| `tortoise/search_engine.py:6656` | `(n:Point)-[r:aboutSubject]->(:Subject)` — typed-degree prior | **adjacency** |
| `tortoise/search_engine.py:3039` | `CALL db.idx.fulltext.queryNodes('Point', $query)` | **fulltext index** |
| the vector leg (`vector_write` / search) | HNSW over `Point.embedding` | **vector index** |
| `tortoise/domain_validators.py:106`–`:244` | `MATCH (req:Point) WHERE req.pointKind IN …` — kind filters, no walk | **none** |

**Verdict: `graph`.** Six independent capability readers; a record-backed copy could not answer any of
them without re-implementing belief propagation, the fulltext index or HNSW in SQL (which is `#13082`
Phase 2 and its stop condition).

### 2.2 `:Source`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/sdk.py:38818` | `MATCH (s:Source)-[:references]->(o:Source)` — source-to-source provenance | **traversal** |
| `tortoise/sdk.py:52878` | `MATCH (s:Source {url:$url})-[:references]->(n)` | **traversal**. |
| `tortoise/sdk.py:15659` (target side) | `(p:Point)-[:extractedFrom]->(s:Source)` — every claim must reach its source (§9.4 ①) | **traversal** |
| `tortoise/search_engine.py:3011` | the fulltext index is on **`Source._searchText` for every Source** | **fulltext index** |

**Verdict: `graph`.** The link is load-bearing: §9.4 makes "every claim walks up to its Source" a
mechanism, not a citation nicety.

### 2.3 `:Event`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/goal_judgement.py:1119` | `MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator` | **adjacency** |
| `tortoise/search_engine.py:6658` | `(m:Event)-[r2:aboutSubject]->(:Subject)` | **adjacency** |
| `tortoise/assembly.py:2629` | `MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event)` | **adjacency** |

**Verdict: `graph`.**

### 2.4 `:Session`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/hosted_api.py:24356`, `:24453`, `:24572`, `:25847`, `:25865` | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point)` — session detail and its turn reads | **adjacency** |
| `tortoise/sdk.py:2917`, `:3499`, `:52258`, `:52381` | the same `CONTAINS` walk from the SDK door | **adjacency** |
| `tortoise/session_reinjection.py:561` | `MATCH (s:Session)-[:CONTAINS]->(seed)` — multi-session evidence pooling | **adjacency** |
| `tortoise/activation_scorecard.py:395` | `(s:Session)-[:CONTAINS]->(p:Point)` | **adjacency** |
| `tortoise/quota.py:1155` | `MATCH (s:Session) RETURN count(s)` | **none** |

**Verdict: `graph`.** ⚠️ Its **property families are another matter** — `capture_redactions`,
`capture_ok`, `capture_extractor`, `machine_id`, `model`, `entity_links_*`, `value_nodes_*` are telemetry
write-stamps with no Cypher reader of their own (they read back through whole-node reads, §6.2). That is
a **property-level** question and is listed in §6, not silently folded into this row verdict.

### 2.5 `:Object` and `:Subject`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/assembly.py:2629` | `(o:Object)<-[:aboutObject]-(e:Event)` | **adjacency** |
| `tortoise/audit.py:810` (and `:2445`, `:2456`) | `OPTIONAL MATCH (carrier:Object) WHERE carrier.name = sup.supersededBy` — the supersession chain | **adjacency** |
| `tortoise/definite_description_backfill.py:433` | `MATCH (o:Object) OPTIONAL MATCH (o)<-[r]-() WITH o, count(r) …` — degree census | **adjacency** |
| `tortoise/sdk.py:34355` | `MATCH (s:Subject {id:$sid})-[r]->(n)` | **adjacency** |

**Verdict: `graph`** for both.

### 2.6 `:GraphEvent` — the shape with no graph-capability reader

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/event_store.py:922` `read_after` | `seq > $after` **ordered range scan**, optional type filter, `LIMIT`/dedupe | **none** |
| `tortoise/event_store.py:557`, `:657` | `MATCH (m:GraphEventMeta) RETURN max(m.last_seq)` — aggregate | **none** |
| `tortoise/event_store.py:1014` `purge_expired`, `:1038` `purge_overflow` | predicate deletes | **none** |
| `tortoise/hosted_backup.py:842` | `max(last_seq), max(first_seq)` — aggregate | **none** |
| `tortoise/sdk.py:13587` (`poll_events`), `tortoise/webhook_delivery.py:1300`, `tortoise/why_snapshot.py:526`, `tortoise/change_feed.py:73` | all page through `read_after` | **none** |

**Verdict: `record_only`.** Not one traversal, not one adjacency, not the fulltext index, not EP state,
not a vector. This is the single largest remaining graph resident on the record-armed lane: §9.1's own
measurement puts `:GraphEvent` at **801 B/turn** — the *entire* 32,032 B/session residue
(`docs/evidence/10000-turns-bytes/README.md`). `docs/plans/2026-11-15-issue-12894-storage-shape.md` §A2.6
reached the same finding from the other direction ("whether those carriers should leave the graph is
explicitly *a placement decision of their own*") and did not claim it; **this document claims it.**

⚠️ **It cannot be moved by §9.1's mechanism, and §5.2 says exactly why.**

### 2.7 The graph-only bookkeeping labels

| shape | readers (`file:line`) | capability |
|---|---|---|
| `:GraphEventMeta` | `event_store.py:491` (MERGE), `:557`, `:657`, `:738`, `:1103`; `hosted_backup.py:842`; `sdk.py:13761` | **none** |
| `:EpMeta` | `sdk.py:36069`, `:36491`; `ep.py:473` | **none** |
| `:Meta` | `residency.py:244`, `:284`, `:292`, `:298`; `sdk.py:55647` | **none** |
| `:WhySnapshot` | `why_snapshot.py:237`, `:303`, `:350`, `:484` | **none** |
| `:Tag` | `sdk.py:21865`, `:22209`, `:28051`, `:28069`; `mcp_server.py:10730` | **none** (the `TAGGED` edges are the traversal; the node is the endpoint label) |
| `:CommitRecord` | `commit_idempotency.py:45`, `:57`, `:65`, `:194` | **none** |
| `:DeliveryReceipt` | `webhook_delivery.py:465`, `:475`, `:487`, `:502` | **none** |
| `:Batch` | `mining.py:94`, `:150`, `:178`; `projection/__init__.py:14295` | **none** |
| `:PipelineStage` | `pipeline.py:349`, `:482`, `:685`, `:731` | **none** |
| `:PackInstall` / `:PackSelection` | `export.py:126`; `hosted_api.py:32965`; `projection/__init__.py:1563`, `:2310`, `:14424` | **none** |

**Verdict `record_only` for all ten**, with the §5.2 blocker recorded — none has a record table, so none
can be expressed as a tier today. They are cheap *individually* (1–2 nodes per graph) and this document
does not pretend otherwise: they are listed because the census is complete, not because they are a lever.

---

## 3. The verdict table

`needs graph?` = does **at least one** reader need one of the five capabilities. `verdict` follows the
issue's rule: **no such reader ⇒ `record_only`, however graph-native it looks.**

| # | shape | readers (summary) | needs graph? | verdict | one-line reason |
|---|---|---|---|---|---|
| 1 | `:Point` core (`id`, content, `pointKind`, `status`) | §2.1, 12 readers | **yes** — EP walk, `IMPL`/`NAND`/`TAGGED`/`extractedFrom` adjacency, FTS, HNSW | **graph** | the belief walk *is* the product; six capabilities read it |
| 2 | `:Point.ep_*`, `baseline_set`, `confidence` | `propagation.py:81,86`; `sdk.py:38073` | **yes** — EP state | **graph** | there is no EP in SQL without building one |
| 3 | `:Point.embedding` | vector leg (HNSW) | **yes** — vector index | **graph** | the index lives in the engine (§12.1) |
| 4 | `:Source` identity + `_searchText` | §2.2 | **yes** — `references`/`extractedFrom`, FTS | **graph** | §9.4 ① makes the link load-bearing |
| 5 | `:Event` core | §2.3 | **yes** — `aboutObject`/`aboutSubject` | **graph** | lifecycle edges are how status folds |
| 6 | `:Session` core (`id`, `harness`, counters) | §2.4 | **yes** — `CONTAINS` | **graph** | the session→turn/claim walk is the read path |
| 7 | `:Session` capture telemetry family | whole-node reads only | **no** | **`record_only`** (property-level; §6.2) | a write stamp, not an edge — but see the blocker |
| 8 | `:Object` / `:Subject` core | §2.5 | **yes** — `aboutObject`, supersession, degree | **graph** | supersession is a chain, not a column |
| 9 | `story_arc` (on **any** label) | **none** — zero Cypher readers | **no** | **`record_only`** ⛔ **move blocked** (§5.1) | its one reader is `public.session_narrative`; no declaration can name a tail-only key (§5.1) |
| 10 | `:GraphEvent` | §2.6 | **no** | **`record_only`** ⛔ blocked | ordered scan + aggregates; no record table (§5.2) |
| 11 | bookkeeping labels (§2.7) | §2.7 | **no** | **`record_only`** ⛔ blocked | key lookups; no record table (§5.2) |
| 12 | `_searchText` on a **non**-episodic `:Source` | FTS | **yes** | **graph** | the fulltext index's field set |
| 13 | `Source.summary`/`_searchText` on an **episodic** Source | declined already (`#10853`) | **no** | **`record_only`** ✅ already shipped | §9.1's prose rule, per ROW |
| 14 | prompt-side `Point.aboutEntities`, `mentions` | `search_engine` flattening | **yes** — feeds the vector/text leg | **graph** | it is input to the embedding text |

---

## 4. Measured bytes per shape

Corpus: `--sessions 6 --turns 10`, `--turn-chars 1500`, 384-dim vectors, record **unarmed** (so turns are
nodes — the population a `record_only` move removes from). Payload = `sum(size(prop))`, a **lower bound**
(see §1.1). Full output: `docs/evidence/13083-capability-census/measurement-*.json`.

| shape | payload bytes | share | note |
|---|---|---|---|
| `:Point` | 668,014 | 96.5 % | of which `embedding` 401,000-ish and `content` ~ 90,000; `story_arc` 90,000 in the `before` arm only |
| `:GraphEvent` | 3,648 | 0.5 % | 19 nodes × ~192 B payload; the §9.1 figure of **801 B/turn** includes block overhead |
| `:Event` | 680 | 0.1 % | |
| `:Source` | 916 | 0.1 % | |
| `:Object` | 198 | 0.03 % | |
| `:Subject` | 139 | 0.02 % | |
| `:Session` | 177 | 0.03 % | 5 sessions — the capture telemetry family is ~20 B/session |

**Read this table carefully.** The payload census says the graph is **`:Point`-shaped**, and inside
`:Point` it is **vector- and content-shaped** — both of which are `graph` verdicts (§3 rows 1–3). The
audit's honest conclusion is therefore *not* "most of the graph can leave": it is **"the shapes that can
leave are small in payload but not in block overhead, and `:GraphEvent` is the one that repays a move."**

---

## 5. The moves

### 5.1 ⛔ `story_arc` on a `:Point` — verdict `record_only`, move BLOCKED by the mechanism's own invariant

**The shape.** §9.1/D1's S1 narrative. `cache_projection.CACHE_FORBIDDEN_COLUMNS` declares it for `source`
and `event` (`#10394`); the **`:Point`** route is named as open in §9.1's own residual text ("the `point`
table is a SEPARATE route … a legacy turn snapshot whose `props` tail carried the prose can still land it on a
`:Point` (`#10429`'s label)").

**The census entitlement is solid.** `story_arc` has **zero Cypher readers on any label**:
`rg -n 'story_arc' tortoise/ -g '*.py' | grep -E 'MATCH|RETURN|WHERE'` returns **nothing**, and the key's one
reader is `public.session_narrative` (`record_text.serve_from_record`). The live write door already refuses it
(`sdk._refuse_unpaired_narrative`). Under the issue's rule the shape is `record_only`.

**The move was built, and the repo's own pin refused it — correctly.** Adding
`CACHE_FORBIDDEN_COLUMNS["point"] = {"story_arc"}` is a **category error**, and two pre-existing tests say so:

- `tests/test_cache_projection.py::test_a_primary_property_round_trips_through_a_record_row` fails with
  *"a declared not-projectable column was never exercised: [('point', 'story_arc')]"* —
  `CACHE_FORBIDDEN_COLUMNS` is keyed by **RECORD COLUMN NAME**, and `public.point` **has no `story_arc`
  column** (`placement_columns()["point"]` does not contain it). An entry naming a non-column would guard a
  name forever while the real route stayed open — exactly the stale-entry failure that pin exists to catch.
- `tests/test_cache_projection.py::test_every_primary_property_has_a_canonical_spelling_that_is_projected`
  pins the dict **exactly** (`{source, event}`), and its own comment says an added entry is *"a decision that
  belongs in this test and in §9.1's placement rows, not in a one-line dict edit"*.

**So the gap is stated, not worked around.** The `:Point` prose arrives through the row's **`props` TAIL**,
and no declaration in the tree can name a tail-only key for a table that lacks the column. The tail's
point-side deny is the FOLD's skip set (`projection/entities.py:_POINT_DENY`), and §9.1 records why that lever
is **destructive** on this key: a `rebuild_all` over a journal whose creation carried the prose would
**DELETE** the property from every legacy `:Point` — a graph-side `REMOVE n.story_arc` that no instrument
ships, on a population where (for pre-`#9555` rows) the graph copy may be the **only** copy.

**What it would be worth, measured** — so the follow-up has a number, not an adjective. Corpus
`--sessions 6 --turns 10`: 60 legacy turn points whose tail carries 1,500 B of prose each. The only thing
varied between arms is whether the projection's `node_from_row` keeps the key.

| arm | `:Point` payload | `story_arc` bytes | `used_memory` delta |
|---|---|---|---|
| **with** the prose | 668,014 | **90,000** | 2,489,608 |
| **without** it | 578,584 | **absent** (= 0) | 2,338,392 |
| **Δ** | **−89,430** | **−90,000** | **−151,216** |

⚠️ **Read the arithmetic honestly, and the instrument's limits with it.** The payload delta is
**1,490.5 B/turn**, not 1,500 — `Point.content` also moved (+570 B) between the runs, so 1,500 B/turn is true
of `story_arc` alone. The arms are **not** a perfectly clean single-variable A/B: the `used_memory`
*benchmark* also differs between them by 95,296 B (5,235,632 vs 5,330,928), because the server is not a
calibrated instrument. But the `story_arc` **payload** measure is exact and deterministic, and the
`used_memory` delta (151,216 B over 60 points ≈ **2,520 B/point**) is larger than the observed noise. Treat
~2,520 B/point as an order-of-magnitude witness that **the block entry costs ~68 % more than `sum(size())`
reports** — which is why every payload number in §4 under-reads true residency.

**The measurement is kept** (`docs/evidence/13083-capability-census/`) because the follow-up needs it: it is
the size of the prize, **not** evidence of a shipped change. **No code from this lane changes projection
behaviour.**

### 5.2 ⛔ NOT SHIPPABLE under this mechanism, and exactly why

The census's largest verdict (`:GraphEvent`, §2.6) and ten bookkeeping labels cannot be moved by
`cache_tier`, and the reason is a **precondition of the mechanism**, not an oversight:

> `cache_tier` is a column on **`public.point`**. `read_record_view` applies the tier predicate **only**
> where `NODE_TABLES[label] == 'point'` (`cache_projection.py:3633`), and a `record_only` row only has
> meaning because a **record table holds it**. A shape with **no record table** has nowhere for the row to
> live once the graph refuses it.

So for `:GraphEvent` and the ten bookkeeping labels the sequence must be: **give it a record home**
(a `journal_event`-backed feed for `:GraphEvent`; a table for the others) → **then** tier it. That is
`#13082` Phase 1 territory ("a migrated query must be *deleted* from the graph path in the same PR, and
the graph-side data it needed becomes a candidate for `record_only` in the next capability audit").

**Filed rather than absorbed:** the `:Point` prose gap is recorded on **`#10429`** (this route's own issue),
and the `:GraphEvent` record home is **`#13094`**. ⛔ Nothing here arms a cap, a retention rule or an
eviction policy — that is `#13084` and P0-c.

### 5.3 The third gap, found by building the first move and watching the repo refuse it

`cache_tier` is **row**-granular; `CACHE_FORBIDDEN_COLUMNS` is **column**-granular. A property the record
stores in its **`props` TAIL** — a key the table has no column for — can be named by neither. That class is
not hypothetical: `public.point`'s `props` tail is the open-set surface every legacy point property arrived
through, and §5.1 is that class's first worked example.

---

## 6. Arguable shapes — listed with the question that would settle each

No guessed verdicts. Each of these is a shape the census **cannot** settle from the code alone.

### 6.1 `:Session` capture telemetry family — verdict `record_only`, move blocked
`capture_redactions`, `capture_ok`, `capture_extractor`, `machine_id`, `model`,
`client_captured_at*`, `entity_links_attempted/created`, `value_nodes_created/held`, `draft_count`,
`commit_count`.

- **Why it is arguable:** no Cypher reader names any of them — but they **are** returned by whole-node
  reads (§6.2), so they are not unread, only un-*needed*. Moving them is a **property-level** placement,
  which the row-level tier cannot express.
- **The question that settles it:** *is there a whole-node read that returns them to a caller who would
  break without them?* If `get_session_detail`/`list_sessions` project them to an API consumer, the
  placement decision is a **contract change**, not a storage move, and belongs to a product ruling; if
  nothing reads them but a test, they are dead weight on both stores and should be dropped, not tiered.

### 6.2 `:Point` listing-only families (`pointKind` filters, `domain_validators` reads)
- **Why it is arguable:** `MATCH (p:Point) WHERE p.pointKind IN …` is Cypher, and it is **SQL-shaped** —
  it does not need a graph. Under the capability rule the shape is `record_only`; under "a Cypher reader
  exists" it may not move without blanking that reader.
- **The question:** *is the validator's latency budget met by the record?* That is a differential-harness
  measurement (`#13082` §2), not a census fact. Verdict deferred, not guessed.

### 6.3 `:GraphEvent` — the *verdict* is settled, the *move* is not
- **The question:** *does `public.journal_event` already carry every field `poll_events` serves, with a
  stable cursor?* The census says every `:GraphEvent` emit sits behind a journal append (`sdk._emit_event`
  → `event_store.append_event`), and `journal_event` is the position table — so the answer is very likely
  yes, which would make the move a **feed re-pointing** rather than a new store. **Verify the field-for-field
  match on a real tenant before proposing it**; a cursor that moves by one is a silent subscriber gap.

### 6.4 `:Source.embedding` on a non-episodic Source
- **Why it is arguable:** §9.4 makes the *link* mandatory and the *summary vector* optional ("it buys exactly
  one query class"). Whether a given Source's vector is read by that class is a demand question.
- **The question:** *what share of vector queries hit a `:Source` embedding?* — P0-c's query-mix measurement.

### 6.5 `UnservedVectorLabels` — already decided, cited not reopened
`:Session` vectors are declared unwritable (`#11747`, `UNSERVED_VECTOR_LABELS`); this census agrees and
changes nothing.

---

## 7. What this document does not do

- **It migrates no Cypher reader.** Every `graph` verdict keeps its reader untouched; that is `#13082`
  Phase 1/2 behind a differential harness.
- **It arms nothing** — no cap (`#13084`), no retention, no eviction.
- **It bounds nothing.** `journal_event` is untouched (`#9025`), and no per-graph cap or node-count rule is
  introduced (`#11758`): this is *what may be resident*, never how many graphs or nodes a tenant may have.
- **It does not delete.** The one shipped move stops a write; no graph copy is removed, and the destructive
  leg remains `#10017`'s tooling.

---

## 8. The external leg — what was taken, and what was checked here

**The question:** how do graph-database deployments decide what to keep resident when RAM is the
constraint?

**What was taken** (one Perplexity `sonar` query; ⚠️ the brief's `mcp__seo-intelligence__perplexity_search`
MCP server is **not present on this box** — no such server in the catalog and no local script — so the same
provider was queried directly, and that substitution is disclosed rather than papered over):

- Deployments size for the **working set**, not whole-graph residency; whole-graph residency is a *sizing
  goal*, sustained working-set residency is the *production acceptance criterion*.
- The acceptance metric is a **cache-hit ratio**: Neo4j publishes `dbms.page_cache.hit_ratio` /
  `dbms.page_cache.usage_ratio` with a target **above 98 %**, sizing page cache from on-disk data volume ×
  a growth factor (~1.2) and reserving heap + OS headroom. Memgraph tracks
  `memgraph_memory_res_bytes` (RSS) and its peak against available RAM.
- No FalkorDB-specific published metric was in the returned set — recorded as a gap rather than invented.

**Which claims were checked in this repo before relying on them:**

| claim taken | checked here |
|---|---|
| RAM is the scarce resource, disk is cheap | §5/§2.1: **$73/GB-month** resident vs **$0.125/GB** disk — 584× |
| resorting to disk costs a cold-restore penalty | §12/§A4: **0.03 s** index-dropped, **1.585 s** index-kept (and that one blocks the instance) |
| the vector index dominates residency | §12.1, and §4 above: `Point.embedding` is the single largest payload family in the corpus |
| a shape nothing reads cannot help the hit ratio | this document's own §2: `:GraphEvent` has **zero** capability readers and is **801 B/turn** of the armed-lane residue |

**What the source did NOT license.** It is a statement about **residency sizing inside a graph**, not about
**placement between stores**; it supports "don't keep what the working set does not touch", and is *not*
evidence for any particular migration order. The migration order stays `#13082`'s (frequency × simplicity,
behind a differential harness).
