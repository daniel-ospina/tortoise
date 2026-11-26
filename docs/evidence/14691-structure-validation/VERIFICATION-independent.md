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
| Probe | depth-2 `tortoise_recall(mode="subgraph")` from each of the 32 declared names; endpoint-type + edge direction read from the returned `{nodes, edges:[{source,type,target}]}`; every id the ingest receipt claims it wrote resolved with `tortoise_get_entity`. ⚠️ Both named tools EXECUTE and return data (measured: `node_count: 38, edge_count: 44` for `Lumen Core`, `get_governance` → `{result: []}`) while the bundle's own `out/tool-surface.json` lists **39 tools and neither name** — so the calls worked against a catalogue that does not advertise them. Recorded because a reader checking this row against `tool-surface.json` would otherwise read it as a fabricated claim. That divergence is a product finding of its own (an executable tool missing from the advertised surface), not a defect in this measurement. |
| Raw data | `out/t6-independent-verification.json` |
| Wrote nothing | no graph write, no extractor change (recorded decision (a)); the only store touch was reading |

## What agrees

The primary record's verdicts are `A1 PASS · A2 FAIL · A3 PASS · A4 PASS · A5 PARTIAL`
(`out/t6-score.json`). This is the second measurement of each:

| Assertion | Primary record | Second measurement (independent) | Agree? |
|---|---|---|---|
| **A1** types | PASS — 28/28 declared elements present with the right kind | every family holds its declared kind: 2 `product-strategy:product`, 5 `product-strategy:feature`, 3 `core:team`, **4 `core:role`**, 4 `core:naturalPerson`, 7 `dev:code`, 2 `product-strategy:customer`, 1 segment | ✅ — with the caveat in W0 |
| **A2** containment | PASS half — 5/5 present, parent is the product (`hasPart` is symmetric, so orientation is not at stake; the parent END is) | 5 distinct `hasPart` pairs: `Lumen Core → {Grid Map, Outage Alerts, Load Forecast}`, `Lumen Field → {Crew Dispatch, Asset Register}`, whole → part | ✅ |
| **A2** dependency | PASS half — 7/7 oriented correctly | 7 `dependsOn` rows, every one dependent → dependency (`event-bus → fact-store`, `web-dashboard → signal-engine`, `auth-service → identity-db`, …) | ✅ |
| **A2** ownership | **FAIL** — 0/6 rows correctly oriented | 4 distinct `ownedBy` pairs, every row `subject --ownedBy--> object`: the declared `->` means **the target owns the source**, so each row asserts *the thing owns the person* | ✅ agree, and see W1/W2 |
| **A2** production (`produces`) | **FAIL** — 0/7 inverted | 7 rows, all `team --produces--> component`; `produces` is `<-` (*the target brought the source into existence*), so each row asserts **the component produced the team** — same mechanism, opposite predicate | ✅ agree, W5 |
| **A2** `managedBy` | **FAIL** — 0/2 inverted | `Jonas Bauer --managedBy--> Northwind Logistics` / `Helio Health`; `managedBy` is `Entity → Subject`, so the row says **the customer manages the person** | ✅ agree, W5 |
| **A3** hierarchy | PASS — 5 pairs under 2 products, not a flat list; 8 write rows for 5 pairs (3 rewritten by a later doc) | 5 distinct parent→child pairs under 2 product parents; the receipt's 8 rows confirm the 3 rewrites | ✅ — see W3 |
| **A4** invention | PASS — 0 invented structure elements of 35 | the probe's `A1.objects` (24) and `A1.subjects` (11) are **lists of name+kind pairs**, and it ran no traceability check — so it does **not** independently corroborate A4's pass. The primary artifact alone carries that number (0 of 28 declared elements untraceable, 0 of 46 relations unstated), and it is the same artifact that names the **two** untraceable names (both knowledge-layer objects, not structure). Scored as: **names listed, traceability NOT re-measured here** | ⚠️ **not independent** — see F4 note below |
| **A5** retrieval | PARTIAL — search text answers 5/5, the structural walk resolves to an entity 0/5 | `tortoise_search` answered all four questions the probe asked (`search_ok: true` ×4, no walk rows at all); `explore_connections("Lumen Core")` returns **one** row and it is an epistemic `IMPL`, not one of its three `hasPart` children. The **0/5 walk figure is the primary scorer's**, over five questions; the probe measured four, and they are not the same four (`who-owns` here vs `product-of-customer` / `what-team-builds` there) | ✅ on what it measured — **the 0/5 is not corroborated by this probe**, only the text leg is |

## What this measurement adds

### W0 — "present" for the gateway component depends on a name match, not a name

The declared element is `ingest-gateway`; the graph's object is
**`ingest-gateway component`** (kind `dev:code`, correct). A1 passes because the
scorer matches a containment, but a caller asking the product for
`ingest-gateway` by that name does not get it — measured: the per-name read
returns `[]`/`not found` for the bare name (`out/t6-score.json`
`door_findings.D2`). The structure is there; the *identifier* the business uses
is not.

### W1 — the product's own ownership reader contradicts its own writer

`tortoise_get_entity(id, type="governance")` is the **supported** door for
*"what does this Subject own?"* — it dispatches to
`TortoiseSDK._get_owned_entities` (`tortoise/sdk.py:62739`), which is
`MATCH (s:Subject) … MATCH (s)<-[:ownedBy]-(e)`, i.e. ownership in exactly the
orientation ONTOLOGY §3.6 declares. Measured on the four owners the corpus names:

```text
Amara Osei      → []
Dana Whitlock   → []
Tomas Ferreira  → []
Jonas Bauer     → []
```

