# #14691 T1–T5 — the five noise tests, measured on a fresh graph

**Head measured:** `31a320e392a7b7bed06d6a33d16b5cc34e7d76e9` (main, includes `#14790`'s write-path
orientation fix). **Graph:** `xv14691_noise` on this lane's own container
(`docker://:@127.0.0.1:16420/tortoise_test_matrix`) — never the shared 6379 instance, never `live-v5`.
**Corpus:** `corpus/noise-corpus.json`, 6 documents, **5,280 chars ≈ 1.3k tokens**.
**Model:** `deepseek-v4-flash` (the extraction default). **Tokens:** 157,787 prompt + 20,158 completion
(23 calls) for pass 1; 161,487 + 18,948 (24 calls) for pass 2 — **358,380 total, 47 calls,
≈272× amplification, `cost_usd: 0.0` on all 47 calls** (the product still cannot price its own
extraction: `calls_without_cost: 47`).

Every write went through `mine_knowledge_from_session`; every read went through `graph_overview`,
`search_knowledge`, `get_entity`, `explore_connections` or `list_knowledge` — the SDK doors the MCP
tools map onto. No Cypher, no hand-rolled HTTP. A question those doors could not answer is recorded
in §7, never worked around.

## 1. The answer in one sentence

**No — this is not yet a logical model of that business: the types are used and mostly correct, but
three of the seven orphans carry names below the naming band and three relations are unsupported (two
of them landing on one invented entity, `the wholesale push`), and every fact is stored
as a new sentence each time it is heard: on a second reading of the same six documents the Point layer
grows **+81 %** (134 → 243) while the entity layers move far less — **Subject +0**, Object +4, Event +14
— so the growth is almost entirely the claim layer, not the model.**

## 2. The five tests

