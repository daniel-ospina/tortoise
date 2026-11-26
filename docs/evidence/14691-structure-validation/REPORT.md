# T6 (#14691) — does extraction STRUCTURE the business, or narrate it?

**Answer: it structures what the business _has_ and does not structure what the
business _is_.** Every declared element — 2 products, 5 features, 3 teams, 4
roles, 4 people, 7 components, 2 customers, 1 segment — arrives with the **right
ontology kind** on a **fresh graph**, containment and dependency edges are
**present and correctly oriented**, and nothing structural is invented. But
**ownership is written inverted on every row** (`subject --ownedBy--> thing`
asserts *the thing owns the person*), the product's own ownership reader
therefore returns nothing for all four owners, and **no supported read door can
walk a structural edge with its direction** — so the graph holds an inventory and
a set of sentences, not a navigable shape.

| | |
|---|---|
| Issue | #14691 — T6 (the owner's "most needed" test, verbatim in the T6 comment) |
| Lane | `test/t6-business-structure` · PR #14723 |
| Graph under test | `xv14691_business_structure` — its own graph on this lane's private FalkorDB (`fdb-lane-2a55a53650`, `127.0.0.1:16413`); **empty before the write** (`Point 0, Event 0, Subject 0, Object 0, Source 0`), never `live-v5` |
| Corpus | `corpus/structure-corpus.json` — 5 documents, 30 turns, **4,295 characters ≈ 1.1k tokens** |
| Code under test | this worktree at `origin/main` = `f37a250d9b22` |
| Extractor | `extractor_v2.py` @ `f37a250d9b22`; `extraction_mode: "llm:deepseek-direct"`, model `deepseek-v4-flash` |
| Second measurement | `VERIFICATION-independent.md` + `out/t6-independent-verification.json` — same graph, read without the scoring script |

## 0. The method rule, and how this run obeyed it

Every write went through `TortoiseSDK.mine_knowledge_from_session(conversation=…,
session_id=…)` — the public door the MCP tool
`tortoise_mine_knowledge_from_session` maps onto, which routes
`_capture_session` → `_extract_session_v2` → the extractor-v4 five-stage
pipeline. Every read went through the **Tortoise MCP server over stdio**
(`python -m tortoise.mcp_server`). Nothing here opens a socket, builds a Cypher
string or touches the record store.

⛔ **The extractor is not changed by this test — it measures.** Where a question
could not be answered through the product's doors, the failure is recorded below
(§5) and **not worked around**.

```bash
export TORTOISE_DB_URI='docker://:@127.0.0.1:16413/tortoise_test_matrix'   # this lane's container
export DEEPSEEK_API_KEY='…'                  # the extraction credential (never read from a file)
D=docs/evidence/14691-structure-validation
./$D/run.sh $D/run_ingest.py                 # 1. write (5 docs) — needs the LLM key
./$D/run.sh $D/run_readback.py               # 2. read back through MCP
./$D/run.sh $D/score_t6.py                   # 3. score the five assertions
./$D/run.sh $D/verify_independent.py         # 4. second measurement (read-only)
```

`run.sh` bakes in no box path and no credential store: `TORTOISE_VENV`,
`TORTOISE_DB_URI` and `DEEPSEEK_API_KEY` are caller-supplied, and the key is
demanded only by the phase that makes a model call.

## 1. The corpus

One fictional software business (Lumen Grid, energy-operations SaaS) described
across five meeting transcripts — the extractor's only v4 door is session
capture, the same scoping fact recorded in `#14606` §1. **No decisions and no
contradictions**: this corpus is only *what the business is made of*. The shape
it declares is committed alongside it (`_declared_structure`), so every
assertion below is a refutable number rather than a judgement.

| doc | date | carries |
|---|---|---|
| `doc-01` | 2026-04-07 | 2 products; `Lumen Core` → Grid Map / Outage Alerts / Load Forecast, `Lumen Field` → Crew Dispatch / Asset Register |
| `doc-02` | 2026-04-14 | 3 teams; 4 roles; 4 people and the team each is on; who owns what (4 named owner→thing pairs) |
| `doc-03` | 2026-04-21 | 7 components and 7 dependencies, stated in the direction the dependency runs |
| `doc-04` | 2026-04-28 | 2 customers, which product each runs, and the segment both sit in |
| `doc-05` | 2026-05-05 | which team builds which component; **re-states** three product→feature facts from `doc-01` |