`[]` for **every** owner, on a graph holding six `ownedBy` rows. The reader does
not throw; it resolves the Subject id and finds no edge pointing into it. So the
inversion is not merely a departure from the declared convention — **it makes
the ownership relation unreadable through the door the product provides for
it**. (Control: the same call on an Object also returns `[]` under either
orientation, so it is not evidence; the four owner Subjects are.)

### W2 — the supported neighbourhood door cannot see structural edges at all

The retired name `tortoise_recall` still answers through a shim, and its
`mode="subgraph"` is the only read that returns edge **direction**. Its declared
replacement, `tortoise_check_confidence(recall=True, …)`, has no subgraph mode.
The live neighbourhood doors, on the same two nodes:

| Door | `Lumen Core` (3 `hasPart` children) | `Amara Osei` (4 structural edges) |
|---|---|---|
| `tortoise_explore_connections(node_id, hops=1)` | **1** row — an epistemic `IMPL` to `control-room customers`; none of the three features | `[]` |
| `tortoise_list_topics(entity_id)` | `{"error": "Entity 1a2165c9fd5-… not found"}` | `{"error": "Entity 1a2165cddc0-… not found"}` |

Two doors, one node, two different wrong answers — `[]` from one, `not found`
from the other — and neither can answer *"what does this product contain?"* or
*"which way does this edge run?"*. This is the second way
`out/t6-score.json`'s `door_findings.D1` was measured, and it explains A5's
`0/5`: the structural walk starts from a Point because the entity is not
addressable.

### W3 — re-stating a fact in a later document duplicates the edge

The receipt holds **8** `hasPart` rows for **5** distinct product→feature pairs:
`doc-01` writes all five, `doc-05` re-states three and writes them again. The
graph therefore carries parallel edges for the same fact. T1 scores that as
growth on a *re-worded* statement inside one ingest; this is the same defect in
the structural layer — a second document that **confirms** a fact adds a second
edge instead of corroborating the first.

### W4 — the structure is traceable; a fifth of the *claims* are not

A4 is written about structure, and on structure it holds: every declared
structure element traces to corpus text. The point layer does not. Audit of the
50 `quote` fields the extractor attached (`out/t6-quote-audit.json`):

| quote fidelity | count |
|---|---|
| verbatim substring of the corpus | 11 |
| faithful paraphrase (≥60% of the quote's words in one corpus sentence) | 27 |
| **best corpus-sentence overlap < 60% — the corpus does not say it** | **12** |

⛔ The `12` is the artifact's own count at the scorer's `<60%` threshold
(`out/t6-quote-audit.json` `not_in_corpus: 12`; `out/t6-score.json`
`claim_layer.invented: 12`), and it is the number that governs. An earlier
revision of this section said **"10 … overlap < 0.5"**, which was wrong twice:
os overlaps are `0.636, 0.875, 0.958, 1.0, 1.0, 1.0, 0.941, 1.0, 1.0, 1.0`, so
**no** audited row falls below 0.5, and `10` is the length of a **capped example
array**, not a count under any threshold. Corrected rather than dropped, because
a number a reader can check against the artifact it cites must be the artifact's
own number.

The strongest example is a point the corpus never makes, attached to a ``quote``
that is not in the corpus at all — taken from `out/t6-score.json`
`claim_layer.invented_examples` (which is where `score_t6.py` derives it), **not**
from the audit's capped `audited` array, where it does not appear:

> "Any future proposal to share or duplicate a feature across the Lumen Core /
> Lumen Field boundary would be a change to this baseline, not a continuation
> of it."

`"any future proposal"`, `"baseline"` and `"no shared feature surface"` appear
nowhere in the corpus (verified by substring search). The structure is not
invented; **the analysis around it is**, and it is presented as a quotation,
which defeats the provenance anchor the record depends on (§2.0 record-is-truth).

### W5 — one mechanism, three predicates: the write path never reconciles word order with declared direction

The direction map is `SEMANTICS_DIRECTIONS` (`tortoise/pack_registry.py`), identical to ONTOLOGY §8:

| predicate | declared | what a row `src --p--> dst` therefore asserts | rows written | correct |
|---|---|---|---|---|
| `dependsOn` | `->` | the dependent falls when the precondition is absent | 7 | **7** |
| `ownedBy` | `->` | **the target owns the source** | 6 (4 pairs) | **0** |
| `managedBy` | `->` | the target is the responsible party | 2 | **0** |
| `produces` | `<-` | **the target brought the source into existence** | 7 | **0** |
| `hasPart` | `<->` | composition (symmetric — orientation not at stake) | 8 (5 pairs) | n/a |

So of the **20 asymmetric structural pairs** this run wrote, **7 are correctly oriented and 13 are
inverted** — and the inversion is not one predicate's quirk: `ownedBy`, `managedBy` and `produces` are
all written in the sentence's word order, two of them against a `->` declaration and one against a
`<-`. The writer (`tortoise/relation_write.py`) passes the model's endpoints straight to
`link_entities(predicate, src_id, dst_id)` with no re-ordering and no per-predicate branch, so there
is one mechanism behind all three failures.

## The one-sentence answer

Measured from this graph alone, a reader could name every product, feature,
team, role, person, component, customer and dependency this business has, and
could recover the product→feature and component→dependency trees only by reading
the *sentences* that mention them — because **who owns what is unreadable**
(the ownership edges point the wrong way and the ownership door answers nothing)
and no supported door walks a structural edge with its direction; so the graph
holds the business's **inventory and its sentences**, not its **shape**.