| # | test | verdict | the numbers |
|---|---|---|---|
| **T1** | duplicate multiplication | **FAIL** | pass 1 → `Point 134 · Event 12 · Subject 10 · Object 25 · Source 6`; pass 2 → `Point 243 · Event 26 · Subject 10 · Object 29 · Source 12`. **+109 Points (+81 %), +14 Events, +4 Objects, +6 Sources, +19 operators, +90 structure rows.** `Subject +0`, `0` same-name entity duplicates. **40 of the 61 Points the write receipt reports for pass 2 (66 %)** re-state a pass-1 Point at ≥ 0.80 content-token overlap (31 of them at 1.00) — and all 40 were written anyway: 28 carry `dedup: "new"` (not recognised at all), and 12 took the `content_hash_hit` path, i.e. recognised as a duplicate and created regardless. Every one of the 12 content-hash hits is a restatement.** Relations did **not** grow: 26 written on pass 1, 21 on pass 2 (−5). |
| **T2** | same fact, two wordings | **FAIL** | the goal fact ("40 restaurant accounts by December … 18 today, 22 to go") is stated in **doc-03** and restated in **doc-06**. Ingesting doc-06 added **+15 Points, +1 Object, +1 Event, +1 Source** and produced **2 distinct goal Points with no shared id** (`shared: []`) — so the goal leg fails: the goal fact is **two sentences per wording**, and a resolution test is the right bar (a complete merge resolves two ids; a count of shared ids would pass a half merge and fail a complete one), so what fails here is that **both** doc-06 candidates resolve to **none** of doc-03's ids. The entity delta is non-zero too. The retrieval probe's **top hit is `doc-03-replay`** — the pass-2 duplicate outranks the original. ⛔ The rule's *"carrying two sources"* leg is **not graded**: the `sources` door answers per-Source POINT COUNTS, not point→Source links, so the question cannot be put to the committed readback at all (gap **D7**). |
| **T3** | orphan share | **FAIL** | of the **30 entities the doors can enumerate**, **7 (23.3 %)** have **no entity→entity edge** in either write receipt — so the *share* leg passes (23 % is far from "the majority is orphaned" — graded strictly: at exactly 50 % neither side is a majority, so the clause fails there). **The second conjunct fails**: `RULE_T3` also requires every orphan's name to be stated in the corpus, graded as verbatim or **≥ 0.80** of the name's own content-token overlap (the band `RULE_T1` uses), and **3 of the 7 fall below that** — `the ownership map` (`core:standard`, best 0.500 vs doc-04), `the cold-chain rules as operational preferences` (`core:strategy`, best 0.600) and `chilled-goods operation` (`core:workflow`, best 0.667). Every orphan is named in §5 with its traceability, and §5 states this predicate's two limits. The **door** says **0 of 30 are isolated** — because it counts a Point mention as a neighbour (§7 D4). |
| **T4** | type collapse | **PASS** | flat distribution `Point 243 · Event 26 · Subject 10 · Object 29 · Source 12`; **13 distinct entity kinds used**: `core:naturalPerson` 3, `core:role` 3, `core:organization` 4, `core:workflow` 3, `core:strategy` 3, `core:agreement` 2, `core:standard` 2, `core:goal` 1, `core:decision` **1**, `core:Project` 1, `agent-ops:rule` 3, `venture:actionItem` 3, `product-strategy:customerSegment` 1. A decision is represented by a **decision-kind entity** (`the delivery partner decision`, `core:decision`), the **3 `core:role` entities are Subject-layer** (Sales Lead, Finance Director, Operations Lead), and documents land as **Sources** (6 per pass). ⛔ Disclosed, not graded: the decision **SENTENCES** are Points and land as `statement` kind — 2 decisions are stated and `decision_point_kinds` is `["statement"]`, with the 80 event-kind Points unrelated to them. The typed representation exists; the decision's own sentence is still a statement. |
| **T5** | invented connections | **FAIL** | **all 47 relations both passes wrote** are sampled — 26 from pass 1 and **21 from pass 2**, because pass 2 is where re-extraction invents names — and the sample is what makes the count honest: pass-1-only sampling reports **2** unsupported edges and still FAILs, so the wider population did not rescue the verdict, it strengthened the evidence by finding the third. **34 verbatim** (both endpoint names appear in one document), **10 paraphrase** (an endpoint is the extractor's own name for a stated thing, or is re-worded at ≥ 0.60 content-token overlap), **3 with no support** — each a name the corpus never says: `the wholesale push` as the target of `related` from `ColdPath Logistics` and from `NordicFreight` (the token `push` occurs **nowhere** in the six documents), and pass 2's `ownedBy: monthly account count report → Priya Nair` (doc-04 says Priya *reports* the count monthly — an action, not an entity; best overlap 0.50). ⛔ Content tokens exclude common English function words: an earlier cut counted them, and `the wholesale push` cleared the 0.60 band on `the` + `wholesale` alone with `push` absent from the corpus. So **three invented edges fail T5**, which is the rule. ⛔ **The PREDICATE axis is not graded by T5** — the graph's predicates are ontology names (`ownedBy`, `related`, …) and the corpus is prose, so a wording test would score a correct normalisation (*"owns"* → `ownedBy`) as an invention. the predicate axis is reported as gap **D6**, including the reason the obvious spelling check cannot carry it. |

Scorer: `score_t1_t5.py` (pure function of `out/ingest-receipt.json` + `out/readback.json`; no graph,
no network, no model call). Each verdict is a rule over numbers, and the rule is quoted beside it in
`out/t1-t5-score.json`.

## 3. The premise: the graph really was empty

`empty_before` in `out/ingest-receipt.json` is the full `graph_overview()` reply taken before any
write: `taxonomy {"Point": 0, "Event": 0, "Subject": 0, "Object": 0, "Source": 0}`,
`structure.total 0`, `operators 0`. `run_ingest.py::refuse_if_not_fresh` **raises** on any content
found anywhere in that reply — it is a function so the bundle's suite drives the refusal itself, not
its predicate. A run against a populated graph exits non-zero and writes no bundle.

## 4. T1 — the noise test, and the mechanism behind it

Pass 2 is not an idempotent replay: it uses distinct session ids (`doc-01-replay` … `doc-06-replay`),
i.e. the same six documents uploaded again, which is the owner's failure mode ("previous graphs became
all noise"). What grew:

| axis | pass 1 | pass 2 | delta |
|---|---|---|---|
| `Point` | 134 | 243 | **+109** |
| `Event` | 12 | 26 | +14 |
| `Subject` | 10 | 10 | **0** |
| `Object` | 25 | 29 | +4 |
| `Source` | 6 | 12 | +6 (one per re-uploaded document — expected) |
| `structure.total` | 101 | 191 | +90 |
| `operators` | 33 | 52 | +19 |
| relations written | 26 | 21 | **−5** |

Two different layers behave in two different ways, and the difference is the finding:

* **Entity identity holds.** `Subject +0`, `Object +4`, and **0** names that resolve to two ids. The
  dedup that exists works at the *entity/name* layer.
* **The Point layer does not.** 61 Points were created on pass 2; **40 of them re-state a pass-1
  Point** at ≥ 0.80 content-token overlap — **31 of the 40 at 1.00** — and **28 of those 40 carry
  `dedup: "new"`** (not recognised at all) while **12 took the `content_hash_hit` path and were
  created regardless**; all 12 hash hits in the run are restatements. `merges: 0`, `deletions: 0` on every document
  in both passes. Examples (full list in `out/t1-t5-score.json` → `tests.T1.restated_points_pass_2`):

  | overlap | dedup | pass 2 said | pass 1 already held |
  |---|---|---|---|
  | 1.00 | new | "We missed four morning restaurant slots in January because of NordicFreight…" | "…NordicFreight's 6pm order cutoff cannot make our morning restaurant slots…" |
  | 0.90 | new | "ColdPath Logistics offers a 9pm order cutoff and a validated cold-chain certificate" | "ColdPath Logistics offers a 9pm cutoff that meets…" |
  | 1.00 | new | "Our cash buffer is 31,000 pounds and is committed to the March supplier payments" | same fact, pass 1 |

* **Event-kind Points re-mint in lockstep.** `points_by_kind.event` goes **40 → 80** — every event-kind
  Point minted on pass 1 is minted again on pass 2, exactly, which is the cleanest single witness of
  the defect: nothing about a re-reading of the same sentence makes it a new event.
* **Relations are the exception.** The relation layer did not grow (26 → 21). Whatever dedup the
  writer applies to edges holds; it is the Point/claim layer above it that multiplies.

`Graph audit` at the end: `node_count 243`, `edge_count 107`, and `missing_sourceKind_source` **67** —
67 Source-bearing rows whose Source carries no `sourceKind`. That is a data-quality defect visible
from the product's own audit and is reported as a gap (§7 D5), not folded into T1.

## 5. T3 — the orphans, named

Population: the 30 entities the typed full scan resolves (**20 of 29 Objects, 10 of 10 Subjects,
0 of 26 Events** — §7 D2). Orphan = **no entity→entity edge** in either pass's `relation_write`.

| orphan | kind | traceable? | why it is standalone |
|---|---|---|---|
| `the ownership map` | `core:standard` | **below the naming band** (best 0.500, doc-04) | the corpus says "write down the ownership"; `map` is this extractor's own word, so at the rule's own tokenisation the name scores 0.500 — below the 0.80 band |
| `revert to NordicFreight as delivery partner` | `core:strategy` | paraphrase of doc-05 | names the reversal decision; the decision is a fact about the corpus's ending, not a node with parts |
| `the own refrigerated van option` | `core:strategy` | paraphrase of doc-01 Option C | a rejected option — standalone by nature |
| `the 4 degrees Celsius cold-chain constraint` | `agent-ops:rule` | paraphrase of doc-02 | a rule with no declared part-of edge to a parent policy |
| `restaurant account` | `product-strategy:customerSegment` | **verbatim** (doc-03/04/05/06) | the segment counted by the goal; it has no containment edge because nothing in the corpus contains it |
| `the cold-chain rules as operational preferences` | `core:strategy` | **below the naming band** (best 0.60, doc-02/03) | invented label |
| `chilled-goods operation` | `core:workflow` | **below the naming band** (best 0.667, doc-02/05) | the words *"chilled goods"* are in doc-02, but not as this name |

**4 of 7 are verbatim or well above the naming band; 3 fall below it. T3 is FAIL** — the *share*
criterion passes (23 % is far from "the majority is orphaned"), but `RULE_T3`'s **second conjunct
does not**: every orphan's name must be stated in the corpus at ≥ 0.80 of its own content-token
overlap (the same band T1 uses), and all three of these are below. The mechanism is the same naming
defect T5 measures one layer out, and the two counts answer different questions: **3 of 47 relations**
have an endpoint the corpus never names (T5's `none` tier — a further **10** are the extractor's own
re-wordings and are `paraphrase`, i.e. SUPPORTED), and **3 of 7 orphans** have a name below this band.

⛔ **Two limits of this predicate, stated rather than left to be assumed away.** (a) It is a **NAME**
test, at **T1's restatement band (0.80)**, not T5's narrower naming band (0.60). At 0.60 only
`the ownership map` (0.500) is still unnamed — `the cold-chain rules as operational preferences` (0.600)
and `chilled-goods operation` (0.667) clear that band, T5's own threshold being inclusive — so the
count is **1 of 7** there; at 0.80 it is **3 of 7**, which is what `RULE_T3` states and what the verdict
uses. Both counts are read off `orphan_traceability[*].best_overlap` in the artifact, not restated by
hand. ⛔ The
tokenisation is the rule's own (`overlap()`, content tokens of >2 characters) — an earlier cut of this
file filtered to >4-character tokens, which dropped `map` from `the ownership map` and scored that name
1.000 instead of 0.500.
The two bands answer different questions and neither is folded into the other. (b) It tests whether
the name is *stated*, not whether the entity is *standalone*: `restaurant account` is counted
explained because the name is verbatim, although doc-03 relates it ("Every new restaurant account
needs a morning delivery slot"). So this conjunct catches an invented **name**; an entity that is
real but not standalone passes it.

## 6. The other two failures in T2's shape

T2's corpus pair is doc-03 (goal stated) / doc-06 (goal restated in different words, nothing new in
it — it restates the forty accounts by December, eighteen held and twenty-two outstanding, the
sub-four-degree cold-chain limit with its morning delivery slot, and **doc-04's** Priya ownership of
onboarding and the restaurant account pipeline). ⛔ It does **not** name Marta or Dev — doc-06 names
neither, while doc-01, doc-02 and doc-04 name both and doc-05 names Marta — and an earlier spelling of
this paragraph quoted "Marta and Dev have it in the ownership note", which appears nowhere in the
corpus at all: the suite pins doc-06 as Marta/Dev-free, so the report and its own pin disagreed about
the same document. Ingesting doc-06 — *during pass 1, on an
already-populated graph* — added:

`Point +15 · Object +1 · Event +1 · Source +1 · structure +14 · operators +1`, with the goal fact
landing as **two Points with no shared id** and the retrieval probe's rank 1 coming from
`doc-03-replay`. So the "+0 entities" leg is missed: entities grew and the goal landed as two Points.
⛔ The "+1 source link" half is **not measured** — the `sources` door answers per-Source point COUNTS,
never point→Source links (D7), so which Point the second Source attached to cannot be read from the
committed artifact. The two Points sharing no id is what the bundle can assert.

## 7. Door findings (measured, not worked around)

* **D1 — `graph_overview("namespaces")` carries no kind names.** It returns
  `namespace` + `name` + `kind_count` only (measured:
  `[{"namespace": "agent-ops", "name": "Agent Operations", "kind_count": 4}, …]`). An agent asking "what kinds does
  this graph hold" gets counts it cannot act on; the bundle builds its kind vocabulary from the
  product's own `pack_registry` instead and says so (`readback.json` → `vocabulary_source`).
* **D2 — Events are not enumerable.** `search_knowledge(entity_type="event")` over every declared
  event kind resolves **0 of the 26 stored Events**. Anything an agent wants to know about events has
  to be answered from a Point's text.
* **D3 — a bare or absent kind answers an empty list, silently.** `search_knowledge(entity_type =
  "object")` (no kind) → `[]`; `kind="core:organization"` → 4. T6 reported the bare-kind variant; this
  is the same wall one layer out, and it is why the population coverage is stated as a number (30 of
  65 = 46 %) instead of assumed.
* **D4 — "connected" is not "modelled".** `explore_connections` reports **0 isolated entities** of the
  same 30 that the write receipts show 7 with no entity→entity edge, because it counts a Point *mention*
  as a neighbour. The door cannot distinguish "this thing is part of the model" from "this thing is
  named in a sentence".
* **D5 — the graph's own audit flags 67 rows** `missing_sourceKind_source`: Sources carry no
  `sourceKind`. Not a T1–T5 verdict input, but it is the product reporting against itself and should
  not be lost.
* **D6 — a relation's PREDICATE is not verifiable through the product's prose.** T5 grades that both
  endpoints of each sampled relation are stated in one document; it cannot grade the *predicate*,
  because the graph stores ontology names (`ownedBy`, `related`, `dependsOn`, `IMPL`, `produces`)
  while the corpus is English. ⛔ The count of rows whose predicate spelling appears in its co-stated
  document is **0 of 47** — and that number proves nothing, because both sides are the SAME ontology
  vocabulary, so a literal spelling match is 0 by construction (`out/t1-t5-score.json` →
  `tests.T5.predicate_axis`). It is recorded so the field exists, not as evidence that the axis is
  clean. So an edge whose endpoints are real but whose *predicate* is invented
  is not caught by this bundle, and that is a hole in the acceptance bar rather than a property of
  the extractor. Closing it needs a predicate-level check (the corpus's own verb, normalised to the
  ontology's predicate) which the doors do not offer today.
* **D8 — three Point totals for one window do not reconcile.** Pass 2 moved `taxonomy.Point` by
  **+109**, the graph's `pointkinds` by **+50 `statement` +40 `event`**, while the write receipt
  reported **61** points. Every point the receipt reports is `statement` kind (`tests.T1.
  point_totals_do_not_reconcile`), so the event-kind Points the graph mints internally never appear in
  a receipt — and the receipt's 61 is therefore the restatement population `RULE_T1` grades, not the
  graph's Point count. Recorded, not reconciled: reconciling them is a product question. It is also
  why `RULE_T1` does not claim to count an event-kind Point re-minted for an already-stated sentence —
  that population is not visible in this artifact — and why §4's "every event-kind Point minted on
  pass 1 is minted again on pass 2, exactly" is **an inference from a count doubling (40 → 80), not a
  per-point match**; D2 says events are not enumerable through the doors, so a match cannot be checked.
* **D7 — a Point's SOURCES cannot be counted.** T2's rule asks whether the goal fact resolves to one
  point *carrying two sources*. The `sources` door answers per-Source **point counts**
  (`{"url": "session:doc-03", "points": 8}`), never point→Source links, so "how many sources does
  *this* point carry" has no answer through the product. Measured in
  `out/readback.json` → `sources.reply`: 12 rows of `url`/`sourceKind`/`points`, no per-point
  reverse index. T2's verdict therefore grades the entity delta and the goal's RESOLUTION (every doc-06 candidate reusing a doc-03 id), and this leg is
  disclosed rather than silently implied (`out/t1-t5-score.json` → `tests.T2.source_link_leg`).

## 8. Gaps — what this bundle does NOT measure

1. **The same-session replay.** Pass 2 uses fresh session ids on purpose (§4). A retry of the *same*
   capture may behave differently (it can key on the session), and that variant has no number here.
2. **Point-level epistemic edges.** A Point's edges to its Subject/Object are not in
   `relation_write`, so T3's orphan population is **entities only**; the share of orphaned *Points* is
   unmeasured, and that is the layer where T1 fails worst.
3. **Event semantics.** Whether one Event per capture is intended is not stated anywhere the lane could
   read; the bundle reports the increment (+14) without judging it, and counts only *event-kind Points*
   (+40) as noise.
4. **One model, one run.** `deepseek-v4-flash`, one ingest per pass, no repetition — so extraction
   variance is unknown and the numbers are one draw.
5. **Cost is not measurable through the product**: `cost_usd: 0.0`, `calls_without_cost: 47`. The token
   counts above are the only cost signal.
6. **Retrieval quality**: this box's `embeddings` extra installs, but the run made no embedding
   assertion; the probes are FTS-led and their ranking is a floor, not a measurement of hybrid recall.
7. **The contradiction (doc-01 vs doc-05).** This bundle did not score whether the corpus's
   contradiction is represented — that is `#14198`'s home and T6 recorded it as a known defect; T1–T5
   do not depend on it.

## 9. What a fix has to act on (not done here — this measures)

The issue forbids changing the extractor in this lane. The measurements point at one seam and one
missing rule:

1. **The Point write path needs the rule the entity path already has.** 40 of 61 pass-2 Points restate
   a pass-1 Point at ≥ 0.80 overlap — 28 tagged `new`, 12 recognised by content hash and written
   anyway; the writer that reconciles a
   *name* to an entity has no counterpart that reconciles a *claim* to an existing Point (the
   `content_hash_hit` path exists and created the row anyway).
2. **"Two wordings, one thing" needs a merge, not a dedup verdict.** T2's failure is not a failed hash
   match — it is that no step asks "is this the same fact, said differently", which is the definition of
   the test.
3. **Deterministic document identity.** Each re-upload minted a Source and a full set of Points. Naming
   the document by content (the corpus's `doc-01`…`doc-06` ids already exist) is what makes re-ingestion
   idempotent rather than merely re-deduped — the comparable practice outside this repo
   (`docs.falkordb.com/graphrag/ingestion`, `…/storage`: deterministic document/chunk ids, idempotent
   `MERGE`, provenance edges, then cross-document entity resolution).

## 10. Reproduce

```bash
export TORTOISE_DB_URI="$(uv run python tools/test_lane.py uri | sed -n "s/^export TORTOISE_DB_URI='\(.*\)'/\1/p")"
bash docs/evidence/14691-noise-validation/run.sh docs/evidence/14691-noise-validation/run_ingest.py
bash docs/evidence/14691-noise-validation/run.sh docs/evidence/14691-noise-validation/run_readback.py
uv run python docs/evidence/14691-noise-validation/score_t1_t5.py
```

`run_ingest.py` refuses a non-empty graph; `score_t1_t5.py` needs no graph at all.