## 2. Ingest — the numbers, and the tokens

5/5 documents captured `ok`, fresh graph, 13–20 s per document (`out/ingest-receipt.json`).

| doc | Points | entities | relations | ack |
|---|---|---|---|---|
| doc-01 | 12 | 9 | 7 | 17.9 s |
| doc-02 | 11 | 16 | 12 | 14.6 s |
| doc-03 | 9 | 8 | 7 | 16.1 s |
| doc-04 | 9 | 8 | 5 | 13.1 s |
| doc-05 | 9 | 19 | 17 | 20.0 s |

**Graph after ingest:** `Point 86 · Event 10 · Subject 11 · Object 24 · Source 5`.
**Structural relation rows written:** `related 14 · hasPart 8 · dependsOn 7 ·
produces 7 · ownedBy 6 · managedBy 2 · IMPL 2` = **46**.

**Extraction tokens spent (the "one token budget"):**

| stage | prompt | completion |
|---|---|---|
| 5 documents, `llm:deepseek-direct` | **130,804** | **17,137** |

**147,941 tokens for ~1.1k tokens of corpus ≈ 134× amplification** — the same
order as `#14606`'s 128×. `deepseek-direct` reports tokens but **no cost**
(`calls_without_cost` on every call), so the product cannot price its own
extraction. The product also emitted **36 warnings** across the five documents
(26 "no resolvable source turn", 1 token-overlap-band, 2 "not written").

## 3. The five assertions

Primary record: `out/t6-score.json`. Second measurement:
`out/t6-independent-verification.json`. Both agree; where they differ in
labelling the difference is stated.

### A1 · Every declared element appears, with the right entity type — **PASS (28/28)**

| family | declared | present | right kind | kind it landed on |
|---|---|---|---|---|
| product | 2 | 2 | 2 | `product-strategy:product` |
| feature | 5 | 5 | 5 | `product-strategy:feature` |
| team | 3 | 3 | 3 | `core:team` |
| **role** | 4 | 4 | 4 | **`core:role`** |
| person | 4 | 4 | 4 | `core:naturalPerson` |
| component | 7 | 7 | 7 | `dev:code` |
| customer | 2 | 2 | 2 | `product-strategy:customer` |
| segment | 1 | 1 | 1 | `product-strategy:customerSegment` |

**The failure this test exists to catch — "everything arrives as `statement`" —
does not happen.** Products are not flattened into statements, teams are not
flattened into roles, roles are not flattened into people, and components are
not flattened into prose. **The one caveat is naming, not typing:** the
`ingest-gateway` component is stored as **"ingest-gateway component"**, so a
caller asking the product for `ingest-gateway` by that name gets nothing
(`list_topics` → `Entity … not found`; `out/t6-score.json` `door_findings.D2`).

### A2 · Containment/ownership edges present and correctly ORIENTED — **FAIL (12/16 oriented)**

Declared directions, from `docs/ONTOLOGY.md` §3.6/§8 and the packs this corpus
activates (`product-strategy:contains` → `hasPart`; `dev:dependsOn`; `ownedBy`
`->` = **the target owns the source**):

| relation | present | correctly oriented | evidence |
|---|---|---|---|
| product → feature (`hasPart`) | **5/5** | **5/5** ✅ | `Lumen Core → Grid Map / Outage Alerts / Load Forecast`, `Lumen Field → Crew Dispatch / Asset Register` |
| component → dependency (`dependsOn`) | **7/7** | **7/7** ✅ | `event-bus → fact-store`, `signal-engine → event-bus`, `signal-engine → fact-store`, `web-dashboard → signal-engine`, `web-dashboard → auth-service`, `auth-service → identity-db`, `ingest-gateway component → event-bus` |
| thing → owner (`ownedBy`) | 4/4 | **0/4** ❌ | every row is `subject --ownedBy--> object`: `Amara Osei → Lumen Core`, `Dana Whitlock → ingest-gateway component`, `Tomas Ferreira → on-call rotation`, `Jonas Bauer → enterprise renewals` |
| customer → owner (`managedBy`) | 2/2 | **0/2** ❌ | `Jonas Bauer --managedBy--> Northwind Logistics` / `Helio Health`; `managedBy` is `Entity → Subject`, so the row asserts *the customer manages the person* |
| team → role | **0** | — | membership arrived as `related` (below), never as a hierarchy edge |

