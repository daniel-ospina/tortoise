# T6 (#14691) — an INDEPENDENT second measurement

This file is **not** the report. `REPORT.md` (same directory) is the primary
record produced by this lane's harness (`run_ingest.py` → `run_readback.py` →
`score_t6.py`, scored into `out/t6-score.json`). This file is a **second,
independent read** of the same graph, made through the product's own MCP door
only, so that every number in the report has a corroborating measurement that
did not come from the scoring script that produced it.

| | |
|---|---|
| Graph under test | `xv14691_business_structure` (the lane's private FalkorDB, `fdb-lane-2a55a53650`, `127.0.0.1:16413`) |
| Corpus | `corpus/structure-corpus.json` — 4,295 characters, 5 documents, 30 turns |
| Probe | depth-2 `tortoise_recall(mode="subgraph")` from each of the 32 declared names; endpoint-type + edge direction read from the returned `{nodes, edges:[{source,type,target}]}`; every id the ingest receipt claims it wrote resolved with `tortoise_get_entity` |
| Raw data | `out/t6-independent-verification.json` |
| Wrote nothing | no graph write, no extractor change (recorded decision (a)); the only store touch was reading |

## What agrees

| Assertion | Primary record (`out/t6-score.json`) | Second measurement | Agree? |
|---|---|---|---|
| **A1** types | 27 of 28 declared elements present with the right kind; `component:ingest-gateway` counted **missing** | the graph holds **7** `dev:code` objects and all five feature kinds are `product-strategy:feature` — but the gateway object is named **"ingest-gateway component"**, so the declared name does not resolve. All four `core:role` subjects are present (`Staff Engineer`, `Site Reliability Engineer`, `Product Manager`, `Account Executive`) | ✅ agree — and the "missing" element is more precisely a **mis-named duplicate**, not an absent one |
| **A2** ownership | present, **INVERTED** on every row that resolved | 4 `ownedBy` rows read in the graph, every one `subject --ownedBy--> object` (`Amara Osei → Lumen Core`, `Dana Whitlock → ingest-gateway component`, `Tomas Ferreira → on-call rotation`, `Jonas Bauer → enterprise renewals`). Declared direction is `->` = **the target owns the source**, so each row asserts *the thing owns the person* | ✅ agree |
| **A2** containment | 5/5 present, parent is the product | 5 distinct `hasPart` pairs, `Lumen Core → {Grid Map, Outage Alerts, Load Forecast}`, `Lumen Field → {Crew Dispatch, Asset Register}` — whole → part, correct | ✅ agree |
| **A2** dependency | 6/7 resolved and correctly oriented | 7 `dependsOn` rows, every one dependent → dependency (`event-bus → fact-store`, `web-dashboard → signal-engine`, `auth-service → identity-db`, …) — correct | ✅ agree (the 7th is the same mis-named gateway object) |
| **A5** retrieval | PASS — 5/5 answered by search text; structural walk resolved to an entity 0/5 | all four T6 questions return the right text through `tortoise_search`; `tortoise_explore_connections("Lumen Core")` returns **one** row and it is an epistemic `IMPL` — *none* of its three `hasPart` children | ✅ agree, and the second measurement names the mechanism (see W2) |

## What this measurement adds

### W1 — the product's own ownership reader contradicts its own writer

`tortoise_get_entity(id, type="governance")` is the **supported** door for the
question *"what does this Subject own?"* — it dispatches to
`TortoiseSDK._get_owned_entities` (`tortoise/sdk.py:62739`), which is
`MATCH (s:Subject) … MATCH (s)<-[:ownedBy]-(e)` — i.e. it reads ownership in
exactly the orientation ONTOLOGY §3.6 declares. Measured on the four owners the
corpus names:

```
Amara Osei      → []
Dana Whitlock   → []
Tomas Ferreira  → []
Jonas Bauer     → []
```

`[]` for **every** owner, on a graph that holds six `ownedBy` rows. The reader
does not throw; it resolves the Subject id and finds no edge pointing into it,
because the writer put the edge the other way round. So the inversion is not
only a departure from the declared convention — **it makes the ownership
relation unreadable through the door the product provides for it**. (Control:
the same call on an Object also returns `[]`, which is expected under either
orientation, so it is not evidence; the four owner Subjects are.)

### W2 — the supported neighbourhood door cannot see structural edges at all

The retired name `tortoise_recall` still answers (a shim), and
`mode="subgraph"` is the only read that returns edge **direction**. Its declared
replacement is `tortoise_check_confidence(recall=True, …)`, which has no
subgraph mode. The live neighbourhood doors are:

| Door | `Lumen Core` (3 `hasPart` children) | `Amara Osei` (4 structural edges) |
|---|---|---|
| `tortoise_explore_connections(node_id, hops=1)` | **1** row — an epistemic `IMPL` to `control-room customers`. None of the three features. | `[]` |
| `tortoise_list_topics(entity_id)` | `{"error": "Entity 1a2165c9fd5-… not found"}` | `{"error": "Entity 1a2165cddc0-… not found"}` |

Two doors, same node, two different wrong answers — `[]` from one,
`not found` from the other — and neither can answer *"what does this product
contain?"* or *"which way does this edge run?"*. This is `out/t6-score.json`'s
`door_findings.D1` measured a second way, and it is why A5's structural walk
resolves to an entity 0/5: the walk starts from a Point because the entity is
not addressable.

### W3 — re-stating a fact in a later document duplicates the edge

The receipt holds **8** `hasPart` rows for **5** distinct product→feature pairs:
`doc-01` writes all five, `doc-05` re-states three of them and writes them
again. The graph therefore carries parallel edges for the same fact. T1 (double
multiplication) would score this as growth on a *re-worded* statement inside the
same ingest, and it is the structural face of the same defect: a second
document that *confirms* a fact adds a second edge rather than corroborating the
first.

## The one-sentence answer

Measured from this graph alone, a reader could name every product, feature,
team, role, person, component, customer and dependency this business has — but
could not say **who owns what** (the ownership edges point the wrong way and the
ownership door answers nothing) and could not walk the product→feature or
component→dependency tree with any supported read, so they could describe the
business's **inventory** and not its **shape**.
