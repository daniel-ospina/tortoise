---
title: Extractor v4 — Architecture
type: engineering
domain: platform
doc_status: draft
created: 2026-09-23
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: EXTRACTOR-V4-ARCHITECTURE.md, extractor pipeline, S2.2 VET, entity resolution
---

# Extractor v4 — Architecture

**Status:** design in progress, 2026-09-23. **Home:** `#4894` (write-path redesign). **Predecessor:** `#1509` (extractor v3, approved 2026-08-20, never built).

**⚠️ Code citations — revision pin.** Every `file.py:NNNN` reference here is pinned to commit **`c79ba1cf2`**. Line numbers drift as the code moves; **the symbol name is authoritative and the line number is a convenience.** Verify against the symbol, not the number.
**Related:** `docs/architecture/STORAGE-ARCHITECTURE.md` · `#1026` (pack slots) · `#2281` (ask/answer over distilled knowledge) · `#2730` (entity attachment) · `#4333` (volume/economics) · `#4240` (edge durability) · `#4911` (secret redaction) · `#4899` (the gate). **Full issue map: §15.**

---

## 1. Why v4 exists

### The measured problem
Production graph: **~25,000 quota nodes in 3–4 active days** (~27 per emitting session). Composition: **14,851 statements (59.5%)**, **7,863 Objects (31.5%)**, 2,233 operators (8.9%). **⚠️ Node counts move continuously — the Object figure is a snapshot and does not match every other figure in these documents; each one is date-stamped at its source.**

**The mechanism, found by audit (50 real instances, `#4894` comment 2026-09-23):** the extractor mints **an entity for the grammatical subject of each statement.**

| Object it created | the statement it came from |
|---|---|
| `the timeout command` | "timeout is not on macOS" |
| `the staging-only constraint` | "…staged only, no commit/push" |
| `the lane ownership rule` | "Lane ownership: #4668/#4648 belong to…" |
| `PR #465` · `issue #3775` · `§24` | pointers |
| `tests/test_frontmatter_validator.py` · `_TOOL_BY_NAME` · `gh api -X PATCH` | references |

**62.3% of Objects are `the <X>` (definite descriptions); 37.7% are referenced by exactly one point; zero duplicate names.** So the problem is **not** duplication — it is **writing things that were never entities.**

### v2 → v3 → v4
| | the question it answers | effect on volume |
|---|---|---|
| **v2** (live) | did we extract it? | baseline |
| **v3** (approved, unbuilt) | did we extract it **correctly**? (dates, verbatim state values, atomic points, supersession) | ⬆️ **increases it** — E2 preserves more, E3 splits into more |
| **v4** (this document) | should we have extracted it **at all**? | ⬇️ **reduces it** |

**v3 has no discard, trim or gate anywhere.** It is a fidelity workstream. **v4 is a selection workstream — and it must land with, or before, v3's volume-increasing parts (E2, E3).**

⚠️ **Part of v3 has already landed.** Its E4 (`"S4 merges, not replaces"`) is live — `merge_embed_lists(embed_list, s4)` runs at `extractor_v2.py:4845` and S2 is preserved. **v3's premise there is stale; re-check the epic's status before building from it.**

---

## 2. Three tiers — and where each one lives

> **Numbering note:** the three tiers are described in the prose below, not as numbered
> headings, so this section's first numbered subsection is **§2.4** — the deliberate
> "fourth layer". The gap is intentional; §2.1–§2.3 have no headings.

> **⚠️ Plus a fourth, proposed 2026-09-23 — the verbatim raw fact. See §2.4.**

**The architecture has three tiers with different cost/quality trade-offs. Earlier drafts conflated the first two, and named the third wrongly.**

| tier | what it is | **where it lives** | cost | retrievable? | lossy? |
|---|---|---|---|---|---|
| **Raw** | the verbatim turns / source material | **Supabase storage — NOT the graph** | expensive (13.8 MB of our 140 MB) | only verbatim, via its `Source` | not at all |
| **Narrative** | the S1 summary — *"what changed"* | **Supabase storage — NOT the graph** | cheap (a few KB per session) | **yes, full-text** | yes |
| **Entities** | **everything the graph holds** — `Source`, `Object`, `Subject`, `Event`, `Point` (+ operators) | **the graph** (record layer for `Source`/`Event`; derived layer for `Object`/`Subject`/`Point`) | the thing we must reduce | yes — and the claims are **arguable** | claims: intentionally. `Source`/`Event`: they are the truth |

⚠️ **Correction (owner, 2026-09-23): the third tier is `Entities`, not "Claims".** Calling it "Claims" hid the fact that the graph also holds `Source`, `Object`, `Subject` and `Event` — and that **only some of those grow without bound.** The tiers are **Raw / Narrative / Entities**, and the column that matters is **where each physically lives**, because that is where the cost is.

### ⚠️ Storage rule for the first two tiers — they are OUTSIDE the graph (owner, 2026-09-23)
**Neither raw nor narrative belongs in the graph. Both live in Supabase storage.**
> *"the narrative is not something we're suggesting to store in the graph (same as raw) but store in supabase."*

**Why this is not a detail:**
- **A narrative in the graph is a narrative in RAM.** The graph is the RAM-resident layer (`STORAGE-ARCHITECTURE.md` §2), so putting prose there pays memory prices forever for text that is only ever **read**, never **argued about**. **The narrative is a searchable string, not a belief** — it has no confidence, is not `NAND`-able, and takes part in no operator.
- **The graph keeps a reference, not the text.** The `Source` entity stays in the graph — it is the provenance anchor every claim points at — while the **heavy text** (turns and narrative) stays in Supabase, reached through it.
- **It is far cheaper than the graph** — the storage research measured **~580× on disk-resident data** (⚠️ **not the *"~1,000×"* an earlier draft stated — that number was unsourced; 580× applies to the disk-resident leg, not to the vector index).** ⚠️ **And *"carries most of the retrieval value"* is NOT measured and is REMOVED** — §10 of this document itself says *"No head-to-head measurement exists."* **The claim to keep is the one that IS supported: the narrative is the middle ground that makes raw turns droppable.**
- **It is the middle ground** that makes raw turns droppable without losing *"what happened in this session?"*
- **It is connected by construction** (§4, S1) — so it links to Objects/Events/Points rather than being an island.
- It directly serves `#2281`: *"ask/answer surface reads ingest-time-distilled knowledge … with raw-turn fallback."* **The narrative IS that distilled layer.**

**⇒ D1 restated, correctly:** the narrative is **not a graph node and not graph metadata** — it is **Supabase-stored text referenced by its `Source`.** It must never be a `Point` (it is not a claim) and never a `Document` node (it is not a discovered document). §9 carries the corrected D1.

### 2.4 A FOURTH layer — the verbatim raw fact (owner, 2026-09-23)
> *"we should perhaps add a 4th layer which is raw facts and also needs extracting … sometimes raw facts were being deformed and that's why memory systems sometimes keep them separate … it wouldn't be in the graph but just raw storage and maybe associated with the graph entity."*

**The problem it solves:** extraction **rephrases**. A `Point` is the model's rendering of what was said, not what was said. During the LongMemEval work, **raw facts were being deformed** — and that deformation is silently baked into the belief layer, where nothing downstream can detect it.

**The layer:** the **minimal verbatim span** each `Point` was derived from, kept **verbatim in raw storage (Supabase), not in the graph**, and **linked to the `Point`/`Object` it supports**. Distinct from the other three by granularity and purpose:

| layer | what it is | granularity | purpose |
|---|---|---|---|
| **Raw** | every turn of the session | whole session | conversation replay |
| **Narrative** | the S1 summary | whole session | *"what happened?"* — searchable, cheap |
| **Entities** | `Source`/`Object`/`Subject`/`Event`/`Point` | graph | belief, argument, reasoning |
| **⭐ Raw fact** *(proposed)* | **the exact span a Point came from** | **one claim** | **fidelity — the truth the Point must not drift from** |

#### ⭐ The experiment already exists — `#3011`, pre-registered and frozen 2026-09-11
**This is not a new idea here, and it already has external evidence behind it.** `docs/experiments/2026-09-11-abc-context-assembly-experiment.md` (frozen; metrics and falsification criteria may not change after the first run) tests exactly this as **arm C**:
> **Arm C — union:** Arm B's subgraph **plus the verbatim source turns each subgraph claim derives from, linked in place** (`C1 ⟵ session 12, turn 4`)

**And the external ablation it cites is strong:**
> **Verbatim beats derived** — arXiv 2601.00821v3 (controlled ablation): verbatim source beats extracted artefacts by **15.9 pts** (LoCoMo) / **22.0 pts** (LongMemEval-S); **union matches chunks; artefacts alone forfeit the gap**.

**⚠️ ⛔ THE THRESHOLDS WERE DELETED ON A FALSE PREMISE — RESTORED FROM THE FROZEN DOCUMENT (2026-09-24).** This block previously said the earlier draft *"rendered"* the hypotheses wrongly and that *"Review found 3 of 3 embellished… the `−5` belongs to `H4`, and the 15 belongs to the FALSIFICATION CRITERIA, not to `H1`. **The specific thresholds have therefore been REMOVED.**"*

**⭐ That correction was itself WRONG, and it deleted three accurate numbers.** Verified against the frozen file (`docs/experiments/2026-09-11-abc-context-assembly-experiment.md`, §2 "Hypotheses (frozen)", unchanged since 2026-09-11):

| # | hypothesis | frozen prediction |
|---|---|---|
| **H1** | Union beats subgraph-alone | **`C > B, ≥15 pts`** ✅ |
| **H2** | Verbatim beats subgraph-alone | **`A > B, ≥10 pts`** ✅ |
| **H3** | Union matches or beats verbatim alone | **`C ≥ A − 5 pts`** ✅ |
| **H4** | Subgraph-alone is sufficient *(falsifier)* | `B ≥ A − 5 pts` |
| **H5** | The renderer/selection path is the binding constraint *(falsifier)* | `B < A − 15 pts` **and** answer-bearing-claim presence ≥ 80% |

**The earlier draft was correct about all three.** The cycle-1 review's premise was wrong: `−5` appears in **both** H3 and H4, and `15` appears in H1 **and** H5's falsifier — **"also present elsewhere" was misread as "not present here"**. **⇒ Restored. The lesson this cost us is recorded in §16.2.**

**⚠️ Still read them from the frozen doc before using them:** `docs/experiments/2026-09-11-abc-context-assembly-experiment.md`. **The falsifier's ROLE is the claim to keep, and it survives: a result where verbatim alone beats the union is a statement about which arm to SERVE AT RETRIEVAL, not a verdict on the belief layer.**

#### ⛔ What the epistemic subgraph is actually FOR (owner, 2026-09-23) — and it is NOT retrieval
> *"the epistemic subgraph is also key for agents who want to change something to understand why it is the way it is, also for calculating what's valuable via EP (so making decisions) and audits too. so the facts are a very different objective than epistemic graph."*

**This matters because the `#3011` framing invites exactly the wrong reading.** If verbatim beats the subgraph **as retrieval context**, that says nothing about the subgraph's purpose — it says the subgraph was being tested as the wrong thing. **Its purposes are three, and none of them is *"be the reader's context"*:**

| what the epistemic layer is FOR | why raw facts cannot do it |
|---|---|
| **Understanding *why* something is the way it is** — so an agent can **change** it safely | raw text says what was said, never what **supports** or **attacks** it |
| **EP-based valuation → decisions** | a confidence is **computed over the operator graph**. Raw text has no confidence, and nothing to propagate over |
| **Audit** | an audit asks *"what did we believe, on what grounds, and what changed it"* — a chain of support and attack, not a transcript |

**⇒ The raw-fact layer and the epistemic layer are DIFFERENT OBJECTIVES, not competing options.** They answer different questions. **Serving verbatim at retrieval while keeping the epistemic layer for decisions, change-explanation and audit are not in tension — that IS the union arm's point.**

**⚠️ Consequence for this design, and it is a constraint:** the fourth layer must **not** be built as a cheaper *substitute* for `Point`s, and `Point` extraction must **not** be weakened on the grounds that the raw span is retained. **The span is the EVIDENCE; the `Point` + its operators are the ARGUMENT.** Both are kept, for different consumers.

⚠️ **`#3011` is OPEN — the experiment has not run.** So the layer is **evidenced externally and unmeasured on our stack.**

#### The mechanism is also already filed — `#2684`
**`#2684`** *"verbatim value-spans on value-bearing points (extractor value fidelity)"* already specifies the schema half: *"value-bearing claims (numbers, dates, prices, counts, quantities) MUST carry their exact value + the verbatim source span (source turn id + span)"*, surfaced by the assembly so a consumer can *"enumerate-then-sum/count"*. **Our 4th layer generalises that from value-bearing points to all points.**

**Related, and not to be duplicated:** `#2405` **CLOSED** (paraphrase-tolerant survival leg — the deformation measurement) · `#2542` family (value-fidelity discipline, cited by `#2684`) · `#2104` (**REPHRASE** dedup) · `#2684` (span schema) · `#3011` (the A/B/C experiment) · `#2281` (**raw-turn fallback** — the same instinct from the read side) · `#2453` (operational values verbatim — §7).

**⚠️ COORDINATION NOT TO MISS (reviewer B2.23): §2.4 WIDENS `#2684`'s scope** — from *value-bearing* `Point`s to *all* `Point`s — **without recording it.** It is a deliberate widening, but **`#2684` is the other issue's scope and the change must be recorded there, not only here.**

**⚠️ Storage consequence, harmonised with `STORAGE-ARCHITECTURE.md`:** the raw fact is **Supabase storage, not the graph**, joined to its `Point` by a reference — exactly the pattern §9.1 uses for the narrative. **If the belief layer carries the span, it stops being cheap; if it carries a reference, the fidelity is free.** That is the design rule this layer depends on.

#### 2.4.1 ⭐ Why the LINK is the load-bearing half — and the pattern has a name (2026-09-24)
**Owner, 2026-09-24:** *"do we vectorise the source or link to it?"* **Both — but the order is the decision, and the link comes first.**

The mechanism is **parent-document retrieval** / **small-to-big**: *embed the small units; keep the parent by ID with no vector; fetch it by walking up.* The field converges on **link, not embed** — a parent is stored by ID **without** embedding, because the reference is what the reader needs, not a second copy of the thing. *(Databricks: "search children, return parents".)*

**⇒ This is the fourth layer's retrieval consequence, and it is measured:**
> **At answer time, the model gets the source's VERBATIM text, not the extracted claim.** Verbatim source beats LLM-extracted artefacts by **15.9 / 22.0 pts** (§2.4, arXiv 2601.00821) — **independent of whether anything is embedded.**

**Two rules this puts on the extractor:**
| # | rule | why |
|---|---|---|
| **1** | **Every committed `Point`/`Object` must carry a reachable link to its `Source` span.** A node that cannot walk up cannot be served as evidence | **the link is how the evidence reaches the model** — without it, the 15.9-pt finding is unreachable and all we can serve is the derived claim |
| **2** | **A `Source` MAY additionally carry one summary vector** (the S1 narrative, kept in Supabase) | it buys one query class nothing else serves — *"which SOURCE is about X"*. Framework practice, **not** a measurement. Storage §9.4 |

**And what is explicitly NOT the mechanism:** *"are these two sources the same?"* is **never** answered by a vector — similarity detects the same **topic**, not the same **document**. Identity is **canonicalised URL + content hash** (storage §9.4 ③).

#### 2.4.2 Lifecycle and superseding in the raw-fact layer
**Owner, 2026-09-23:** *"i was talking about the verbatim raw facts layer and how we'd handle lifecycle/superseding there."*

**The rule: the raw-fact layer has NO lifecycle and NO status. It is APPEND-ONLY and immutable.**