**The `#14606` defect is systemic, not corpus-specific.** On a second, entirely
different corpus the extractor again writes ownership in the word order the
sentence uses — and nothing canonicalises it to `ownedBy`'s documented sense.
`tortoise/relation_write.py`'s structural arm still passes the model's endpoints
straight through to `sdk.link_entities(edge.predicate, edge.src_id, edge.dst_id)`
with no direction argument and no re-ordering.

**Team→role is not modelled as containment at all.** The person→team and
person→role links exist, but as `related` — a directionless predicate:
`Dana Whitlock --related--> Platform team`, `Dana Whitlock --related--> Staff
Engineer` (and the same for the other three people). "Who is on team Y" is
therefore answerable only by reading the sentence, not by walking a hierarchy.

### A3 · Hierarchy is not collapsed — **PASS (5 parent edges, 2 parents, 5 children)**

Two products hold five features with real `hasPart` edges; the parents are the
products and the children are the features. **Not a flat list.** The one defect
here is duplication, not collapse: the receipt carries **8** `hasPart` rows for
**5** distinct pairs — `doc-05` re-states three `doc-01` facts and writes the
edges **again** (parallel edges for one fact). T1 scores growth on a re-worded
statement inside one ingest; this is the same mechanism in the structural layer —
a later document **confirming** a fact adds a second edge instead of
corroborating the first.

### A4 · Nothing invented — **PASS for STRUCTURE, FAIL for CLAIMS**

All 24 objects and 11 subjects trace to corpus text: `control-room customers`,
`field crews`, `on-call rotation`, `enterprise renewals`,
`ownership accountability convention`, `the seven-component dependency graph` are
all directly evidenced in the transcripts. No product, team, role or component is
inferred from naming alone.

**The point layer does not hold the same line** (`out/t6-quote-audit.json`). Of
the 50 `quote` fields the extractor attached to its Points:

| quote fidelity | count |
|---|---|
| verbatim substring of the corpus | 11 |
| faithful paraphrase (every token present, re-punctuated) | 27 |
| **best corpus-sentence overlap < 0.5 — the corpus does not say it** | **10** |

The sharpest example is a claim the corpus never makes, carrying a `quote` that
is not in the corpus:

> "Any future proposal to share or duplicate a feature across the Lumen Core /
> Lumen Field boundary would be a change to this baseline, not a continuation
> of it."

`"any future proposal"`, `"baseline"` and `"no shared feature surface"` appear
**nowhere** in the corpus (substring-verified). Two more of the same class:
"Lumen Core and Lumen Field are distinct delivery surfaces serving distinct users
(operators vs. field crews), not a single platform with a mobile skin" and
"Fact-store resilience should not be treated as a lower-priority item…". The
structure is traceable; **the analysis around it is invented, and it is presented
as a quotation** — which defeats the provenance anchor §2.0 depends on.

### A5 · Retrieval by structure — **PARTIAL (search 5/5, structural walk 0/5)**

| question | expected | answered from search text | structural walk |
|---|---|---|---|
| What features belong to Lumen Core? | Grid Map, Outage Alerts, Load Forecast | ✅ all three, rank 2 | ❌ root resolved to a **turn**, 1 neighbour |
| Who is on the Platform team? | Dana Whitlock, Tomas Ferreira | ✅ ranks 1–2 | ❌ root resolved to a **sentence**, 3 neighbours |
| What depends on the fact-store? | event-bus, signal-engine | ✅ ranks 3 and 1 | ❌ root resolved to a **sentence**, 5 neighbours |
| Which product does Northwind Logistics run? | Lumen Core | ✅ rank 1 | ❌ root resolved to a **turn**, 1 neighbour |
| Which team builds the signal-engine? | Platform team | ✅ rank 4 | ❌ root resolved to a **sentence**, 5 neighbours |

