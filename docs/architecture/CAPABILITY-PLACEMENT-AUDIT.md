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

The population is not the ontology's wish list; it is what a writer **actually puts on a node**. ⚠️ **The claim
is scoped, not 'everything in the database':** it covers the **memory substrate** (rows 1–6) plus every
**graph-only bookkeeping label reached from the code paths this audit read** (rows 7–17). It is **not** a census
of the registry / tenancy / metering substrate — and the labels found but NOT censused are listed in row 18
rather than silently omitted.

Two writers reach the graph, and both were read:

1. the **live tenant write path** (`sdk.*` + `projection/entities.py`'s folds), and
2. the **record → cache projection** (`cache_projection.py`, `NODE_TABLES` → 6 labels), which is what a
   rebuild materialises.

The inventory below is the union. `d` = the label's record table in `cache_projection.NODE_TABLES` (a record
HOME — ⚠️ **not a tier**: `cache_tier` is a column on `public.point` only, and `cache_projection.py:3633`
applies the tier predicate `if table == "point"`, so a `record_only` row-tier is mechanically expressible for
`:Point` alone and every other label needs its own migration — §5.3's third precondition); `—` = graph-only
bookkeeping with **no record table** (relevant in §5.2).

| # | shape (label) | written by | record table | node count in the measured corpus |
|---|---|---|---|---|
| 1 | `:Point` (claims, operators, turns) | `sdk.create_point`/`update_point`, `projection/entities._upsert_point_props`, `projection/propagation` | `public.point` | 66 (60 turn points + claims) |
| 2 | `:Source` (documents, transcripts) | `sdk._create_source`, `_upsert_source`, `_upsert_document`, `sdk._materialize_session_source` | `public.source` | 2 |
| 3 | `:Event` (lifecycle, AgentSession) | `sdk.create_event`, `_upsert_event`, `hosted_api` commit path | `public.event` | 3 |
| 4 | `:Session` | `sdk._write_session_and_turns`, `projection/entities.py:3871`, `hosted_api:16304` | `public.session` | 7 |
| 5 | `:Object` | `sdk.create_object`/`create_entity`, `_upsert_object` | `public.object` | 2 |
| 6 | `:Subject` | `sdk.create_subject`, `_upsert_subject` | `public.subject` | 2 |
| 7 | `:GraphEvent` (change feed) | `event_store.append_event` (`sdk._emit_event`) | **—** | 67 |
| 8 | `:GraphEventMeta` (seq allocator) | `event_store.next_seq` | — | 1 |
| 9 | `:EpMeta` (EP version) | `sdk._advance_ep_version` | — | 1 |
| 10 | `:Meta` (residency declaration) | `residency.py:284` | — | 0 (arm not exercised) |
| 11 | `:WhySnapshot` | `why_snapshot.py:484` | — | 0 (arm not exercised) |
| 12 | `:Tag` | `projection/edges.py:1881` (`MERGE (t:Tag {name:$tag})`, reached from `sdk`'s tag sync) | — | 0 (arm not exercised) |
| 13 | `:CommitRecord` (idempotency) | `commit_idempotency.py:45` (`_MERGE_CYPHER`), `:65` (`_UPDATE_CYPHER`, the `status` transition) | — | 0 (not exercised) |
| 14 | `:DeliveryReceipt` (webhook) | `webhook_delivery.py:487` | — | 0 (not exercised) |
| 15 | `:Batch`, `:PipelineStage`, `:PackInstall`, `:PackSelection`, `:PackManifest` | `mining.py`, `pipeline.py`, `pack_state.py`, `hosted_api:32965`, `pack_manifest_store.py:467` | — | 0 (not exercised) |
| 16 | `:ContextSubscription` (webhook registration) | `webhook_delivery.py:406` (`CREATE (s:ContextSubscription {…})`) | — | 0 (not exercised) |
| 17 | `:OnboardingState` / `:OnboardingStep` | `onboarding/state.py:317` (`MERGE (n:OnboardingState {org_id})`), `:322`/`:929` (`MERGE (s:OnboardingStep {org_id, step_id})`), `:324`/`:934` (`MERGE (n)-[:COMPLETED_STEP]->(s)`) | — (`COMPLETED_STEP` is an EDGE type, not a record table — `NODE_TABLES` has six labels and onboarding is not one of them) | 0 (not exercised) |
| 18 | **found, NOT censused** — `:Pipeline` (`pipeline.py:525`), `:MeteringRecord` (`metering.py:108`, `:840`, `:1146`, `:1608`), `:MeteringUnmeteredIncrement` (`metering.py:616`), `:WebhookEvent` (`hosted_api.py:47787`), the tenancy/registry labels `:Team` (`sdk.py:46612`), `:Membership` (`:47262`), `:APIKey` (`:47409`), `:TeamMeta` (`:46671`), `:Graph` (`:46753`), `:SignupToken` (`hosted_api.py:34901`), `:Invitation` (`hosted_api.py:30343`), and the **retired** `:Document` | sdk / metering / pipeline / migration | — | **not censused — a GAP, see §7** |

⚠️ **`:Document` is in this row because it is a MIGRATION STATE, not a live shape — and its readers are still
named, so nothing is hidden behind the omission.** Post-D10 a document **is** a `:Source`
(`projection/edges.py:834-843`), so the label only exists on a graph that has not yet run the relabel. Its only
readers are that migration's own whole-node property read (`migrate_kinds.py:296-300`, `MATCH
(n:{_LEGACY_DOCUMENT_LABEL}) RETURN ID(n), n.id, n.url, …` — capability **`none`**) and the deprecated-alias
acceptance at `projection/edges.py:2965-2966`; its only writer is the relabel itself (`migrate_kinds.py:430-437`,
`SET n:Source … REMOVE n:Document`) — `:297`/`:296-300` is its ENUMERATOR, a reader, named two sentences above and
deliberately not repeated here as a writer. **No placement verdict is available on it**: the
label is removed by the migration, so "what may be resident" is not a question that has an answer — the population
question is "has this graph been relabelled yet", which is `#5140`'s. ⛔ An earlier draft gave it its own censused
row (the first `:Document` row, immediately before this one) while giving it no §2 reader census and no §3 verdict,
which made the population statement claim a census it never received.

⛔ **Row 18 is a real gap, labelled as one.** This audit read the memory-substrate writers and the bookkeeping
labels on their paths; it did **not** classify the billing / tenancy / pipeline substrate. **No placement
decision on `:Pipeline` or `:MeteringRecord` may be made from this document.** (`:Pipeline` is named
explicitly because the same module writes the censused `:PipelineStage`.)

Rows 10–17 are declared **not exercised by the instrument** rather than counted as zero-cost — a shape
the corpus does not create is a shape this document has *not* measured, and saying so is the difference
between a census and a guess.

### 1.1 The instrument

`docs/evidence/13083-capability-census/probe_shape_bytes.py` — a fixed, synthetic corpus (deterministic
ids, texts and 384-dim vectors), seeded through the **production chokepoints** (`sdk._write_session_and_turns`,
`sdk._capture_session`, the `sdk.create_*` doors, and `cache_projection.record_plan` for the projection
arm). Bytes are the server's own `INFO memory → used_memory` (the instrument §9.1's own measurement uses,
`docs/evidence/10000-turns-bytes/README.md`) plus per-property figures summed in Python as `len(str(value).encode())` and emitted as `repr_bytes_by_label`
— a **RENDERED** size, because `size()` refuses a scalar and returns an ELEMENT COUNT for a list, so no
single Cypher spelling yields stored bytes for every property type.

**Its limits are quoted, not discovered later** — `used_memory` includes block/index matrices and its
baseline drifts between runs; and the per-property column is a **rendered** size: exact for a string, but an
**over-report of ~4.7×** for a numeric array (a 384-dim float vector renders as ~7,187 characters; its stored
`float32` payload is 1,536 B). §4 shows both columns and derives an array's footprint from its width.

---

## 2. The reader census — every reader at `file:line`, by capability

Each row is a **reader**, not a mention — and a `SET` expression that READS the node counts as a reader. The
capability column is what the reader *needs*; a reader that needs none of the five is SQL-shaped and is
marked **none**.

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
| `tortoise/search_engine.py:4422-4426` | `MATCH path = (seed)-[:IMPL\|NAND*1..{max_hops}]-(n:Point)` — `expand_structural_hops`, a **variable-length** belief walk (min hop distance) | **traversal** |
| `tortoise/search_engine.py:6297` | `(op)-[r2:IMPL\|hasPart]-(other:Point)` — the operator-expansion leg | **adjacency** |
| `tortoise/search_engine.py:6656` | `(n:Point)-[r:aboutSubject]->(:Subject)` (inside `_SUBJECT_SOURCE_SCOPED_PROBE`, `:6655-6658`, consumed only by `subject_binding_available()` at `:6661-6674`) — an **edge-existence probe** answering "can this graph carry `aboutSubject` edges at all", not a per-node degree prior; it fails OPEN | **adjacency** |
| `tortoise/search_engine.py:3039` | `CALL db.idx.fulltext.queryNodes('Point', $query)` | **fulltext index** |
| `tortoise/search_engine.py:4350-4352` | `run_structural_query`'s `MATCH (n:{label}) WHERE …` — a plain label scan, no pattern | **none** |
| `tortoise/search_engine.py:3938` | `CALL db.idx.vector.queryNodes('Point', 'embedding', $limit, vecf32($query_vec))` — the HNSW leg | **vector index** |
| `tortoise/domain_validators.py:105` (and the same property-join shape at `:174-182`, `:197-205`) | `MATCH (req) WHERE (req:Point OR req:Object) AND (req.pointKind IN $req_kinds OR req.objectKind IN $req_kinds)`, then `OPTIONAL MATCH (wp:Point {wf_id: req.enabled_workflow})` — **property-equality joins only**; no relationship appears in any pattern | **none** |
| `tortoise/domain_validators.py:153-158` (`orphan_use_case`) | `OPTIONAL MATCH (uc)<-[:hasPart]-(op:Point {is_operator:true, op_type:'composedOf'})-[:hasPart]->(jtbd:Point)` — a **2-hop `hasPart` walk**, i.e. every parented use-case is found THROUGH the tree | **adjacency** |
| `tortoise/domain_validators.py:241-243` | `MATCH (n:Point {status:'draft'}) WHERE n.is_operator = false AND NOT (n)--()` — the `orphaned_draft` rule (#131) tests **existence of any edge**, which is adjacency | **adjacency** |
| **the whole-node accessor family** — `tortoise/sdk.py:24633`, `:25506`, `:27101`, `:27197`, `:27296` (`get_point`'s `_cache_answer`), `:40513`; `tortoise/record_serve.py:306` (`READ_POINT_BY_ID`, `status=SERVED`); `tortoise/projection/__init__.py:14169`; and the label-generic `tortoise/record_reconcile.py:533` | `MATCH (n:Point …) RETURN properties(n)` — the **whole node**, handed back as a dict. ⚠️ **A whole-node read IS a reader in this census** (§2.4 counts it for `capture_redactions`, row 14 for `Point.mentions`), and an earlier draft omitted the whole family from this table while counting it elsewhere — which is how the `story_arc` "zero readers" error survived three drafts | **none** |

**Verdict: `graph`.** Seventeen reader rows, **fourteen of them capability-carrying** (three are deliberately
`none`: `search_engine.py:4350-4352`, a plain label scan; `domain_validators.py:105`, a property-equality join;
and the whole-node accessor family above), **fourteen of them across six files** (`projection/propagation.py`,
`sdk.py`, `mcp_server.py`, `projection/edges.py`, `search_engine.py`, `domain_validators.py`) — the three
`none` rows bring `record_serve.py`, `projection/__init__.py` and `record_reconcile.py` as well; a
record-backed copy could not answer any of them without re-implementing belief
propagation, the **variable-length hop walk**, the fulltext index or HNSW in SQL (which is `#13082` Phase 2 and its stop condition).

⚠️ **A correction this census needed against itself.** An earlier draft cited
`domain_validators.py:106`–`:244` as one "kind filters, no walk" row and quoted a query
(`MATCH (req:Point) …`) that **does not exist** — the code reads `MATCH (req) WHERE (req:Point OR
req:Object)`. The range also contains `:241-243`, whose `NOT (n)--()` is an **adjacency** predicate, which
is the shape's own second capability — and `:153-158`, whose two-hop `hasPart` pattern is a third: the
first correction of this row split it only two ways and still filed `orphan_use_case` as `none`. All three
are fixed above (three rows, not two): the cite is split, and the mis-quoted text is gone. The error was
invisible to a reader who did not open the file, which is exactly the failure this document exists to
prevent — recorded rather than quietly edited.

### 2.2 `:Source`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/sdk.py:38818` | `MATCH (s:Source)-[:references]->(o:Source)` — source-to-source provenance | **traversal** |
| `tortoise/sdk.py:52878` | `MATCH (s:Source {url:$url})-[:references]->(n)` | **traversal**. |
| `tortoise/sdk.py:15659` (target side) | `(p:Point)-[:extractedFrom]->(s:Source)` — every claim must reach its source (§9.4 ①) | **traversal** |
| `tortoise/search_engine.py:3039` | `CALL db.idx.fulltext.queryNodes('<label>', $query)` — the index `:3011`'s note says is built on **`Source._searchText` for every Source** | **fulltext index** |
| `tortoise/session_source_migration.py:432-434` | `SET new.provenance_spans = CASE WHEN coalesce(size(new.provenance_spans),0)=0 THEN $spans ELSE new.provenance_spans END` — a **`SET` that READS the node**, i.e. a reader by §2's rule | **none** |

⚠️ **That last row is the rule that put it here.** §2's method statement says a `SET` expression that reads
the node is a reader. `provenance_spans` is this audit's own instance of that — and it was **missing from an earlier
draft**, which is the more instructive failure: a `SET`-that-reads is exactly what a `MATCH`-only grep walks
past. Capability `none` (a property guard: it fills an absent value and never walks), so the verdict stands.

**Verdict: `graph`.** The link is load-bearing: §9.4 makes "every claim walks up to its Source" a
mechanism, not a citation nicety.

> ⚠️ **The fulltext index is declared on FIVE labels, not two.** This is the census's most easily missed
> reader: `_FULLTEXT_INDEX_LABEL_FIELDS` (`projection/__init__.py:6815-6831`) declares `Point`, `Event`,
> `Subject`, `Object` and `Source`, and the query is **label-parametric** (`search_engine.py:2944` derives
> the label from `entity_type`), so ONE call site serves all five. The FTS row is carried for `:Point`
> (§2.1) and `:Source` (§2.2) *and* for `:Event` (§2.3) and `:Object`/`:Subject` (§2.5); leaving it out of
> the last three made them look adjacency-only. No verdict flips — all five are `graph` either way — but an
> incomplete enumeration is exactly the defect a census exists to prevent.

### 2.3 `:Event`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/goal_judgement.py:1119` | `MATCH (e:Event)-[:aboutObject]->(o) WHERE o.id = $indicator` | **adjacency** |
| `tortoise/search_engine.py:6658` | `(m:Event)-[r2:aboutSubject]->(:Subject)` | **adjacency** |
| `tortoise/assembly.py:2629` | `MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event)` | **adjacency** |
| `tortoise/search_engine.py:3039` | `CALL db.idx.fulltext.queryNodes('Event', $query)` — the label-parametric FTS leg (`:2944` derives the label from `entity_type`; `sdk.py:52011` passes `"event"`); the index declares `("Event", ["subject","name"])` at `projection/__init__.py:6825` | **fulltext index** |

**Verdict: `graph`.** ⚠️ Two whole-node reads are absent from the table above and belong to it by §2's own rule:
`tortoise/session_indexer.py:746` (`MATCH (e:Event {eventId: $eid}) RETURN properties(e)`) and the
label-generic `tortoise/record_reconcile.py:533` (`MATCH (n:{label}) RETURN properties(n)`). Both are
capability **`none`**, so they add no capability and change no verdict — they are named so that the census's
"each row is a reader" claim is true rather than merely convenient.

### 2.4 `:Session`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/hosted_api.py:24356`, `:24453`, `:24572`, `:25847`, `:25865` | `MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point)` — session detail and its turn reads | **adjacency** |
| `tortoise/sdk.py:2917`, `:3499`, `:52258`, `:52381` | the same `CONTAINS` walk from the SDK door | **adjacency** |
| `tortoise/session_reinjection.py:561` | `MATCH (s:Session)-[:CONTAINS]->(seed)` — multi-session evidence pooling | **adjacency** |
| `tortoise/activation_scorecard.py:395` | `(s:Session)-[:CONTAINS]->(p:Point)` | **adjacency** |
| `tortoise/session_schema.py:855-862`, `:892`, `:906-917` (`audit_session_retrofit`, reached by the shipped CLI `tools/session_retrofit_audit.py`) | the short-turns query counts `stored_turns` through `OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point)` before returning `s.capture_ok, s.capture_extractor, s.harness, s.capture_lane` — **four of the seven expressions** in that RETURN (`s.id, s.turn_count, stored_turns` precede them), so the guard against reading it as a two-property projection is "four of seven", not "four, not two"; `WHERE s.capture_ok = true RETURN count(s)`; and the entity-link under-count, `OPTIONAL MATCH (s)-[r1:aboutObject]->()` + `(s)-[:CONTAINS]->(t:Point)-[r2:aboutObject]->()`, `WHERE live_edges > s.entity_links_created` | **adjacency** |
| `tortoise/quota.py:1155` | `MATCH (s:Session) RETURN count(s)` | **none** |

**Verdict: `graph`.** ⚠️ Its **property families are another matter, and the family SPLITS.**
`capture_ok`, `capture_extractor`, `capture_lane`, `entity_links_created` and `entity_links_attempted` are
**`graph`** (verdict row 7) and may not move: the retrofit audit (`session_schema.py`, row above) returns
them from queries whose ANSWERS need adjacency — `stored_turns` comes from a `CONTAINS` walk, `live_edges`
from two `aboutObject` walks — so a `public.session` row cannot reproduce either, however plain the
projection looks. **`turn_max_index` is `graph` too**, by the same rule and one query family over — but the justification is
**single-site**, and an earlier draft of this paragraph claimed two. At `hosted_api.py:43929-43946` the
returned answer IS composed with the walk: `total = int(nodes or 0)` then `total = max(total, mark + 1)`,
the anti-phantom prior count. At `sdk.py:4152-4159` the walk supplies a **sibling column** —
`_prior_mark_index(_prior[0][0], _prior[0][1])` takes only `turn_max_index` and `turn_count`, and
`collect(t.id)` is consumed by a different variable (`_split_graph_turn_ids`) — so that site alone does not
carry the mark's answer, and citing it as if it did was wrong. One site is enough under the rule that the
`graph` column is not a scale of agreement.
⚠️ An earlier draft said these properties had "no Cypher reader of their own", and its §2.4 table omitted
`session_schema.py` entirely, which is why the falsifying readers went unseen; §6.1 records the correction.
The **remainder** (`capture_redactions`, `machine_id`, `model`, `client_captured_at*`, `value_nodes_*`,
`draft_count`, `commit_count`) is a MIXED set, and precision here is the point — it is not one claim:

- **Read by Cypher, `none` capability:** `value_nodes_*` are named by the commit-path state load
  (`hosted_api.py:21561`) and incremented by a `SET` that reads them
  (`hosted_api.py:22234-22236`); `machine_id`, `model`, `client_captured_at`,
  `client_captured_at_source` are named by the session-detail read (`sdk.py:52229-52251`), with
  `client_captured_at` also read inside a `SET` (`sdk.py:4018-4021`); **`draft_count` and `commit_count`**
  are read inside the same commit-statement `SET` (`hosted_api.py:22237-22240`) — §1's reader rule, exactly
  as `provenance_spans` is counted in §2.2, and an earlier revision of this paragraph omitted them.
- **No shipped query NAMES it — but it IS read back as a WHOLE NODE:** `capture_redactions`. `sdk.py:2682` and
  `:2705` only WRITE it (`SET s.capture_redactions=$redactions`) and no product `RETURN`/`WHERE` names the
  property — the one Cypher read that does name it is a TEST assertion
  (`tests/test_capture_secret_redaction_4911.py:250-254`), which is not a PRODUCT read — but two
  whole-node reads carry it, and a whole-node read is a reader by this document's own vocabulary
  (row 14 counts `sdk.py:27210`'s `get_point` — *"Returns dict of all properties"* — as one). `tools/backfill_copy_turns.py:188-192`
  is `RETURN id(s), s.id, properties(s)`, and its own comment at `:177-183` names this key explicitly as one
  the record must not lose (*"a null `capture_redactions` is a MEANINGFUL value … dropping the key would make
  the record unable to falsify that reading"*); `projection/__init__.py:14484` is `MATCH (s:Session) RETURN
  properties(s)`, the pre-wipe snapshot restored by a whole-schema writer (`session_schema.py:493-505`). Its
  named-COLUMN reader is the Postgres record (`record_read.py:1001`). ⚠️ An earlier revision of this bullet
  said "the graph has never read this count back" — false, and the same error class this section is written
  against, which is why it is corrected rather than reworded away.

Every Cypher site above is a point lookup or a property guard — capability **`none`** — so `record_only`
(row 7b) stands on the CAPABILITY rule, not on their being unread; a **property-level** question listed in
§6, not silently folded into this row verdict. ⚠️ One site changes what §6.1 can ask: the
`value_nodes_*`/`draft_count`/`commit_count` increments live in the **same statement** as the Session
`MERGE` (`hosted_api.py:22231-22243`), so no `public.session` row can join that atom — the read is
`none`-capability, but the WRITE is graph-coupled, which is a live input to the placement ruling.

### 2.5 `:Object` and `:Subject`

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/assembly.py:2629` | `(o:Object)<-[:aboutObject]-(e:Event)` | **adjacency** |
| `tortoise/audit.py:810` (and `:2445`, `:2456`) | `OPTIONAL MATCH (carrier:Object) WHERE carrier.name = sup.supersededBy AND id(carrier) <> id(sup)` — **no relationship in the pattern**; a property-equality scan | **none** |
| `tortoise/definite_description_backfill.py:434` | `MATCH (o:Object) OPTIONAL MATCH (o)<-[r]-() WITH o, count(r) …` — degree census | **adjacency** |
| `tortoise/sdk.py:34355` | `MATCH (s:Subject {id:$sid})-[r]->(n)` | **adjacency** |
| `tortoise/search_engine.py:3039` | `CALL db.idx.fulltext.queryNodes('<Object or Subject>', $query)` — the same label-parametric leg (`entity_type="object"` at `assembly.py:2118`, `aggregate.py:547`, `coverage_loop.py:345`; no **product** caller passes the literal `'subject'` (test modules do — `tests/test_search_engine_gaps.py:1395`, `:1559`, `tests/test_integration_search.py`) — `mcp_server.py:4368` is a tool DOCSTRING, and the leg rides the caller-supplied value through the passthroughs at `mcp_server.py:5035`, `:5475`, `:7911`); the index declares `("Subject", ["name"])` at `projection/__init__.py:6826` and `("Object", ["name"])` at `:6830` | **fulltext index** |

`:Tag` belongs on this row too, and an earlier draft got it **wrong** by calling it bookkeeping: every tag
read resolves the node **through the edge** — `sdk.py:28069`
(`MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) RETURN properties(p)`),
`sdk.py:28051` (`MATCH (t:Tag) OPTIONAL MATCH (p:Point)-[:TAGGED]->(t) …` — the tag list with counts),
`sdk.py:21865` (`MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) WHERE t.name IN $names`) /
`mcp_server.py:10730` (`OPTIONAL MATCH (t)<-[:TAGGED]-(q)`), and
`sdk.py:22209` (`MATCH (t:Tag) WHERE NOT (t)<-[:TAGGED]-() DELETE t`, the orphan GC). A traversal needs its
**endpoint in the graph**, so "the node is only the endpoint label" is not an exemption — it is the
justification. **Verdict: `graph`.**

**Verdict: `graph`** for both.

### 2.6 `:GraphEvent` — the shape with no graph-capability reader

| reader (`file:line`) | what it does | capability |
|---|---|---|
| `tortoise/event_store.py:991-993` (`read_after`, whose `def` is `:922`) | `e.seq > $after` **ordered range scan**, optional type filter, `LIMIT`/dedupe | **none** |
| `tortoise/event_store.py:557`, `:657` | `MATCH (m:GraphEventMeta) RETURN max(m.last_seq)` — aggregate | **none** |
| `tortoise/event_store.py:645`, `:1103` | `min(e.seq)` / `max(e.seq)` over `:GraphEvent` — aggregates | **none** |
| `tortoise/event_store.py:801`, `:879` | per-page key existences inside the emit path | **none** |
| `tortoise/event_store.py:1043` | `MATCH (n:GraphEvent) RETURN count(n)` — the overflow path's whole-label total, counted BEFORE `purge_overflow` (`:1038`) decides the excess. An aggregate, not a key existence and not a delete; the two purge deletes are `:1029`/`:1053`, on the row below | **none** |
| `tortoise/event_store.py:1029` (`purge_expired`) / `:1053` (`purge_overflow`) | predicate deletes — `WHERE n.ts < $cutoff` and `ORDER BY n.seq ASC LIMIT $overflow` + `DETACH DELETE` (the **def** lines are `:1014`/`:1038`) | **none** |
| `tortoise/hosted_backup.py:842`, `:890`, `:1182`, `:1383` | `max(last_seq), max(first_seq)` and whole-graph export scans — aggregates | **none** |
| `tortoise/sdk.py:13766` (the `read_after` call inside `poll_events`, `:13642`) | pages the feed | **none** |
| `tortoise/why_snapshot.py:526` | pages the feed to rebuild a why-snapshot | **none** |
| `tortoise/webhook_delivery.py:1041` (`event_head_seq`) | `max(e.seq)` — aggregate | **none** |
| `tortoise/webhook_delivery.py:1300` | `read_after(proj, int(sub.get("cursor") …))` — the subscriber delivery page | **none** |

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
| `:GraphEventMeta` | `event_store.py:557`, `:657`; `hosted_backup.py:842`; `sdk.py:13761`; `webhook_delivery.py:1052` (the head fallback) (reads); `event_store.py:491`/`:738` are the `MERGE`s and `event_store.py:1103` a `MATCH … SET m.first_seq` **RMW** that advance or restore the watermark (⚠️ written with the file on every cite — a bare `:491` after `webhook_delivery.py:1052` is that file's `rid` params line, a `:738` there is a deadline loop and a `:1103` a docstring) | **none** |
| `:EpMeta` | `sdk.py:36069`; `ep.py:473` | **none** |
| `:Meta` | `residency.py:244`, `:292`; `sdk.py:55647` (reads); `:298` is a `DETACH DELETE` scoped by an IDENTITY key only (`MATCH (n:Meta {key:$key})`) — see the criterion note under this table | **none** |
| `:WhySnapshot` | `why_snapshot.py:237` (read); `:303-304` is `_drop` — `MATCH (w:WhySnapshot) WHERE w.point_id IN $ids DELETE w`, ORPHAN cleanup scoped by IDS THE CALLER ALREADY HOLDS (not retention, and not time-scoped); `:350-351` is the retention prune — `WHERE w.assembled_at < $cutoff WITH w LIMIT 5000 DELETE w` (time-scoped) | **none** |
| `:CommitRecord` | `hosted_api.py:22228-22232` (the live commit path's read — it consumes `x.value_nodes_declared` as a predicate); `commit_idempotency.py:57` (read), `:65` — `MATCH … SET r.status=$status RETURN r.session_id, r.status, r.write_ops_billed, r.written_at, r.value_nodes_declared`, a **read-modify-write** (`CommitRecordStore.update` consumes all five returned columns, so `RMW` is the right word — an earlier draft called it read-free, which was wrong), and `:194` — the idempotency `DELETE`, whose `WHERE r.status = 'partial' AND coalesce(r.write_ops_billed, 0) = 0 AND r.value_nodes_declared IS NULL` **consumes three node properties as predicates**, so by this table's own criterion it is a reader (an earlier draft called it "not a read"); `hosted_api.py:21691-21692` (`MATCH (r:CommitRecord …)` then `SET r.telemetry=$t, r.budget_warn=$warn`) and `:23760-23761` (`SET r.write_ops_billed=1`, a LITERAL — not a parameter) have **no `RETURN`** and assign parameters/literals, so they read nothing of the node and are WRITERS (§1 row 13). ⚠️ Three drafts of this row called writers readers, then over-corrected `:65` to read-free, then called `:194` read-free; the table now separates read / RMW / predicate-consuming DELETE / writer, site by site | **none** |
| `:DeliveryReceipt` | `webhook_delivery.py:475` (`RETURN properties(r)`); `:502` is the `MAX_RECEIPTS` `DETACH DELETE`, `:487` the CREATE — see the criterion note under this table | **none** |
| `:Batch` | `mining.py:94`, `:178`; `projection/__init__.py:14295` | **none** |
| `:PipelineStage` | `pipeline.py:349` (read); `:685`/`:731`/`:754-756` are `SET` statements — writes with no read of the node, not `RMW` and not reads | **none** |
| `:PackManifest` | `pack_manifest_store.py:524` (`MATCH (m:PackManifest) RETURN m.namespace, …`), `:539` (`RETURN m.namespace, m.yaml`), `export.py:133` (`MATCH (m:PackManifest {namespace:$ns}) RETURN m.yaml`); `:550` is the `DELETE`, `:467` the writer named in §1 | **none** |
| `:PackInstall` / `:PackSelection` | `export.py:126` (`:PackInstall`); `pack_state.py:878-881` (`MATCH (s:PackSelection) RETURN s.revision, s.configured_at, s.updated_by`); `projection/__init__.py:2335`, `:13801-13802`, `:17586-17587` (`MATCH (s:{_PACK_SELECTION_LABEL}) RETURN properties(s)` — **whole-node reads**, invisible to a `:PackSelection` grep because the label is the constant at `:2256`, the same interpolation that hid `:PackManifest`) | **none** |
| `:ContextSubscription` | `webhook_delivery.py:425`, `:435`, `:444`, `:452`, `:1113`, `:1134-1136` (the due-subscription read); `why_snapshot.py:557` (read). ⚠️ The `CREATE` and the `DETACH DELETE` named in §1 are both in **`webhook_delivery.py`** — `:406` and `:461` — not in `why_snapshot.py`, whose `:406` is `ids.append(pid)` and whose `:461` is a comment; neither file's writer is a reader | **none** |
| `:OnboardingState` / `:OnboardingStep` | `onboarding/state.py:759-763` (`MATCH (n:OnboardingState {org_id:$org_id})-[:COMPLETED_STEP]->(s:OnboardingStep) RETURN s.step_id`) | **adjacency** → **`graph`** (§3 row 11c) — listed here only so the label is not missing from the table |

**Verdict `record_only` for every row above except `:OnboardingState`/`:OnboardingStep` (which is `graph`)** — that is **12 record-only labels across 11 rows**, `:PackInstall`+`:PackSelection` sharing one.
Each row cites readers; where a line is a **read-modify-write** it is labelled `RMW` rather than presented as
a pure read, and the writer itself is the one named in §1. ⚠️ Two labels were moved OUT of this table by
review: `:Tag` (`graph` — see §2.5) and the onboarding pair (`graph` — row above). §5.2's blocker is
recorded for the rest — none has a record table, so none
can be expressed as a tier today. They are cheap *individually* (1–2 nodes per graph) and this document
does not pretend otherwise: they are listed because the census is complete, not because they are a lever.

⚠️ **The criterion for a predicate `DELETE`, stated once so the rows above stop looking inconsistent.** A
`DELETE`/`DETACH DELETE` is a **reader** when its `WHERE` (or its `MATCH` map) has to evaluate **node content** —
`commit_idempotency.py:194`'s `r.status = 'partial' AND coalesce(r.write_ops_billed,0)=0 AND
r.value_nodes_declared IS NULL` is the model — and it is **not** a reader when it is scoped by an **identity key or
a clock** the caller already holds (`residency.py:298`'s `{key:$key}`, `why_snapshot.py:303-304`'s
`point_id IN $ids`, `why_snapshot.py:350-351`'s `assembled_at < $cutoff` + `LIMIT 5000`). §2.6's
`event_store.py:1029`/`:1053` are listed as readers because `:1053` **orders by `n.seq`** (a property the statement
must read to choose its victims) even though `:1029`'s `ts < $cutoff` does not — so that row is the one genuinely
borderline case, and it is capability `none` either way. ⚠️ **`webhook_delivery.py:502-504` is the test of the rule,
not an exception to it**: `MATCH (r:DeliveryReceipt {sub_id:$sid}) WITH r ORDER BY r.ts DESC, r.receipt_id DESC SKIP
$keep DETACH DELETE r` **does** choose its victims by two node properties — the very test that makes `:1053` a
reader — and it is exempt only because its `MATCH` map pins the subscription identity. What it does NOT contain is
a `LIMIT`; `SKIP $keep` keeps the newest `$keep` and deletes the rest, which an earlier draft of this note
got backwards. Four drafts read these sites as a mix of "reader" and "not a read" because the rule was never
written down; it is written down here, with its own hardest case named.

---

## 3. The verdict table

`needs graph?` = does **at least one** reader need one of the five capabilities. `verdict` follows the
issue's rule: **no such reader ⇒ `record_only`, however graph-native it looks.**

| # | shape | readers (summary) | needs graph? | verdict | one-line reason |
|---|---|---|---|---|---|
| 1 | `:Point` core (`id`, content, `pointKind`, `status`) | §2.1, 17 reader rows (14 capability-carrying + 3 `none`) | **yes** — EP walk, `IMPL`/`NAND`/`TAGGED`/`extractedFrom` adjacency, FTS, HNSW — **all five** capabilities | **graph** | the belief walk *is* the product; **all five** capabilities read it |
| 2 | `:Point.ep_*`, `baseline_set`, `confidence` | `propagation.py:81,86`; `sdk.py:38073` | **yes** — EP state | **graph** | there is no EP in SQL without building one |
| 3 | `:Point.embedding` | vector leg (HNSW) | **yes** — vector index | **graph** | the index lives in the engine (§12.1) |
| 4 | `:Source` identity (+ `_searchText` on a **non**-episodic / post-`#10000` Source — the rows below split the episodic case out) | §2.2 | **yes** — `references`/`extractedFrom`, FTS | **graph** | §9.4 ① makes the link load-bearing |
| 5 | `:Event` core | §2.3 | **yes** — `aboutObject`/`aboutSubject` adjacency **and a fulltext index on `subject`/`name`** | **graph** | lifecycle edges are how status folds, and `name`/`subject` are FTS fields |
| 6 | `:Session` core (`id`, `harness`, counters) | §2.4 | **yes** — `CONTAINS` | **graph** | the session→turn/claim walk is the read path |
| 7 | `:Session` retrofit-audit / turn-mark properties (`capture_ok`, `capture_extractor`, `capture_lane`, `turn_max_index`, `entity_links_created`, `entity_links_attempted`) | `session_schema.py:855-862`, `:892`, `:906-917`; `sdk.py:17082`; and `hosted_api.py:43929-43946` (`RETURN s.turn_max_index, s.turn_count, count(t)` → `max(count(t), mark + 1)`, the anti-phantom prior count; note `sdk.py:4152-4159` returns the mark but consumes it WITHOUT the walk — see §2.4) | **yes** — returned from queries whose answers need `CONTAINS` / `aboutObject` adjacency | **graph** | the audit's shortfall count, its entity-link under-count and the hosted prior-count read ARE adjacency queries; no `public.session` row answers any of them |
| 7b | `:Session` telemetry, the remainder (`capture_redactions`, `machine_id`, `model`, `client_captured_at*`, `value_nodes_*`, `draft_count`, `commit_count`) | point lookups and `SET`-that-reads: `hosted_api.py:21561`, `:22234-22240` (`value_nodes_*`, `draft_count`, `commit_count`), `sdk.py:52229-52251` (session detail), `sdk.py:4018-4021`; whole-node reads for `capture_redactions` (`tools/backfill_copy_turns.py:188-192`, `projection/__init__.py:14484`) | **no** — `none` capability | **`record_only`** (property-level; §6.1) | read, but by nothing that needs the graph — property guards and node dumps, not edges; the counter increments are graph-coupled though, so see §6.1 |
| 8 | `:Object` / `:Subject` core | §2.5 | **yes** — `aboutObject` adjacency (`assembly.py:2629`), the `-[r]->` walk (`sdk.py:34355`) **and a fulltext index on `name`** | **graph** | an Event reaches its Object by EDGE; ⚠️ `supersededBy` is a **string matched by equality** (`audit.py:810`), not a walk — SQL-shaped, and an earlier draft wrongly cited it as the reason |
| 9 | `story_arc` (on **any** label) | **no Cypher query NAMES it** — but six whole-node reads carry it (`sdk.py:24633`, `:25506`, `:27101`, `:27197`, `:27296`, `:40513`); capability **`none`** | **no** | **`record_only`** ⛔ **move gated on a deliberate pin decision** (§5.1) | its one NAMED reader is `public.session_narrative`; the graph carries it only via `RETURN properties(n)`, which is `none`-capability by the same disposition row 14 gives `Point.mentions`; the mechanism CAN deny it, two policy pins currently refuse the entry |
| 10 | `:GraphEvent` | §2.6 | **no** | **`record_only`** ⛔ blocked | ordered scan + aggregates; no record table (§5.2) |
| 11 | bookkeeping labels (§2.7) | §2.7 | **no** | **`record_only`** ⛔ blocked | key lookups and label scans; no record table (§5.2) |
| 11c | `:OnboardingState` / `:OnboardingStep` | §2.7 | **yes** — `completed_steps()` walks `COMPLETED_STEP` | **graph** | the docstring is explicit: *"Canonical completed-step ids from the edge set (never a second store)"* — the edge IS the store |
| 11b | `:Tag` | §2.5 | **yes** — `TAGGED` is how every tag is read | **graph** | the edge is the read; the node is its endpoint |
| 12 | `_searchText` on a **non**-episodic `:Source` | FTS | **yes** | **graph** | the fulltext index's field set |
| 13 | `Source.summary` on an **episodic** Source | the record door reads it as the WITNESS and declines to project it (`cache_projection.py:2648-2655`); **Cypher DOES name it, at capability `none`** — `session_source_prose_migration.py:218-223` (`_SOURCE_ROWS`, `RETURN s.url, s.sourceKind, s.summary, …`, issued by `find_session_sources_carrying_prose` and run on every rebuild via `reconcile_after_replay`), `:1263-1268` (`_HITS_ROWS`) and `:1292-1300` (`_CONTROL_LEXICON_ROWS`, `WHERE s.summary STARTS WITH $prefix … AND s.documentKind IS NULL` — exactly this episodic population) | **no** — property projections and a prefix predicate | **`record_only`** ✅ already shipped | a display label — the index reads `_searchText`, not this. ⚠️ **An earlier draft wrote "no Cypher reader names it", which was false** and is the same false-negative-reader class corrected for `story_arc` and `capture_redactions`: the verdict rests on CAPABILITY, never on "unread" |
| 13b | `Source._searchText` on an **episodic** Source (BOTH derivations) | FTS — `CALL db.idx.fulltext.queryNodes('Source', $query)` (`search_engine.py:3039`) over the index declared for the WHOLE label (`projection/__init__.py:6831`), populated by both Source writers (`sdk.py:19637`, `#3518`) | **yes** — fulltext index | **`graph`** | ⚠️ The record door's per-ROW decline (`cache_projection.py:2658-2660`, the post-`#10000` lexicon rows) is a RECORD-projection decision, not a licence to drop the graph copy: whatever the graph holds, the index reads it, and dropping it "un-indexes a restored session" (`:2648-2649`). A pre-`#10000` row's value is prose derived FROM `summary` (`:2077-2081`) — prose, but still index input |
| 14 | `Point.mentions` (the held-phrase JSON tail prop, `projection/entities.py:1206`) | `sdk.py:27210` (`get_point` — *"Returns dict of all properties"*, the whole-node read); `projection/__init__.py:22118-22120` — a **write-only** fold (the branch is computed in Python at `:22109` from the journal payload, so the `SET` reads no node; `none`); `sdk.py:18841` is **not** a reader of the stored shape — it reads the extractor PAYLOAD dict, `pt` bound at `:18769` | **no** | **`record_only`** ⛔ gated — the two test pins refuse the declaration (§5.1) | nothing walks, filters or indexes it, and it is **not** an embedding input (`sdk.py:15975` composes from `quote`/`content` only) — but the move is blocked |

⚠️ **Row 14 was corrected in review — twice, and the second correction is the instructive part.** It first
read `prompt-side Point.aboutEntities, mentions` with a **`graph`** verdict whose reader was "`search_engine`
flattening" — a reader with no `file:line` that does not exist. Two defects there: (a) `aboutEntities` is not
a node property — `projection/entities.py:2423` lists **`about_entities`, `aboutEntities`** under *"structural
/ edge-carried — never node props via passthrough"* (the snake entry at `:1808` sits in `_META_KEYS` as
*"handled as graph edges"*), and the edge it becomes is `aboutSubject`, already censused at §2.5, so the row
double-counted a censused shape; (b) `mentions` **is** stored (`projection/entities.py:1206`,
`_POINT_JSON_RECORD_PROPS`), but nothing needs a graph capability to read it, and the embedding text is
`quote` + edge-derived names (`embed_compose.py:284`, called at `sdk.py:15975`) — neither half is an input. ⚠️ Stated precisely: the ONE call site passes `props.get("quote"), content=content` and **no** `names=`, so the composed text at a write is the quote alone; the `names` arm exists in `embed_compose` and attaches at the recompose seam (`#11739`), which reads `aboutSubject` edges — already this row's §2.5 census, not a `mentions` reader.
⚠️ The FIRST correction then cited two things that are **not readers**: `sdk.py:18841` reads the extractor
payload dict rather than a node, and `projection/__init__.py:22118-22120` is a write-only fold that its own
definition of `RMW` (§2.7) does not cover. Both are replaced above by the real whole-node read, `sdk.py:27210`.
The verdict was right the first time and the evidence was not — which is why every cite in this document is
meant to be opened, not trusted. The move stays blocked by exactly what gates `story_arc`: the mechanism CAN
deny a tail-only key (§5.1 measures it), and the two test pins are what refuse the declaration.

---

## 4. Measured bytes per shape

Corpus: `--sessions 6 --turns 10`, `--turn-chars 1500`, 384-dim vectors, record **unarmed** (so turns are
nodes — the population a `record_only` move removes from). Per-property figures are **rendered** sizes
(§1.1), with an array's stored footprint derived from its width. Full output: `docs/evidence/13083-capability-census/measurement-*.json`; every figure below is
re-derived from those files.

**⚠️ The per-property column is a RENDERED size, not the engine's stored size.** The probe reads each value
and sums `len(str(value).encode())`; `size()` cannot substitute (it refuses a scalar and returns an element
COUNT for a list). For **string** properties the two coincide and the figure is exact. For **numeric arrays
they do not**, and the gap is an order of magnitude: the rendered text of a 384-dim float vector is ~7,187
characters of digits, commas and spaces, while its stored `float32` payload is 384 × 4 = **1,536 bytes**. A
review caught the first cut publishing the rendered figure as bytes. Both are shown below; the **stored**
figure is derived from the width, never read off the rendered column.

| shape | nodes | rendered bytes | stored footprint | share (of the RENDERED total) |
|---|---|---|---|---|
| `:Point` | 66 | **668,014** | | |
| … of which `embedding` | | 474,348 (rendered) | **101,376** (66 × 384 × 4 B, `float32`) | **69.4 %** |
| … of which `content` | | 93,287 (exact — a string) | 93,287 | **13.7 %** |
| … of which `story_arc` (this arm only) | | 90,000 (exact) | 90,000 | 13.2 % |
| `:GraphEvent` | 67 | **12,768** | 12,768 (text-dominated — see the caption) | **1.87 %** |
| `:Source` | 2 | 916 | 916 (text-dominated) | 0.13 % |
| `:Event` | 3 | 680 | 680 (text-dominated) | 0.10 % |
| `:Session` | 7 | 303 | 303 (text-dominated) | 0.04 % |
| `:Object` | 2 | 198 | 198 (text-dominated) | 0.03 % |
| `:Subject` | 2 | 139 | 139 (text-dominated) | 0.02 % |
| `:GraphEventMeta` / `:EpMeta` | 2 | 4 | 4 (INTEGER scalars — stored is ~24 B, see the caption) | 0.001 % |
| **total (rendered)** | | **683,022** | | |

⚠️ **The `stored footprint` cells for the non-`:Point` rows BELOW are the RENDERED figure, and they are a few percent
low — read them as rendered.** The two columns coincide only for a **string**: an integer scalar stores 8 bytes
while rendering shorter, so e.g. `:GraphEvent.seq` is 67 × 8 = **536 B stored** against **125 B rendered**
(`measurement-before.json`), and the label totals inherit that gap. Six of those labels are text-dominated, so
the error is within a few percent and **no conclusion in §4 or §5 depends on it** — but a cell that is neither a
rendered nor a stored figure is exactly what this table exists to avoid, so it is labelled rather than implied.
The `:Point` rows, where the vector and the prose live, are the ones that are exact (a string) or width-derived
(the vector).

(Percentages are of the rendered total, 683,022 — the only total the instrument can produce without a
per-type conversion for every property. They are used for SHAPE, not for a billing estimate.)

**Read this table carefully.** The census says the graph is **`:Point`-shaped (97.8 % of rendered bytes)** and
that inside `:Point` the two largest families are **`embedding` (474,348 B rendered, 69.4 %; 101,376 B stored)
and `content` (93,287 B rendered and stored, 13.7 %)**. ⚠️ **Those two are comparable only on the STORED
basis** (101,376 vs 93,287 B) — on the rendered basis the vector is not comparable at all, which is exactly
the ~4.7× over-report the paragraph above warns about. Quoting 14.8 % for a stored figure against a rendered
total was wrong and was corrected in review. Both families are `graph` verdicts (§3 rows 1–3).
`:GraphEvent` is **1.87 %** of payload but is the **largest non-Point resident**, and among §1's rows 1–7 — the
memory substrate itself — it is the only one with **no** capability reader (the twelve bookkeeping labels of §2.7
have none either, so it is emphatically not the only label in the document); its ~190 B/node payload understates
it, because §9.1's own measurement puts it at
**801 B/turn** once block overhead is counted.

**The audit's honest conclusion is therefore not "most of the graph can leave":** it is *"the shapes that
can leave are small in payload, `:GraphEvent` is the one that repays a move, and the rest of the graph is
vector and text that a graph operation genuinely reads."*

## 5. The moves

**The census found the verdicts; three PRECONDITIONS decide which of them can move today, and the census
names all three precisely.** One move was built and then withdrawn — not because the mechanism cannot express it, but
because two policy pins in `tests/test_cache_projection.py` refuse the declaration until someone deliberately
widens them, and that decision belongs to the issue that owns the route.

### 5.1 ⛔ `story_arc` on a `:Point` — verdict `record_only`; the mechanism CAN deny it, two pins gate it

**The shape.** §9.1/D1's S1 narrative. `cache_projection.CACHE_FORBIDDEN_COLUMNS` declares it for `source`
and `event` (`#10394`); the **`:Point`** route is named as open in §9.1's own residual text ("the `point`
table is a SEPARATE route … a legacy turn snapshot whose `props` tail carried the prose can still land it on a
`:Point` (`#10429`'s label)").

**The census entitlement.** `story_arc` is **named by no Cypher query on any label**: `rg -n 'story_arc'
tortoise/ -g '*.py'` returns 147 hits and **not one** is inside a `MATCH`/`RETURN`/`WHERE`. Its one **named** reader
is `public.session_narrative` (`record_text.serve_from_record`). The live write door already refuses it
(`sdk._refuse_unpaired_narrative`).

⚠️ **`story_arc` IS read — by whole-node reads, and an earlier draft's "zero Cypher readers" was false under this
document's own rule.** Six product sites return the whole `:Point` node — `MATCH (n:Point {id:$id}) RETURN
properties(n)` at `sdk.py:24633`, `:25506`, `:27101`, `:27197` (`:27296` inside `get_point`'s `_cache_answer`) and
`:40513` — so a legacy point whose `props` tail carries the prose is returned WITH that key today, and the deny's
visible effect is **`get_point` ceasing to return it for legacy points**. This is the exact error class §2.4 and
row 14 record twice for `capture_redactions` and `Point.mentions`: "unread" is false, "capability `none`" is the
real reason the verdict is `record_only`. **The verdict does not change** — a whole-node read needs none of the
five capabilities — but the entitlement sentence did, and the `before` arm's `Point.story_arc` = 90,000 B is the
proof the key is a node property at all.

**The mechanism reaches it — measured, not assumed.** `_props_from_row`'s tail filter is
`tail_denied = forbidden | {placement spellings}`, i.e. it denies the **plain** forbidden set as well as its
column-spelling widening (`cache_projection.py:2710`). So `CACHE_FORBIDDEN_COLUMNS["point"] = {"story_arc"}`
**does** drop the key from a point row's `props` tail. ⚠️ An earlier draft of this document claimed the
opposite ("no declaration in the tree can name a tail-only key") — that was **wrong**, and it was wrong in the
direction that matters: it told `#10429` the closure was impossible when it is one declaration plus a test
edit. Verified in-process: without the entry `node_from_row("Point", row)` keeps `story_arc`; with it, the key
is dropped.

**What actually refuses it is a TEST POLICY, and both pins are deliberate:**

- `tests/test_cache_projection.py:186-195` pins the dict **exactly** (`{source, event}`) so that adding an
  entry cannot slip in as "a one-line dict edit" — its own comment says the decision *"belongs in this test
  and in §9.1's placement rows"*.
- `tests/test_cache_projection.py:243-246` asserts every declared not-projectable name is a **real, exercised
  COLUMN** of that table. `public.point` has no `story_arc` column, so a tail-only entry reddens it — the pin
  cannot distinguish "a stale entry guarding a name that projects freely" from "a tail-only key, which has no
  column to exercise".

**So the decision is real and it is `#10429`'s:** either the completeness pin learns a **tail-only category**
(a declared name with no column, exercised through the `props` tail), or this route stays open. This document
records the finding and the number; it does not widen another issue's pin.

⛔ **And the fix must be a projection-only deny, not a fold deny — but for a narrower reason than an earlier
draft gave, and that draft's mechanism was wrong.** ⚠️ **An earlier draft said a `_POINT_DENY` entry would make a
`rebuild_all` DELETE the property from every legacy `:Point`. That is false.** `_POINT_DENY`
(`projection/entities.py:2472`) has one operational consumer on the WRITE path: it is passed as a skip set into
`_persist_extra_props(…, self._POINT_HANDLED | self._POINT_DENY, …)` at `:3183-3192`, which **drops the key from
the `SET n += $extra` map** — nothing is REMOVEd. It has four other executable readers, and naming them is the
point: two warning filters in the same file (`:3228`, `for key in self._POINT_DENY - self._POINT_HANDLED`; `:3278`,
`or key in self._POINT_DENY or key in self._META_KEYS`) and two in `tortoise/consistency.py` (`:136` re-exports it,
`:260` builds `_DENY_REASON` entries from it). ⚠️ **An earlier draft of this paragraph wrote "`rg -n '_POINT_DENY'
tortoise/` returns the definition, that call site, and comments" — that is false: it returns 20 hits across four
files, including the four sites just named.** Verified by opening all four: **none of them derives a
`REMOVE`/`DELETE`**, so the conclusion stands and the evidence sentence around it did not.

**The real reason the fold deny is the wrong instrument is a different one, and the code names it.** The
destruction the tree actually documents is a **whole-graph restore wipe** — `MATCH (n) DETACH DELETE n`, which
*"cannot be row-scoped … so a graph holding ANY row whose PROSE the record does not carry must NOT be restored at
all: copy that prose out first, because the wipe destroys the only copy"* (`projection/entities.py:2327-2333`,
§17.2, `#11325`). That hazard is **independent of which deny carries the entry**, which is why a fold deny does not
mitigate it. What does separate the two denies is **which payload the key arrives in**: the journal fold handles a
payload key (`_POINT_DENY`, whose own comment at `:1826-1827` describes it as the list *"for payload keys the
replay DELIBERATELY drops"*), while this route's key
arrives in a **record row's `props` tail** and is denied by `_props_from_row`'s `tail_denied` (`cache_projection.py:2710`).

⚠️ **And a graph-side clear DOES ship — scoped, and not to this population.** `update_point(<id>, story_arc=None)`
is an in-place clear (`projection/entities.py:2337-2338`, `#11325`) that applies **only where
`public.session_narrative.narrative` still holds the text**, and the comment warns it must **never** be aimed at the
row's own `props` tail: the clear's own record write consumes that tail, so the patch merges right-hand and
**overwrites the prose**. For the legacy tail-only rows this census is about, that clear is therefore not
applicable — which is the accurate form of "deleting nothing", and it is a stronger argument for the projection
deny than the wrong one: the projection deny stops the WRITE on exactly the population where no graph-side clear
can safely reach.

**What it would be worth, measured.** Same corpus both arms (`--legacy-prose` on BOTH), and the ONLY thing
varied is the declaration: `before` pops the entry, `after` sets it. No prose-less arm is compared against a
prose-bearing one.

| arm | `:Point` payload | `story_arc` bytes | `:Point` nodes |
|---|---|---|---|
| **before** (entry absent) | 668,014 | **90,000** | 66 |
| **after** (entry present) | 578,014 | **0** | 66 |
| **Δ** | **−90,000** | **−90,000** | 0 |

That is **exactly 1,500 B/turn** over 60 seeded legacy turn points, and the whole delta is `story_arc` —
`Point.content` and `Point.embedding` are identical between the arms (93,287 and 474,348 in both), which is
the check that the arms are one variable. Total `:Point` payload falls **13.5 %**.

⚠️ **`used_memory` is NOT quoted for this pair.** The instrument's baseline is not stable between runs: the
committed pair differs by **847,064 B** — an order of magnitude larger than the 90,000 B payload delta, which is why
`used_memory` cannot carry a residency figure here. ⚠️ **An earlier draft also cited "49,504 B" from a prior pair;
that run is not committed anywhere in this branch, so the number is not re-derivable and has been dropped** — in a
document whose method promises every figure can be re-derived, an uncommitted figure is a claim to trust, not a
measurement.
Because the drift is run-dependent and has been observed both larger and far smaller than the 90,000 B payload
delta, any residency marginal read off it is a number the next run can contradict. The **payload** figure is
exact and deterministic; the residency figure is **unmeasured**, and saying so is the difference between a
measurement and a decoration.

**The instrument and both JSON arms are committed** (`docs/evidence/13083-capability-census/`) so `#10429` can
re-run the closure the moment the pin decision lands.

### 5.2 ⛔ NOT SHIPPABLE under this mechanism, and exactly why

The census's largest non-Point verdict (`:GraphEvent`, §2.6) and twelve bookkeeping labels cannot be moved by
`cache_tier`, and the reason is a **precondition of the mechanism**, not an oversight:

> `cache_tier` is a column on **`public.point`**. `read_record_view` applies the tier predicate **only**
> where `NODE_TABLES[label] == 'point'` (`cache_projection.py:3633`), and a `record_only` row only has
> meaning because a **record table holds it**. A shape with **no record table** has nowhere for the row to
> live once the graph refuses it.

So for `:GraphEvent` and the twelve bookkeeping labels the sequence must be: **give it a record home**
(a `journal_event`-backed feed for `:GraphEvent`; a table for the others) → **then** tier it. That is
`#13082` Phase 1 territory ("a migrated query must be *deleted* from the graph path in the same PR, and
the graph-side data it needed becomes a candidate for `record_only` in the next capability audit").

**Filed rather than absorbed:** the `:Point` prose gap is recorded on **`#10429`** (this route's own issue),
and the `:GraphEvent` record home is **`#13094`**. ⛔ Nothing here arms a cap, a retention rule or an
eviction policy — that is `#13084` and P0-c.

### 5.3 The three preconditions, side by side

| precondition | shapes it blocks | what unblocks it |
|---|---|---|
| **no record table** → no tier can refuse the row | `:GraphEvent`, `:ContextSubscription`, the eleven other bookkeeping labels | a record home, then `cache_tier` (§5.2) |
| **a policy pin** → the declaration is refused although the seam would honour it | `point.story_arc` | `#10429`: widen the completeness pin with a tail-only category (§5.1) |
| **a record table but no tier COLUMN** → having a row is not having a tier | every non-`:Point` label whose move is **whole-label** (none in this census — rows 1/4/5/6/8 are `graph`) | a `cache_tier` column (one migration per label) **plus** the `if table == "point"` arm at `cache_projection.py:3633` widened; ⚠️ this does **not** unblock row 7b, which is a **property-level** move the row-level tier cannot express (§6.1) |

None of these is a census failure. Each is the mechanism's **precondition**, named so the next slice is executable
instead of another audit.

## 6. Arguable shapes — listed with the question that would settle each

No guessed verdicts. Each of these is a shape the census **cannot** settle from the code alone.

### 6.1 `:Session` capture telemetry — SIX properties ARE graph-read, the remainder is `record_only`

`capture_redactions`, `capture_ok`, `capture_extractor`, `capture_lane`, `machine_id`, `model`,
`client_captured_at*`, `turn_max_index`, `entity_links_attempted/created`, `value_nodes_created/held`,
`draft_count`, `commit_count`.

⚠️ **`capture_lane` is `graph`, and an earlier draft never mentioned it at all.** It is the fourth of the
four TELEMETRY properties in the short-turns query's RETURN — the seventh expression there, after `s.id`,
`s.turn_count`, `stored_turns`, `s.capture_ok`, `s.capture_extractor` and `s.harness`
(`session_schema.py:860-861`) — so by row 7's own rule it is returned from a query whose answer needs a
`CONTAINS` walk and **may not move**. It is also the third member of the
capture-provenance triplet (`session_projection.py:68`: `client_captured_at`,
`client_captured_at_source`, `capture_lane`) of which the remainder list above names only the first two.
Had a lane executed row 7b as written, it would have found `capture_lane` unclassified and tiered it by
analogy with `machine_id`/`model` — moving a property whose reader's answer is an adjacency walk. That is
the same failure this section's correction below records, one draft later.

⛔ **Correction — an earlier draft of this section said *no Cypher reader names any of them*, which is
false.** `session_schema.py` names four, inside `audit_session_retrofit` (`tools/session_retrofit_audit.py`):
`capture_ok`/`capture_extractor` are returned by the short-turns query at `:855-862`, whose row count comes
from `OPTIONAL MATCH (s)-[:CONTAINS]->(t:Point)`; and `entity_links_created`/`entity_links_attempted` are
compared against `live_edges` at `:906-917`, computed from two `aboutObject` walks. `:892`
(`WHERE s.capture_ok = true RETURN count(s)`) and `sdk.py:17082` (the `session_existed` retry decision) are
`none` on their own — but the rule is *at least one reader*, and two of these readers need adjacency, so the
four are **`graph`** (verdict row 7) and **must not move**. A projection can sit inside an adjacency query
without looking like one, which is why the QUERY STRUCTURE is what this row is classified on. Note the earlier
draft's own remedy — *"if nothing reads them but a test, they are dead weight …
drop, not tier"* — would have deleted data two shipped audit classes depend on.

What remains arguable is the **remainder** of the family — `capture_redactions`, `machine_id`, `model`,
`client_captured_at*`, `value_nodes_*`, `draft_count`, `commit_count`:

⚠️ `turn_max_index` is **NOT** in this remainder: it is `graph` (row 7). An earlier draft listed it here as
`none` on the strength of the session-detail read (`sdk.py:52251`) alone, missing the reader whose ANSWER is
the mark/count comparison over the `CONTAINS` walk (`hosted_api.py:43929-43946`) — row 7's rule applied to a
property, exactly the miss `capture_lane` records above, one property later. (That justification is ONE site:
`sdk.py:4152-4159` returns the mark but consumes it without the walk, and is cited in row 7 only for that
contrast.)

- **Why it is arguable:** they are **not unread** and must not be treated as such. `value_nodes_*` are
  named by the commit-state load (`hosted_api.py:21561`) and incremented by a `SET` that reads them
  (`hosted_api.py:22234-22236`); `machine_id`/`model`/`client_captured_at*` by the session-detail read
  (`sdk.py:52229-52251`) with `client_captured_at` also read inside a `SET` (`sdk.py:4018-4021`); and
  `draft_count`/`commit_count` are read inside that same commit-statement `SET`
  (`hosted_api.py:22237-22240`). Every one is a point lookup or property guard — capability **`none`** —
  which is why the verdict is `record_only` on the CAPABILITY rule, never on the false premise "unread".
  Moving them is a **property-level** placement, which the row-level tier cannot express.
- **The question that settles it:** *does every one of those readers keep working off a `public.session`
  row?* ⚠️ For most of them the honest answer is "yes, once the row is read" — but the counters are a
  different case: `value_nodes_created`, `draft_count` and `commit_count` are incremented in the **same
  statement** as the Session `MERGE` (`hosted_api.py:22231-22243`), so their WRITE is graph-coupled even
  though their read needs no capability. That makes this a **contract/placement ruling**, not a storage
  move: `value_nodes_*` is the budget numerator the commit reconciliation consumes, and the
  capture-provenance columns are served to SDK callers and mirrored by the hosted endpoint. ⛔ An earlier
  draft's remedy here — *"if nothing reads them but a test, they are dead weight on both stores and should
  be dropped, not tiered"* — rested on a premise this section disproves, and would have deleted counters
  the commit path and the session read depend on. They are not deletion candidates; they are candidates
  for a product ruling on placement. (`capture_redactions` is the one property here with no named-column
  Cypher read — but two whole-node `properties(s)` reads carry it, `tools/backfill_copy_turns.py:188-192`
  and `projection/__init__.py:14484`, so it is read, not merely written: its disposition is the same
  placement question, not a free deletion.)

### 6.2 `:Point` listing-only families (`pointKind` filters)

⛔ **Two rules in the same file are NOT in this family.** `domain_validators.py:153-158`
(`orphan_use_case`, a 2-hop `hasPart` walk) and `:241-243` (`orphaned_draft`, `AND NOT (n)--()`) are
classified **adjacency** in §2.1 and are **`graph`** — the first correction of §2.1 filed the whole
`:106`–`:244` range as `none` and the second still missed `:153-158`. Only the pure kind/property-filter
rules are arguable here.

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
  introduced (`#11758`: the price of record is a share of STORAGE, not a node count): this is *what may be
  resident*, never how many graphs or nodes a tenant may have.
- **It does not delete.** No product code changed in this slice, so nothing was removed from any graph. ⚠️ The
  destructive leg is **not** available to cite for THIS class: `#10017` ("purge the pre-`#10000` turn nodes")
  was closed **`NOT_PLANNED`** on 2026-11-06, and the tool that does ship — `tools/purge_legacy_turns.py`, the
  `DETACH DELETE` of legacy episodic turn `:Point`s — targets a different population and never names
  `story_arc` (`grep -c story_arc tools/purge_legacy_turns.py` → `0`). So there is no shipped purge to point at
  for this shape. The legacy copies this
  document declines to fold-delete therefore remain, which is a *stronger* reason for the §5.1 refusal, not a
  weaker one.
- ⛔ **It is NOT a census of the whole database.** Row 18 of §1 lists the labels found but **not censused**
  (`:Pipeline`, the metering substrate, the tenancy/registry labels, the retired `:Document`). Those get no reader census and no
  verdict here, and a placement decision on them must not be made from this document. That is a **gap**, not
  a scope choice dressed as one.

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
| resorting to disk costs a cold-restore penalty | `docs/evidence/12766-fulltext-restore/README.md` (90 000-node row): **0.032 s** index-dropped, **1.585 s** index-kept (and that one blocks the instance) |
| the vector index is a major residency term | §12.1, and §4 above: `embedding` (101,376 B stored) and `content` (93,287 B) are the two largest payload families in the corpus — comparable, neither dominating |
| a shape nothing reads cannot help the hit ratio | this document's own §2: `:GraphEvent` has **zero** capability readers and is **801 B/turn** of the armed-lane residue |

**What the source did NOT license.** It is a statement about **residency sizing inside a graph**, not about
**placement between stores**; it supports "don't keep what the working set does not touch", and is *not*
evidence for any particular migration order. The migration order stays `#13082`'s (frequency × simplicity,
behind a differential harness).