**Why — and it is not a preference:**
1. **A raw fact is *evidence*. Evidence does not expire.** *"On 2026-09-19 p99 was 480 ms"* remains a **true record of what was said** after a later fact says 210 ms. The later fact supersedes a **belief**, never the **record that the belief was once held**.
2. **Superseding a raw fact would destroy the audit trail the layer exists to provide.** §2.4's whole purpose is *fidelity* — keeping the exact thing the `Point` must not drift from. A layer that can be rewritten is not a fidelity layer.
3. **Hindsight reached the same conclusion independently** — and stated it better than we had: *"A retain path that dropped a fact because it resembled one already stored would be a retain path you could not trust to have kept what you sent it."*

**⇒ So where does liveness live? On the `Point`, never on the span.**

```
Point (graph — carries live / superseded / draft, EP confidence, operators)
   │  span reference  (point_id → source_turn_id + char span)
   ▼
raw fact (Supabase — immutable text. Carries NO status. Ever.)
```

**Three consequences that follow, and each is a rule:**

| # | rule | why |
|---|---|---|
| **1** | **Status is per-`Point`, not per-span.** One span may be cited by several `Point`s; they are superseded independently | the span is shared *evidence*; liveness attaches to the *claim*, not the quote |
| **2** | **Read direction is `Point → span`, never `span → is it live?`** | asking raw storage whether a fact is live has **no answer** — liveness is not derivable from raw text. It exists only in the graph |
| **3** | **Nothing is ever deleted on supersession.** The only deletion trigger is **GDPR / retention** | deleting superseded evidence is how the audit gets a hole in it |

**⚠️ Putting status on the raw fact would also break the storage invariant.** The layer is the **truth** (`derived = fold(truth)`, §2). *"This evidence is no longer current"* is a **judgment**, and judgments are **derived** — they belong to the `Point`, which is derived. A status flag on the truth layer would be a **judgment stored as truth**, and it would give the same lifecycle **two sources of truth**.

**✅ Adoptable, and it applies to the GRAPH side (Hindsight, no contradiction with any recorded decision):** **invalidation by RELOCATION rather than a flag.** They move superseded rows to a separate archive *"so recall needs no state predicate… no query pays for your cleanup."* Snapshotted edges, reversible. **Our current `Object.status='superseded'` fold is the flag form** — every read carries `status != 'superseded'` as a predicate. **Relocation is the alternative worth testing when recall cost is measured**; it is not required now, and **our fold keeps the event stream as the reconstruction source**, which relocation must preserve.

**⚠️ Not to be confused with §5a.** §5a is about **graph** lifecycle (Objects, `CORRECTS`) and the refusal of recency-wins. **This subsection is about the raw-fact layer — where the answer is simpler: there is no lifecycle, by design.**

#### 2.4.3 Who carries the span? `Point`s only — measured, not assumed
**Owner, 2026-09-23:** *"are only points connected to raw facts or also objects/subjects/etc? not sure so asking."*

**Measured on the live graph (2026-09-23), by walking every edge in and out of each type:**

| type | how it reaches raw | mechanism | measured |
|---|---|---|---|
| **`Point`** | **directly** | `(Point)-[:extractedFrom]->(Source)` | **14,567 — the only edge type with instances that lands on a `Source`; `aboutSource` (`Point|Document|Event`→`Source`) is registered in `session_link.ENTITY_LINKED_TRIPLES` but has no instances** |
| **`Object`** | **only through a `Point`** | `(Point)-[:aboutObject]->(Object)` | 27,310 inbound |
| **`Event`** | through a `Source` | `(Source)-[:references]->(Event)` | 2,190 |
| **`Subject`** | through an `Event` | `(Subject)-[:performs/participatesIn]->(Event)` → `Source` | 16 |

**Two facts worth keeping separately:**
- **`extractedFrom` is a `Point`→`Source` edge** (`projection/entities.py:655`, `:1864`) — **not** an `Object`→`Source` edge. *(An earlier reading of this document assumed `extractedFrom` hung off entities. It does not.)*
- **ZERO edges originate from an `Object`** — 27,310 in, **0** out. Objects are **pure sinks**, reached only from `Point`s.

