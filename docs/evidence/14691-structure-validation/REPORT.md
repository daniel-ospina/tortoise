# #14691 T6 — does the extractor STRUCTURE the business, or just narrate it?

**Answer, one sentence: yes to the inventory, no to the wiring.** Every product, feature, team,
role, person, component and customer in this corpus survives extraction under a sensible type and
the product→feature tree is built correctly — but a reader cannot describe how the business is
*connected*, because all six ownership edges are stored **inverted** (and the product's own ownership
door then answers `[]`), team membership and role assignment arrive as the meaning-free `related`
predicate, **team→role containment does not exist at all**, and **no MCP door reports a structural
edge's direction** — so the inversion is invisible to any agent reading the graph the way the product
tells it to.

| | |
|---|---|
| Lane | `test/t6-business-structure` |
| Graph under test | `xv14691_business_structure` — its own graph on this lane's private FalkorDB (`fdb-lane-2a55a53650`, `127.0.0.1:16413`) |
| Corpus | `docs/evidence/14691-structure-validation/corpus/structure-corpus.json` |
| Code under test | this worktree at `origin/main` = `f37a250d9b22` (`git rev-parse HEAD origin/main` match at run time) |
| Run | 2026-11-26, 05:37–05:43 UTC · 5 captures, 81.7 s of capture ack time (1.4 min), measured per document in `out/ingest-receipt.json` |
| Extractor | `extraction_mode: "llm:deepseek-direct"`, model `deepseek-v4-flash` |
| Predecessor | #14606 (`docs/evidence/14592-operating-model-validation/`) — its harness and method are REUSED here, not rebuilt |

---

## 0. The method rule, and how this run obeyed it

Every write went through `TortoiseSDK.mine_knowledge_from_session` — the method `tool_registry` maps
`tortoise_mine_knowledge_from_session` straight onto, and the extractor v4's only door. Every read
went through the **Tortoise MCP server** over stdio (`python -m tortoise.mcp_server`, `fastmcp.Client`
against the real server process). Nothing in `run_ingest.py`, `run_readback.py` or `score_t6.py`
opens a socket, builds a Cypher string, or reads the record store.

⛔ **One question could not be answered through the product at all**, and it is the one assertion 2
turns on: *is this structural edge oriented src→dst?* `GET /v1/entities/{id}/relations`
(`tortoise/entity_reads.py:entity_relations`) is a real product door that answers it — and it has
**no MCP or SDK door**. `AGENTS.md` forbids hand-rolling HTTP to `/v1/…`, so this run did **not**
reach around: the question is recorded as door finding **D1**, and the orientation verdict below is
scored from the **write receipt** (`relation_write.edges`, what the product reported writing) plus
`tortoise_get_entity` reads of the endpoints — never from a graph read the product does not expose.

Reproduce (steps 3–5 read the graph step 1 wrote, so they must run in one sitting):

```bash
export TORTOISE_DB_URI='docker://:@127.0.0.1:16413/tortoise_test_matrix'   # this lane's container
export DEEPSEEK_API_KEY='…'                       # extraction credential; never read from a file
# optional: TORTOISE_VENV=/path/to/a/built/venv   (default: <worktree>/.venv)

docs/evidence/14691-structure-validation/run.sh docs/evidence/14691-structure-validation/run_ingest.py
docs/evidence/14691-structure-validation/run.sh docs/evidence/14691-structure-validation/run_readback.py
docs/evidence/14691-structure-validation/run.sh docs/evidence/14691-structure-validation/score_t6.py
```

---

## 1. The corpus

Five short **meeting transcripts** about one software company (Lumen Grid, an energy-operations SaaS),
authored for this test and never derived from any existing graph. Delivered as transcripts because
**v4's only door is session capture** (#14606 G7 — there is still no document-corpus door).

| doc | date | carries |
|---|---|---|
| `doc-01` | 2026-04-07 | **product tree** — 2 products (Lumen Core, Lumen Field) and 5 features |
| `doc-02` | 2026-04-14 | **organisation** — 3 teams, 4 roles, 4 people, membership, and who owns what |
| `doc-03` | 2026-04-21 | **architecture** — 7 components and 7 dependencies |
| `doc-04` | 2026-04-28 | **customers and market** — 2 customers, 1 customer segment |
| `doc-05` | 2026-05-05 | **cross-links** — which team builds which component, which product each customer runs |

**4,295 characters ≈ 1,074 tokens** (5 documents, 30 turns). The declared structure is machine-readable
in the fixture itself (`_declared_structure`), so every number below is compared against the corpus,
not against a memory of it: 28 named elements, 5 containment pairs, 4 membership pairs, 4 role pairs,
4 ownership pairs, 7 dependency pairs, 2 serves pairs.

No decisions and no contradictions — this corpus asks the different question *what is the business
made of*.

---

## 2. Ingest — what the product reported, and what it cost

Isolation confirmed **before** the first write (`tortoise_graph_overview`):

```json
"counts": {"Point": 0, "Event": 0, "Subject": 0, "Object": 0, "Source": 0},
"total_entities": 0
```

After five captures:

```json
"taxonomy":  {"Point": 86, "Event": 10, "Subject": 11, "Object": 24, "Source": 5}
"structure": {"operators": 5, "total": 81}
"pointkinds": [{"kind": "statement", "count": 51}, {"kind": "event", "count": 30}]
"sources":   5 agentSession sources, 9–12 points each
```

The graph installs its own pack set (starter): `agent-ops`, `dev`, `portfolio`, `marketing`,
`product-strategy`, `pm`, `venture` — recorded in `out/ingest-receipt.json` → `installed_packs`.

| doc | ack (s) | LLM calls | prompt tok | completion tok | points | relations written |
|---|---|---|---|---|---|---|
| doc-01 | 17.9 | 3 | 25,328 | 3,672 | 12 | 7 (5 `hasPart`, 2 `IMPL`) |
| doc-02 | 14.6 | 4 | 26,079 | 2,992 | 11 | 12 (8 `related`, 4 `ownedBy`) |
| doc-03 | 16.1 | 4 | 26,345 | 3,512 | 9 | 7 (`dependsOn`) |
| doc-04 | 13.1 | 4 | 25,496 | 2,366 | 9 | 3 written · **2 dropped** (`transacts`) |
| doc-05 | 20.0 | 4 | 27,556 | 4,595 | 9 | 17 |
| **total** | **16.3 avg** | **19** | **130,804** | **17,137** | 50 | **46** |

### The one token budget

| | |
|---|---|
| extraction tokens | **147,941** (130,804 prompt + 17,137 completion) over **19** model calls |
| corpus | 4,295 chars ≈ 1,074 tokens |
| **amplification** | **≈138×** |
| cost | **not reported** — `cost_usd: 0.0` beside `calls_without_cost: 19` on every call, exactly as in #14606 |

Doc-01 also records a `graph_ops.total` of `1,177` (the receipt's own field; the
five captures record `1,177 / 684 / 880 / 728 / 853`); the amplification, not the
wall clock, is the unit economics.

---

## 3. Read-back — the five assertions, each with a number

All numbers below are **derived** by `score_t6.py` from `out/ingest-receipt.json` +
`out/readback.json` (nothing is hand-counted); the full working is in `out/t6-score.json`.

### A1 · Every declared structure appears, with the right entity type — **PASS (28/28)**

| role | declared | present | right kind | kind they arrive as |
|---|---|---|---|---|
| product | 2 | 2 | 2 | `product-strategy:product` |
| feature | 5 | 5 | 5 | `product-strategy:feature` |
| team | 3 | 3 | 3 | `core:team` |
| role | 4 | 4 | 4 | `core:role` |
| person | 4 | 4 | 4 | `core:naturalPerson` |
| component | 7 | 7 | 7* | `dev:code` — all seven |
| customer | 2 | 2 | 2 | `product-strategy:customer` |
| customerSegment | 1 | 1 | 1 | `product-strategy:customerSegment` |

**This is the strongest part of the result.** Nothing is flattened into statements: products are
`product-strategy:product`, features are `product-strategy:feature`, teams are `core:team`, roles are
`core:role`, people are `core:naturalPerson`. The full materialised kind population is 24 Objects and
11 Subjects — and 11 Subjects is exactly 3 teams + 4 roles + 4 people.

**\* The component type is a caveat, not a clean pass.** No shipped pack declares a `component` kind —
the nearest declared concepts are `product-strategy:architecture` (an Object kind, `subclassOf:
Document`) and `dev`'s `software`/`infrastructure`/`api`/`database`/`code`. The extractor chose
**`dev:code` for all seven**, whose own kindDef is *"Source code that implements features"*. A runtime
service is not source code. Measured extras in the type axis — **precision, not traceability**. ⛔ Read the table below as
over-typing evidence ONLY. It is not A4's evidence, and **two of its five rows are the bundle's
own untraceable names**: `the seven-component dependency graph` and `ownership accountability
convention` do not appear in the corpus text, which is why **2 of the 35 entity names the
extractor created are untraceable** (A4's own count in §3, and the figure
`VERIFICATION-independent.md:32` calls "the two untraceable names"). The other three rows are
stated in the text and are over-typed anyway; an earlier revision of this paragraph claimed
traceability for all of them and a later one claimed it for "every row", so neither should be
restored:

