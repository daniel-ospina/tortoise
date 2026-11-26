# #14691 T6 — does the extractor STRUCTURE the business, or just narrate it?

**Answer, one sentence: yes to the inventory, no to the wiring.** Every product, feature, team,
role, person, component and customer in this corpus survives extraction under a sensible type and
the product→feature tree is built correctly — but a reader cannot describe how the business is
*connected*, because all six ownership edges are stored **inverted**, team membership and role
assignment arrive as the meaning-free `related` predicate, **team→role containment does not exist at
all**, and **no MCP door reports a structural edge's direction** — so the inversion is invisible to
any agent reading the graph the way the product tells it to.

| | |
|---|---|
| Lane | `test/t6-business-structure` |
| Graph under test | `xv14691_business_structure` — its own graph on this lane's private FalkorDB (`fdb-lane-2a55a53650`, `127.0.0.1:16413`) |
| Corpus | `docs/evidence/14691-structure-validation/corpus/structure-corpus.json` |
| Code under test | this worktree at `origin/main` = `f37a250d9b22` (`git rev-parse HEAD origin/main` match at run time) |
| Run | 2026-11-26, 05:37–05:43 UTC · 5 captures, 1.9 min of extraction |
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

**4,295 characters ≈ 1,074 tokens** (5 documents, 32 turns). The declared structure is machine-readable
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

Doc-01 also records `graph_ops.total ≈ 1,000` per capture; the amplification, not the wall clock, is
the unit economics.

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
service is not source code. Measured extras in the type axis (precision, all traceable to text):

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
| paraphrase (≥60% of the quote's words in one corpus sentence) | **27** |
| **invented** (<60% overlap — the corpus never says it) | **12** |

Examples of the invented 12 (`out/t6-score.json` → `claim_layer.invented_examples`):

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
| **D1** | is this structural edge oriented `src → dst`? | **No.** The edge and its type are visible; its direction is gone by the time a caller reads it. | `explore_connections` UNIONs `(n)-[r]->(m)` with `(n)<-[r]-(m)` and keeps `type(r)` only (`tortoise/navigation.py:172-179`). The one read that reports direction, `entity_reads.entity_relations`, is behind `GET /v1/entities/{id}/relations` and has **no MCP/SDK door** (`tortoise/entity_reads.py:533-559`). |
| **D2** | does `expand_relationships` report an entity's STRUCTURAL edges? | **No — it returns `[]` for a node that has edges, so a connected entity reads as ISOLATED.** | Lumen Core: 1 row (its `IMPL` to `control-room customers`), 19 real neighbours. Amara Osei / Platform team / event-bus: **0 rows** each, while `explore_connections` sees 6 / 10 / 10. The read covers `IMPL|NAND|hasPart` only (`entity_reads.py:551-559`) — and `hasPart` only where it is an operator/point edge. |
| **D3** | does the full-scan door accept the **bare** kind the packs declare? | **No — a bare kind returns an EMPTY list with no error, while the namespaced spelling returns rows.** | `search_knowledge(kind="product", entity_type="object")` → `[]`; `kind="product-strategy:product"` → 2 rows. Same for `team`/`core:team`. A caller who reads `objectKinds: [product]` in a manifest and asks for it is told *"there is nothing"* — an answer, not a refusal (AX: a surface must not answer where it cannot know). |
| **D4** | does `graph_overview(section="topics", entity_id=…)` resolve an entity that two other doors resolve? | **No — `{"error": "Entity … not found"}` for every node asked about.** | Lumen Core, Amara Osei, Platform team, event-bus — all four resolve through `get_entity` and walk through `explore_connections`. |
| **D5** | can a structure element be resolved by NAME? | **Not reliably** — `explore_connections(query=…)` resolves names to **Points**, and one component's stored name carries a welded collective noun (`ingest-gateway` → `ingest-gateway component`, from the sentence *"owns the ingest-gateway component"*). | `out/readback.json` → `unresolved_elements`, `elements[].walk_root_kind` (empty on 5/5 retrieval walks). |

**Two of these compound into the finding that matters most.** D1 (no direction on read) + D2 (the
expand door silent about structural edges) mean **an agent cannot discover the `ownedBy` inversion
through the product**. It must read the write receipt, or query REST behind a door it has no MCP
route to. That is precisely how #14606's inversion survived its own read-back: the graph was wrong and
the reads said "nothing to see".

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
4. **`check_structure` returned `[]`** on a graph holding 12 inverted-or-untyped relation classes —
   the structural validator sees no problem, which is consistent with #14606's G1 observation.

---

## 7. Independent corroboration (a second lane, measured on this graph)

While this run was being assembled, a **second lane** independently probed the same graph over MCP and
left its measurement at `out/t6-independent-verification.json` (its producer is `/tmp/t6_probe2.py` +
`/tmp/t6_probe4.py`, **not** committed — so the file is queued evidence, not reproducible from this
bundle; kept verbatim rather than deleted). It lands on the same numbers from a depth-2 subgraph read:

* the same 5 `hasPart`, 7 `dependsOn` rows and the same `ownedBy` rows with `src = person, dst = thing`;
* the same `dev:code` typing for all seven components, the same duplicate `mid-market utilities` /
  `mid-market utilities segment` nodes, and the same extra `dev:architectureDoc` + `agent-ops:rule` nodes;
* the same `expand_relationships` silence on structural edges (`Amara Osei: []`, `Lumen Core: 1 row`);
* `graph_overview(topic=…)` answering `Entity … not found` for every node — D4/Finding 5 above.

Two independent reads, one result. ⚠️ It also means **two lanes were writing into one evidence
directory on this worktree** — recorded here rather than tidied away, because a lane that deletes a
peer's artifact destroys the only copy of an independent measurement.

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
| `out/readback.json` | verbatim MCP replies (784 KB): scans, 32 element walks, endpoint reads, link witness, probes | `run_readback.py` |
| `out/t6-score.json` | the five assertions with their numbers, evidence rows and verdicts | `score_t6.py` |
| `out/tool-surface.json` | the live MCP surface (39 tools) with each tool's own `inputSchema` | `run_readback.py` |
| `out/t6-independent-verification.json` | a second lane's independent probe of the same graph (§7) | **not this bundle's producer** — see §7 |
| `out/events.jsonl` (**not committed**) | the SDK event log, scratch, reproducible via `run.sh` | `run_ingest.py` |

---

## 9. Mandate-preflight disclosure

The dispatch brief carried a `[mandate-preflight]` block naming two files this lane was ordered to
read and **cannot**, both absent on this box with no provisioning path:

* `/home/cmux/.pi/agent/state/LANE-BRIEF-PREAMBLE.md` — MISSING
* `/home/cmux/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` — MISSING

**I did not read them and am not claiming to.** The brief's own text was treated as the authority, per
the block's instruction. The consequence is concrete: `tortoise`'s `AGENTS.md` skill table orders a
lane to read `TORTOISE-GRAPH-BRIEF.md` **before it derives**, and that instruction could not be obeyed
here — the graph was read through the product instead (`#14606`'s bundle, `ONTOLOGY.md` §1/§3.6/§8,
the pack manifests, and the receipts this run produced).