**Why the span still rides `Point` even though other edges can reach a `Source`:** the carrier is
the node that makes a *deformable claim* — a `Point` is the propositional unit whose truth is
re-ranked by EP, so it is the thing whose support has to be re-fetchable at answer time. `Object`
and `Subject` are referents, and `aboutSource` — registered for `Point`, `Document` and `Event`
sources alike (`session_link.ENTITY_LINKED_TRIPLES`, mirrored in `projection/entities.py`) — has no
instances in the live graph, so no non-`Point` node reaches a `Source` today.
*(An earlier revision of this section reasoned from "the only edge on a `Source`" to "so `Point` is
the only possible carrier" — that does not follow, and the premise was an edge-type over-claim; the
carrier choice rests on the argument above, not on the edge census. #5007 review.)*

**⇒ Today, `Point`s are the sole raw-linked type. Everything else reaches raw transitively.**

**✅ And that is the right design — it is not an accident to be tidied. The reason:**
> **Span-level provenance exists to catch DEFORMATION, and only a claim can be deformed — in the sense that matters.**

A `Point` is a **paraphrase** of the text, and that is exactly where a **propositional** fidelity break happens — an inverted condition, a dropped qualifier, a number attached to the wrong subject. **The span is the check on the claim.** An `Object`'s failure is **referential, not propositional**: it either picks out the right thing or it does not, and the check for that is **whether it refers at all**, not whether it was rendered faithfully.

**⚠️ DO NOT WRITE "an Object is a name, so it cannot be deformed" — that claim is FALSE, and THIS DOCUMENT'S OWN §1 EVIDENCE FALSIFIES IT.** 62.3% of Objects are **synthesized definite descriptions** minted from a noun phrase (*"the timeout command"*) that need not appear verbatim anywhere, and the audit found an Object extracted from a sentence that was **negating its existence** (*"timeout is not on macOS"*). **A synthesized description can be wrong about what it names — it is a REFERENCE, and references fail.**

**⇒ The correct statement is narrower and survives: an Object's failure mode is NON-REFERENCE, and span-provenance is not the tool for it — a reference test is. The span catches what the claim CLAIMED; the reference test catches whether the name picks anything out.**

**⚠️ It would also be actively harmful, not merely redundant.** The Object side is already reachable (27,310 edges). Adding a direct `Object`→span link would create **a second route to the same evidence** *and* multiply it at every hub — the worst current hub has **123 referring `Point`s**, so one hub would gain 123 links carrying nothing that walking to the `Point` did not already give. **Same fan-out trap as §11 of `STORAGE-ARCHITECTURE.md`.**

**✅ Confirmed by the filed design, independently:** `#2684` scopes itself to **"value-bearing *claims* (numbers, dates, prices, counts, quantities)"** — i.e. **`Point`s**. **The span carrier was never intended to be an entity.** Our fourth layer widens `#2684` from value-bearing `Point`s to all `Point`s — **it does not widen it to another node type.**

**⚠️ Two real gaps this measurement exposed (both pre-existing, neither caused by this design):**
1. **`aboutSubject` = 0 edges**, and only **1** `Subject` exists. **The subject layer is effectively unwired** — Subjects reach the graph only via `performs`/`participatesIn` to Events (8 each).
2. **2,608 of 4,798 `Event`s (54%) have no `Source`.** ⚠️ **Do not read this as a defect without checking D2** — D2 rules *"do NOT mint a `Source` for events"*, so a self-anchoring lifecycle Event with no `Source` is **the intended shape**. Flagged as *worth confirming*, not as a bug.

#### ⭐ ✅ THE ENTITY-PROVENANCE DECISION — research returned, and it changes the claim above
**Researched 2026-09-23 because the owner asked; findings in §16.** Seven comparables were examined: **entity-level provenance is the FIELD NORM — 5 of 7 have it** (GraphRAG `Entity.text_unit_ids` · LightRAG `source_id` + `file_path` · Graphiti/Zep `MENTIONS` edges → episodes · Mem0 entity↔memories · ArcGIS property-value provenance). **The convergent pattern is a POINTER, not a payload, and the granularity is the chunk or episode — never character spans on entities.**

**⚠️ And the field falsifies the "names cannot be deformed" intuition a SECOND time** — entity linking's own pipeline is **mention → candidate → disambiguation**, and **the mention IS a span**. W3C PROV standardises it as `prov:mentionOf`. **So the intuition is not merely wrong in our graph; it is wrong as a general claim about entities.**

✅ **RECOMMENDED: keep `Point`-only as the SPAN carrier, and ADD entity ORIGINS ONLY — at most ONE reference per entity.** Reasons: it is the field's granularity (chunk/episode); it is ~**0.7–0.9 MB** at today's scale; and **it is what makes a non-referential entity DETECTABLE.** ⛔ **NOT per-mention links** — that is the 123× fan-out trap, and it is what §11 of the storage doc caps (owner: **200**).

**⭐ And the reason to add it is NOT duplication — our objects have ZERO duplicate names. It is NON-REFERENCE:** an entity minted from a negated sentence (*"timeout is not on macOS"*) has **no origin span to point at**, so an origins link is **the detector** for exactly the failure §1's audit found. **Precision matters here: this document must not claim provenance is for dedup.**

⚠️ **No backfill is possible** — origins are unrecoverable for existing entities (**zero** outbound edges, measured above). **New entities only; the existing 7,863 stay origin-less.**

---

## 3. Step 0 is not the only source-varying step

**The wrong assumption, corrected (owner, 2026-09-23):** *"An asana task completed has a very specific format, or a github issue completed… I imagine we need to optimise some of the other steps too."*

A GitHub PR already **is** an ontology shape — title, body, state, number, timestamps, and explicit references. **For structured sources most of the work is mapping, not inference.**

| | transcript | GitHub PR | Asana task |
|---|---|---|---|
| "what changed" | **inferred** from dialogue | **stated** in title/body | **stated** in the status change |
| a candidate is | inferred | title + body | the task + its fields |
| an edge is | inferred | **`closes #N` — a real typed edge** | **`blocked by`** |
| an event is | inferred | open/close/merge — **already timestamped** | created/completed |

**⇒ A source type declares a MAPPING, and that mapping configures steps S0, S1, S2 and S5.** The `Source` type vocabulary exists and packs already declare `extraction.sourceTypes` (§8). **What is missing is the field→type mapping declaration.**

**⚠️ AND IT IS NOT MERELY UNDECLARED — THE S0 HALF IS NOT BUILT.** Confirmed in code: `_edus_from_conversation(conversation)` + `chunk_transcript(edus, target=chunk_size)` are **transcript-only**. There is **no field→type mapping in the reader at all** — so *"S0 is mechanical"* is true, and **S0 is also the step that cannot yet read a GitHub PR.** **A lane must not assume the mapping exists because the field does.**

**Consequence to make explicit:** structured sources are **cheap and exact**; unstructured sources are **expensive and approximate**. That should be a declared property, not a surprise.

---

## 4. The steps

### 4.0 What each step is FOR — the purpose test

**Owner review, 2026-09-23:** *"let's review the purpose of the steps because it seems the whole s1.5 is muddled."* This table is the answer, and it is what exposed the defect below: **two steps had no distinct purpose.**

| step | its ONE purpose | mechanical or model? |
|---|---|---|
| **S0 READ** | turn any source into one common shape + the mapping it carries, so later steps are source-agnostic | **mechanical** |
| **S1 NARRATE** | restate the input as connected prose in which the **relationships are stated**, using the input's own entity wording — so extraction **reads** relationships instead of **inventing** them | model |
| **S2.1 EXTRACT** | read the narrative (and the raw) and emit **candidate `Object`s (entities)**, **`Point`s**, **`Event`s** and their connections | model |
| **S2.2 VET** | **adversarially falsify** the candidates — entity or mere reference? atomic? did we account for everything the narrative said? | model (Jev) |
| **S2.3 CLASSIFY** | **assign each SURVIVING candidate its pack kind** — against the declared kind vocabulary | model (Jev), with a **mechanical kNN fast path** |
| **S3 RESOLVE** | **find what already exists for these candidates**, disambiguate using **the narrative as context**, **and attach** | mechanical + model (ambiguity only) |
| **S5 CONNECT** | (1) wire **this batch's own story**; (2) compare the batch **against the stored graph** — duplicate? lifecycle change? contradiction? | model decides, code writes |
| **S6 COMMIT** | write entities + connections + metadata/lifecycle | **mechanical** |

**The test this table applies: can you state the step's purpose in one clause that no other step shares?** That test deleted **S4** and then deleted **S1.5** entirely — see §4.1.

**⇒ EIGHT steps: S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6.** ⚠️ **The gap in the numbering is cosmetic and should be closed** (S1–S8) once the design settles — but **do not renumber yet**: `#4894`, `#4899` and other issues reference S-numbers in comments already posted.

### 4.1 The step list

```
S0  READ       source → common shape + THE MAPPING it carries      [per source type]
S1  NARRATE    shape → connected narrative, verbatim entity wording
S2.1 EXTRACT   narrative (+ raw) → candidate Object / Point / Event AND their connections  [LLM]
S2.2 VET       adversarial review of the candidates (validity + coverage)             [Jev]
S2.3 CLASSIFY  each SURVIVOR → its pack kind, against the declared vocabulary      [Jev + kNN]
S3  RESOLVE    look up what exists for these candidates → disambiguate (narrative as
               context) → AND ATTACH the new context to the surviving entity
S5  CONNECT    two distinct sets: within this batch · to the graph
S6  COMMIT     create entities + connections + metadata/lifecycle
```
**Eight steps.** No S4 and no S1.5 — both were deleted by the purpose test in §4.0. ⚠️ **The numbering gap is deliberate for now:** S-numbers are referenced in posted comments on `#4894` / `#4899`, so **renumber only once the design settles.**

### S0 — READ  *(mechanical — no LLM)*
**In:** **a SOURCE** — a document, a code file, a meeting transcript, or a conversation. **Out:** the common shape the rest of the pipeline consumes, plus the field→type MAPPING that source type declares.

Normalises the shape **and applies the declared field→type mapping** (§3). Passes the mapping forward so later steps know what is already typed versus what must be inferred.

**✅ Confirmed mechanical (owner asked, 2026-09-23).** The live code does this in plain Python **before any model call**: `_edus_from_conversation(conversation)` segments the input into units, and `chunk_transcript(edus, target=chunk_size)` splits it for processing. **The first LLM call in the entire pipeline is S1** (`run_s1(...)`). So S0 is **parsing + mapping** — it must never be handed to a model, and it is exactly why structured sources (a GitHub PR) are cheap and exact while transcripts are not (§3).

#### ⭐ D10 and the version model — what the extractor must respect

**⭐ CORRECTED 2026-09-24 (owner): a document is a SOURCE, not a graph node.** *"I am suggesting making them sources so our entity layer can be extracted from them… That way our entity layer becomes a proper abstraction over documents, code, meeting transcripts (all sources) — that's the reasoning/knowledge layer."*

**⇒ S0's input is a source FILE, uniformly, whatever it is.** ⚠️ **CORRECTED 2026-09-24 — an earlier version of this line said *"There is no `:Document` node in the graph to read from, and none is created"*. THAT IS FALSE ABOUT THE CODE.** The `:Document` label **exists, is written and is read**: `projection/entities.py:1527` (`_upsert_document`) emits `MERGE (d:Document {id:$id})` at `:1572`/`:1740`; `ingest.py:188/266/307/412` `MATCH (d:Document …)`; **and `quota.py:524` counts `MATCH (d:Document)` as a COUNTED quota resource (`#1726`).** `docs/ONTOLOGY.md` §4.4 still declares *"Graph label is `:Document`"*.
**⇒ The accurate statement: D10 folds `:Document` into `:Source`, AND THAT MIGRATION HAS NOT LANDED.** Today the label is real, writable and **metered**. *(Production currently holds 0 such nodes because the commit lane has never run — a **data** fact, not a code fact, and **not** grounds for calling the change free.)* **The consequence for the extractor stands: nothing downstream is document-specific.** A spec, a repo file and a call transcript differ only in their **mapping**, never in their treatment. **Full shape: `STORAGE-ARCHITECTURE.md` §9.3. ⭐ The ontology half HAS now landed: `ONTOLOGY.md` v3.15 (PR #5022, issue #5013)** — but the **code** migration has not, and it is bounded by `#2489`: the replay key moves `coalesce(title, name)` → `url`, and rebuild does NOT repair pre-existing graphs.

**⭐ The five consequences of D10 — ruled 2026-09-24, and the reasons they matter to the EXTRACTOR (full analysis: `STORAGE-ARCHITECTURE.md` §9.5).**

| # | ruling | why the extractor cares |
|---|---|---|
| **Q1** | **`aboutDocument` is KEPT; only its target label moves** (`:Document` → `:Source`) | ⛔ **It is NOT collapsed into `aboutSource`.** `aboutDocument` is in `DERIVABLE_STRUCTURAL_RELS` (`#2489`) and is resurrected at the old point by pass-2b; `aboutSource` is deliberately excluded. **Merging them would silently delete that rebuild guarantee.** Its **replay key** must move `coalesce(title, name)` → `url` **in the same step as the label**, or the rebuilt edge resolves to nothing |
| **Q2** | **Two classification axes, kept separate:** `sourceKind` (*what kind of source*) + `documentKind` (*what genre of document*) | **This is S2.3's target vocabulary.** ⚠️ An intermediate reading wrongly concluded D10 killed `documentKind` — **it does not.** It stops being an Object-subclass vocabulary and becomes a **GENRE axis over `sourceKind: document`.** **S2.3 assigns against the pack's declared kinds; the axes are not interchangeable, and `sourceKind: document` must not be treated as a genre** |
| **Q3** | `doc_status` **⛔ dropped**; `format` moves to `:Source`; `documentKind`/`title`/`topics`/`summary` kept; `content` leaves the graph | **Dropping `doc_status` removes an unjournalled raw-`SET` write** (`ingest.py:262-266`) — one of only two in the system. **It is also a real extractor input today**, so anything reading it must switch to the read-the-entities lookup. `content` leaving the graph is the S0 input contract (§3) |
| **Q4** | The `documents` cap is **re-pointed at `:Source`** — not retired, not folded into the node cap | a `:Source` is **equally invisible to the main node cap**, so the reason the cap exists does not expire; retiring it **ungates `/v1/index/docs`** |
| **Q5** | Fold + change the replay key + migrate — **free TODAY, and that expires** | **zero `:Document` nodes and the commit lane has never run** ⇒ the migration is free now. **It cannot be deferred past this extractor's first COMMIT run** (`#2489`: rebuild does not repair pre-existing graphs) |

**⚠️ And one finding that is NOT about D10 — T6, and it touches S6 COMMIT directly.** While checking the above, a separate defect surfaced: **a `:Source` mutates in place** — `_upsert_source` bumps `updatedAt`/`version`/`contentHash` on a hash-differing `ON MATCH`, and the hosted commit path flips a status, **and neither is journalled.** **That breaks `derived = replay(journal)` and contradicts our own D7** (and the field's append-only evidence rule: *changes are handled by new correction events, not in-place edits*). **A re-fetched source that has changed is a NEW VERSION, not an edit.** Reasoned in `STORAGE-ARCHITECTURE.md` §9.5 and §9.6; belongs to that doc's §3, and is tracked separately.

#### ⭐ The version model — and the one thing S6 must do (owner ruling, 2026-09-24)

**`extractedFrom` records the SOURCE, not the source VERSION.** Identity (`url`) is not version (`contentHash`). **The version rides on the link as `sourceVersion`** — and it is **per-link**, because `extractedFrom` is many→many. Without the version on that edge, *"are these entities current?"* is **unanswerable from the graph** — *"are they trustworthy?"* is answerable today (confidence, NAND, supersession), but *"are they about the content we now hold?"* is not. **Both are reads; only one has an anchor.**

**S6's obligation, in one line:** when a run writes derived nodes, it must **record the version it read on the extraction link, as `sourceVersion`** (`ONTOLOGY.md` §4.6) — the `contentHash` of the version read. That is the whole extractor-side change — and it is cheap, because D30 keeps content out of the graph, so a version costs **three timestamps and a hash**.

**⚠️ Scope of the anchor — no longer Point-only (resolved 2026-09-25).** `sourceVersion` rides on `extractedFrom`, which is declared `Point → Source`. Derived **Events** and **Documents** reach their source through their own links, not through `extractedFrom`. That gap is now **DECIDED** (`#5199`, owner-approved 2026-09-25): the anchor extends to the **derivation** `references` link — see `docs/architecture/STORAGE-ARCHITECTURE.md` §9.6 for what it costs (and for the paths where it is honest-absent rather than present), and `ONTOLOGY.md` §3.4/§4.6 for the model — note that `ONTOLOGY.md` is deliberately **frozen on this branch** (§4.6 still states the pre-decision Point-only scope pending owner review on `#5199`); **§9.6 is the operative record until that wording lands**. Identity/mention and referential-containment links are **not** version-scoped, because there is no version of an identity to compare.

| Rule | Statement |
|---|---|
| Identity | `url` — stable across versions |
| Version | `contentHash` — identifies a **version** of that identity |
| Raw content | **append-only** — a differing hash on re-fetch is a **new version, never an edit** |
| Extraction | **version-scoped** — the version read is recorded on the extraction link as **`sourceVersion`** |
| Version change | appends a **journal record** that **closes** the current version's window and opens the new one — never an overwrite |

**⭐ One node per `url` — the version history lives in the journal, and what is superseded is the FACTS.** `:Source` MERGEs on `url`, so exactly one node per source carries the **current** version; a version change creates no second node and rewrites no older one. No Source→Source supersession edge exists or is needed: successor facts attach to the standing `:Source` (`extractedFrom` is keyed by `url`), and the earlier facts are replaced through the ordinary `CORRECTS` mechanism. **The source is the identity; the entities are the belief.**

**⭐ The policy is B — mark stale now, supersede on re-inference (owner).** A re-fetched source's entities are **marked stale immediately** and **superseded only when re-inference produces their successors**. **Not A (immediate supersession)**: A withdraws the belief *before* producing its successor, so between the source changing and re-inference running the graph asserts **nothing** about a subject it previously had a position on — strictly worse than a stale-but-present belief. **`stale ≠ wrong`:** supersession is **additive**, never a delete.

**⚠️ Interval-closing is part of the WRITE, not a later repair.** An unclosed `validTo` reads as *"still true"* indefinitely — the field names this as **the #1 production bug** in temporal knowledge graphs, and it is exactly what T6 produces today. **And document-level alone would not answer the owner's question**: if you supersede only the document, the **facts are untouched** — *"what did we believe before?"* is a question about **facts**, which is why the version belongs on the **link**. Tracked: `#5038` · `#5024` · `#5025` · `#5026`.

### S1 — NARRATE
**In:** the common shape. **Out:** a **connected narrative**.

**The format matters and is the owner's requirement (2026-09-23):**
> *"important for S1 to keep verbatim wording of Entities (… not separating by a list of objects, a list of events, etc. but by having the connection … 'Object A changed because of Points X,Y,Z and that happened through Event W' is the typical form, without inventing entities if the input has none itself."*

**Why it works:** the connections are **in the sentence**, so S2 *reads* them rather than re-inferring them. Re-inference from prose is where `the <X>` entities and mis-links come from. **Verbatim entity wording** also prevents paraphrase-duplication (`the plan doc` vs `the delivery plan`).

**⚠️ Caveat:** the narrative is the **transport, not the embedded unit.** What gets embedded is still the **atomic extracted claim**. A narrative sentence carrying three facts retrieves badly.

### S1.5 — ⛔ REMOVED (owner review, 2026-09-23)

**There is no S1.5.** It was two things, and **both have now been removed for different reasons** — the review half as a defect, the lookup half as a **mis-ordering**.

#### (a) The review half — deleted as a defect
*"Was S1 too coarse?"* was **asked twice** (here **and** as S2.2's question 3), **on the wrong artifact** (atomicity is a property of the *items*, not of the prose), and **blind to the failure it targeted** (if extraction drops 2 of 3 stated things, a narrative-side check sees a *fine* narrative and passes). **Both questions now live at S2.2, on the items.**

#### (b) The lookup half — deleted as a mis-ordering (owner, 2026-09-23)
> *"does it make sense to do the lookup before we have entities? shouldn't we first find the entities and do the lookup and we can use the narrative to disambiguate when uncertain?"*

**No — it does not make sense, and the reason is causal: the lookup is keyed on entities, and entities do not exist until extraction produces them.**

| | lookup BEFORE extraction | lookup AFTER extraction |
|---|---|---|
| **keyed on** | **strings the narrative happens to name** | **the candidates extraction actually identified** |
| **knows types?** | no — prose words | **yes** — candidate entities with kinds |
| **can it find what matters?** | only what the prose chose to name | **everything extraction proposes, including things the narrative never named** |

**⇒ The lookup moves into S3, and becomes keyed on candidates.** Doing it earlier was looking up **words from prose**, not entities — a strictly weaker operation that also **could not cover S3's key set**, which is why the two lookups never merged.

**The correctness argument is now in ONE place.** The old design split the dedup guarantee between *"extraction knew A existed"* and *"S3 resolved the candidate"* — two places that can disagree. **With the lookup inside S3, S3 is the single authority on whether a candidate is new or existing.** A guarantee held in one place is a guarantee; the same guarantee held in two is a bug waiting for them to diverge.

#### ⚠️ This REOPENS D4 — and narrows it along its own reasoning
**D4 (decided)** reads: *"One entity-keyed neighbourhood lookup after the narrative, consumed by **both** extraction **and** the judgment."*

**D4's stated justification is about salience** — *"something could be discarded even though it changes something that was already there"* — **which is a claim about the JUDGMENT, not about extraction.** That reason survives intact and the judgment keeps the neighbourhood.

**What D4 never justified is extraction consuming the lookup.** Its only reason was *"so extraction emits attach-to-A instead of create-A"* — and **that reason is now unnecessary, because S3 resolves authoritatively.**

**⇒ D4 is NARROWED, not reversed:** one entity-keyed neighbourhood lookup, **after** extraction, consumed by **the judgment** (salience) **and by S3** (resolution). **Extraction no longer consumes it.**

**✅ STATUS: `DECIDED (narrowed)` — the owner ruled.** The narrowing is in force: the lookup sits **after** extraction, and it is consumed by the salience judgment and by S3 only. **Extraction does not consume it.** *(An earlier version of this line said the narrowing was still proposed and awaiting confirmation; §9's D4 row says `DECIDED (narrowed)`, which is correct — §16.2 records that this two-ways status was itself a defect.)* This follows from D4's own stated reason — the extraction consumer was never covered by it.

> **✅ Superseded and deleted (reviewer B2.20, then §16.2).** This line used to say the narrowing was *"PROPOSED and awaiting the owner's confirmation"* while §9 recorded D4 as `DECIDED (narrowed)` — a two-ways status the second review cycle flagged. **The owner has since ruled: D4 is `DECIDED (narrowed)`.** There is no pending confirmation, and no third phrasing.

> ✅ **Note — `#4511` is CLOSED, and it no longer gates anything.** It was the blocker *when the lookup sat before extraction* (`_fts_rows` read `r.get("kind")` while the callee emits `point_kind`, so every prior's type returned **blank**). **The lookup has since moved into S3 (§4.2), so `#4511` is now simply a fixed bug, not a dependency of this design.** ⚠️ **Still unmeasured (§14):** whether giving extraction the neighbourhood ever reduced what it created — the question that justified the old ordering, and the one the new ordering makes moot.

### S2.1 — EXTRACT
**In:** the narrative **+** the raw (see the finding below). **Out:** candidate **`Object`s (entities)**, candidate **`Point`s**, candidate **`Event`s**, and their connections.

> ⚠️ **Vocabulary — "claim" is NOT a thing** (owner, 2026-09-23). `docs/ONTOLOGY.md` v3.14 (`#4369`) declares it: **"claim" is the sanctioned user-facing *gloss* for a belief `Point`** — *"It is **not a distinct kind**: no `claim` type and no `claim` pointKind, and no canonical node write value."* **The type is `Point`.** Earlier drafts of this document said *"entities + claims"*, which named a gloss and an abstraction as if they were node types. **Write `Object` and `Point`.**

This is v2's S2, with priors in hand. Still proposes only; decides nothing.

**✅ FINDING (2026-09-23) — the live code ALREADY hands extraction the raw alongside the story.** The call is `run_s2(model, story, master, session_date=…, edus=edus, …)` (`extractor_v2.py` ~4789) — **`edus` is the raw segmented input, passed together with the computed story.** **⇒ D3-Option 4 is not a proposed change; it is the status quo.** The narrative is a **focus**, and the raw is present too.

> ⚠️ **What this does to D3.** The open question is no longer *"may extraction consult the raw?"* — it already does. It becomes the much smaller **"what does the narrative add that the raw does not?"** It also means the measurement (§10) must compare **narrative + raw** against **raw alone**, not narrative against raw.

### S2.2 — VET *(the counter-filter; owner proposal)*
> *"S2 sounds ok, but seems like it just needs a counter filter (Jev doing adversarial review), so we have step S2.1 LLM entities list and S2.2 jev"*

**Jev, asked to FALSIFY, not confirm.** Two **levels**, and they are different shapes — one is many cheap decisions, the other is a single batch verdict.

#### Level 1 — per-candidate (many, cheap, evaluated in parallel)
1. **Adversarial:** is this actually an entity, or a reference? → the `PR #465` / `the X` class (**62.3% of Objects**)
2. **Atomicity:** is this ONE claim, or several fused together? *(Atomicity is a property of the item — this is where it belongs, not on the narrative.)*
3. **Worth keeping — judged against THE PACK (`#1026`'s `valueGate`).** Not a generic value judgment: **is this durable, per the pack's own declaration?**

> **This is where the pack stops being prose.** `packs/dev/manifest.yaml` already declares `memory_granularity` — *"Durable: problem-family reasoning … Ephemeral: issue/PR numbers, CI status, test counts, commit hashes, tool workarounds, sprint mechanics."* Today that string is **rendered into the S1 prompt and not enforced** (`_granularity_text()`). **S2.2 is the step where the declaration becomes a gate** — the missing `valueGate` slot of `#1026`, realised as an adversarial question. See `#4899`.

#### Level 2 — per-batch: did this batch come out right? (a batch verdict, not a per-item one)
1. **Coverage:** **did we account for everything the narrative said?** If the narrative stated three things and extraction emitted one, that is a **loss** — and it is only visible by comparing the items **against** the narrative. ⚠️ *This is the corrected form of the old "was S1 too coarse?" question, which was wrongly asked before extraction (see S1.5's removal note).*
2. **Level of abstraction:** is this session at the abstraction **this pack** asks for? A narrative that spent itself on CI status and commit hashes did **not** — even if every individual candidate passed Level 1.

**Both are batch questions.** Coverage needs the whole item set compared against the whole narrative; abstraction is a property of the batch, not of one candidate.

#### ⚠️ Owner question: if the narrative is poor, should we send it back? (2026-09-23)
> *"should that happen entity per entity or/and for narrative to be at the right level of abstraction as per the packs? basically, if the narrative is poor, should we send it back?"*

**YES — send it back. But the TRIGGER must be the outcome, not a speculative review of the prose.**

| | speculative pre-check (rejected) | **outcome-triggered re-narrate (proposed)** |
|---|---|---|
| **when** | before extraction, every session | **after S2.2, only when the batch verdict is poor** |
| **cost** | one model pass on **every** session | a pass **only on sessions that actually failed** |
| **basis** | a judgment about prose | **a measured keep-rate** — concrete |
| **recoverable?** | if the narrative is poor, extraction still ran on it | **re-narrate before extraction has produced anything terminal** |

**Why this is the right shape:** the whole reason a narrative-side review looked attractive is the case where the narrative is *so* poor that extraction yields nothing usable. **But that case is only distinguishable from a good session by looking at what extraction actually produced** — so the check belongs *after* extraction, and it should be **triggered by the yield**, not run speculatively beforehand.

**Mechanism (proposed):** if the batch verdict is poor — *e.g. VET discarded almost everything, or the keep-rate is below a threshold* — **re-narrate ONCE with the pack's `memory_granularity` emphasised, then re-extract.** ⚠️ **Bounded: once, then accept.** A model-call loop can oscillate — the second narrative can be worse than the first, and nothing in the design currently prevents that. **A retry, not a loop.**

**Outputs:** `KEEP` · `DISCARD` · `SPLIT` *(for a fused item — extraction may then split it from the raw, which it already has)* · **`RENARRATE`** *(batch-level only)*.

⚠️ **`MERGE-INTO-EXISTING` is deliberately NOT a VET output.** VET runs **before** S3 (§4.2), and S3 is **the single authority on whether a candidate is new or an existing node**. A VET stage that emitted `MERGE-INTO-EXISTING` would be deciding new-vs-existing itself — the VET/S3 circular dependency §16.2 identifies. **Merging is S3's decision, made after the gate; VET may only keep, discard, split, or ask for a re-narration.**

**⚠️ Unmeasured, and it needs the small-sample-first method:** *whether re-narrating actually improves yield*, and *what keep-rate counts as "poor"*. Both are thresholds that must be set by hand on tens of real sessions with the owner correcting the rule — **not chosen in advance.**

**⚠️ There is no S4 — do not restore one.** A previous draft listed a separate *"S4 — granularity review"* and folded it back in here. **It is not a step**: its purpose was identical to question 2 above. **The step list is the EIGHT steps of §4.1: S0, S1, S2.1, S2.2, S2.3, S3, S5, S6 — there is no S4 and no pre-load step; the lookup lives in S3 (§4.2).** ⚠️ **An earlier version of this line said "seven" and omitted S2.3 — §4.0 and §4.1 say eight, and eight is correct.**

**Why Jev fits:** it is a **decision-only** model — it cannot return a value outside the supplied schema, so it cannot invent an entity or a name. Three primitives, **mixable in one call**: *Choice* (pick from a list), *Score*, *Noul* (0–1 probability). Doc: `https://jevtypesafeai.com/docs#apis`. ⚠️ Measured **$0.000243/item at 3 questions** (~$6/month at 25k items) — **~4–7× cheaper than a frontier LLM, not the vendor's claimed 40–400×** (self-tested).

**Fail-open on uncertainty** (keep, don't drop) — a wrong keep is noise; a wrong drop is memory loss.

### S2.3 — CLASSIFY (assign the pack kind — *after* the gate, on SURVIVORS only)
**Owner, 2026-09-23:** *"For the extraction journey, I think we're missing a Jev substep to classify as per the pack, no? maybe part of S2.2? or not sure if we already have it. Maybe more efficient (and reliable) as its own step with what survives S2.2."*

**✅ It exists — and it is already built, already flag-gated, and already positioned after the gate. It is switched OFF.**

| what exists | where |
|---|---|
| **`KindClassifier.classify_items(items)`** → `{assignments: {id: {kind, margin, mode}}, stats, warnings}`, **never raises (fail-open)** | `kind_classifier.py:179` |
| **A closed vocabulary** — the stats counter `closed_vocab_rejects` exists | `kind_classifier.py:200` |
| **A mechanical kNN fast path** → margin gate → `nearMisses` rerank → **batched LLM only for the low-margin tail** | `#1695` |
| **The deferred pass** — `_collect_classify_items(embed_list)` → `classify_items` → `_apply_classify_kinds` | `extractor_v2.py:4467` / `:4492`, called at `:4803` and `:4888` |
| **The flag** — `TORTOISE_CLASSIFY_LATER`, unset today = *"the LEGACY pipeline"* | `extractor_v2.py:512`, `:4656` |
| **The seam for Jev** — `llm_tail` is already a constructor parameter | `kind_classifier.py:522` |

**⇒ So the answer is: we HAVE it, it is `TORTOISE_CLASSIFY_LATER=1`, and it is OFF.** Three things are wrong with its current state, and they are the actual work:

**1. It is OFF, so classification happens INLINE — inside S2.1's big call.** When the flag is on, `core_only=classify_later` is passed to S2 (`:4782`) so the extractor **only assigns core kinds and leaves domain kinds `unclassified`**, and the separate pass fills them in. When it is off, the extraction LLM emits kinds as part of its prose output. **That is the wrong place for them** — see below.

**2. The owner's improvement is real and specific: it must run on SURVIVORS.** ⚠️ `_collect_classify_items` collects from **`embed_list`** — the whole candidate set — and **`classify_consolidation` does not remove items from `embed_list`; it decides a write operation per point** (`:3994`). **So a `DISCARD` (`#4899`) would currently be classified anyway.** *Classifying a row you are about to throw away is wasted work **and** it is worse than wasted: **the discards are exactly the rows whose kind is least meaningful**, so they pollute the classifier's own signals.*
> **⇒ The wiring requirement: a discarded candidate must be removed from (or marked in) `embed_list`, or `_collect_classify_items` must skip it.** Without that, `#4899` and S2.3 ship in the wrong order and S2.3 pays for `#4899`'s savings.

**3. It should be its OWN step, not a S2.2 substep — and the reason is the fast path.**
- **As its own step, the cheap path works:** the kNN covers the easy majority with **no model call**, and only the low-margin tail is batched. **Inline, you pay the large extraction model to classify every item** — because the big call has already committed to emitting a kind in its prose.
- **It passes the §4.0 purpose test:** *"assign the pack kind"* is a clause **no other step shares.** S2.2 asks *"should this exist?"*; S2.3 asks *"what IS it?"* **Different questions, different failure modes** (S2.2 fails as *"we kept junk"*; S2.3 fails as *"we mislabelled it"*).
- **And a decision-only model STRUCTURALLY enforces the vocabulary.** A `Choice` over supplied kinds **cannot return a kind outside the pack** — whereas an extraction LLM emitting kinds inline can only be **hoped** to stay within the declared set. `#1026` requires *"entityCues must reference declared kinds"*; **a separate Jev step is what makes that requirement true rather than aspirational.**

**⚠️ Q2 — WHICH vocabulary does S2.3 assign against? (owner ruling, 2026-09-24; `STORAGE §9.5`)** Two axes exist and **they are not interchangeable**:

- **`sourceKind`** — *what kind of SOURCE is this?* (`document`, `conversation`, `github_issue`, `agentSession`) — **declared by the PACK** (`extraction.sourceTypes`), and it is a property of the **arrival**, decided at **S0** from the mapping, **not inferred by a model**.
- **`documentKind`** — *what GENRE of document is this?* (research, planDoc, apiSpec, transcript…) — **the vocabulary S2.3 assigns against.**

**The trap, named:** `sourceKind: document` is **a source type meaning "this arrived as a document"** — **it is not a genre.** Assigning `sourceKind` values as if they were kinds, or collapsing the two axes into one field, is a **category error** (the library world keeps document *type* and *genre* in **separate fields for decades**). **S2.3 assigns GENRE (and other pack kinds); it must never be asked to re-derive the source type**, which S0 already knows mechanically and for free.

**Position in the journey — after the gate, before resolution:**
```
S2.1 EXTRACT  → S2.2 VET (gate) → S2.3 CLASSIFY → S3 RESOLVE → S5 CONNECT → S6 COMMIT
```

**Why before S3 and not after:** S3's *attach* step and S5's *chain* enforcement key on kinds (`_select_pack_kinds`, the chain enforcer), so classification must precede them. **And why after S2.2:** so only survivors are classified.

⚠️ **Owner confirmation pending** — *"maybe part of S2.2? or not sure if we already have it"* — **the evidence says: we have it, it should not be a substep, and it belongs after the gate.**

### 4.2 The ordering — cheapest first, subject to correctness
**Owner, 2026-09-23:** *"the point is that Jev could likely do the classification way cheaper, no? so fine if it's its own step. Although not sure if we should mechanically dedup first as that can reduce the set further? anyhow, check the ordering makes most sense."*

**Both halves are right, and checking the order found something worse than expected.**

#### ⛔ What the code does TODAY — the order is inverted
Resolved by call graph, not by line number:

| runs | where | what |
|---|---|---|
| 1st | `:4803` | classify pass on `embed_list` |
| 2nd | `:4888` | classify pass on `complete_list` (after S4's merge) |
| **last** | **`:4995`** | **`execute_embed(...)` — which contains `classify_consolidation` (`:3994`), i.e. the dedup/consolidation gate** |

**⇒ Today we classify the whole set TWICE and then dedup it.** The mechanical, free, set-reducing operation runs **dead last**, after the two most expensive passes. **Every ordering principle says the opposite.**

#### The rule is NOT "cheapest first" — it is cost per unit of SAFE rejection
⚠️ **CORRECTED 2026-09-23 after research.** An earlier draft said *"order by cost ascending"*. **That is wrong, and it is wrong in a way that would have put our best filter last.** The formal result (research, `docs/research/2026-09-23-pipeline-stage-ordering/`):

> for a stage 1 with cost `c₁` and rejection rate `r₁`, and a stage 2 with `c₂`/`r₂` — **`1` before `2` ⟺ `c₁/r₁ < c₂/r₂`.**

**"Cheapest first" is the special case `r₁ ≥ r₂`** — a *sufficient* condition, not a necessary one. **A near-free filter with near-zero rejection sorts LAST under cost ordering and FIRST under the correct rule.**

**Two further rules outrank cost entirely:**
1. **Losslessness outranks cost** — a stage that discards nothing can run anywhere; and
2. ⛔ **A stage runs behind an audit gate, and its admissible actions are `KEEP` / `NOOP` / `DISCARD` / `MERGE`** — **merged only when nothing distinguishing is lost** (§16.4). ⚠️ **A FUSE rule was previously forbidden here; the owner authorised merging near-duplicates on 2026-09-24, with Jev as arbiter (O4).** **What did NOT change: a merge must never cross a difference in a number, a name, a negation, or a condition, and both sides' evidence survives.**

**⚠️ And recall compounds MULTIPLICATIVELY.** Two stages that each keep 95% silently lose ~10% of the durable set. **A volume target with no recall floor is gameable** — you can always hit a node count by writing nothing. **Any reduction target must be paired with a measured retention floor (→ §16).**

#### ⭐ THE BIGGEST LEVER IS MISSING FROM THIS DIAGRAM ENTIRELY — and it runs BEFORE S1
A four-way audit of the write path found **FOUR distinct dedups at FOUR different keys**, and the earliest one does not exist:

| id | key | where | exists? |
|---|---|---|---|
| **D-a** | **source** | **URL only** | ⚠️ **URL-keyed only** — a content-hash is a provenance anchor (`#4005`), **not a dedup key** |
| **D-b** | **within-batch exact** | `:3967-3969` | ✅ |
| **D-c** | **vs store, raw content hash** | `sdk.py:12412` (`_find_point_by_content`; the shared predicate builder is `_dedup_match_clauses`, `:12364`) | ✅ |
| **D-d** | **semantic** | `:3021+` | ✅ |

**⇒ `S0a NORMALIZE + HASH → S0b SOURCE DEDUP` is genuinely absent, and it is the largest single volume lever available.** Catching the same conversation firehose twice is a **whole-narrative** save, not a claim-level one — and since **S0 is already mechanical and free**, this costs nothing to add.

#### The five correctness constraints — and ONE of them was FALSE
| # | constraint | consequence |
|---|---|---|
| 1 | **Exact dedup needs NOTHING** — identical text is identical, entity-free | **may run first** |
| 2 | **Non-exact (paraphrase) dedup needs semantic judgement** | **only in VET** — never before it |
| 3 | **The mechanical half of the gate needs NO graph** | **may precede S3** |
| 4 | ✅ **CONSTRAINT 4 IS TRUE — AND AN EARLIER "CORRECTION" BROKE IT.** *"CLASSIFY needs no graph; the LOOKUP needs KINDS → CLASSIFY before RESOLVE"* | ⛔ **RESTORED 2026-09-24. The 2026-09-23 correction here was WRONG and had the pipeline backwards.** It cited `_derive_queries` (`extractor_v2.py:1744-1778`) — which genuinely never reads `kind`, but **builds FTS query strings only; it is not the resolution lookup.** The lookup is **`resolve_entities` (`:2755`), called at `:4926`**, which builds `ent_refs` with `{"name":…, "kind": str(e.get("kind",""))}` and hands each to **`_find_existing_entity(entities, name, kind)` (`:2705`) — whose EXACT-MATCH branch folds and compares `kind`:** `str(e.get("kind","")).strip().lower() == kind_folded`, with a bare-form fallback only when unambiguous. **So the match half DOES need kinds.** ⚠️ **And the code agrees on the order: the classify pass runs at `:4803`/`:4888`; `resolve_entities` at `:4926` — after it.** |
| 5 | **Against-priors consolidation needs the lookup** | **cannot precede S3** |

**⚠️ Constraint 4 stands, and it is the basis of the S2.3-before-S3 order.** **CLASSIFY runs after the gate** (so only survivors are classified) **and BEFORE S3** — because `_find_existing_entity` (`:2705`) uses `kind` as its exact-match discriminator, so a lookup that runs before classification cannot exact-match and degrades to the ambiguous bare-form path. ⚠️ **Moving it after S3 manufactures duplicates — the exact failure this document exists to prevent.** *(An earlier version of this line said the opposite; it was wrong. §16.2.)*

#### The resulting order
```
S0a NORMALIZE + HASH   mechanical, free                                      ← NEW
S0b SOURCE DEDUP       URL-keyed today; the biggest missing lever           ← NEW
S1  NARRATE
S2.1 EXTRACT
S2.2a-α EXACT DEDUP    within-batch + against-store                          ← NEW (store half)
S2.2a-β NEAR-DUP       detect; MERGE only when nothing distinguishing is lost (Jev arbitrates)
S2.2b VET              the gate (identifier-only DISCARD) + adversarial      [Jev]
S2.3 CLASSIFY          kNN fast path → Jev tail, on SURVIVORS only            [kNN + Jev]
S3  RESOLVE + D-d      lookup → against-priors semantic dedup
S5  CONNECT
S6  COMMIT
```

#### ⚠️ The against-store exact dedup has FOUR preconditions, and the fourth does not exist
1. **Key unification** — one hash, one normalization.
2. **Sound-only** — applies only where the key is provably exact.
3. **Union-merge** — merging **unions the attachments** (edges, provenance, sources).
4. ⛔ **An ATOMIC write** — `create_point(dedup=True)` is a **check-then-CREATE**, and there is **no uniqueness constraint on `Point`**. **Two concurrent writers both see "absent" and both create.** **Nothing in the current write path closes that window**; the Postgres target's single transaction does.

**⛔ AND THE MERGE RULE IS NOT NEGOTIABLE — UNION THE ATTACHMENTS; NEVER LOSE EITHER SIDE'S EVIDENCE.** ⚠️ **CORRECTED 2026-09-24 — an earlier version of this paragraph cited `#4899`'s `OVERRIDES:` ruling (*"never from merging two claims into one"*) as forbidding `FUSE` outright. THAT RULING WAS SUPERSEDED BY THE OWNER ON 2026-09-24 (O4):** near-duplicate claims **MAY** be merged, arbitrated by **Jev over the claim plus the narrative/raw data**, **on a high bar and only when nothing distinguishing is lost** (→ §16.4). **What did NOT change: a merge must never cross a difference in a number, a name, a negation or a condition, and both sides' evidence survives.** The supersession is recorded on `#4899`, replacing the old ruling.

**⚠️ Precision on the owner's "dedup first" — it SPLITS into two different operations at two different places:**
- **Source-level and within-batch exact dedup** — need no graph, **belong first.** They shrink the set for the gate, the classifier and the lookup.
- **Against-priors consolidation** (`classify_consolidation`) — needs the S3 lookup, so it **stays after S3.** Its docstring step 1 is *"NOOP (identical) — normalized content equals **a prior**"* — **a prior is a lookup result.**

**And the doc's "classify once" also fixes a double-classification**: today `:4803` and `:4888` both run, because the set is re-classified after S4's merge. **One pass, after the gate, on survivors.**

#### ✅ Jev for classification is cheap, and the seam already exists
**Classification is exactly ONE question** — a `Choice` over the supplied kinds. Measured Jev cost: **$0.000144 at 1 question** (`$0.000194` at 2q, `$0.000243` at 3q) — **~4–7× cheaper than a frontier model**, not the vendor's claimed 40–400×.

**⚠️ But the bigger saving is that most items never reach Jev at all.** The kNN + margin fast path covers the easy majority with **no model call**; only the **low-margin tail** is batched. So Jev's cost is bounded by the **tail size, not the item count**:
> **25k items/month at a ~20% tail → ~5,000 calls → ~$0.72/month.**

**And ⚠️ `llm_tail` is NOT a ready-made Jev seam — that was over-read.** It is a **BOOLEAN** (`kind_classifier.py:93`); the line cited as a seam is `:522`, the **offline-eval CLI disabling it**. The tail is a **hardcoded prompt + a JSON parser**, so **swapping Jev in requires an adapter, a prompt, and a parser** — it is a *small* piece of work, but it is work, not a config flag.

**⚠️ And the "structural enforcement" argument is weaker than it reads.** `closed_vocab_rejects` **already exists** (the closed vocabulary is enforced today), so Jev's `Choice` does not add a new guarantee the pipeline lacks — it removes a class of **malformed / off-vocabulary** emission. And a repo-wide search for `entityCues` returns **no matches** — do not cite it.

**⚠️ And a hard constraint this ordering must respect:** ⚠️ **`#4899`'s Phase-1 mechanical DISCARD has known false positives** — *"a bare `#\d{3,}` in a working note is discardable; **a PR number a decision turned on is not**"* — and **only the lookup or a semantic judgment can tell them apart.** Phase 1 runs before the lookup deliberately, which is why `#4899` **gates it behind a flag, records every discard with its rule id, and requires the counterfactual to be recoverable.** **Any reordering that moves the gate earlier must preserve those three.**

### 4.3 ⚠️ What the steps above do NOT yet cover (review-provided, all unaddressed)
**The step list is the SHAPE, not a complete specification.** Five gaps were identified by review and are recorded here rather than silently implied by the diagram. **None is a decision; all must be closed before implementation.**

| # | the gap | why it matters |
|---|---|---|
| **G1** | **No step PERSISTS the narrative or the raw-fact span.** S6 is *"write entities + connections + metadata/lifecycle"*. §4.0 has no persistence purpose beyond S6. | ⛔ **D1 and §2.4 are not implementable from the steps as written.** If the narrative and the span are not written by a named step, they are not written. **Needs an explicit persistence step (or an explicit S6 sub-job).** |
| **G2** | **No EMBEDDING-DECISION step.** `STORAGE-ARCHITECTURE.md` §12.2a **decides** the `EMBED` gate exists (*"a row can be worth KEEPING but not worth EMBEDDING"*, *"the lever is what gets an embedding"*) — **and this document never mentions it.** S2.3's kNN path **silently assumes every candidate is embedded.** | ⛔ **The two documents would ship contradictory designs.** The `EMBED` gate and the `KEEP` gate **share one rule set and enforce at TWO points** (§12.2a) — so the second enforcement point needs a home in this step list. |
| **G3** | **No per-step FAILURE POLICY.** Only S2.2 (*"fail-open — keep"*) and S2.3 (*"never raises"*) state one. | **The S3 failure is the dangerous one: the natural fail-open marks candidates `new` — MANUFACTURING DUPLICATES, the exact failure S3 exists to prevent.** Also: partial S6 has no transaction/retry boundary vs `#4240`/`#4716`; and **re-narrate has no tie-break** for a worse second narrative. |
| **G4** | ⛔ **`#4911` (no secret redaction) + an append-only immutable raw layer = IMMORTAL SECRETS.** | **`#4911` is listed in §15 as an unrelated capture-path issue. It is not unrelated** — §2.4.2 makes the raw layer **append-only by design**, so **a secret captured once is a secret retained forever.** Redaction must be designed *with* the layer, or the layer's immutability becomes a liability. |
| **G5** | **No migration or backfill for the ~25k existing nodes.** | No backfill, no re-extraction path, and **no decision on the ~4,900 `the <X>` Objects.** Also unaddressed: **what happens to the live S4 `merge_embed_lists` when S4 leaves the step list** — the code and the design must not disagree. |

**⚠️ And G2 is where the two documents bind:** `STORAGE-ARCHITECTURE.md` §12.2a's decided rule (*one rule set, two enforcement points*) **requires a step to hold the second point.** Until G2 is closed, **the storage doc's central lever has no implementation site.**

### S3 — RESOLVE (find what exists · disambiguate · attach)
**In:** the surviving candidates **+ the narrative (as context)**. **Out:** every candidate either **resolved to an existing entity** or **marked new**, with the new context attached.
> *"when we find duplicates we then attach the right context to them so e.g. if in S1 we have a narrative connecting to Object A and we find Object A is already in the graph, then we connect the new things to Object A via edges/operators."*

**S3 owns the lookup** — moved here from S1.5 (§4.2). The lookup is keyed on **candidates**, and candidates only exist **after** extraction.

**Three things happen, in this order:**
1. **Look up** what already exists for these candidates. *(Mechanical.)*
2. **Disambiguate** — for each candidate, is it the existing entity or a new one? **The narrative is the context that answers this.** *"Is this the same Object A?"* cannot be answered from a bare string — only from the surrounding prose. *(Jev — a `Choice` over the supplied candidates.)*
3. **Attach** — for a resolved candidate, connect the new context to the existing entity.

⚠️ **The narrative is an INPUT to this step.** The old S3 listed only *"candidates + what exists"* — leaving the disambiguation context out of the step that needs it most. **Owner, 2026-09-23:** *"we can use the narrative to disambiguate when uncertain."*

**Not find-and-report — find-and-attach.** Mechanical unless there is genuine ambiguity. **S3 is the single authority on whether a candidate is new or existing** (§4.2).

### S5 — CONNECT (two separate jobs, done in order)

**S5 does two things that look similar and are not.** Both only ever **decide** — nothing is written here. The writing is S6.

**Job 1 — wire up this batch's own story.** The narrative says *"Object A changed because of Points X, Y, Z, and it happened through Event W."* S5 checks that the pieces S2.1 pulled out actually agree with that sentence, and creates the connections **among the new things**: A ↔ W, W ↔ X/Y/Z. These are relationships **inside what we just read**.

**Job 2 — compare this batch against what is already stored.** For each new item: is it already in the graph? Does it **change the state** of something (D7 — a lifecycle change)? Does it **contradict** a belief the graph already holds? These are relationships **between the new and the old**.

**Why they are two steps and not one:** they fail in completely different ways. Job 1 fails as *"the batch doesn't hang together"* — a quality problem in the reading. Job 2 fails as *"we just duplicated something, or silently contradicted a live belief"* — a correctness problem in the graph. **A single step doing both would have to report both kinds of failure through one channel**, and they need different handling: Job 1 is a re-read, Job 2 is an operator (`IMPL`/`NAND`) or a supersession.

**Division of labour: the decision model decides, code writes.** Asking a model to emit a graph write directly is how you get an untyped or half-formed edge. **The model answers a narrow question; code turns the answer into a write.** *(This is D6 as ruled — the model proposes both sets.)*

### S6 — COMMIT
New entities + connections + **metadata**. The metadata includes **lifecycle events, which are appended — never written onto the Object** (§5).
**⭐ And every committed node carries its link to the `Source` span** (§2.4.1) — a node that cannot walk up to its source cannot be served as evidence, which is the measured half of the fourth layer (**verbatim beats derived by 15.9 / 22.0 pts**).

---

## 5. The connection and lifecycle model

**⭐ The owner's statement of what the connection layer is FOR (2026-09-24)** — and it is the product justification for this whole section:
> *"the core idea of what we do is being able to understand how an Object relates to events, subjects and points so we can not only have 'what is' but why and the context around it (who, when)."*

**⇒ Two consequences, and the second one is measured.**
1. **The connection layer is not a nice-to-have — it is the product.** *"What is"* is retrieval; *"why, who, when"* is the **graph** around the entity. **A claim with no edges is a fact without context.**
2. **⭐ Traversal is cheap enough to be the default mechanism for it — measured: 1.55–2.35 ms** (storage §12.1d). *"Which claims are about this entity, which event carries it, which source it came from"* is a walk from a node we already hold. **No similarity is involved once the node is in hand** — so the why/who/when value **does not depend on vectorising the entity at all** (storage §12.2c). **What a vector is for is FINDING the entry node, not reasoning from it.**

**The owner's requirement (2026-09-23):**
> *"if a PR replaces a previous Object by a new one, or simply kills a previous object, we need to connect that in the graph appropriately so the graph doesn't keep the old object live. And adding the reasons why (epistemics) is important, and the PR becomes the event connected to both that provides the context of when the change happened."*

**This is the ontology's state-centric model (§2) exactly:**

```
Object A  (superseded — was live)
    ▲
    │
  Event W   ← the PR. Carries "when". The connector.
    │
    ▼
Object B  (new — live)
    ▲
    │
Points X, Y, Z   ← the reasons. Carry "why".
```

**Two mechanics make it work, both already recorded in the ontology:**
1. **Lifecycle changes are appended as Events and never written onto the Object.** `Object.status` is a **fold cache** over lifecycle events (§4.2, §2) — *the event stream is the reconstruction source.* **So the graph never edits Object A; it appends "A was superseded by W," and A's live-ness changes on read.** This is the same property that makes the store rebuildable.
2. **The Event carries "when"** — which is why **the PR should *be* the Event**, not merely be referenced by it. For structured sources this is free: the PR already has its state and timestamp.

### Detecting a lifecycle change — three routes, in order of reliability
1. **The source says it** — `closes #465`, a state transition. Free, exact. *(This is the source-mapping point, §3.)*
2. **The lookup says it** — S3 finds Object A live and the narrative says it changed.
3. **Jev classifies it** — *"does this input change the state of an existing entity, and how?"* — a **Choice** over `created / superseded / deprecated / unchanged`, with a probability. This is Jev's *intent routing* pattern.

---

## 5a. Supersession — we already have it. The gap is DETECTION, not mechanism.

**Owner, 2026-09-23:** *"we'll likely need to implement some superseding system or something like other memory systems do for facts, no? where the more recent fact wins? or the fact attached to the right entity base / done entity lifecycle?"*

**⚠️ The premise is wrong in a useful way: the system already exists, is fully wired, journaled, and tested.** Measured on the live graph (2026-09-23):

| what | where (code) | live count |
|---|---|---|
| `sdk.supersede(old_id, new_id)` / `supersede_point` | `sdk.py:5509` / `:5527` | — |
| `apply_supersessions` — *"the ONE consumer-side discipline"* (`producer side: extractor_v2._supersession_records`) | `commit_ops.py:299` | — |
| `CORRECTS` edge — `(new:Point)-[:CORRECTS]->(old:Point)` | `subgraph.py:28` | **74** |
| `Object.status='superseded'` + `supersededBy` (a **fold**, not a field) | `projection/entities.py:1497` | **32 of 7,867 Objects (0.4%)** |
| superseded `Point`s | — | **74** |

**⇒ The mechanism fires ~32 times against 7,867 Objects (a later snapshot than the 7,863 at §1).** It is not missing — **nothing detects the change.** That is the finding, and it is the same gap §5 names: **the extractor must produce the supersession record, and today it almost never does.** Building a supersession system is not the work; **wiring detection into extraction is.**

**Two forms exist and they are different, deliberately:**
- **Point-level:** the `CORRECTS` edge. ⚠️ For **Points there is no `superseded_by` property** — `subgraph.py:28` records that it is **derived by following `CORRECTS`**. (`Object`s *do* carry `supersededBy` as a fold cache.)
- **Entity-level:** an `ObjectSuperseded` **lifecycle Event** + `_fold_object_superseded` → `Object.status`. **Appended, never written onto the Object** (D7).

### ⛔ "The more recent fact wins" is REFUSED — contradiction test
**There is a recorded decision on exactly this, and it says no.** **`#2354`** (OPEN, epistemic-team) — *"uncertainty verdict layer — close/keep/reject on correction conflicts via quarantine+promote/retract; **sweep never auto-resolves**"*:
> *"the agent loop can drain a decision queue deterministically and **the sweep NEVER auto-resolves**. Auto-accept applies only when EP confidence clears the threshold; everything else queues."* … *"no path auto-resolves a queued decision"*

**Rejected on two independent grounds, either sufficient:**
1. **It contradicts a recorded decision.** Per the contradiction test the route is **a reopen, argued with the owner** — never a quiet adoption.
2. **It is the wrong mechanism for us, and the reason is knowable.** Recency-precedence is what **Hindsight** uses — `"the fact with the LATEST `mentioned_at` is authoritative"` — and they use it **because they deleted `confidence_score`** (migration 2026-04-02). **We kept confidence** (owner ruling, Option 1). Recency is the hedge you reach for **when you have no contention layer**; we have one.

**⚠️ And the reason it matters beyond consistency: a recency rule is SILENT.** It resolves a conflict **without leaving a record**, so *"why is it this way?"* stays unanswerable — the exact property the epistemic layer exists to provide (§2.4). **A `CORRECTS` edge or an `ObjectSuperseded` Event IS the record.** A tiebreak is the absence of one.

### ✅ The owner's second option is what we already have — and it is the right one
**"the fact attached to the right entity / the entity lifecycle" is the built design, not an alternative:** the fact attaches via `aboutObject`, and the **Object's lifecycle** decides live-ness — with `Object.status` as a **fold over appended lifecycle Events**, so the event stream stays the reconstruction source.

**⚠️ One trap to design around, which follows from §5's correction:** a fact that **caused** A's death must **not** die with A. **Blind propagation of staleness down `aboutObject` would kill the reasons along with the thing they killed** — and the reasons are exactly what makes the change auditable. **The rule is: the Object's status propagates to *descriptive* facts about it, never to the *epistemic* operators that judged it.**

---

## 6. Where Jev sits (whole pipeline)

| step | Jev's job | shape |
|---|---|---|
| **S2.2** | entity or reference? atomic? did we account for what the narrative said? | Noul + Score — **adversarial** |
| **S3** | is this the same entity as that? | **Choice** over supplied candidates |
| **S5** | does this change a lifecycle? contradict a belief? | **Choice** over `created/superseded/deprecated/unchanged` |

**All of these can be evaluated in ONE call, in parallel, against the same state** — Jev evaluates its questions concurrently, so the second question costs no extra latency. **But it cannot be one *atomic* question per the vendor's own doctrine:** a 28-item test produced 5 self-contradictions (confident on level, unconfident on keep), which is the signal that a bundled question is not atomic.

---

## 7. Constraints the architecture must respect

### Atomicity is required by the epistemic layer — NOT an optimisation
**Owner, 2026-09-23:** *"we need to have the right granularity for Points to be logical. If we have many arguments mixed together, it's not possible to NAND/IMPL nor modulate relevance."*

**Operators connect only epistemic targets** (`Point↔Point`, `Event↔Point`) — you cannot `NAND` a paragraph. EP confidence propagates over a **factor graph of atomic claims**; a fused claim gives one confidence number to several distinct beliefs.

**⇒ Volume reduction must come from *not writing* claims — never from *fusing* them.** v3's move to atomic Points (`E3`) is a **requirement of the epistemic layer**.

### State values and numeric facts — the carve-out is NARROWER than it looks
v3's **E2** requires state-value facts be preserved verbatim (*"personal best 5K time = 27:12, as of <date>"*) rather than filtered as "counts are noise." `STATE_VALUE_CARVE_OUT` exists in `_granularity_text()` today for exactly this.

**⇒ Any mechanical filter MUST carry this carve-out, or it will drop the facts users care about most.** See `STORAGE-ARCHITECTURE.md` §7 for the low-cost storage question.

#### ⚠️ But the existing carve-out does NOT cover decision-relevant numbers — measured (`#2453`)
`#2453` root-caused this: the carve-out is scoped to **user-personal state** (personal bests, schedules, preferences) and **explicitly tells the mapper to DROP process metrics as "mechanics tokens"** — *"counts/logistics are ephemeral"*. **That policy is right for personalisation memory and starving for engineering-decision memory**: on a number-dense session (latencies, durations, counts) it drops exactly the values the decision turns on.

**⇒ There are TWO value classes, and today's policy keeps one and discards the other:**

| class | example | today | must be |
|---|---|---|---|
| **Personal state** | *"personal best 5K = 27:12"* | retained (E2 carve-out — **`#1534`**, now CLOSED) | retained |
| **Operational / decision values** | *"p99 latency 480 ms at 3k rps"* | ⚠️ **RETAINED — THE FIX ALREADY LANDED** (commit `4a690d0be`, 2026-09-07, PR `#2456`) | retained |

**⚠️ THIS ROW WAS WRONG, AND THE ERROR WOULD HAVE DRIVEN A BAD DISCARD.** An earlier draft said operational values are *"DROPPED as a mechanics token (`#2453`)"*. **`#2453` landed.** `STATE_VALUE_CARVE_OUT` was **extended** (`extractor_v2.py:145-171`) with an operational-value paragraph — *"a concrete value that is the SUBJECT of a decision, observation, or plan is DURABLE too… Measurements, deadlines/freezes, thresholds/TTLs, versions, and counts are carried VERBATIM"* — using the **very examples the draft called dropped** (*"the p95 hit 4.2 seconds"*, *"a ten minute TTL"*). It is **rendered, not merely defined**: a `VALUE_FIDELITY_RULE` (`:230`) goes into the S2/S4 `{anti_routine}` slot, and `_granularity_text()` (`:732-744`) appends it to S1.

**⇒ What is actually missing is MECHANICAL ENFORCEMENT.** The rule is **prompt-only** — `valueGate` still does not exist in code. **That is the real gap, and it is `#4899`'s**, not a value that is being thrown away.

**⚠️ And a new mechanical DISCARD (`#4899`, §16) would previously have inherited this bug** — had it been defined against the *old*, stale reading, it would have discarded operational values harder. **It now inherits the CORRECT policy instead** (`#2453`'s landed carve-out protects them), so the DISCARD must be defined **against the two-class table above**, not against the prompt string alone — **and the surviving gap is enforcement, not policy.**

#### Where numeric facts should live (owner, 2026-09-23) — structured, so they are never re-processed
> *"getting numeric facts stored somehow in a table and available for search so they're not re-processed … an optimisation pattern for better recall that relates to the extractor and needs to be harmonised here."*

**The proposal:** numeric facts (amounts, durations, counts, measurements) are stored as **structured rows** — subject + attribute + value + unit + date — **rather than only as prose inside a claim.** They stay searchable, and **a query never has to re-read prose or re-run extraction to recover a number.**
- It compounds with **D8** (state values → structural storage, testing whether embeddings can be skipped).
- It is **the same question `STORAGE-ARCHITECTURE.md` §7 raises from the storage side** — the two sections must be read together.
- **⚠️ Harmonise, do not duplicate.** `#2453` (retain operational values verbatim) · `#1534` **CLOSED** (E2 state-value Tier-A extraction) · `#2817` (no numeric/locale canonicalisation exists — PT-BR/EN amounts, blocks the value layer) · `#2782` (money needs amount + currency on entities) · `#2521` (numeric aggregation across sessions, counted under supersession) · `#2820` (tracking map — its **"state & value model"** workstream owns all of this).

**⇒ The extractor decides what is retained; the storage layer decides where it goes. Neither can be built alone.**

---

## 8. Already in the code — do not rebuild

| thing | where |
|---|---|
| **`Projection`** — a **two-method** event-sourced Protocol (`apply`, `rebuild`) with `InMemoryProjection` as an existing second implementation | `tortoise/projection/__init__.py` |
| **`classify_consolidation`** — pure, no LLM, no graph I/O, post-extraction/pre-write; emits `noops`/`deletions` on a channel not written to the graph | `tortoise/extractor_v2.py` (E7/`#1539`) |
| **`KindClassifier`** — kNN top-5 → margin gate → `nearMisses` rerank (no LLM) → batched LLM for the low-margin tail → fail-open + census. **Jev replaces its LLM tail.** | `tortoise/kind_classifier.py` (`#1695`) |
| **S2/S4 merge** | `merge_embed_lists` (def `extractor_v2.py:2284`; **called at `:4845`** — ⚠️ an earlier draft cited `:4838`, which is `stage_stats`) |
| **Pack enforcement machinery** — `chains` with `enforcement` levels, `nearMisses`, `subclassOf`, `equivalentTo`, `extraction.sourceTypes` | `packs/*/manifest.yaml`; 20+ files reference each |
| **The narrative prompt slot** — S1's granularity rule is already injected | `_granularity_text()` |

---

## 9. Decisions (owner, 2026-09-23)

| # | decision | status |
|---|---|---|
| **D1** | The narrative lives in **Supabase storage, referenced by its `Source`** — **NOT in the graph**, not a `Document` node, not a `Point`. **Amended 2026-09-23:** *"metadata on its `Source`"* was too loose — the graph keeps the **reference**; the **text** stays out of the graph, and therefore out of RAM. | **DECIDED (amended)** |
| **D2** | A GitHub PR is **an `Object` (WorkItem) + its merge is an `Event` + its body is a `Source`.** A completed task is a **lifecycle `Event` whose status is the fold** — not a second node. | **DECIDED** |
| **D3** | The narrative's abstraction level → **Option 4: the narrative is a *focus*, the raw text is the *source*** (extraction may consult raw). Chosen *for now*, pending a measurement. | **PROVISIONAL** |
| **D4** | **Salience is relational.** One **entity-keyed neighbourhood lookup**, consumed by **the judgment** (salience) **and by S3** (resolution). **⚠️ NARROWED 2026-09-23:** extraction no longer consumes it. | **DECIDED (narrowed)** |
| **D5** | One adversarial reviewer — **one step, two questions, one artifact**: item validity **+ coverage**, both at **S2.2**. | **DECIDED** |
| **D6** | **Two distinct connection sets** — within-batch and to-graph, both **proposed by the model** (*"LLM proposed good"*). | **DECIDED (confirmed)** |
| **D7** | **Lifecycle = appended Events + folded status**, the PR *as* the Event. | **DECIDED** |
| **D8** | **State values → structural storage**, testing whether embeddings can be skipped. ⚠️ **`#1534` CLOSED and `#2453` LANDED (`4a690d0be`) — the RETAIN half is done; the ENFORCEMENT half is `#4899`'s.** | **DECIDED (retain half landed)** |
| **D9** | **The lookup follows extraction.** **S3 owns it** — keyed on candidates, narrative as disambiguation context. **No pre-load, no S1.5.** ⚠️ **S3's scope overlaps `#2730`'s — reconcile before building.** | **DECIDED** |
| **D10** ⭐ | **A document is a SOURCE, not a graph node.** `:Document` folds into `:Source`; content moves to raw storage; **liveness is a READ of its entities** (high confidence, not superseded, not NANDed). The entity layer becomes **uniform over documents, code and transcripts.** *"that's the reasoning/knowledge layer."* **⭐ The ontology half is LANDED: `ONTOLOGY.md` v3.15, PR #5022 (29 sites).** **⚠️ The code half has NOT landed, and it is bounded by `#2489`** (replay key `coalesce(title,name)` → `url`; rebuild does not repair pre-existing graphs). **Five consequences ruled 2026-09-24 — see the §S0 block above and `STORAGE §9.5`:** **Q1** `aboutDocument` kept, label retargeted only (**NOT** collapsed into `aboutSource` — that would delete a rebuild guarantee); **Q2** two axes kept (`sourceKind` + `documentKind`); **Q3** `doc_status` dropped, `format` moves, `content` leaves the graph; **Q4** the `documents` cap re-pointed at `:Source`; **Q5** fold + rekey + migrate — **free today, expires when the commit lane first runs**. | **DECIDED 2026-09-24** |
| **D11** ⭐ | **Stay on hosted FalkorDB; optimisation deferred.** Revisit with users; price **self-hosting** first (SSPLv1 — same engine, no rewrite). | **DECIDED 2026-09-24** |
| **D12** ⭐ | **Near-duplicate MERGE is authorised**, with **Jev as arbiter** over the claim plus narrative/raw data. ⛔ **Supersedes the `OVERRIDES:` on `#4899`** (*"never merging two claims into one"*) — **the owner reversing their own earlier ruling.** ⚠️ High bar; never across a differing number, name, negation or condition — **the list has since grown: §16.4 carries the current never-across classes** (it adds language, date/scope, and the pair-read load-bearing connective, `#5139`; the authoritative predicate there is `_boundary`). | **DECIDED 2026-09-24** |
| **D13** ⭐ | **The working method for each step is draft → run → look → refine → repeat**, *"until good."* Replaces the sample-vs-deterministic debate. | **DECIDED 2026-09-24** |

### The provenance rule derived from D2
**Do NOT mint a `Source` for events.** No system found uses an event as the *primary* provenance anchor — the anchor is always a document or chunk, with the activity as a *second dimension*. (PROV *permits* naming an event node — `prov:Generation` etc. may carry an identifier — and Cognee has `PipelineRun`; both keep the document as the anchor.) **No source states a reason for not doing it** — reported as a strong negative finding, not a rule.
**⚠️ And PROV has no `Source` class at all** — in PROV "source" is a *relation* (`hadPrimarySource`, `wasDerivedFrom`). Our `Source`-as-a-class deviates from PROV.

### ⛔ Adopted-vs-refused on the research (contradiction test)
- ✅ **PROV's `Entity ⊥ Activity` disjointness is CONSISTENT** with our ontology (Event and Object are already separate core types). No conflict — usable.
- ⛔ **REFUSED: GraphRAG's consolidation rule** — *"If the provided descriptions are contradictory, please resolve the contradictions and provide a single, coherent summary."* A resolved contradiction is **one** claim, not two, and the losing side has no node left to support or contradict. **Directly destructive of the epistemic layer.** Reported as a **counter-precedent**, never a candidate.

---

## 10. The narrative-first hypothesis — tested, and the better argument

The owner's hypothesis: *"narrative matters more for us because we're focused on logic/contentions, they're not."*

**VERDICT: partly defensible — the premise is true, the inference is unevidenced.**

- ✅ **The premise holds.** GraphRAG **erases** contention (consolidation), Graphiti **replaces** it (`invalid_at` = valid-time termination; the loser is dead, not argued against), Mem0 **accumulates** it without ever asserting a relation. **None holds two live claims in mutual tension with typed support/attack and independent confidence.** Tortoise is genuinely distinct.
- ⛔ **The inference does not follow.** **Zero** systems anywhere extract support/attack relations from a machine-generated narrative — and the field that *does* model support/attack between claims, **computational argumentation, extracts from RAW text.** Universally, as far as the search reached.
- ⛔ **Competitor practice cannot be recruited as evidence either way** — no competitor has *any* stated reason for extracting from raw. It was never a decision. **"Nobody asked."**
- ⛔ **Zero precedents for summarise-then-extract.** The one apparent candidate was miscited and corrected — it is the *reverse* direction (input = raw long document, output = a compressed graph).
- ⛔ **No head-to-head measurement exists** (raw vs summary, for entities, relations *or* claims).

### ⭐ The stronger argument the evidence leaves open — the real justification for narrative-first
**It is not "contention vs facts." It is CROSS-SOURCE CLAIM NORMALISATION:**
- Argument mining's claims are **co-located, co-authored, single-document** — nothing needs resolving across sources.
- **Tortoise's IMPL/NAND edges join claims ACROSS sources** (sessions, issues, PRs, meeting notes). That requires **(a) recognising two independently-worded propositions as the same claim** before any support/attack edge can join them, and **(b) normalising hedging, scope and stance.**
- **A synthesis step is the classical mechanism for exactly that.**

**⇒ This is a better argument than the stated one, it is untested, and the evidence neither confirms nor refutes it.**

### A second reframing worth keeping
**Tortoise's input is largely DIALOGUE, not documents.** Turning a transcript into a narrative is arguably **discourse reconstruction**, not compression — making the GraphRAG comparison apples-to-oranges. If so, narrative-first may be an **input constraint, not a design choice.**

### Why D3-Option 4 is the right call regardless
Option 4 (narrative as *focus*, raw as *source*) **hedges precisely this unresolved question** — it does not require the narrative to be lossless, because extraction can go back to the raw for what matters. **The research justifies the choice rather than merely permitting it.**

### ⚠️ The falsifier to measure
The research names it: **does the summary lose the hedging / scope / stance information that a claim's confidence depends on?** Adjacent evidence (no head-to-head study exists): summaries can be unfaithful, hallucination propagates across summary chains, and summarising raises precision while **dropping recall**. **Any measurement must be run on OUR corpus — none of these transferred cleanly.**

### ✅ Update 2026-09-23 — a STATED reason for raw extraction now exists
Hindsight is the first system found with a *stated* reason, and it supports **Option 4**:
- **A summary cannot see prior state, so its counts and absences are batch-scoped and misleading.**
- **Summaries are marked UNTRUSTED downstream** — *"a reading aid, not evidence"* — editing on their strength is forbidden.
- **Re-extraction is expected and always from raw:** changing the ingest config *"means reprocessing documents, which re-extracts from the original text and therefore discards any curation you had already done."* **Raw is the re-derivable ground truth; curation on derived facts is not.**

**⇒ Option 4 is no longer merely hedged — it is evidence-backed.** The narrative stays the cheap searchable tier and a *focus*; extraction keeps access to raw as the source.

---

## 11. Verification against Hindsight (Vectorize) — 2026-09-23

**Why checked:** Hindsight's paper proposes `world` / `experience` / **`opinion`-with-confidence** / `observation` — the closest comparable to our contention model. Repo `vectorize-io/hindsight` (**MIT**) read at HEAD `f00ad1fe`; paper arXiv **2512.12818**; ACL 2026 demo `2026.acl-demo.27`.

### VERDICT: it COMPLICATES our model — a comparable built our closest feature, then deleted it

| | |
|---|---|
| **Paper (Dec 2025)** | `opinion` = `(t, c, τ)` with **`c ∈ [0,1]` confidence**, updated by an LLM verdict `Assess(o,f) → {reinforce, weaken, contradict, neutral}` via `c ± α` |
| **Shipping code (2026-09)** | **`opinion` REMOVED. `confidence_score` COLUMN DROPPED** (migration dated **2026-04-02**). Constraint is now `fact_type IN ('world','experience','observation')`. |
| **Trail** | 2026-01-15 delete opinions → 01-21/26 `mental_model` → renamed `observations` → **04-02 drop `confidence_score`** |
| ⚠️ **The vendor still advertises it** | The blog and the **July 2026 ACL camera-ready abstract** still claim *"evolving opinions with confidence scores"* — **three months after the column was dropped.** The docs caught up; the marketing did not. |

### ⛔ No typed contention relation has ever existed
```python
CheckConstraint("link_type IN ('temporal','semantic','entity','causes','caused_by','enables','prevents')")
```
**Seven edge types — none adversarial.** The `contradict` verdict was never persisted as an edge.

### What replaced confidence — and it is genuinely cheaper
1. **`proof_count`** — *"not an increment counter, but a **count of distinct source facts, recomputed** each time new sources fold in."*
2. **`Trend`** — a qualitative band computed **algebraically from evidence timestamps**: `STABLE / STRENGTHENING / WEAKENING / NEW / STALE`.

**Neither is assigned at write time; both are derived from the evidence. Neither can be attacked.**

### The comparison that matters
| system | two conflicting claims | contention relation? | confidence? |
|---|---|---|---|
| **GraphRAG** | erased | ❌ | ❌ |
| **Graphiti/Zep** | replaced (`invalid_at`) | ❌ | ❌ |
| **Mem0** | accumulated, no relation | ❌ | ❌ |
| **Hindsight (paper)** | accumulated + scalar-updated | ❌ | ✅ per-opinion float |
| **Hindsight (code)** | **replaced/deleted — latest `mentioned_at` wins** | ❌ | ❌ **removed** |
| **Ours** | **held in tension** | ✅ typed IMPL/NAND | ✅ per-claim, **propagated** |

**No comparable holds two conflicting claims in tension with a typed relation.**

### Where Hindsight refuses to erase — in prose, not the schema
At answer time the rule is recency-precedence (*"the fact with the LATEST `mentioned_at` is authoritative"*) — **but when the temporal rule cannot settle it, the model is told to name the conflict:**
> *"When the data is genuinely ambiguous: SAY SO in your answer. Name the conflicting facts. … Acknowledging ambiguity is a successful answer, not a failure mode."*

**⚠️ So their contention hedge is a sentence the model writes and then discards — not a stored object.** They consider that sufficient in production and on SOTA benchmarks.

---

## 12. Adoptable vs REFUSED (contradiction test applied)

### ✅ ADOPTABLE — contradicts no recorded decision
| finding | why we want it |
|---|---|
| **Raw layer append-only on purpose** | *"A retain path that dropped a fact because it resembled one already stored would be a retain path you could not trust to have kept what you sent it."* Our own argument, independently arrived at. |
| **Anti-collapse rule in dedup** | *"If they differ in ANY important detail — a number/quantity, a named entity or language, **a negation**, or a condition — set `action` to `keep`."* A deliberate refusal to collapse a difference; mirrors our atomicity constraint. |
| **Invalidation by RELOCATION, not a flag** | `{"state":"invalidated"}` *"does not set a flag to be filtered later. It **moves the row out of the active table into a separate archive** … So recall needs no state predicate… **no query pays for your cleanup**."* Causal edges snapshotted onto the archived row. **Reversible.** Composes with D7. |
| **Provenance ids are an ALLOWLIST, never an exclusion list** | Because `based_on` carries ids addressing *different tables*, an exclusion-list check *"would report every one of them as missing."* A transferable bug we should not rediscover. |
| **Evidence carries exact quotes** | `ObservationEvidence{memory_id, quote, relevance, timestamp}` — *"Each evidence item must include an exact quote."* The same instinct as our `quote` field (`#1509` E3). |
| **Every derived belief carries a machine-readable back-reference** | `source_memory_ids` + `based_on` is what lets them detect a conclusion that lost its grounding. |
| **Extraction from raw; summaries marked untrusted** | ✅ **confirms D3-Option 4** (§10). |
| **No event node in provenance** | ✅ **confirms D2's rule** — the anchor is always a document/utterance. |

### ⛔ REFUSED — contradicts a recorded decision ⇒ a REOPEN, not an adoption
The contention + confidence model **is a recorded decision** (`ONTOLOGY.md` §3.1 *"evaluations … are Statements (Points) with EP confidence — not edges"*; the operator layer; `#1509` §2.4 ontology-first). **Hindsight's practice contradicts it, so it cannot be adopted — but it must go in front of the owner as a reopen, with these five challenges stated plainly:**

1. **⭐ They built `opinion`+confidence and deleted it in ~4 months.** If we cannot say what we do *differently* from `Assess(o,f) → c ± α`, then "per-claim scalar confidence updated by LLM evidence-assessment" is a **demonstrated dead end**, not a novel design.
   **→ Our answer:** theirs is **evidence-driven scalar drift** — each new fact independently bumps each opinion it resembles, with **no support graph to traverse**. Ours is **propagation over a typed factor graph.** Different mechanisms. **But the difference is currently argued, not measured.**
2. **No typed support/attack exists in the nearest comparable** — 7 edge types, none adversarial. The burden is on us to show why an **edge** beats a **prompt rule + recency precedence**.
3. **They resolve; we hold.** *"the page says Y — and can say why — instead of preserving both a paragraph apart."* A **deliberate ruling against our shape.**
4. **Their hedge is in prose and they consider it sufficient** at production scale. **Our contention-as-graph-structure is a real cost that must be justified on something their benchmark does not measure: audit, adjudication, or propagation.**
5. **⭐ `proof_count` + `Trend` may be the cheap 80%.** A recomputed distinct-source count plus evidence-recency bands deliver a corroboration signal with **zero** LLM belief-judgement and **zero** propagation arithmetic. **These are the parts Hindsight kept.**
   **→ Our answer:** `proof_count` measures corroboration by **independent mentions**; EP confidence measures **whether a claim survives its attackers**. Hindsight cannot represent *"ten supporting mentions AND contradicted by a stronger argument"* — it has no place to put the attack. **But this too is argument, not measurement.**

### The sharpest open question this leaves
**Hindsight's answer to "how do you avoid erasing a real disagreement?" is: keep both at the evidence layer, resolve at the conclusion layer, refuse to pick when the temporal rule does not settle it — and say so in words. Our answer is to make the disagreement itself a stored object.** Nothing found contradicts that being *possible*; nothing found supports it being *necessary*; and the one system that had the closest feature **removed it.**

### Not established
- **No independent evaluation or critique of Hindsight was found.** Everything beyond the ACL peer review is vendor-reported; the benchmark claims are **vendor-asserted** (**not** verified here).
- **The reason for the deprecation is NOT public** — the code shows the arc, not the reasoning. Whether it was a *quality* judgement (confidence wasn't useful) or an *architecture* migration (folded into mental models, which then lost the scalar) is **unresolved**. If we want it before touching our own confidence field, it is not in the repo or the paper — **it would have to be asked of the maintainers.**
- All code findings are **static reads**; no retain/recall cycle was executed.

## 13. How Hindsight actually finds connections and relevance — read from the code (2026-09-23)

**This is the section that matters** — *not* their confidence field. Hindsight's connection machinery is read straight from `engine/retain/link_utils.py` (45 KB), `link_creation.py`, `causal_links.py`, and `engine/search/link_expansion_retrieval.py`. All static reads at HEAD `f00ad1fe`.

### 13.1 The shape: bounded seeds, then bounded expansion
Recall starts from **`GRAPH_SEED_LIMIT = 20` semantic seeds**, then *expands* through the stored signals. It never searches everything and never asks a model whether two facts relate.

### 13.2 There are exactly THREE connection signals
| signal | how it is produced | score |
|---|---|---|
| **Entity** | at **query time**, a self-join through a `unit_entities` mapping table — **no stored edge** | `COUNT(DISTINCT entity_id)` shared with the seed set |
| **Semantic** | **precomputed at insert time** — a kNN graph, each new fact linked to its nearest existing facts **above a cosine threshold** | the cosine similarity (`weight`) |
| **Causal** | **LLM-extracted during fact extraction** | `weight + 1.0` — *"boosted as highest-quality signal"* |

**That is all of it.** Two of the three are mechanical; only causality needs a model.

### 13.3 ⭐ Where the LLM is allowed to connect — and the bound is brutally tight
The whole causal prompt is:
```
Link facts with causal_relations (max 2 per fact). target_index must be < this fact's index.
Type: "caused_by" (this fact was caused by the target fact)
```
**Read the three constraints:** `target_index` refers to a fact **in the same extraction batch**; it must be **earlier in the batch** (`< this fact's index`); and a fact may propose **at most 2** such links.

**⇒ The LLM never sees the graph and is never asked to find connections to it.** Cross-batch connection is **mechanical only** (ANN + entity join). The model is a *within-batch, backwards-only, fan-out-2* proposer.

### 13.4 Every expansion is bounded, and one bound is a timeout
| bound | value |
|---|---|
| seeds | `GRAPH_SEED_LIMIT = 20` |
| ANN neighbours per fact | `top_k = 50` |
| **causal links per fact** | **2** |
| temporal links per fact | `MAX_TEMPORAL_LINKS_PER_UNIT = 20` |
| **entity fan-out** | **`graph_per_entity_limit = 200`** (a LATERAL cap, *"to prevent high-fanout entities from exploding the self-join intermediate rows"*) |
| **when over budget** | **`graph_expansion_timeout` DROPS entity expansion entirely** |

**They treat connection fan-out as a resource to be bounded, not a quality signal to be maximised** — and they would rather lose a whole arm of recall than exceed the budget.

### 13.5 Relevance = fusion of independent legs, not one judgement
Recall legs (semantic · BM25 · graph · temporal) are merged by **reciprocal rank fusion** (`k = 60`) and then **reranked** by a cross-encoder. **No LLM decides whether a fact is relevant.** Relevance emerges from independently-ranked lists being combined.

### 13.6 ⭐⭐ THE FINDING THAT BITES US: they materialised entity edges — then DELETED them
From migration `e9b2c7d1f3a4_drop_entity_memory_links.py` (2026-05-26):
> *"Entity edges are no longer stored in `memory_links`. The /graph endpoint derives them on demand from `unit_entities`, and recall already used the `unit_entities` self-join. **Storing entity rows duplicated state we never read from the link table — on a 10k-unit benchmark bank, entity rows were 53% of all link rows (~190 MB after indexes) and recall never touched them.**"*

**Our `aboutObject` is the single largest edge type in our graph at 27,310 edges.** This is the comparable's measured answer to exactly that shape, and the answer is: **the edge rows were 53% of all link rows, recall never read them, so they were deleted and the relationship is derived at query time from a mapping table.**

⚠️ **This does not automatically transfer** — their `/graph` endpoint *does* derive the edges on demand, so the capability is preserved, and **we have not measured whether our recall reads `aboutObject`.** That measurement is the precondition.

### 13.7 Two more transferable mechanics
- **Precompute the expensive lookup OUTSIDE the write transaction** (`pre_computed_ann_links`, "Phase 1"): *"Runs on a separate connection OUTSIDE the write transaction to avoid holding locks during expensive HNSW index probes."*
- **Split ANN queries per `fact_type` so the planner uses partial HNSW indexes** — *"Without the fact_type filter, the planner falls back to sequential scan (~50x slower)."*

### 13.8 What this does to our design
| our step | their practice | verdict |
|---|---|---|
| **S5 CONNECT — two sets, within-batch *and* to-graph** (D6, decided) | LLM does **within-batch only**, max 2, backwards; **to-graph is mechanical** (ANN + entity join) | ⚠️ **A direct challenge to D6.** Not a contradiction of a *recorded* decision — D6 **is** the recorded decision, and it is the newer one — so this is evidence for the owner, **not** a change we may make. |
| **S3 MATCH → AND ATTACH** | entity resolution is a **join key**, and the link rows were deleted as pure storage cost | ✅ **consistent** — and it says our attach step should cost little. |
| **minting an `Object` per entity** | entities are extracted to **link facts** (*"Extract anything that could help link related facts together"*) and kept in a mapping table | ⚠️ **The strongest available support for the `^the ` finding.** Their entity extraction *purpose* is linking; ours has become node creation. |
| **connection fan-out** | bounded everywhere, with a timeout that drops an arm | ⚠️ **We bound nothing today** — `the plan doc` (63) and `config/ci-surfaces.yml` (123) are unbounded. |

**⇒ The three questions this raises for D6 — for the owner, not for us to decide:**
1. **Should the LLM be allowed to propose connections to the graph at all**, or should cross-batch connection be mechanical (embedding + entity) with the model confined to within-batch, as Hindsight confines it?
2. **Should `aboutObject` be a stored edge or a derived join?** ✅ **MEASURED 2026-09-23 — the answer is split, and that decides it.** The primary search path does **not** read it (`exit 1`, three files, case-insensitive) — so Hindsight's premise holds *there*. But **four default-ON sites do**, two of them customer-callable (`tortoise_belief_timeline`, `topic_summarize`), plus two on the ingestion path (`mining._temporal_wire` **mints a NAND** from a 2-hop traversal), and **23 test files assert the edge**. **Deleting it would silently break two public surfaces → the comparable's conclusion does NOT transfer.** The structural door stays open though: **zero** `aboutObject` edges carry any property. ⚠️ **And deriving is what creates the fan-out problem** — a join on a hub with ~1,200 claims yields ~1,200 rows *per anchor*, which is exactly why Hindsight carries a `LIMIT 200` and a timeout that drops the whole arm. **See `STORAGE-ARCHITECTURE.md` §13 for the full table and the durability finding, and §11 for why the connection layer is cheap to keep.**
3. **Should every expansion have a hard fan-out cap**, with the option to drop an arm under budget pressure?

---

## 14. Not established — do not treat as known
- **Whether the neighbourhood lookup changes extraction's output is UNMEASURED — and the new ordering makes it moot.** The old design put the lookup before extraction so extraction would emit *"attach to A"*; S3 now resolves authoritatively instead (§4.2), so the question is no longer load-bearing. **What IS still unmeasured: whether S3's disambiguation is accurate**, and the cheap test is to replay a handful of real sessions **by hand** and show the candidates matched vs created (small-sample-first).
- The `~8.6 Objects/session` figure is **inferred**, not directly measured — `extractedFrom` points at `:Source`, which carries no `id`, so Objects could not be grouped per session.
- **Nothing measures how much of the surviving 2,963 non-`the` Objects is still junk** — `PR #465` proves a non-`the` Object can also be a bare reference.
- The `^the ` regex is a **floor**, not the whole fix.

---

## 15. Issue map — what this document informs

**Purpose:** every section above exists to inform a filed issue. This is the index, so a reader holding an issue finds the reasoning, and a reader holding a section finds the work it drives.

| section | issue(s) | what the issue takes from here |
|---|---|---|
| §1 the mechanism (`^the <X>`, 62.3% of Objects) | **`#4899`** · `#4894` | the **mechanical DISCARD outcome on E7** — the `valueGate` slot, no LLM, flag-gated |
| §1 v2 → v3 → v4 | `#1509` (epic) · `#4894` | **v3 increases volume; v4 must land with or before v3's E2/E3** — and v3's **E4 has already landed** (`merge_embed_lists`, `extractor_v2.py:4845`), so its premise is stale |
| §2 three tiers | `#2281` | the **narrative is the ingest-time-distilled layer** with raw-turn fallback |
| §2 narrative placement (D1) | `#4894` | narrative = `Source` metadata, **not** a `Document` node |
| §3 source-varying steps | `#2714` · `#1844` | source types declare a **field→type mapping** that configures S0/S1/S2/S5; structured sources are exact and cheap |
| §4 S0–S6 | `#4894` · `#1509` | the step architecture itself |
| §4 S3 RESOLVE (was S1.5) | `#4511` (**CLOSED** — now just a fixed bug, no longer a dependency) · `#2730` | **entity attachment** is the mechanism; the lookup is keyed on candidates and disambiguated by the narrative |
| §4 S2.2 VET + §6 Jev | **`#1026`** (the `valueGate` slot) · `#4899` | Jev as the **decision-only** mechanism; measured **$0.000243/item at 3 questions**, **~4–7× cheaper than frontier, not 40–400×** |
| §4 S3 MATCH | `#2730` | find **and attach** — Jev as a `Choice` over supplied candidates |
| §4 S5 CONNECT + §5 lifecycle (D6/D7) | **`#2552`** · **`#4716`** | operator edges are **not wired or persisted** (measured **0/4** on the planted-operator corpus); the mint bypasses E7 and endpoint refs are never remapped |
| §5 lifecycle events | `#4240` | lifecycle is **appended Events + folded status** — which is why the missing journal is a durability bug |
| §7 atomicity + §7 state values | **`#2453`** (LANDED) · `#2820` · `#4432` | the `STATE_VALUE_CARVE_OUT` must survive any filter; `'extraction emits statement only'` is contradicted by the live rule extractor. ⚠️ **The former "`#1509` (E2, E3)" citation was wrong — `#1509` has no §9 and no E2**; **E2 is `#1534`'s slot** (CLOSED) |
| §8 already in code | `#1026` · `#2714` | packs already declare `chains`/`nearMisses`/`subclassOf`/`sourceTypes` — **the work is enforcement, not design** |
| §9 decisions D1–D13 | `#4917` | the handoff record |
| §10–§12 research | `#2730` · `#4333` | the Hindsight checks |
| §13 connections & relevance | **`#2730`** · **`#1026`** · `#4240` | cross-batch connection is **mechanical** in the nearest comparable; entity edges were **deleted** at 53% of link rows |
| §14 not established | `#4894` | the small-sample-first measurement list |
| **capture-path issues (same pipeline, not steps)** | `#4897` · `#4911` · `#4714` · `#4614` | mid-word truncation at 5,000 chars · **no secret redaction** · **2 of 4 harnesses 404** · invisible quota refusals |

**The three questions this document raises but does not decide** (§13.8) belong to **D6** and are the owner's: cross-batch connection by LLM or mechanically; `aboutObject` as edge or join; hard fan-out caps.

**Not yet filed from this document (candidates, not decisions):**
- **The raw-vs-narrative measurement** (D3's falsifier, §10) — the next real work, and the one that settles whether Option 4 stays provisional.
- **Fan-out caps** and **the `aboutObject` join-vs-edge question** — mirrored in `STORAGE-ARCHITECTURE.md` §13, because they are storage decisions as much as extraction ones. ⚠️ **(2026-09-24: both now have filed gaps — the fan-out cap `#5010`, the derive-vs-store question still owner-pending.)**

---

## 16. Decision protocol run (AGENTS.md, 2026-09-23)

Each open decision was run through the mandated sequence: **contradiction test first → research (does it actually need a human?) → the required artifact.**

| decision | contradiction test | needs the owner? | artifact / action |
|---|---|---|---|
| **Keep contention + EP confidence** (ruled Option 1) | **No contradiction** — it *is* the recorded decision (`ONTOLOGY.md` §3.1). The field converges **against** it. | ruled | ✅ **`OVERRIDES:` posted on `#2730`** — the field's default is resolve/erase; we hold in tension |
| **Granularity: never fuse claims** | **No contradiction** — owner ruling. GraphRAG's consolidation contradicts it. | ruled | ✅ **`OVERRIDES:` posted on `#4899`** — the field consolidates |
| **D6: LLM proposes to-graph connections** | **Contradiction** — Hindsight's mechanical cross-batch contradicts D6, so **not adoptable**. Route = reopen. | **still open** | reopen in front of the owner (§13.8). **No `OVERRIDES:`** — LLM edge extraction *is* the field default, so D6 is not against the grain |
| **`aboutObject`: stored edge vs derived join** | No contradiction — no decision mandates materialization | **no** | ✅ **MEASURED + RULED: KEEP the stored edge.** The primary search path **does not** read it, but **four default-ON sites do** (two customer-callable) → deriving would silently break two public surfaces. **Owner 2026-09-23: the remaining unknown — *are those tools ever called?* — is unanswerable pre-launch, so the question is DEFERRED, not decided.** Keep for now; optimise with users. |
| **Fan-out caps** | No contradiction; field converges (Hindsight caps at **200** *and* carries a timeout that drops the whole arm); aligns with our volume goal | **no** | ✅ **ADOPTED at 200** (owner, 2026-09-23: *"fine on adding the cap"*). **Above our current worst hub (123), so it binds nothing today and cannot lose data now** — a guard rail against runaway fan-out, **not** an optimisation. Refine with real usage. See `STORAGE-ARCHITECTURE.md` §11.5. |
| **Keep the epistemic + entity connection layer** | No contradiction — it *is* the product. The field's cost-driven default (Hindsight derives instead of storing) points the other way. | ruled | ✅ **KEPT — owner, 2026-09-23.** Cheap because edges are **property-free** and Postgres holds them **on disk** (~3 MB ≈ $0.0004/mo). **`OVERRIDES:` posted on `#4333`** — never cut this layer to save money. See `STORAGE-ARCHITECTURE.md` §11. |
| **D3: raw vs narrative** | No contradiction (D3 is provisional) | **no** | ✅ The owner already approved the measurement. **The measurement decides** — not a gate |

**Deliberately NOT marked:** our class-based `Source` deviates from PROV (§9.2 of the storage doc), but modelling a source/document as a node is **not** against the industry default — GraphRAG, Graphiti and Hindsight all anchor to a document node. Deviation noted; **no `OVERRIDES:`** because it is not a departure from practice.

**⚠️ The "decision ledger" does not exist in this repo.** `docs/plans/2026-09-22-2814-rebuild-config-durability.md:316` records it: *"the 'decision ledger' earlier drafts named does not exist in this repo (`rg -il 'decision ledger'` matches only this plan)"*. **The `OVERRIDES:` comment on the issue is the marker's only home** — do not cite a ledger that isn't there.

### 16.1 The review cycle — outcome recorded (2026-09-23)

**Two fresh-context reviewers per document, complementary lenses; full consolidation in `REVIEW-CONSOLIDATED-2026-09-23.md` (6 P0 · 34 P1 · 18 P2).** This section records **what was done with each category**, so a later lane can tell a correction from an omission.

| category | count | disposition |
|---|---|---|
| **P0 — a false load-bearing claim, or two documents that contradict each other** | **6** | **All 6 corrected in place — A4 only after §16.2 caught it** (A1 vector-index model · A2 truth/derived test · A3 `#2453` landed · A4 VET/S3 circular dependency — `MERGE-INTO-EXISTING` removed from VET's outputs, restoring S3 as the sole authority · A5 the 10× cannot reproduce itself · A6 *"an Object is a name"*). **The corrections are marked in place — most with `⚠️ CORRECTED`, some with the error named directly in the row (A1 *"REWRITTEN TWICE"*, A3 *"THIS ROW WAS WRONG"*, A6 *"DO NOT WRITE"*).** |
| **P1 — code-evidence, citation, or arithmetic defects** | **34** | **All corrected.** Arithmetic recomputed (B1.1 §1, B1.2/B1.3/B1.5/B1.19, §5's table); citations fixed or the claim deleted (B1.16 `#1509 §9`/`E2`, B1.18 `MITIGATES`, B2.9 `llm_tail`, B2.18 `#3011`); **B2.1's constraint 4 kept visible with the evidence that falsifies it**, so it cannot be re-derived. |
| **P2 — completeness gaps** | **18** | **Recorded, not silently dropped** — this document's §4.3 (G1–G5) + `STORAGE-ARCHITECTURE.md` §5.1/§14/§15. |
| **Genuinely a decision, not a correction** | **6** | ⛔ **Escalated to the owner — §16.3. These are NOT applied and NOT decided.** |

**⚠️ The rule applied throughout, and it is the one that matters:** where a claim about **process** had no artifact to check it against, it was **DELETED rather than reworded** (the `#3011` thresholds, *"carries most of the retrieval value"*, the `#2453` reading). **Rewording a self-referential claim just generates the next review round; deleting it ends the question.**

### 16.2 ⛔⛔ THE SECOND REVIEW CYCLE FOUND THAT THE *FIRST REVIEW* WAS WRONG — and this is the most important lesson in this document
**Cycle 2 (2026-09-24, four fresh-context reviewers + two gap analysts) checked the *corrections*, not the original text. Five of them were false. Three trace to the same source: `REVIEW-CONSOLIDATED-2026-09-23.md` — the cycle-1 review itself — whose findings were applied verbatim without re-verification.**

| what the doc now said | what the primary source says | where the falsehood came from |
|---|---|---|
| *"`#3011`'s thresholds were embellished 3 of 3; REMOVED"* | the frozen doc **carries all three** (`H1 ≥15`, `H2 ≥10`, `H3 −5`) | **cycle-1 B2.18** — conflated *"also in H4 / also in the falsifiers"* with *"not in H1/H3"* |
| *"the lookup does not need kinds → constraint 4 evaporates"* | `_find_existing_entity` **uses `kind` as its exact-match key** | **cycle-1 B2.1** — read the query builder and called it the lookup |
| *"there is no `:Document` node and none is created"* | `:Document` is written, read, **and quota-counted** (`quota.py:524`, `#1726`) | not from cycle 1 — a **lane inference** from "D30 puts raw outside the graph" |
| *"All 6 P0s corrected in place"* | **at the time, A4 was NOT corrected** — `MERGE-INTO-EXISTING` was still in VET's outputs, so the VET/S3 circularity stood | **cycle-1 A4's disposition was claimed, not verified** · **✅ NOW FIXED** — `MERGE-INTO-EXISTING` is removed from VET's outputs (§S2.2) and §16.1 is corrected |
| *"the narrowing is PROPOSED… ✅ Recorded as such in §9 (D4)"* | §9 D4 says **`DECIDED (narrowed)`** | **cycle-1 B2.20** — the "fix" replaced one two-ways status with another · **✅ NOW FIXED** — the ⚠️ pending line is deleted and §S3 states `DECIDED (narrowed)` |

**⇒ THE MECHANISM, STATED PLAINLY: a review finding is a CLAIM, not a fact.** Applying one is a **change to the artifact**, and it must be verified against the same primary source the finding cites — **otherwise the review becomes a propagation vector for its own errors**, and the `⚠️ CORRECTED` marker makes the wrong text *more* credible than the right text it replaced. **That is exactly what happened here: the doc's markers became evidence for the wrong reading.**

**⇒ THE RULES THIS PRODUCES, and they are binding on future cycles:**
1. **Verify a finding against its cited primary source before applying it.** A line range, a frozen doc, a `gh` output — **open it.**
2. **A `⚠️ CORRECTED` marker is not evidence.** It says the text was *changed*, not that the change was *right*.
3. **Correct the review record too.** `REVIEW-CONSOLIDATED-2026-09-23.md` B2.1 and B2.18 are **themselves wrong** and are the propagation vector; a corrected artifact with an uncorrected review leaves the next lane free to re-apply the error.
4. **"Also present elsewhere" is not "absent here".** Two of these five are that single misreading.

**⚠️ And the honest counter-note, so this does not become an argument against reviewing:** cycle 2 also found the things cycle 1 got **right** and this document now depends on — the `#4997` index drift, the four-layer placement, the four dedup keys, the `c/r` ordering rule and the sequence of the ten `!`-corrections that hold. **The failure was not review; it was unverified application.**

### 16.3 ✅ ALL SIX DECISIONS CLOSED (owner, 2026-09-24)
**These were escalated per the AGENTS.md protocol. They are now answered — recorded so no future lane re-opens them.** Full detail: `STORAGE-ARCHITECTURE.md` §14.1.

| # | ✅ ANSWER | what changes in THIS document |
|---|---|---|
| **O1** | **The conflict was mine.** `#2826` **A2** (the real decision row) recommended the journal be authoritative; `#2881` reversed it by recommendation; the doc recorded the reversal and flagged *"the row is the owner's to answer."* **The two questions are different and both hold: DURABILITY = the store's backup; REBUILDABILITY = the journal.** | **No reopen.** The wording is scoped: *the journal is the derived layer's rebuild source; it is not a durability mechanism.* ⚠️ **`rebuild_all` already treats an incomplete log as the authority — that is a defect, and it gets its own issue.** |
| **O2** | ⛔ **WITHDRAWN — the owner rejected the framing:** *"we're not optimising stupidly for a number… this is not a corporate OKR setting exercise. We need to balance multiple things at each step of the pipeline and on each architecture decision."* | **No volume target, no recall floor, no node budget anywhere in this document.** The objective is **great recall and reasoning at an affordable cost**; the method is **manual, step-by-step review** until a calibration set exists. **§4.2's objective is re-stated accordingly — see below.** |
| **O3** | ✅ **ADOPTED — emit the DECLARED outcome word (`DISCARD`), matching the engine's existing classifier.** | S2.2's outputs use the declared vocabulary. |
| **O4** | ✅ **Merge IS authorised — with Jev as arbiter over the claim plus narrative/raw data.** ⛔ **This supersedes the `OVERRIDES:` ruling on `#4899`** (*"never merging two claims into one"*). | ⚠️ **`FUSE` becomes an ADMISSIBLE action**, so §4.2's *"NEVER `FUSE`"* rule is replaced: **`KEEP` / `NOOP` / `DISCARD` / `MERGE`**, where MERGE is bounded (below). |
| **O5** | ✅ **Neither — draft and iterate:** *"we draft something (a prompt, a step of the extraction workflow, etc) and run it and see the result then refine and run again, until good."* | **The working method for every step in §4.1: draft → run → look → refine → repeat.** Replaces the sample-vs-deterministic debate. |
| **O6** | ✅ **A document is a SOURCE** — *"making them sources so our entity layer can be extracted from them… that's the reasoning/knowledge layer."* | **S0 reads a SOURCE, not a `Document` node.** Documents, code files, meeting transcripts and conversations are **the same kind of input**; the entity layer is uniform over them. **Document liveness is a READ of its entities, never a stored field.** See `STORAGE-ARCHITECTURE.md` §9.3. |

### 16.4 ⭐ O4 applied — what "merge near-duplicates" means here, bounded by the evidence
**Owner ruling:** *"yes we can merge near duplicate claims. maybe we can have jev with the claim and the narrative/raw data/both arbiter that."*

**Design, as bounded by the research (research §16.3 — `STORAGE-ARCHITECTURE.md`):**
- **The arbiter is Jev**, and the input is **both claims plus the surrounding context** — the narrative, the raw span, or both. *(Which of the three is exactly the thing to settle by the draft-and-run loop, O5. Do not decide it by argument.)*
- **The bar is HIGH, and this is evidence-driven, not caution:** multiple independent sources find over-aggressive consolidation *"destroys specific details needed for factual QA"*, and **one production system deliberately sets a high threshold because a false merge costs MORE than keeping near-duplicates.** Practical thresholds cluster at **~0.95**.
- ⛔ **A merge must NEVER happen across a difference in: a number or quantity, a named entity, a language, a negation, a condition, or a load-bearing connective whose role changes across the pair.** (This is Hindsight's own anti-collapse rule, and it is the concrete form of *"never when something distinguishing is lost"*.) The last class is read from the PAIR, not the token: a connective is frame (syntax) or an operator (meaning) by the same spelling, so the boundary compares the two sides' semantic-slot occupancy and refuses a slot each side fills with a member the other lacks — `and`/`or`, `then`/`else`, `than`/`as`, `to`/`from`, `but`/`so`. It is reported as a `substituted_content` difference — or as another identity dimension when the pair differs in that too, since `_boundary` reports the first of `sorted(identity)` (`nor` is also a negator, so `negation`; a co-differing condition reports `condition`) — (`#5139`, residuals `#5325`). The authoritative predicate is `_boundary`: its value branch covers a number/quantity or a date, and its identity branch reports one of `_IDENTITY_DIMENSIONS` (`negation`, `condition`, `scope`, `language`, `substituted_content` — `unreadable` is the fail-closed sentinel, not a class). The connective class is enumerated by `_CONNECTIVE_SLOTS` / `_CONNECTIVE_MEMBERS`. This list is a reading aid, not the enumeration.
- **⇒ The admissible outcomes are `KEEP` / `NOOP` / `DISCARD` / `MERGE` — where MERGE preserves BOTH sides' evidence and attachments.** 

**⚠️ And the honest caveat:** the evidence says merging can LOSE information. **The owner has authorised it; the bar and the never-across-differences list are how it is made safe.** If the draft-and-run loop shows merges losing detail, *that* is the signal to raise the bar — not a number to hit.