| node | typed as | why it is over-typed |
|---|---|---|
| `control-room customers` | `customerSegment` | a descriptor of who runs the product, not a segment |
| `field crews` | `customerSegment` | same |
| `mid-market utilities` **and** `mid-market utilities segment` | `customerSegment` ×2 | one segment, **two nodes** — an entity-resolution miss (#12047) |
| `the seven-component dependency graph` | `dev:architectureDoc` | the doc's own opening sentence became a Document |
| `ownership accountability convention` | `agent-ops:rule` | a sentence *about* what ownership means became a rule Object |

### A2 · Containment/ownership edges present and correctly ORIENTED — **FAIL**

| class | present | oriented correctly | verdict |
|---|---|---|---|
| product → feature (`hasPart`) | **5/5** | 5/5 (parent end is the product) | ✅ |
| person → team (`related`) | **4/4** | n/a — `related` records **no** direction or meaning | ⚠️ |
| person → role (`related`) | **4/4** | n/a — same | ⚠️ |
| **team → role** | **0** | — | ❌ **the class does not exist** |
| thing → owner (`ownedBy`) | **4/4** (6 rows) | **0/6 — every row INVERTED** | ❌ |
| dependent → dependency (`dependsOn`) | **7/7** | **7/7** | ✅ |
| team → component (`produces`) | 7/7 | **0/7 — every row INVERTED** | ❌ |
| customer → product | 2/2 | carried only as `related` — the `transacts` rows were **dropped** | ⚠️ |

**One line that counts it.** Of the **22 asymmetric structural rows** this run wrote (`hasPart` is
symmetric and `related` has no direction), **7 assert what the corpus says and 15 assert the reverse**
— **13 distinct pairs**, because 6 `ownedBy` rows are only 4 pairs. All seven correctly-aimed rows
are `dependsOn`; the other fifteen — `ownedBy` (6 rows), `managedBy` (2) and `produces` (7) — assert
the reverse.

⛔ This paragraph sat INSIDE the table until the second review cycle, which closed the table after
row 5 and rendered the last three rows (including the `produces` **0/7 INVERTED** one) as visible
pipe text. `markdownlint` cannot see that — a headerless pipe block is a paragraph, not a table, so
MD055/MD056 never fire — which is why the required `docs` leg stayed green over a broken evidence
table. It is below the table now, and the new test pins one row from each half.

**The mechanism, for each failure.**

1. **`ownedBy` is written in the word order the sentence was stated in — which is the reverse of the
   predicate's declared sense.** §3.6 rules `ownedBy` `->` as *"the TARGET owns the source (`investment
   --ownedBy--> fund`)"*. Every row this run wrote has the **person as `src` and the owned thing as
   `dst`**:

   | reported row | src kind | dst kind | declared sense of `src --ownedBy--> dst` |
   |---|---|---|---|
   | `Amara Osei → Lumen Core` | `core:naturalPerson` | `product-strategy:product` | *Lumen Core owns Amara Osei* |
   | `Dana Whitlock → ingest-gateway component` | `core:naturalPerson` | `dev:code` | *the component owns Dana Whitlock* |
   | `Tomas Ferreira → on-call rotation` | `core:naturalPerson` | `core:workflow` | *the rotation owns Tomas Ferreira* |
   | `Jonas Bauer → enterprise renewals` (×2) | `core:naturalPerson` | `core:workflow` | *the thing owns Jonas Bauer* |
   | `Amara Osei → Lumen Core` (doc-05 rewrite) | `core:naturalPerson` | `product-strategy:product` | same |

   This is **#14606's G2 defect, unchanged and now measured on a product-ownership edge** — the very
   case an owner would query first. The writer passes the model's endpoints through unchanged
   (`relation_write.py` calls `sdk.link_entities(edge.predicate, edge.src_id, edge.dst_id)` with no
   re-ordering, and holds no `ownedBy`-specific branch); nothing canonicalises to the declared sense.

   **The product's own ownership door proves it from the graph side.** `tortoise_get_entity(id,
   type="governance")` dispatches to `sdk._get_owned_entities`, whose query is
   `MATCH (s)<-[:ownedBy]-(e)` (`tortoise/sdk.py:62762-62782`) — the **declared**
   orientation. Asked about all four owners the corpus names, it returns **`[]` for every one**
   (`out/readback.json` → `link_witness.*.governance_owned_by`), on a graph holding six `ownedBy`
   rows. So the inversion is not a convention violation a reader could notice — **it makes ownership
   unreadable through the one door the product provides for it.** Finding **D6**.

2. **`produces` is inverted by the same mechanism, in the opposite direction.** §8 puts `produces` at
   `<-` — *"the TARGET brought the source into existence"* — so `A --produces--> B` asserts that **B
   produced A**. All seven rows are `team --produces--> component`, which asserts the **component
   produced the team**. One mechanism, two predicates: *the write path never reconciles the sentence's
   word order with the predicate's declared direction.*

3. **`managedBy` is inverted too** (2 rows): `Jonas Bauer --managedBy--> Northwind Logistics`,
   while §3.6 types `managedBy` as `Entity → Subject` — the **target** is the responsible party, so
   the row says the customer manages Jonas.

4. **Membership and role assignment survived only as `related`.** `memberOf` and `holdsRole` are the
   canonical predicates (§3.6) and neither was used; the model emitted `related`, whose declared
   semantics is `-` ("no confidence transfer") and which declares **no endpoint kinds**. The edges
   exist and point the right way (member→team, person→role), but they carry no meaning a reader can
   act on: `related` between a person and a team and `related` between a customer and a market segment
   are the same edge type.

5. **`team → role` containment is absent entirely (0 edges).** The team and its role are connected
   only *through the person* (`person --related--> team`, `person --related--> role`). So "the Product
   team contains the Product Manager role" is not representable in the graph as built.

6. **The customer→product link lost its predicate.** doc-04 stated `Northwind Logistics transacts
   Lumen Core`, and the product **dropped it, naming the reason**:

   ```text
   narrative relation not written (#11237): transacts 'Northwind Logistics' → 'Lumen Core'
   — `transacts` binds no declared write path (relation_write_paths() -> ()) — it is neither in
     the structural writer's predicate set nor bound to a mechanism, so nothing is written
   ```

   doc-05 re-stated it, and it landed as `related` — a predicate with no meaning. So "which product
   does this customer run" is answerable **only** by reading the sentence, not by following an edge.

### A3 · Hierarchy is not collapsed — **PASS (5/5 distinct pairs, 2 parents, 5 children)**

The product→feature tree survives intact: `hasPart` connects exactly `{Lumen Core, Lumen Field}` to
exactly `{Grid Map, Outage Alerts, Load Forecast, Crew Dispatch, Asset Register}`, with the declared
parent as `src` on every row. Not a flat list.

⚠️ **8 `hasPart` write rows for 5 distinct pairs** — doc-05 rewrote three of doc-01's containments.
Whether the graph holds 5 edges or 8 is **not measurable over MCP**: `explore_connections` returns
each neighbour once (a BFS visited-set), so a duplicate edge is invisible by construction. That is a
read gap, not a duplicate-edge verdict (T1's idempotency test is what measures it; out of scope here).

### A4 · No invented structure — **PASS (structure) / FAIL (the claim layer above it)**

**As the issue words it** (*"every product/team/role/component traces to text; nothing inferred from
naming alone"*): **0 of 28 declared structure elements untraceable**, and **0 of 46 relations are
unstated** (every edge's two endpoints are named in the same corpus document; 8 of 46 are not
co-stated within a single *sentence*, which is the stricter number and is recorded too).

The extractor also **refused to invent a value type** it could not anchor, exactly as in #14606:
`value-bearing point 'We sell to mid-market utilities — regional electricity distributors…' resolved
to turn 3 by the token-overlap band, not a verbatim anchor`.

**But the layer hanging off the structure invents, and that must be said in the same breath.** Of the
50 `quote` spans the product recorded as the source of a claim:

| | count |
|---|---|
| verbatim (a real substring of the corpus) | **11** |
| paraphrase (≥60% of the quote's content words in one corpus sentence) | **27** |
| summary — the same share only across ONE corpus turn (a multi-fact span the single-sentence rule cannot see) | **3** |
| **no ≥60% support in any corpus turn** — the corpus does not state it | **9** |

⛔ **The unit of support is a TURN, not a sentence, and the difference is a number.** 12 of the 50
fall below the *single-sentence* band, but **3 of those 12 are faithful multi-fact summaries the
corpus does state** — doc-02's three teams, doc-03's seven-component architecture, doc-04's
enterprise-renewals row — so a sentence-scoped count is not an invented count, and any gloss reading
*"the corpus never says it"* off that 12 would assert a property the measure cannot establish. The
sentence-scope figure is `below_sentence_band` (**12**, the population `out/t6-quote-audit.json`
counts as `not_in_corpus`); the **invented** count is **9**, and it is the one this report carries.
The turn unit is bounded on purpose: support cannot be assembled across unrelated paragraphs, so a
recombined claim still scores low and the FAIL direction is unchanged.

⛔ **The overlap histogram beneath that band is persisted, not retyped.** `claim_layer.bands`
carries it as `11 / 1 / 27` over the non-verbatim quotes (`below_0_5` / `from_0_5_to_0_6` /
`at_or_above_0_6`), so **11 fall below 0.5**; of the **12** at `< 0.6`, 9 of those are
unsupported by any corpus turn — the invented count, not the sentence-scope one.

⛔ **And the search SPAN is corpus-wide, which the receipt now states.** A row's `doc` is the
document the extractor attributed it to; the turn band is measured against every corpus turn. Exactly
**one** of the 30 supported rows clears the band only through a different document: doc-04's
`Enterprise renewals, owned by Jonas Bauer …` scores **0.571** inside doc-04 and **0.714** on a
doc-02 turn, and `claim_layer.supported_outside_own_doc` is **1** (the row is carried in
`supported_outside_own_doc_examples`). Doc-scoping the band instead would re-grade a fact the
business does state as *invented*, so the scope stays corpus-wide and is **recorded per row**
(`best_own_doc_turn_overlap`) rather than left for a reader to infer.

Examples of the invented 9 (`out/t6-score.json` → `claim_layer.invented_examples`):

```text
"The session confirmed there is no shared feature surface between the two: every feature
 belongs to exactly one product."
"the two products are distinct delivery surfaces serving distinct users (operators vs. field
 crews), not a single platform with a mobile skin."
"Any future proposal to share or duplicate a feature across the boundary would be a change to
 this baseline, not a continuation of it."
```

None of those sentences is in the corpus; they are the S4 gap-review synthesising meta-claims and
attributing them to a `quote`. The product **half-detects** this itself — `26` of 36 warnings are
`has no resolvable source turn (no quote match)` — but `quote` is still populated on the
self-invented spans, so a reader who trusts the field is trusting a paraphrase. This is a **claim-
layer** finding (the T1–T5 axis), recorded separately from A4's structure verdict so the two are
never conflated.

### A5 · Retrieval by structure — **PARTIAL: 5/5 by text, 0/5 by structure**

| question | expected | answered by search TEXT | best rank | did the walk land on an ENTITY? |
|---|---|---|---|---|
| what features belong to Lumen Core | Grid Map, Outage Alerts, Load Forecast | ✅ all three | 2 | ❌ |
| who is on the Platform team | Dana Whitlock, Tomas Ferreira | ✅ both | 1, 2 | ❌ |
| what depends on the fact-store | event-bus, signal-engine | ✅ both | 3, 1 | ❌ |
| which product does Northwind Logistics run | Lumen Core | ✅ | 1 | ❌ |
| which team builds the signal-engine | Platform team | ✅ | 4 | ❌ |

Every question gets a **correct answer**, and every one of them is answered by a **sentence that
happens to contain the words** — not by walking the structure. `tortoise_explore_connections(query=…)`
resolved **all five** structure questions to a **Point** (`[user] And the features of each?`,
`Tomas Ferreira is the Site Reliability Engineer on the Platform team`, …), never to the product,
team or component the question names. So the answer to "features of product X" is a stored sentence
that lists them — the tree in A3 is real, but no door reaches it from a question.

That is the "pile of notes" failure at retrieval time: the graph can *state* the structure and cannot
*traverse* it.

---

## 4. Door findings — what the product could not answer, and where the answer lives

Every one of these was measured through MCP; none was worked around.

| id | question | answer over MCP | evidence |
|---|---|---|---|
| **D1** | is this structural edge oriented `src → dst`? | **NO — the edge and its type are visible, its direction is not.** | `explore_connections` UNIONs `(n)-[r]->(m)` with `(n)<-[r]-(m)` and keeps `type(r)` only (`tortoise/navigation.py:172-179`). The one read that reports direction, `entity_reads.entity_relations`, is behind `GET /v1/entities/{id}/relations` and has **no MCP/SDK door** (`tortoise/entity_reads.py:533-559`). |
| **D4** | does `expand_relationships` report the STRUCTURAL edges of an entity, the way it reports the epistemic ones? | **NO — it returns `[]` for a node that has edges, so a structurally-connected entity reads as isolated.** | Lumen Core: 1 row (its `IMPL` to `control-room customers`), 19 real neighbours. Amara Osei / Platform team / event-bus: **0 rows** each, while `explore_connections` sees 6 / 10 / 10. The read covers `IMPL\|NAND\|hasPart` only (`entity_reads.py:551-559`) — and `hasPart` only where it is an operator/point edge. |
| **D3** | does the full-scan door accept the **bare** kind the packs declare? | **NO — a bare kind returns an EMPTY list with no error while the namespaced spelling returns rows.** | `search_knowledge(kind="product", entity_type="object")` → `[]`; `kind="product-strategy:product"` → 2 rows. Same for `team`/`core:team`. A caller who reads `objectKinds: [product]` in a manifest and asks for it is told *"there is nothing"* — an answer, not a refusal (AX: a surface must not answer where it cannot know). |
| **D5** | does `graph_overview(section='topics', entity_id=…)` resolve an entity that `get_entity` and `explore_connections` both resolve? | **NO — it answers 'Entity … not found' for 7 of the 7 nodes asked about, including nodes those two doors return.** | Lumen Core, Amara Osei, Platform team, event-bus — all four resolve through `get_entity` and walk through `explore_connections`. |
| **D6** | does the product's OWNERSHIP door — `tortoise_get_entity(id, type='governance')` — return what each person owns? | **NO — it returns `[]` for all 4 owners asked, on a graph holding 6 `ownedBy` rows.** | `tortoise_get_entity(id, type="governance")` → `_get_owned_entities` reads `MATCH (s)<-[:ownedBy]-(e)` (`sdk.py:62762-62782`), the declared orientation; the written rows point the other way, so nothing points *into* a Subject. |
| **D2** | can a declared structure element be resolved by NAME through the MCP doors? | **NO for 2 of 32** | `out/readback.json` → `unresolved_elements` (2 rows) and `elements` (32 walks, `root_kind`); the *retrieval* half is a different artifact — `out/t6-score.json` → `A5_retrieval.probes[].walk_root_is_entity`, false on 5/5. ⚠️ The **walked** population is 32, **not** the 28 declared elements §1 names: `run_readback.ALL_ELEMENTS` is the 28 declared names plus every `ownedThing` in `_declared_structure.ownership`, and the 32 rows carry `Lumen Core` and `ingest-gateway` a second time each. Of those 32, 2 do not resolve — `explore_connections(query=…)` resolves names to **Points**, and `ingest-gateway`'s stored name is `ingest-gateway component` (from the sentence *"owns the ingest-gateway component"*). ⚠️ An earlier revision wrote `elements[].walk_root_kind`, a field that exists in neither file. Re-derived by `score_t6._door_findings`, so the artifact cannot state a count its own rows deny. |

⛔ **THE IDs ABOVE ARE THE ARTIFACT'S OWN** (`out/t6-score.json` → `door_findings`,
emitted by `score_t6.py`). An earlier revision of this section had D2/D4/D5 permuted
against that file — D2 was labelled the expand door (the artifact's **D4**), D4 the
overview door (**D5**) and D5 name resolution (**D2**) — so a reader cross-referencing
"D2" between this report and `out/t6-score.json` (or `VERIFICATION-independent.md:44`,
which cites the artifact's D2 for name resolution) landed on two different findings.
Renumbered to the emitter, and the new test now pins each id to its ARTIFACT QUESTION
rather than to the bare token, which is what let the permutation pass unnoticed.

**Three of these compound into the finding that matters most.** D1 (no direction on read) + D4
(the expand door silent about structural edges) + D5 (the overview door not resolving the entity) + D6
(the ownership door answering nothing) mean **an
agent cannot discover the `ownedBy` inversion through the product**. Every read it is offered says
either "no edges" or "not found". That is precisely how #14606's inversion survived its own
read-back: the graph was wrong and the reads said "nothing to see".

---

## 5. What a reader could and could not describe from the graph alone

**Could:** the inventory. *Two products, five features, which feature belongs to which product; three
teams; four roles and who holds each; four people; seven components and what depends on what; two
customers and the segment they sit in.* All of it present, correctly typed (components aside), and
correctly oriented for composition and dependency.

**Could not:** the wiring of people to things. *Who owns which product or component* — stored
backwards, and unreadable in either direction through MCP. *Which team a role belongs to* — absent.
*What relationship a person has to their team or role* — present as `related`, indistinguishable from
"customer relates to market segment". *Which product a customer runs* — only as `related`, after the
`transacts` row was dropped.

---

## 6. Constraints of this run, stated so the numbers are not over-read

1. **Retrieval is FTS-only.** The worktree venv could not be built with the `embeddings` extra
   (`uv sync --offline` fails on `nvidia-cublas`/`jaxlib`; the base `uv sync --no-dev` succeeded), so
   every result carries `scores.vector: null`. The rankings in A5 are a **floor**, exactly as in
   #14606 §7 — and they were still sufficient to return every expected fact.
2. **One corpus, one run, one model** (`deepseek-v4-flash` via `deepseek-direct`). No idempotency
   re-run: A3's duplicate `hasPart` write rows are recorded but not resolved here.
3. **The orientation verdict rests on the write receipt plus `get_entity`**, because no MCP read
   reports structural direction (D1). It is therefore a statement about *what the product says it
   wrote*, corroborated by an independent depth-2 probe (§7) that reached the same rows from the
   graph.
4. **`check_structure` returned `[]`** on a graph holding 15 inverted relation rows (13 distinct
   pairs) — the structural validator sees no problem, which is consistent with #14606's G1
   observation. The figure is the artifact's own (`A2_edges.totals`); an earlier revision said "12
   … classes", which matched no field in the bundle.

---

## 7. Independent corroboration (a second lane, on the same worktree)

While this run was being assembled, a **second lane** independently probed the same graph over MCP and
committed its own probe and write-up into this bundle:

| path | what it is |
|---|---|
| `verify_independent.py` | its probe — depth-2 `tortoise_recall(mode="subgraph")` per declared name, endpoint types and edge directions read from the returned `{nodes, edges}`, every id in the ingest receipt resolved with `tortoise_get_entity`. ⚠️ **A re-runnable RE-MEASUREMENT in its own report format — NOT the producer of the committed artifact** (that file's `measured_by` names the peer's `/tmp` probes; the two report shapes are disjoint beyond `graph`). §8 records the same split |
| `out/t6-independent-verification.json` | its raw measurement |
| `VERIFICATION-independent.md` | its write-up (**not** this report — the primary record) |
| `out/t6-quote-audit.json` | its quote-fidelity audit |

It reaches the same numbers from a different read: the same 5 `hasPart` and 7 `dependsOn` **pairs**,
and the same **4** `ownedBy` pairs (the receipt's 6 write rows reduce to those 4 — `ownedBy 0/6` is
the RECEIPT's row count, `4` is the pair count both reads land on); the same `dev:code` typing for all seven components; the same duplicate
`mid-market utilities` / `mid-market utilities segment` nodes; the same `expand_relationships`
silence on structural edges; `tortoise_list_topics` answering `Entity … not found` for nodes two
other doors resolve; and the same quote audit — whose own buckets are 11 verbatim / 27 paraphrase /
12 `not_in_corpus`, i.e. the same **12** below the single-sentence band — which this report's
TURN-scope decider splits into **11 verbatim / 27 paraphrase / 9 unstated in any turn** plus the
**3** faithful multi-fact summaries of the corpus the single-sentence rule cannot see
(`claim_layer.summary_in_one_turn`; see §3/A4). ⛔ **The 9 is THIS report's count, not a field of
that audit.** The audit measures the SENTENCE-scope population (12); the decider measures the TURN
and splits that same 12 into 3 the corpus does state and 9 it does not, so the two reads describe
one population at two scopes rather than agreeing on a number by coincidence. The decider's four
buckets partition its `quotes_total` (11 + 27 + 3 + 9 = 50) and the audit's three partition its own
(11 + 27 + 12 = 50); ⛔ **an earlier revision of this sentence named 11 / 27 / 9 as *the audit's*
buckets**, which is the defect this paragraph now fixes — the ATTRIBUTION was wrong, not the
arithmetic, and a reader comparing sums alone would have learnt the wrong lesson from it. Its count
of inverted
pairs — **13** — is the number this report's A2 line derives independently.

**Its strongest finding was not in my first cut, and I re-measured it before adopting it**: the
`type="governance"` ownership door returns `[]` for every owner (§3/A2, D6). That claim cited
`sdk.py:62762` and I read the source, then asked the door in this bundle's own readback — four owners,
four empty answers — rather than take the finding on trust.

⚠️ **Two lanes were writing into one evidence directory and one git branch at the same time.** Its
commits (`8bfd61a`…`4732c77`) include my working tree — the peer committed my files as they stood,
then deferred to `REPORT.md` as the primary record when it found it. **Nothing was discarded** (the
rule that a lane adopts rather than resets uncommitted work applies to a peer's files too), and this
section exists because a lane that deletes a peer's artifact destroys the only copy of an independent
measurement. It is also the process fact worth carrying back: the pre-dispatch collision check passed
(`CLAIM: unclaimed for T6`, no branch, no WIP) and still two lanes converged on one worktree.

---

## 8. Artifacts

| path | contents | producer |
|---|---|---|
| `corpus/structure-corpus.json` | the corpus (5 transcripts, 4,295 chars) + its machine-readable `_declared_structure` | authored for this run |
| `run.sh` | the invocation; both inputs caller-supplied, no box-bound path, no credential store; the LLM key is required **only** by `run_ingest.py` | — |
| `run_ingest.py` | step 1 — the five captures, SDK-write only; writes `out/ingest-receipt.json` | — |
| `run_readback.py` | step 2 — MCP-read only: kind scans, per-element edge walks, link witness, 5 structure probes, `check_structure`; writes `out/readback.json` | — |
| `score_t6.py` | step 3 — derives every number in §3 from the two receipts; writes `out/t6-score.json` | — |
| `out/ingest-receipt.json` | per-document full result envelope: `relation_write.edges`, `warnings[]`, `stats.llm` tokens | `run_ingest.py` |
| `out/readback.json` | verbatim MCP replies (809,812 bytes · 791 KB): scans, 32 element walks, endpoint reads, link witness, probes | `run_readback.py` |
| `out/t6-score.json` | the five assertions with their numbers, evidence rows and verdicts | `score_t6.py` |
| `out/tool-surface.json` | the live MCP surface (39 tools) with each tool's own `inputSchema` | `run_readback.py` |
| `verify_independent.py` | a second lane's independent MCP probe (depth-2 subgraph reads) | **not this bundle's producer** — §7 |
| `VERIFICATION-independent.md` | that lane's write-up — not the primary record | **not this bundle's producer** — §7 |
| `out/t6-independent-verification.json` | its raw measurement | **not this bundle's producer** — §7 |
| `out/t6-quote-audit.json` | its quote-fidelity audit (11 verbatim / 27 paraphrase / 12 not in corpus) | **not this bundle's producer** — §7 |
| `out/events.jsonl` (**not committed**) | the SDK event log, scratch, reproducible via `run.sh` | `run_ingest.py` |

⚠️ **`out/readback.json`'s provenance binding, stated exactly.** That artifact carries
`provenance.probe_sha256`, the hash of the `run_readback.py` that produced it. It was produced at
`d4fc8fe51ee5`, where that file hashed
`5a441859d7f953a91d493d9606b8c8404018a40eac35b658a75e4fbe6200f192`; the file in this tree hashes
`276be41328cf4df8cbbfe3fd1b941a68d114d10cd7e06b6a08ceec6a8a57725d`, because four edits landed after it: a later commit renamed
one unused loop variable (`element_kind` → `_element_kind`) to clear a `B007` finding on the
`agent-infra-ci / lint` leg — **not a required context** (`main`'s protection, the active
ruleset and `.mergify.yml` require `python-ci-gate`, `docs`, `pricing-artifact`,
`test-isolation`, `license-surface`, `legal-e2e` and `ai-review-gate`; `agent-infra-ci` is in
`python-ci-gate`'s `needs:` in no spelling), but the repo keeps `ruff check .` at a zero
baseline for new code, so the rename was taken rather than left as debt. **The rename is inert** — mechanically verified by renaming
it back and diffing against `d4fc8fe`: zero differences. **The other two edits are the
error-vs-empty guards this review added** — `_call()` now records the MCP `isError` flag instead of
discarding it, and the D4 emitter refuses a witness whose call FAILED, asking the scorer's
`call_failed` — the whole call, envelope AND reply — rather than keeping a second, narrower
spelling of "the call failed" — so a future
run cannot manufacture D4 from a call that never answered. Both are inert to the numbers in the committed
artifact (they change what a future run would refuse, not what this one measured), and the sweep
that measured them is §8.7. **The fourth edit is comment-only and it removes a claim this tree
denied** (#14723's rebase repair): the D6 comment cited the ownership read by LINE RANGE,
and `origin/main`'s creation hook inserted lines into `tortoise/sdk.py` above that method, so the
range no longer opened on the `def` — the class of defect this bundle's own citation arms exist to
catch, found by them rather than by a reader. It now cites the SYMBOL and names no line: a symbol is
what a reader can resolve, and a line range beside it is exactly what moved. The artifact's numbers
stand and only the binding is stale. It is stated here rather than silently regenerated, because regenerating a
791 KB receipt would rewrite every number downstream of it to fix a comment-sized defect.
`tests/test_14691_structure_evidence.py` pins the binding against that stated exception, so a future
drift reds instead of going unnoticed (which is what let this one through the first review).

### 8.1 What the falsifier suite actually pins (5th review cycle)

The 5th review cycle found one class, seven times over: the suite pinned what the *test* believed and
never the edges between the committed artifacts, the prose that quotes them, and the inputs they are
derived from. Measured mutants that stayed **green** before this cycle — each now reds, and each arm
names its mutant in its own docstring:

| mutant (all measured green before) | arm that now reds on it |
|---|---|
| `out/t6-independent-verification.json`'s `A2` replaced with a hand-written note, `declared` emptied | `test_the_orphan_second_measurement_agrees_with_the_primary` |
| `out/t6-quote-audit.json` `verbatim 11→50`, `not_in_corpus 12→0` | `test_the_quote_audit_artifact_is_re_derived_from_the_receipt` |
| `REPORT.md` §7 `(11 / 27 / 12)` → `(50 / 0 / 0)` | `test_the_quote_breakdown_in_the_report_is_the_claim_layer` |
| a door finding's **answer** flipped `No → YES` | `test_each_door_finding_answer_states_the_artifacts_ANSWER` |
| A1/A3 heading **counts** rewritten (`28/28` → `35/35`, `5/2/5` → `99/1/42`) | `test_report_headline_counts_are_the_artifacts_counts` |
| `directions_source: UNAVAILABLE`, `directions: {}` | `test_the_directions_table_is_the_ruling_table` |
| `empty_before` carrying `total_entities: 500` / `node_count: 900` / a real `pointkinds` list | `test_the_isolation_refusal_is_enforced_on_the_whole_overview` |
| an `{"error": …}` witness reply read as an answered-empty one (it manufactured D4) | `test_an_ERROR_reply_is_not_an_EMPTY_answer` |
| an unreadable `src_kind` graded `correct`, moving `asymmetric_rows_correct` 7 → 8 | `test_an_UNREADABLE_kind_is_not_graded_correct` |
| the independent measurement's `A2` rows **`src`/`dst`-swapped** (an unordered pair set could not see it) | `test_the_orphan_second_measurement_agrees_with_the_primary` |
| `t6-score.json`'s own `A4_no_invention.quotes` block set to `50 / 0 / 0` | `test_the_committed_score_block_is_the_derivation` |
| a **fabricated** row in `t6-quote-audit.json`'s `audited[]` | `test_the_quote_audit_artifact_is_re_derived_from_the_receipt` |
| a door answer led by "No" and then reversed in the same cell | `test_each_door_finding_answer_states_the_artifacts_ANSWER` |
| the fresh-graph refusal's **`if`** deleted (the predicate still measured clean) | `test_the_refusal_FIRES_on_the_run_path` |
| the ownership probe's `call_failed` guard deleted (a failure with `reply: []` counted as an answer) | `test_an_ERROR_reply_is_not_an_EMPTY_answer` |
| both `bare_kind_probes` rewritten to error envelopes, D3 still reported | `test_an_ERROR_reply_is_not_an_EMPTY_answer` |

Three content defects were corrected rather than re-pinned:

- **`not_in_corpus` was the wrong bucket, and it truncated — and then its NAME was the wrong claim.**
  In `t6-score.json` it was a 10-row list of the **best-anchored paraphrases** (overlaps 0.636–1.0) —
  the opposite population — so a consumer reading its length as "quotes the corpus never states" got
  the display cap, `10`, which is the figure the PR description carried. The 5th cycle made it a
  **count**; the 10th cycle found that the count's NAME still asserted the same thing (3 of its 12
  rows the corpus does state), so it is now `below_sentence_band` — named for the scope it measures —
  and `invented` carries the finding. The paraphrase rows live under `paraphrase_examples`, and each
  capped list carries its OWN `*_examples_truncated` flag (one shared boolean could not say which
  sample was complete).
- **The quote band's unit of support was one SENTENCE, which graded faithful multi-fact summaries as
  inventions** (10th review, P2). `Three teams exist: Platform (engineering), Product (product
  management), and Customer (commercial).` scores 0.30 against a single sentence and 0.80 against its
  own turn, because a summary cannot put ≥60% of its words into one sentence — so the old table read
  "**invented** (<60% overlap — the corpus never says it) | **12**", calling 2 of those 12 (and a
  third, doc-03's component list) things the corpus does not say. The decider is now the **turn**:
  **9** invented, **3** summaries, with the sentence-scope population kept as `below_sentence_band`
  (`12`) and the peer audit's own count. The FAIL direction is unchanged — 9 is still not 0.
- **D6's owner set and its `ownedBy` figure were typed by hand** (10th review, P3): the witness set
  `{"Amara Osei", …}` was a literal, so a run whose witnesses named anyone else would have made
  `asked_owners` empty and silently WITHDRAWN the bundle's headline finding. Both are now derived —
  the owners from the corpus's declared `people`, the row count from the write receipt's `ownedBy`
  edges — and the row count is emitted as `evidence.owned_by_rows`.
- **An unclassifiable `produces` row was graded `correct`** because the check was
  `"INVERTED" if src_kind_is_an_agent else "correct"`. It is now its own
  `UNCLASSIFIABLE` bucket, counted in `asymmetric_rows_unclassifiable` and in neither arm.
- **The fresh-graph refusal read two fields, and its enforcement was unpinned.** It is now
  `refuse_if_not_fresh()` — a function the falsifier drives to observe the `SystemExit` itself, not
  just the predicate behind it — over an `occupancy()` walk of the whole `graph_overview()` reply.
  Excluded, each because it is not content: `namespaces` (the installed kind vocabulary),
  `status.connected` (a flag), `stale.limit` (the page size, which the first cut of the walk wrongly
  counted and which would have refused every real run), and `audit.exit_code` (a diagnostic code —
  its content signals `node_count` and `summary.total_issues` are still counted). The exclusion is a
  path SUBTREE match, so a field that later becomes an object cannot silently start refusing runs.
  ⚠️ That last exclusion is a **CONSTRUCTED** case, not a measurement: the receipt's own
  `empty_before` carries `audit.exit_code: 0` and no committed artifact carries `3`, so the "measured"
  wording an earlier revision used was falsified by checking. The falsifier drives the constructed
  case directly.

⚠️ **One residual, stated rather than silently closed:** `run_readback.py`'s own `report["ok"]` still
only inspects three `layers` calls, so it does not see a failed `bare_kind_probes` or
`expand_relationships` call either. That file's bytes are the provenance binding of a committed 791 KB
artifact — it cannot be edited without regenerating a receipt this box cannot reproduce (no graph, no
LLM key) — so the fix was taken on the **consumer** side, where `score_t6` now refuses to read an error
envelope as an answer for D4/D6 and no longer lets D3 outlive its own `bare_kind_probes` — if those
probes did not answer, D3 is withdrawn rather than reported from a failed call. The D3 finding itself
is carried in `readback.json` as measured.

⚠️ **Two lanes measured these cycles in parallel, into this one worktree.** §8.1 and §8.2 are
each one lane's record; they overlap because both lanes found the same class and pinned it
independently, and both sets of arms are kept. Two reconciliations were needed and are stated
rather than hidden: `examples_truncated` became one flag **per list** (a single boolean could not say
which sample was incomplete, so §8.2's arm now checks each bucket's own flag), and the
source-substring wiring pin for the run path was retired in favour of the behavioural arm that drives
`main()` against a stub SDK — a substring pin cannot see an inserted filter, and hard-coding one
refactor's spelling is friction on the next.

### 8.2 What the 6th review cycle added (the artifact's EVIDENCE, and the prose everywhere)

The 5th cycle's lesson — *nothing in the tree opened this file* — was applied to `out/t6-quote-audit.json`
and **not** to the primary artifact, and the prose pins reached two headings out of five. Nine findings
(2 P1, 4 P2, 3 P3) were measured on `43fbd256`; each now reds:

| mutant (all measured green at `43fbd256`) | arm that now reds on it |
|---|---|
| `out/t6-score.json`'s `corpus_chars` / `edge_count` / `warnings` / `entity_names_checked` / every `ownership[].verdicts` / every A5 probe's ranks / `by_doc` tokens rewritten | `test_the_score_artifact_is_what_the_scorer_DERIVES_from_its_inputs` |
| `invented_examples` ↔ `paraphrase_examples` swapped, or both emptied | `test_the_example_BUCKETS_are_the_bands_their_names_claim` |
| D6's answer reversed in substance, leading "No" kept | `test_each_door_finding_ANSWER_is_the_artifacts_answer_in_SUBSTANCE` |
| an unreadable `managedBy` row graded `INVERTED` (`inverted` 15 → 16, pairs 13 → 14) | `test_an_UNREADABLE_kind_is_not_graded_INVERTED_either` |
| the independent artifact's `witness_governance_door_returns_empty` / row counts / `advertised_tools` / `typed_nodes` rewritten | `test_the_second_measurements_CORROBORATION_fields_are_pinned` |
| every `audited` quote row replaced with a sentence the receipt never recorded | `test_every_audited_quote_ROW_is_a_real_quote_of_a_real_document` |
| §2's isolation block (the report claiming 15 entities before the first write), the A5 heading, §3's counts, §7's pair/tool figures | `test_report_figures_across_the_sections_are_the_artifacts_figures` |
| §2's per-document cost row (`doc-04 3 written` → `9`, and the ack column) | `test_the_cost_table_is_the_receipt` (the tables arm did NOT red on this — measured on `df788ab`, which is why it has its own row) |
| `if contamination:` → `if False:`, and an inserted filter making the branch INERT with every substring intact | `test_the_refusal_FIRES_on_the_run_path` (the source-substring arm it replaced was retired — see §8.1's reconciliation note) |

Two content fixes came with it: `managedBy` had `produces`'s two-way bug mirrored (an unreadable
`dst_kind` graded the row `INVERTED`), now `UNCLASSIFIABLE` like its twin; and the PR body's A4 figure ("10 … overlap < 0.5") was corrected **in the body itself, before any
clean attestation existed** — the artifacts say **12** at `< 0.6` in the single-SENTENCE scope
(`below_sentence_band`; 9 of those are unsupported by any corpus turn, which is the invented count),
of which **11 fall below 0.5** — a figure the scorer now
persists as `claim_layer.bands` (`11 / 1 / 27` below-0.5, 0.5–0.6, at-or-above), so it is read
rather than retyped. ⚠️ It was unbacked before that: no artifact carried a 0.5 threshold, and the
only `11` the bundle held was `claim_layer.verbatim`, a disjoint bucket. The A3 figure
("5 `hasPart` parents") was corrected in a PR comment instead, because a record was already on the
body and editing it would have wiped the proof: the artifacts say **2 parents / 5 children**.

⚠️ **One residual, unchanged and stated rather than silently closed:** `run_readback.py`'s own
`report["ok"]` still inspects only three `layers` calls, so it does not see a failed
`bare_kind_probes` / `expand_relationships` call. That file's bytes are the provenance binding of a
committed 791 KB artifact, so the fix is on the CONSUMER side — `score_t6.is_error_reply` /
`call_failed` / `measured_empty`, plus the test-side `_measured()` — where a failed call is no longer
readable as an answered-empty one.

### 8.3 What the 7th review cycle added (every table row and every figure)

The 9 findings of the 6th cycle were verified closed, and the cycle then found eight more of the same
shape: the headline pins reached the A1/A3 *headings* and three A2 cells, while the rest of every
table and most of §2/§3/§7/§8's numbers were free. All eight were measured green on `1de08152`; each
now reds:

| mutant (all measured green at `1de08152`) | arm that now reds on it |
|---|---|
| A2 `\| **team → role** \| **0** \|` → `**7**` (stating a class the report declares ABSENT), the `4/4` cells → `9/4`, `(6 rows)` → `(11 rows)`, `dependsOn`'s correct cell → `14/7`, `customer → product 2/2` → `9/2` | `test_every_TABLE_ROW_in_the_report_is_derived_from_an_artifact` |
| A1 `\| component \| 7 \| 7 \| 7* \|` → `9 \| 9 \| 9*`, `\| team \| 3 …` → `6 …`, an A5 rank `2` → `5` | same |
| §2's after-ingest `taxonomy` / `structure.total` / `pointkinds` / `5 agentSession sources`; every per-doc ack/calls/token cell and the totals row; `0 of 28 …`, `8 of 46 …`, `50 quote spans`, `Examples of the invented 12`, §7's `13`, §8's bytes/KB and `32 element walks`; the receipt's own `ack_seconds` | `test_the_after_ingest_block_cost_table_and_remaining_figures_are_derived` |
| `out/readback.json`'s `unresolved_elements` emptied (D2 keeps saying "2 of 32"), `structure_check` given an error row (§6.4 says it returned `[]`), `tool_surface.count` 39 → 99, `"ok"` → `false` | `test_the_readback_artifact_backs_the_findings_it_is_cited_for` |
| every `out/tool-surface.json` entry's schema replaced with `{"description": "fake", …}` | `test_the_tool_surface_artifact_carries_a_SCHEMA_for_every_tool` |
| an independent `A2.ownedBy` row reversed (so the peer read corroborates "half correct" instead of "0/6 inverted"), `explore_rows: 99`, every `A5.*.search_ok` → false | `test_the_independent_measurements_FINDING_payload_is_pinned_row_by_row` |
| D6's answer reworded to say the door WORKS while keeping its leading "No" | `test_each_door_finding_ANSWER_is_the_artifacts_answer_in_SUBSTANCE` (now full containment, not a six-word key) |

The door-finding ANSWER cells now carry the artifact's `answer_over_mcp` verbatim, so the table cannot
assert a reversal and the arms are exact rather than approximate.

---

---

### 8.4 What the 8th review cycle added (two fail-opens closed, and the ratchet)

The 8th cycle measured two holes in arms this section had already reported as closed. Both were the
class the PR exists to close — a cell or a line stating something the artifact denies — and both are
closed by making the arm EXACT rather than by adding one more pin beside a superset test.

| mutation (each ALONE, `61 passed` before these arms) | arm that now reds |
|---|---|
| D1's answer cell, appended INSIDE the bold cell: *"The direction is in fact fully visible and correct on every edge read."* — the 5th-cycle arm was a PREFIX test, so one cell could state the finding and its negation, and D2/D4 shared both arms | `test_each_door_finding_answer_states_the_artifacts_ANSWER` (now exact on the normalised cell; D2's "walked" qualifier moved to its evidence column, so nothing was dropped) |
| §1 `(1.4 min)` → `(16.3 min)` — a supported number (the MEAN ack) on the wrong line | `test_the_figures_the_8th_review_measured_as_FREE_are_now_pinned` |
| §3 `` `managedBy` is inverted too** (2 rows) `` → `(6 rows)`; §8 `32 element walks` → `39` (the tool-surface count); doc-03 `7 components and 7 dependencies` → `9 components`; VERIFICATION `because 6 ownedBy rows are only 4 pairs` → `4 … 6 pairs`; §1 `Five short meeting transcripts` → `Six` | same arm, each read from the field that produces it and LINE-SCOPED |

The last row is the net arm's own limit, stated plainly: `test_no_FIGURE_in_the_two_documents_is_free`
asserts membership in the UNION of every artifact value, so it reds a number **no** artifact carries
and cannot red a right-looking number in the wrong place. It is unchanged and still does exactly that;
the six figures above moved out of the net and onto their own fields.

The build red this head also carried was the untyped-receiver ratchet: `tools/sdk_privatise.py
--check` reported `11914` against a `11912` baseline, both rows added by this suite's own
`readback.update(saved)` restores. `tools/sdk_privatise.py --check` is a ratchet on receivers the
sweep cannot type, so the restores are spelled `readback |= saved` — `dict.__ior__` mutates in place
and returns self, so the module's dict is restored exactly as before, and `--check` is exit 0 at
`11912`.

### 8.5 What the 9th cycle closed (a section that was CARRIED, not measured)

The 8th cycle's remaining P3 sat one level below the "absent is not empty" class this bundle exists
to close: `refuse_if_not_fresh` required the three `_OVERVIEW_KEYS` to be *present*, not *measured*,
so an overview carrying `"status": None` — or `"status": {}` — beside an empty `taxonomy` and a
`structure` whose only field is `total: 0` was **ACCEPTED**. The real reply's `status` is
`{"connected": true, "counts": {...}, "total_entities": 0}` (`out/ingest-receipt.json` →
`empty_before.status`), so neither shape is legitimate: the section was carried and nobody read it,
and the freshness premise would rest on a field that measured nothing.

`_unmeasured_sections()` now names every required section that is absent, null or empty, and the
refusal acts on that list. The arm `test_a_CARRIED_but_empty_overview_section_is_not_a_MEASUREMENT`
reds on the pre-fix guard — measured, reverting the guard to key-presence fails it while the rest of
the suite stays green — and it still ACCEPTS the receipt's own `empty_before`, so a carried section
is refused without refusing a real run on a fresh graph.

### 8.6 What the round after the 9th closed (ten paths a fresh reviewer re-measured as FREE)

A reviewer at `7bf32ccdb7` re-ran the prior rounds' mutants against this head and
measured ten still green. Each is now pinned by an arm that reds on its own
mutation, and each arm names that mutation:

| Was free | Now pinned by | Reds on |
|---|---|---|
| the verdict line in `VERIFICATION-independent.md` (`A2 FAIL` → `PASS`) | `test_the_VERIFICATION_verdict_line_is_the_score_artifact` | the doc's A1..A5 words vs `t6-score.json.verdicts` |
| the `isError` envelope branch of `is_error_reply` (deleting it) | `test_the_isError_envelope_branch_is_LOAD_BEARING` | the branch's own shape, plus a success envelope that must still ANSWER |
| `node_count` / `edge_count` accepted any digits | `test_the_peer_probe_counts_are_the_exact_measured_numbers` | `38/44` → `3800/4400` |
| the peer artifact's `A3` block (`hasPart_rows_in_receipt`, `children`) | `test_the_peer_A3_block_is_the_receipt_and_the_corpus` | 8 → 13 rows; children 5 → 2 |
| `out/tool-surface.json` schema content | `test_the_tool_surface_SCHEMAS_are_tool_specific` | every `params` → `["junk"]`; blanked descriptions; the 256/30 populations |
| `5 documents, 30 turns` in the peer document | `test_the_VERIFICATION_corpus_row_is_the_receipt` | `30` → `35` (a real number elsewhere) |
| `**8** hasPart rows for **5** pairs` | `test_the_W3_hasPart_counts_are_the_peer_artifact` | `8` → `13` |
| the cached overview taxonomy in `readback.json.layers` | `test_the_readback_LAYERS_taxonomy_is_the_receipts_overview` | `Object: 24` → `84` |
| `graph_ops.total` "in the low thousands" (an adjective) | `test_the_graph_ops_claim_is_the_receipts_five_totals` | now the receipt's own five totals |
| the printed audited-overlap list | `test_the_audited_overlap_list_is_the_audit_artifact` | `0.636` → `0.875` (both real rows) |
| a bare integer read as "a commit sha" | the `_PROSE_ALLOWED` sha pattern | `1234567 sources, 9876543 nodes` in §6 |

### 8.7 What the round after that closed (the fifth-cycle step, swept end to end)

The same reviewer's next pass re-measured §8.6's claims and found two of the twelve
still open, plus six new free paths — all twelve are now closed, and each is
pinned by an arm that reds on its own mutation:

- **plain-text / `list[str]` error replies (P1, was open).** The producer's own
  failure text — `["Error: timeout while expanding relationships"]` — is not JSON,
  so `is_error_reply` returned `False` and the door read as ANSWERED-EMPTY: all
  seven witness replies replaced with that shape left 73 passed, the artifact
  re-derived byte-identically, and **D4 was published from calls that never
  answered**. `is_error_reply` now matches failure text in a bare string and in an
  all-string row list, and the suite's `_measured()` DELEGATES to it instead of
  re-deciding the question with a narrower rule of its own.
- **`readback.json.resolved` (P3, was open).** 35 entries → 1 left 73 passed. Every
  element the artifact resolved must still resolve to the id the element carries;
  the two names in `unresolved_elements` must NOT resolve (that is D2's finding).
- the corroboration table's **verdict and Agree columns** (`✅ agree` →
  `❌ DISAGREE`, `PASS half — 5/5` → `FAIL — 0/5`): pinned to the artifact's totals,
  and the Agree cell derived per row — A4 is the one row that must **not** agree,
  because the peer probe ran no traceability check.
- the peer document's **Graph under test** row → pinned to `t6-score.json.graph`,
  and `live-v5` (the production graph this bundle must never touch) refused.
- `out/tool-surface.json` **schema attribution** — rotating `(params, required)`
  between tools preserves every shape and both populations (256/30), so a digest
  over the name→schema mapping is what reds; the shapes alone did not.
- the peer artifact's **`*_listed` booleans** (`tortoise_recall_listed`,
  `tortoise_get_governance_listed`) → derived from membership in
  `out/tool-surface.json`; flipping them to `True` would delete the product finding
  that both tools EXECUTE while the surface advertises neither.
- the witness rows' **`mechanism`** (`IMPL` → `hasPart`) → rows must stay epistemic,
  and exactly six of seven witnesses must still be isolated (D4).

### 8.8 What the sixth round closed (a prose finding that carried no derivation)

- **P1 — the prose door findings.** D1 and D5 arrive from `readback.json` entirely
  as prose, and the scorer copied their `answer_over_mcp` through unread: reversing
  D1 to *"YES — … its direction is visible too"* left 85-passed-green and
  self-contradicted the `the_read_that_can` field in the same object. A prose
  finding now survives only while its answer's POLARITY matches what its own
  evidence says, and while its evidence is non-empty.
- **P1 — D4 outliving its witnesses.** The append path guarded against errored
  calls; the PROSE D4 did not. With all seven witness replies replaced by failure
  text the finding stayed, its evidence still naming six "isolated" nodes, and the
  artifact re-derived identically. D4 now requires a NON-ERRORED silent witness, and
  the producer agrees: `run_readback._call()` records the MCP `isError` flag instead
  of discarding it, and its D4 emitter refuses a failed witness.
- **P2 — the peer's second copy of the witness rows.** `witness_neighbourhood_doors`
  in `out/t6-independent-verification.json` carries the same rows; `IMPL` → `hasPart`
  there contradicted both documents and was free. Pinned to the same rule and to the
  same row counts (one epistemic row, on `Lumen Core`).
- **P3 — `readback.db_uri`.** Nothing read it: pointing it at
  `live-v5.prod.example:6379/live-v5` left the suite green. Now pinned to the graph
  the score names, on the lane's own host.
- **P3 — `score_t6.py --check` was a NO-OP.** `sys.argv` was never parsed: the
  scorer always rewrote the artifact and exited 0, so the gate the review brief names
  would silently REPAIR a corrupted artifact and report success. It now compares and
  exits 1 without writing — and it refuses to score at all when the ruled direction
  table cannot be imported (the import used to degrade to `{}` under a bare
  `try/except`, which made the comparison depend on the caller's `PYTHONPATH`).

### 8.9 What the seventh round closed (one predicate, one count, one port)

- **P1 — a THIRD error predicate.** The producer's round-6 guard spelled its own
  five-token list while the scorer spelled nine, so the two DISAGREED on 6 of 20
  reply shapes and `run_readback` still wrote D4 for `["Failure: …"]`,
  `["Refused: …"]`, `["Unauthorized"]` — manufacturing the bundle's central
  product finding from a call that failed in a spelling its own guard did not know.
  The producer now ASKS the scorer (`from score_t6 import is_error_reply`), and the
  contract is pinned by BEHAVIOUR over 20 shapes, taken from the file rather than
  restated — its body is unpinned content and a literal would not have held.
- **P3 — D4's evidence counts.** `nodes_read_as_isolated_by_expand` and its
  byte-identical duplicate were read by nothing: rotating one made the artifact
  contradict its own `link_witness` block with the size held at 809,812. Every count
  is now the witness's own `explore_neighbour_count`, the two copies must agree, and
  `code_path` must name a file this tree has.
- **P3 — the db_uri port.** The arm claimed to pin "the lane's own port" and pinned
  none: 16413 → 16414 stayed green. The host:port is now the receipt's own, so the
  readback and the ingest cannot be talking to different containers.

### 8.10 What the eighth round closed (the envelope half, the SET, and the claim in `code_path`)

- **P2 — the envelope half of the producer's gate.** Delegating the `reply` was
  not delegating the CALL: `_call()` reports a failure as
  `{"ok": False, "error": "MCP isError: …"}`, so a gate that only asked about
  `reply` still answered and the producer wrote D4 from an MCP `isError` call.
  Deleting those two lines — with REPORT's documented hash synced — left the suite
  green. It now asks `call_failed` (envelope AND reply), and the behaviour arm
  exercises five envelope shapes as whole calls.
- **P3 — D4's evidence SET.** Each count was pinned but not the key set: rotating a
  key (`Jonas Bauer: 9` → `Lumen Core: 19`) is byte-neutral, keeps the 809,812 size,
  re-derives cleanly, and made the artifact claim a node that ANSWERED one row was
  read as isolated. The key set must now equal the silent witnesses exactly.
- **P3 — the claim inside `code_path`.** The sentence names the predicates the read
  covers; at constant length it could be rewritten to `NAND|IMPL|ownedBy only` and
  stay green. The arrow-list is parsed out of the field, must be exactly
  `IMPL|NAND|hasPart`, and each token must be a predicate the graph actually has.

### 8.11 What the ninth round closed (the citation's line range)

The last survivor of the `code_path` class: the FILENAME and the predicate SET were
pinned, the **line range** was not. `entity_reads.py:551-559` → `:533-541` is the
same length, keeps the artifact at 809,812 bytes, re-derives cleanly, and moves the
citation onto `entity_relations`' def line — the read that returns NO direction,
i.e. exactly the contrast the field exists to draw. The cited lines are now read
out of the file: the range must mention each predicate it claims to cover and must
still say the read is epistemic-only.

### 8.12 What the tenth round closed (every door citation, not just D4's)

The round-9 fix pinned D4's line range; the reviewer measured the SAME defect on
the other three findings, each byte-neutral and `--check`-green:

- **D1** — a second copy of D4's claim: `structural_edges_seen` (`hasPart` →
  `ownedBy`), the `expand_relationships` range (`551-559` → `533-541`) and the
  `navigation.py` range (`172-179` → `166-173`) were all free. The predicate list
  is now the graph's own (four real structural predicates, disjoint from the
  epistemic ones) and each cited range is read out of the file and asked whether it
  is about what it is cited for (`UNION`, the three epistemic predicates).
- **D3** — its evidence duplicated the probes' counts, so the artifact could claim
  `bare_returned 0/0 → 2/3` while its own answer says "EMPTY list", i.e. refute
  itself. The counts are now derived from `bare_kind_probes`, and a FAILED probe is
  refused as a measurement.
- **D6** — `sdk.py:62762-62782` → `:61000-61019` (unrelated code) was free. The
  cited lines must mention `ownedBy`, and `owners_asked`/`rows_returned` must be the
  witnesses that actually answered nothing.

### 8.13 What the eleventh round closed (the sentence, not only the citation)

The round-10 arm verified the cited FILE; the reviewer measured that it never
verified the CLAIM sentence, and filed four survivors — each byte-neutral:

- **D1's prose predicate list** — `IMPL|NAND|hasPart` → `IMPL|NAND|ownedBy` in the
  sentence itself, which then contradicts the code it cites, D4's `code_path` and
  REPORT §4. The token set in the prose must now be the same three.
- **D1's `the_read_that_can` route** — a route the product does not serve was free.
  The route must be spelled by some module under `tortoise/`.
- **D3's `namespaced_returned`** — counts the probes never produced (and could
  exceed the graph's edge total). Each count must be positive and the total bounded
  by the graph the artifact reports.
- **D6's citation** — satisfied by any range that merely mentioned `ownedBy`; the
  range must now DEFINE `_get_owned_entities`.
- **D5's "for every node asked about"** — a quantifier with no population beside it
  is unfalsifiable, so a claim about "every" now requires a non-empty node
  population in the evidence.

### 8.14 What the twelfth round closed (a quantifier, a tally, a route, a query)

Four survivors, each a claim that its arm verified one level too loosely:

- **D5's "for every node asked about"** — a LITERAL sentence beside a derived
  `reported_not_found`. With five of seven `list_topics` replies rewritten, the
  artifact could claim every node failed while its own evidence held two. The
  sentence is now derived: *"for N of the M nodes asked about"*, with both
  populations in the evidence.
- **D3's `namespaced_returned`** — bounded by positivity and the graph's edge total,
  which let `product-strategy:product` 2 → 9 through beside a probe row saying 2, and
  let a kind no pack defines (`foo:code`) through on a `":" in kind` test. Every
  count is now the row count of its OWN namespaced probe.
- **D1's route** — "some module's text contains it" admitted a PUT packs route at
  the same character length, and admitted the right one on a docstring rather than
  the decorator that serves it. The route must now match a served `@app.<verb>`
  pattern.
- **D6's citation range** — ending at the `def` line and its comments kept the suite
  green while the `MATCH` the sentence quotes sat outside the cited range; the range
  must carry the query.

### 8.15 What the thirteenth round closed (an UNREACHABLE guard, a verb, a quoted construct)

- **P3 — D5's arm was UNREACHABLE.** It was wrapped in `if "every" in text`, and
  the round-12 fix removed the word "every" from the sentence, so the arm could
  never fire: inserting `raise` as its first statement still left 92 passed. A guard
  that cannot fire is not a guard. The arm is now unconditional and derives the
  sentence's numerator and denominator from the probes, the `reported_not_found`
  ids from the witnesses, and its "the other doors return these nodes" clause from
  `explore_neighbour_count`.
- **P3 — D1's route arm was VERB-blind.** It collected path patterns only, so
  `GET` → `PUT` on the right path stayed green (a write verb claiming a read) and a
  same-length pack-manifest route passed. The verb is now part of the match, and the
  sentence must name the module that serves the route.
- **P3 — the citation arms checked a TOKEN, not the construct.** `"MATCH" in lines`
  passed for a range whose quoted `MATCH (s)<-[:ownedBy]-(e)` was outside it, and
  `"UNION" in lines` passed for the root-branch join. The quoted fragment is now
  taken from the sentence and required in the range, and D1's citation must merge
  BOTH arrow forms (`-[r]->` and `<-[r]-`), which is the claim the sentence makes.
  Correcting the fragment also fixed an inaccuracy this exposed: the artifact quoted
  `MATCH (s:Subject)<-[:ownedBy]-(e)` where `sdk.py` has `MATCH (s)<-[:ownedBy]-(e)`.

The generic coverage arm (`test_no_FIGURE_in_the_two_documents_is_free`) is a
**floor, not a substitute for a pin**: it proves a number is carried by some
artifact, never that it belongs to the field quoting it — which is why six of the
ten above were green under it. A future round that finds an unpinned cell should
add a pin, not widen an allowlist.

### 8.16 What the fourteenth round closed (a retry that truncated, a flag nobody read, two arms that could not red)

- **P1 — the name-variance retry truncated a DECLARED name.** Every declared team
  ENDS with its kind word (`Platform team`, `Product team`, `Customer team`), so
  dropping the trailing kind word turned a legitimate name into a bare common
  noun — `Customer team` → `Customer`, which the corpus says on its own. Both
  `Grid Map --managedBy--> Customer team` and `Asset Register --managedBy-->
  Customer team` (never stated) then cleared A4 through the retry alone. The
  retry now additionally requires the un-welded form to BE a declared name and
  the welded form not to be, so `ingest-gateway component → ingest-gateway`
  survives and `Customer team → Customer` is refused. The committed artifact is
  unchanged by this (its only variance row is the legitimate one) — the fail-open
  was reachable only through a corrupted receipt, which the new arm constructs.
- **P2 — the summary cap set a flag nothing read.** `quotes` declares
  `summary_examples_truncated`, while the setter derived
  `f"{bucket}_examples_truncated"` and the summary bucket is `summary_in_one_turn`,
  so a capped summary sample told the consumer the sample was COMPLETE. Masked by
  this corpus holding 3 summaries; the arm forces `CAP = 0` to observe it.
- **P2 — the turn band's SPAN was unstated.** See §3: one row clears the band only
  through another document, and the artifact now counts and samples it
  (`supported_outside_own_doc`, per-row `best_own_doc_turn_overlap`).
- **P3 — `_KIND_WORDS` was hand-typed while its comment claimed derivation.** The
  table is now explicit (`_KIND_OF_GROUP`, keyed by the declared structure's own
  groups) and the set is READ from it; a declared group with no kind word raises at
  import instead of leaving a silent gap.
- **P3 — D6 could still vanish with no record.** When fewer than two declared
  owners answered empty, the finding was simply absent from `door_findings`. The
  artifact now carries `withheld_findings`, derived from the same witness reading,
  naming the finding and why it was withdrawn.
- **Three arms could not red, and one had the wrong sense.** D1's module check was
  a tautology (`… or "entity_reads" in sentence`, on a sentence that begins
  `entity_reads.`); D5 compared `reported[name]` to the witness id the artifact was
  derived from (`X == X`); and D5's "two doors …" clause was guarded by its own
  wording, with the `reject` branch written in the `return` sense — so
  `two doors reject` passed. D1 now derives REACH from the served handler's AST,
  D5 compares the reported id to the probe's own `args`, and the clause's verb
  decides the sense (`return` → positive neighbour count, `reject` → zero,
  anything unmeasured → red).

### 8.17 What the fifteenth round closed (the EVIDENCE column, the citation copies, and the duplicate figure) — and what it did NOT

A fresh-context reviewer at `0368cbb7` measured **1 P1 + 4 P2**; this round closed the P1
and three of the P2s, and states the residual rather than implying it away.

- **P1 — a door finding's EVIDENCE cell was graded by nothing.** The ANSWER cell is exact
  (an arm above), so the column a reader uses to decide whether the door is broken could
  contradict its own measurement: `**0 rows** each` → `**1 rows** each` (D4), `19 real
  neighbours` → `20`, `false on 5/5` → `false on 6/5` (D2) and `containment is absent
  entirely (0 edges)` → `(1 edges)` (§3/A2 finding 5) each left `97 passed`.
  `test_the_door_EVIDENCE_cells_state_the_artifacts_numbers` derives every one of those
  figures from the artifact that produces it — but only until this paragraph existed:
  §8.18 measured two of the four GREEN again once §8.17 quoted the literals they
  grep for, and both arms are now scoped to the owning cell.
- **P2 — the report's COPIES of the door citations were free.** `readback.json`'s copies are
  pinned to code content, but the report repeats them: `navigation.py:172-179` → `166-173`
  (the round-10 mutant, applied to the prose) and `sdk.py:62762-62782` → `61000-61019` both
  left `97 passed`. `test_every_CODE_citation_in_the_documents_is_one_an_artifact_carries`
  grades the SAME site — the same range, one line **inside** it, or a wider span that begins at
  most **20 lines** before the artifact's start (`_CITATION_WIDEN_BOUND`) and ends **exactly**
  where it ends — so the honest wider span (`:533-559` against the artifact's `:551-559`, slack
  18 at the start and 0 at the end) passes while a moved range, a range bracketing the site
  (`:1-99999`), a range opening more than 20 lines early in unrelated code (`:62741-62782` at 21;
  `:62743-62782` at 19 still passes, measured), and a range that pads its END onto the site
  (`:62762-99999`, 37,217 lines) all red (each measured).
- **P2 — the DUPLICATE figure.** A pin shaped `expected in line` grades ONE copy, so
  `| … **5/5** | 5/5 (parent end…)` kept its second copy free: mutating EITHER `5/5` stayed
  green and only mutating BOTH reddened. `test_the_A2_class_table_derives_BOTH_cells_from_the_artifact`
  derives both columns from `A2_edges.totals`, and `§8.9`'s `809,812 bytes` is graded by
  `test_EVERY_bytes_figure_in_the_documents_is_the_committed_file`, which reads every
  occurrence (`809,813` reds).
- **P2 — the §8 exclusion.** The bound STARTED at `### 8.1` and ran to §9, so it excluded
  `8.1`–`8.16` under a reason that named only the mutant tables. Round 15 exported the bound to
  `## 8.` and WIDENED the hole in the same move: that also swallowed `## 8. Artifacts` — this
  bundle's own artifact INVENTORY — under a reason false of it, because that table IS a set of
  bundle measurements. Measured: adding `scored across 1,234 checks` to its `out/t6-score.json`
  row left the whole suite green under the `## 8.` bound. The exclusion is now the review LOG
  only (`### 8.1`–`### 8.18`), and `§8`'s artifacts table is scanned like any other claim table
  (the `1,234` mutant reds).
- ⛔ **THE RESIDUAL, stated rather than closed: a prose figure that is neither a table row
  nor named by a dedicated arm is still supported by set membership alone.** The reviewer's
  measured population was `154` of `373` single-token `+1` mutants staying green across
  §1–§7. The figures it VERIFIED are all now pinned (§8.17's third bullet and
  `test_the_CLAIM_layer_and_A2_figures_are_the_artifacts`: `(11 + 27 + 3 + 9 = 50)`,
  `12 of the 50`, the invented `9`, and `6 ownedBy rows are only 4 pairs` — each reds on
  `+1` at the sentence that owns it, re-measured in §8.18 after this section's own
  quotes had made two of them free). **A global rule was tried and refused**: requiring every prose figure to
  be carried by exactly one artifact value reds on honest small integers (`2 products`,
  `5 features`, `3 teams` are non-distinctive by construction) and the only way to keep it
  green was an allowlist of 24 figures — the hole wearing a gate's clothes. Closing the rest
  honestly means pinning the sentence the next figure lives in, which is a per-sentence
  obligation no global rule can discharge; the count is left to whoever re-measures it.

### 8.18 What the sixteenth round closed (the review log that graded its own cells)

This lane folded `origin/main` again (`0204a219aa90`, `#14741`), which made the
`fast_shards` header block's base token name a tree this head no longer folds — the
third occurrence of the rot that block documents, corrected there in the same fold-named
shape (`config/ci-surfaces.yml`), with every figure re-derived on the merged tree (nine
shards at 827.8 s, two at 827.7 s, `watchdog_minutes 28`, headroom 2.03, `fast_pool()`
1342 against 1341 on the fold, delta exactly `tests/test_14691_structure_evidence.py`,
reverse delta empty).

The substantive finding is §8.17's own: **the review log quoted every literal the
round-15 pins searched for, so the log graded the cells it described.** Both arms read
`flat` — the whole document, whitespace-collapsed — and §8.17's prose spells
`**0 rows** each`, `false on 5/5`, `containment is absent entirely (0 edges)`,
`(11 + 27 + 3 + 9 = 50)` and `12 of the 50` verbatim (two of them across a line break,
which `flat` also erases). Measured at `c76156f`, each mutant applied to the OWNING cell
alone, whole file:

| mutant, owning cell only | before | after |
|---|---|---|
| D4 `**0 rows** each` -> `**1 rows** each` | GREEN | RED |
| D2 `false on 5/5` -> `false on 6/5` | GREEN | RED |
| §3/A2 finding 5 `(0 edges)` -> `(1 edges)` | GREEN | RED |
| §7 `(11 + 27 + 3 + 9 = 50)` -> `12 + 27 + 3 + 9 = 50` | GREEN | RED |
| §3/A4 `12 of the 50` -> `13 of the 50` | GREEN | RED |

So §8.17's "all four mutants now red" held only until §8.17 existed: the paragraph that
recorded the fix is what made its own pins vacuous. Both arms now grade the CELL
(`_report_table_row`) or the CLAIM's own paragraph (`_report_paragraph`), never the
document — nine mutants (the five above plus the audit partition, the invented count, the
ownedBy pair count and the asymmetric row total) each red at the owning site alone,
measured. The citation and bytes arms were checked for the same leak and are not
vulnerable: they add or scan copies rather than asking whether a literal appears anywhere
(measured — a moved `sdk.py` range and a `+1` bytes figure each red alone).

⛔ The class, not the instance: **a pin whose haystack is the document that records its
own review is a pin over the review.** Scope every figure to the cell that owns it.

### 8.19 What the fix lane closed at `4944c705` (the four band pins that graded this log)

§8.18's class, one instance short of closed — and it was hiding in an arm whose NAME says it
grades "the paragraph that describes it". `test_the_over_typed_table_matches_the_paragraph_that_describes_it`
also carried four pins on `claim_layer.bands` (**11 fall below 0.5**, **12** at `< 0.6`,
**9 of those are unsupported by any corpus turn**, and the **11 / 1 / 27** triple), and every
one of those sentences existed ONLY in this log — §3/A4, the section that discusses the band, never
stated the histogram at all. So the four pins read `flat`, the whole document, and were satisfied by
the log that records them: measured, each figure could be mutated HERE and the arm reddened, while
there was no body copy of it to mutate. The decisive measurement is the class sweep rather than the
instance: stripping sections 8.1 to 8.18 left exactly ONE arm red — this one — so the remaining
population of log-graded arms was a single site, not a family.

The fix is §8.18's, applied to the site: the histogram is a measurement of `claim_layer`, so **§3/A4
now states it** and the arm grades it in that paragraph (`_report_paragraph`), never in the document.
The four assertions also moved to the claim-layer arm, where the §3/A4 paragraphs are graded — the
A1 caveat's arm was grading a subject its own docstring never named. This log's copies of the figures
stay as history; they are no longer load-bearing.

---

### 8.20 The seventeenth round — the citation bound's END, and the prose that outlived the code

The round-16 review's headline was that bounding a wider citation's START left its END free: a
range opening exactly on the artifact's start and padding its END by 37,241 lines left the suite
at `102 passed`. Shape 3 now pins the END (`hi - other_hi == 0`) while keeping the 20-line START
allowance the one honest wider span needs (18); measured at that head, the end-padded form applied
to the D6 evidence cell reds the citation arm and the honest `:533-559` still passes.

The same round found three descriptions that had outlived the code they describe, and the fix for
each is the subject of this arm rather than a tidy-up:

- the comment above `_prose_tokens()`'s exclusion regex named the bound as `### 8.1`..`### 8.18`,
  a NUMBER, while the
  code excludes `### 8.1` up to `## 9.` — so every round that appends a section silently widens
  the exclusion and makes the stated population false. It now names the HEADING RANGE, and the
  superseded `## 8.` state survives only as a rejected past.
- §8.17's citation bullet named **no number** for the START allowance at all, so a lane
  reconstructing the rule from the prose could read the live `_CITATION_WIDEN_BOUND` as dead code
  and delete it — which reds the one honest wider citation. It now states 20 against the honest
  18, and the classes refused beyond it.
- a paragraph deleted in round 16 took the §8 exclusion's *reason* with it, leaving a
  justification the next lane could only "fix" by un-excluding a log full of historical mutants.
  The reason is restored beside the code that needs it.

### 8.21 What the fix lane verified at `2cd46746e1b1`, and the one claim it reddened

⛔ **This is a fix lane's measurement, not a review round**, and it mints no attestation. Every
closure the rounds above record was re-measured here, independently, because a fixer's own claim is
a claim: **18 mutants, 17 RED and the single GREEN the bound exists to keep green**, each applied
ALONE to the cell that owns the figure (or, for the two anchor probes, to the heading the arm reads),
REPORT.md restored between runs and its sha256 compared (`fcda6e02…` before and after every one).
Round 15's six P1/P2 figures (D4's `0 rows` and `19 real neighbours`, D2's `5/5`, §3/A2 finding 5's
`(0 edges)`, §7's `(11 + 27 + 3 + 9 = 50)`, §3/A4's `12 of the 50`) each RED; the §8-exclusion mutant
(adding `scored across 1,234 checks` to the artifacts table's `out/t6-score.json` row) RED; the four
§3/A4 band figures RED; renaming the `## 9.` heading so the exclusion's lookahead no longer matches
RED (the arm fails loud rather than silently grading nothing); and the citation bound RED in all
five refused shapes (`61000-61019`, `61000-62782`, `1-99999`, `62762-99999`, `62741-62782`) while
`62743-62782` — 19 lines of start slack, inside the 20-line allowance — stayed GREEN, which is what
makes the allowance a live bound rather than a blanket.

⛔ **THE D6 SITE WAS RE-DERIVED ON THE REBASE ONTO `origin/main` `e2a9c695ad`, and the figures above
are the re-derived ones.** `#14624`'s creation hook added lines to `tortoise/sdk.py` ABOVE this
method, so `_get_owned_entities` moved from `62739-62758` to `62762-62782` — a citation is a claim
about a line range, and a range that no longer opens on the `def` is a claim the tree denies. The
suite reds on exactly that (`test_a_door_finding_PROSE_claim_is_derived_not_merely_cited`, both
arms), which is how the move was found rather than carried. What did NOT move is the substance:
the refused shapes are stated relative to the site, so 21 lines of start slack is still RED and 19
is still GREEN, and the artifact was REGENERATED from its producer (`score_t6.py`) rather than
hand-edited, so `out/t6-score.json` is still `build_score()`'s output. The graph figures this
report scores (owners asked, rows returned, `ownedBy` row count) are the FIXTURE's and were not
re-measured by this rebase — only the code citation was re-pointed.

The one defect this pass found was in the rule's own description, not the rule. `_prose_tokens()`'s
comment stated the excluded population as `### 8.1` to `### 8.19`; it was written when 8.19 was the
last round, and §8.20 was added one commit later, so the sentence was false at the head that carries
it. A reader taking it as the bound would write a regex ending at 8.19 and silently un-exclude this
section. The comment now names the NEXT TOP-LEVEL HEADING (`## 9.`) instead of a section number, so
it cannot rot again as rounds are added. No assertion changed: this is a description of a bound the
code already implements, and it is graded the only way a description can be — by a reader being able
to reconstruct the rule from it.

### 8.22 Round 24 — the guard's own two figures, derived rather than restated

The round-23 rows carry the prose's `19` and `21` as literals, which bounds the START allowance to
19 or 20 — but the sentences that state it say **20** (this predicate's docstring, and §8.20's
`20-line START allowance`), and nothing graded that. Measured at `77ba9891d656`, each mutant applied
alone and the file restored byte-for-byte (`sha256sum` compared): `_CITATION_WIDEN_BOUND = 19` left
**103 passed**, and the docstring's honest-wider-span slack `18` → `17` also left **103 passed**,
while both sentences became false at the tree they describe.

The same pass found the class's last literal in the block that forbids it: the `#:` comment above
`_CITATION_WIDEN_BOUND` says "STATE EVERY VECTOR HERE AS AN OFFSET, never as a literal line
number" and then named `entity_reads.py:533-559` against the artifact's `:551-559`. Pointing that
literal at unrelated code (`:500-559`) left **103 passed**, because the round-22 guard reads
`_cites_a_site_in.__doc__` and a `#:` comment is invisible to `__doc__`.

So both figures are now DERIVED rather than reworded a fourth time:

- the honest wider span's slack is derived from the two documents against the artifacts — a
  document citation the artifacts do not carry verbatim, reaching back from an artifact's start and
  ending exactly on its end — and the docstring's stated `start slack <n>, end exact` must equal it;
- the stated START allowance must equal `_CITATION_WIDEN_BOUND`, so `20` cannot remain prose while
  the constant moves to 19;
- the "no literal line range" guard now covers BOTH homes of the rule — the predicate's docstring
  and the `#:` block, read from this file's source — and the block states no figure of its own.

Mutants, each applied alone: bound `20` → `19` RED · `20` → `30` RED · slack `18` → `17` RED ·
a literal re-added to the `#:` block RED · a start INSIDE the range admitted RED · control
**103 passed**. Every red is exactly
`test_the_citation_shape_table_is_the_predicate_the_docstring_states`. Nothing else changed: no
artifact, pin, row or predicate moved.

### 8.23 Round 25 — the END of a wider span was graded by nothing below the site's whole span

Shape 3 requires a wider citation to end EXACTLY on the artifact's end, and the predicate's
docstring says so. Nothing graded it below the site's own span: at `c6c3439cb5e8` the table's only
end-padded row padded by the FULL span, so a tolerance narrower than that was free. Measured at
that head, each
mutant applied alone and the file restored byte-for-byte (`sha256sum` compared): widening
`hi - other_hi == 0` to `<= 5` — and to `<= 1` — left **103 passed**, while the sentence "requires
it to end at EXACTLY the artifact's end" became false at the tree it describes.

The start side had already been swept one offset at a time (round 22, after a restated figure went
wrong the same way); the end side had one row. It is now swept the same way: every end offset
`1..span` past a site, at the site's own start, must be refused.

Mutants, each applied alone: end tolerance `<= 5` RED · `<= 1` RED · the equivalent rewrite `<= 0`
GREEN (so the sweep is not red on a change that keeps the rule) · bound `20` → `19` RED · slack
`18` → `17` RED · a literal re-added to the `#:` block RED · control **103 passed**. Every red is
exactly `test_the_citation_shape_table_is_the_predicate_the_docstring_states`. No artifact, pin,
row or predicate changed.

### 8.24 Round 28 — each sweep's own EXTENT was graded by nothing

Rounds 25–27 graded the sweeps' ROWS and every copy of the figures beside them — and left the thing
those rows are generated from free: the loops' bounds. The docstring states a RANGE — *"SWEEPS
every offset in 1..span + 3"*, and *"every end offset 1..span past the artifact's end"* — and a
needle proves the SENTENCE exists, not that the loop implements it. Measured at `8764d4dd3217`, each
mutant applied
alone and the file restored byte-for-byte (`sha256sum` compared):

| mutant | result | what became false |
|---|---|---|
| start sweep `range(1, span + 4)` → `range(1, span + 1)` | **103 passed** | "SWEEPS every offset in 1..span + 3" |
| end sweep `range(1, span + 1)` → `range(1, span)` | **103 passed** | "every end offset 1..span" |

A sweep that stops early is the same defect the sweeps were added to remove, one level down: it grades
nothing past the offset where it stops, and the sentence keeps claiming it does.

**Fix (extent derived from the table that actually ran, not restated).** Both sweeps are now built as
data before their rows are emitted, so their extent is a value the arm can read; the stated start
bound is parsed from the docstring and compared against the swept maximum, and the stated end range
against the swept set. Mutating the loop and mutating the sentence now each red the same arm.

Mutants, each applied alone, file restored byte-for-byte: start sweep → `span + 1` RED · start sweep
→ `span + 3` (one short) RED · end sweep → `span` RED · end sweep → `span + 2` RED · docstring `+ 3`
→ `+ 2` RED · docstring end range → `1..span - 1` RED · the end-sweep sentence deleted RED · control
**103 passed**. Every red is exactly
`test_the_citation_shape_table_is_the_predicate_the_docstring_states`. No artifact, pin, row or
predicate changed; no new test file, so no `config/ci-surfaces.yml` row is needed.

### 8.25 Round 29 — the extent's OTHER copy (REPORT §8.23) was still free

Round 28 graded the two sweeps' extents against the sentences that state them, and graded the copy
that lives in the predicate's docstring. The same extent has a second home — §8.23's normative
sentence, *"It is now swept the same way: every end offset `1..span` past a site, at the site's own
start, must be refused"* — and that copy was graded by nothing, which is the defect round 27 closed
for the allowance and slack figures one home at a time.

Measured at `9e49da657a0e`, the mutant applied alone and REPORT.md restored byte-for-byte
(`sha256sum` compared): §8.23's `every end offset \`1..span\`` → `1..span + 1` left **103 passed**
while the sentence claimed a sweep the loop does not run.

So the arm now parses §8.23's stated extent (spacing-normalised, so a re-wrap cannot hide it) and
requires it to equal the swept set's maximum — the same value the docstring's copy is graded
against. A missing sentence reds rather than silently grading nothing.

§8.23 also described the PRE-fix table in the present tense ("the table's only end-padded row pads
by the FULL span"), which has been false since round 25 added the sweep: it is now scoped to
`c6c3439cb5e8`, the head it was measured at.

Mutants, each applied alone at this head: §8.23's extent → `1..span + 1` RED · the §8.23 sentence
deleted RED · control **103 passed**. Every red is exactly
`test_the_citation_shape_table_is_the_predicate_the_docstring_states`.

### 8.26 Round 30 — the extent was read from the DICT, so the emission was free

Round 28 graded each sweep's extent from the dict that FEEDS its rows, so the loop's BOUND was
covered. The emission is a second place a sweep can stop short, and it was graded by nothing.
Measured at `ea006476a872`, each mutant applied alone and the file restored byte-for-byte
(`sha256sum` compared):

| mutant | result | what became false |
|---|---|---|
| `if k > span: continue` in the start sweep's emission loop | **103 passed** | "SWEEPS every offset in 1..span + 3" |
| `if k > 1: continue` in the end sweep's emission loop | **103 passed** | "every end offset 1..span" |

The dict still covered `1..span + 3`, so the extent assertion round 28 added stayed green while the
rows it is supposed to generate stopped at the site's span — the same defect one level down again,
and it is the defect class §8.24's own paragraph names.

**Fix (the swept set is read from the rows the TABLE carries).** The emission and the extent now
share one key template per sweep, and the graded set is `{k for k in sweep if key(k) in table}` —
so a short bound, a short emission, an over-long bound and a HOLE in the middle all red the same
arm. Set equality, not `min`/`max`: a middle hole leaves an intact min/max pair, which is why the
first form of round 28's assertion could not have caught it. §8.23's extent is compared against
the same table-derived maximum.

Mutants, each applied alone at this head, file restored byte-for-byte: start bound → `span + 1` RED ·
start bound → `span + 3` RED · start bound → `span + 5` RED · end bound → `span` RED · end bound →
`span + 2` RED · start emission stops at `span` RED ("rows miss offsets [21, 22, 23]") · start
emission hole at `5` RED · end emission stops at `1` RED · end emission hole at `3` RED · docstring
start extent `+ 3` → `+ 2` RED · docstring end extent → `1..span - 1` RED · §8.23's extent →
`1..span + 1` RED · §8.23's sentence deleted RED · control **103 passed**. Every red is exactly
`test_the_citation_shape_table_is_the_predicate_the_docstring_states`. No artifact, pin, row or
predicate changed; no new test file, so no `config/ci-surfaces.yml` row is needed.

---

## 9. Mandate-preflight disclosure

The dispatch brief carried a `[mandate-preflight]` block naming two files this lane was ordered to
read and **cannot**, both absent on this box with no provisioning path:

- `/home/cmux/.pi/agent/state/LANE-BRIEF-PREAMBLE.md` — MISSING
- `/home/cmux/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` — MISSING

**I did not read them and am not claiming to.** The brief's own text was treated as the authority, per
the block's instruction. The consequence is concrete: `tortoise`'s `AGENTS.md` skill table orders a
lane to read `TORTOISE-GRAPH-BRIEF.md` **before it derives**, and that instruction could not be obeyed
here — the graph was read through the product instead (`#14606`'s bundle, `ONTOLOGY.md` §1/§3.6/§8,
the pack manifests, and the receipts this run produced).