So the user-facing answer is **correct five times out of five** — but every one
of them comes from *text*, and the structure walk (the read that would let an
agent answer the same question without prose) resolves to an entity **0/5**.
That is the finding: retrieval by structure is not available even where the
structure exists.

## 4. The three defects `#14606` asked T6 to score

| `#14606` defect | T6 result |
|---|---|
| `ownedBy` written **inverted** | **REPRODUCED, 4/4 rows**, on a different corpus — so the cause is the writer's pass-through, not the corpus. Confirmed a second way: the product's own reader (`tortoise_get_entity(id, type="governance")` → `_get_owned_entities`) returns `[]` for all four owners (§5 W1) |
| cross-document contradiction not represented | **not applicable** — this corpus has none by design |
| search returns the superseded side | **not applicable** — nothing is superseded; the adjacent T6 analogue is `doc-05`'s **duplication** of `doc-01`'s edges (A3) |

## 5. Door findings — recorded, never worked around

**D1 · Edge direction is not readable through any supported neighbourhood door.**
`tortoise_explore_connections("Lumen Core")` returns **one** row (an epistemic
`IMPL`) and **none** of its three `hasPart` children; called on a Subject
(`Amara Osei`) it returns `[]`, while `tortoise_list_topics` answers
`{"error": "Entity … not found"}` for the same id — two doors, one node, two
different wrong answers. `navigation.entityProfile`/`tortoise_traverse` UNION
`(n)-[r]->(m)` with `(n)<-[r]-(m)` and keep only `type(r)`, so direction is gone
by the time they answer. The **only** read that returns
`{source, type, target}` is the retired `tortoise_recall(mode="subgraph")`,
served through a shim; its declared replacement
`tortoise_check_confidence(recall=True, …)` has no subgraph mode. The
direction-carrying read (`entity_reads.entity_relations`, behind
`GET /v1/entities/{id}/relations`) has **no MCP or SDK door**.

**D2 · A declared element cannot always be resolved by the name the business
uses.** `ingest-gateway` is stored as `ingest-gateway component`; the bare name
resolves to nothing.

**D3 · A bare kind returns an empty list with no error.** The full-scan door
answers `0` rows for `object:product` and `subject:team` while returning rows for
`product-strategy:product` (2) and `core:team` (3). A silent zero for a legal
spelling is indistinguishable from "this graph has no products".

**W1 (second measurement) · The writer and the reader disagree.** On a graph
holding six `ownedBy` rows, the ownership door returns `[]` for every owner.
`_get_owned_entities` is `MATCH (s:Subject) … MATCH (s)<-[:ownedBy]-(e)` — the
declared orientation. Nothing points *into* a person, because the extractor wrote
the edge the other way. The inversion is thus not a documentation quibble: it
makes the relation **unreadable through the door the product provides for it**.

## 6. Repro / artifacts

| artifact | what it is |
|---|---|
| `corpus/structure-corpus.json` | the corpus and the shape it declares (`_declared_structure`) |
| `run.sh` · `run_ingest.py` · `run_readback.py` · `score_t6.py` | the four phases; no box path, no credential store |
| `out/ingest-receipt.json` | per-document product envelopes: `stats`, `stats.llm` tokens, `warnings`, `relation_write.edges` |
| `out/readback.json` · `out/tool-surface.json` | every MCP read, verbatim, plus the surface the server advertised (39 tools) |
| `out/t6-score.json` | the five assertions with per-element evidence and the door findings |
| `out/t6-quote-audit.json` | the 50-quote fidelity audit behind A4's claim-layer failure |
| `verify_independent.py` · `out/t6-independent-verification.json` · `VERIFICATION-independent.md` | the **second** measurement, taken through MCP without the scoring script |

## 7. The one sentence

**A reader could name everything this business is made of, and could not say who
owns any of it** — the graph holds the business's inventory and the sentences that
describe it, not its shape.
