---
title: "Epic #7949 — Research Brief (extractor v4 capstone: the object layer and the packs' business logic)"
type: engineering
domain: platform
doc_status: live
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: extractor v4, expansion packs, object layer, capstone evidence
issue: daniel-ospina/tortoise#7949
stage: epic-research
---

# Epic Research Brief — #7949 extractor v4 CAPSTONE

**Axes researched** (from `00-align.md`'s assumptions register): Strategy (is "map of the business" a real
category, and what do comparables do), Workflow Patterns (how extraction pipelines resolve mentions,
connect batches, and materialise hierarchies), Tech Stack (declarative ontology/manifest as an extraction
constraint; part-whole modelling), Assumptions Register (A-a…A-f, validated or not).

**Search + queries (Step 0.0 PREFLIGHT):** `web_search model="sonar"` (rung 2) — **rung 1 unavailable**:
`mcp_load seo-intelligence` → `Unknown MCP server 'seo-intelligence'` (the server is absent from this
machine's `.mcp.json`; the research skill's own measurement of 2026-09-24 predicted this and says to re-test,
which I did) — queries: **5 distinct queries, all 5 returned** (8 HTTP calls in total: 5 first attempts + **q2 and q5 each
returned HTTP 429 `request_rate_limit_exceeded` on the 2-wide batch and were retried ALONE after backoff,
both succeeding** + 1 re-run of a query whose first attempt returned `bytes=0` through my own quoting slip (an unquoted
filename — see the `failed:` slot below); **no query was lost**, and no 429 is counted as a miss). `exa` was not used and is not a rung.

**Graph (Step 0.0b PREFLIGHT):** `≥40 hit(s) over 5 queries that ran` (`extractor OR pack OR hasPart OR
capstone` · `object to object edge hierarchy decomposesInto hierarchy pack business logic` · `entity
resolution attach instead of mint definite description` · `operator edges planted corpus 0/4 measurement` ·
`capstone E1 volume unchanged refuted`; `/v1/search` pages at 10 and reported `truncated: true`, so each
count is a **page**, written `≥`) — **built on it**: the graph already held the decisive prior
(`1a1a9915562-9062dfd3576d`: *"A non-referential noun phrase is a MENTION, not an entity: the extractor must
decline to mint it and record it on the cl[aim]"*) and the capstone bundle's own history (cite drift after
the +1101-line `#10513` re-point; `blocker-states.json` as a V12 anti-staleness measure; `#9736`'s
re-registration) — **wrote: not written**, two distinct observed failures: (a)
`tortoise_create_operator` rejects `label=` (`Unexpected keyword argument`) so the `evidence` verb cannot
create its IMPL edge; (b) the point create itself returns **`-32006 Team points limit reached (25000)`** —
the graph is at quota, so no write can land until it is raised or evicted (`#10976`). **failed: none** — every query ran. (Transient, recorded for honesty: `operator edges planted corpus 0/4
measurement` returned `HTTP 200 bytes=0` on its first attempt because my own unquoted filename split the
argument; it was re-run cleanly and is one of the five counted above. A quoting slip of mine is not a query
that failed to run.)

**Adoption gate (Step 5.6):** `owner question` for the cross-batch mechanism (§13.8 Q1) — the field
converges on a **tiered** pipeline, but D6 (`EXTRACTOR-V4-ARCHITECTURE.md:1400`) records "both sets
model-proposed" as the decision that stands, and a converging standard cannot override a recorded decision;
the finding is therefore **evidence for the reopen**, which is what §Cross-Phase Contradictions states.

---

## Reframed problem (Step 0)

**"[The epistemic team] is trying to [make the ontology read as a map of the business] but [the extraction
pipeline has no consumer for the pack's declared part-whole relations, and mints an entity for every
grammatical subject] which results in [5,314 objects with 23,814 claims attached and ZERO edges between
them — a catalogue, not a map]."**

Two reframings that changed the research, not just the wording:

1. **Not "how do we add a feature" but "what does the field do when a schema is declared and the extractor
   ignores it".** The literature's answer is not a new algorithm — it is that *relation prediction, not
   entity extraction, is the graph-level bottleneck* [3]. That reframes A1 from "wire a missing consumer" to
   "the class of failure is well known and has known remedies".
2. **Not "should we connect objects" but "at which tier"**: the field's convergent answer to cross-batch
   linking is a **tiered** pipeline, not a binary LLM/mechanical choice — which reframes §13.8 Q1 (see
   §Cross-Phase Contradictions).

---

### Strategy Context

**[⚠️ single-source] Is "map of the business" a category, or our invention?** It is a category, and the one
comparable examined converges on the same shape: declared classes + properties, then hierarchy, then query
[4]. ⚠️ Single source — treat "the comparables converge" as a hypothesis, not a survey. Palantir-style "ontology as
the operating model" and the enterprise-KG literature both separate **class/property extraction** from
**entailment-based hierarchy construction** [4] — i.e. hierarchy is a *second* step with its own mechanism,
which is precisely the seam `#7949` C3 measures.

**[HIGH]** The failure our baseline shows is the literature's named failure: extracted triples *"often
remain disconnected local structures"*, and **entity normalization, decomposition and abstraction** are what
create cross-triple connectivity [2]. ⇒ A3's 64 %-unlinked measure is not a Tortoise quirk.

**[HIGH]** Ontology-constrained extraction **works when the constraint is in the prompt and the violations
are fed back**: schema-conditioned prompting with iterative refinement improves schema compliance and
extraction quality [1]; hallucinated/unsupported triples and **omissions of explicitly stated relations** are
the recurring failure modes [5]. ⇒ A1's remedy is a *declared-consumer in the extraction loop*, not a
post-hoc backfill.

**[⚠️ emerging]** The pack-as-manifest design is directionally standard (declarative vocabulary + constraints),
but no comparable was found that ships **exactly** this shape (per-pack `relations[].semantics` +
`hierarchies` with a write-path verdict). Treat "our manifest is unusually complete" as unvalidated.

**Profit impact (for `00-align.md` §Profit, restated):** nothing found supports a thousands-class price
point; the recorded tiers are $0/$9/$25/$149. The magnitude stays a **hypothesis**.

---

### UX Pattern Research

⚠️ **Scope note:** no comparable product's rollup UI was examined, so this section contains **no UX precedent**
— the axis `epic-research` defines (*"how similar features work in comparable products"*) is **unresearched**,
and is recorded as such rather than dressed up.

**[⚠️ hypothesis] The rollup is the user-visible artefact** — "which work items serve this goal, and on what
evidence" is the query shape the category is *sold on*. ⚠️ **Unsourced**: it follows from [4]'s
hierarchy-then-query shape and from A1 itself, not from any product page or interview. It is nonetheless the
A1 clause `#11001` does **not** cover, and the one a hand-off is most likely to drop.

A rollup over a *transitive* part-whole edge silently mixes immediate and inherited parts.
**[HIGH] (three sources)** The ontology literature is explicit: model `is_part_of` as transitive with inverse
`has_part`, **but keep a separate non-transitive direct-part relation** to avoid confusing immediate with
inherited parts [1]; classical parthood is transitive, but **transitivity can fail for context-dependent part
relations** [2]; and applying `part-of` where `proper-part-of` is meant yields trivial or misleading
inferences [4]. **[1] also records that OWL cannot express antisymmetry, so accidental cycles may go
undetected** — stated as [1]'s own note. ⚠️ *(An intermediate revision of this brief de-attributed that
clause to an unnamed "scale-and-granularity" paper; that was an over-correction of a reviewer's inference and
is reverted, because [1]'s snippet records the clause itself.)*

⚠️ **This is a direct risk to C3's proof, not a background fact.** `docs/ONTOLOGY.md` §3.7 defines `hasPart`
as Object→Object, N-ary, `dcterms:hasPart`, with **inverse traversal covering "part of" — no separate
`partOf` edge**. So a rollup that walks `hasPart` transitively will answer with inherited parts, and a cycle
would inflate the answer with no error. **C3 must say which reading its rollup uses** (direct-only or
transitive-with-cycle-guard) and score against ground truth labelled the same way.

---

### Workflow Pattern Research

**[HIGH] A2 (attach instead of mint) is the convergent practice, and our policy is its inverse.** ⚠️ **Inference**, not a claim of any one source: the three-bucket record below is *our* rendering of the field's practice, and no source proposes a `HOLD` bucket by name. Entity
resolution is a pipeline — normalization, **blocking**, candidate comparison, classification, clustering,
canonicalisation — and the recommended default is to treat **"new entity" as an alternative**, creating a
node only when **no existing entity is a sufficiently strong match** [2, §blocking]. Incremental/streaming
ER compares against existing graph entities continuously [1, 4, 6]. Against that: live-v4 mints an Object
for the grammatical subject of each statement, and **62.3 % of Objects are single-point `the <X>` definite
descriptions** — and the graph already records the correct policy as a point
(`1a1a9915562-9062dfd3576d`: a non-referential noun phrase is a MENTION, not an entity).

**[HIGH] Blocks that are too large DECREASE F1 even as cost falls** [2-of-q4], and blocking keys are what
make the O(N²) candidate space tractable [3, 4].

**⚠️ On the *direction* of the asymmetry — directional reasoning, NOT a finding.** The sub-claim that the
asymmetry "favours attaching" is **not** established by either source above (both are about block size, and
the first points the other way). What the field does establish is the **reversibility argument**, which runs
the other direction from our habit: a **false merge silently unifies two distinct entities and is far harder
to unpick** than a bad mint, because the merge has already propagated through every edge that referenced
either side. That is why the practice is *attach with a strong-match bar*, not *attach readily* [2-q2].
**Minting-when-genuinely-new is correct** and must stay available — which is exactly what the three-bucket
record encodes: `attached` / `minted` / **`held`**, where `held` is the honest answer when the match is
neither strong nor clearly new.

⇒ **C4's three-bucket record follows from the reversibility argument** (our inference, not a source's requirement):
without `held`, the only two outcomes are a wrong attach (hard to unpick) and an unnecessary mint (easy to
merge away later). This is load-bearing — C4 is a declared **mechanism exception**, so the default is being
built, not just measured.

**[HIGH] A routed hybrid is the convergence; [⚠️ single-source] the THREE-tier *structure* is not.** What two
independent sources establish: **string/blocking → ANN candidates → LLM adjudication reserved for ambiguous
or borderline candidates**, with caching and batching [4-q4 — a practitioner note, see the authority caveat
below]; and a production hybrid that routes **8.3 % of triples** through an LLM, moving relation-extraction
F1 **86.2 % → 88.4 %** at **+$0.008/triple** [5-q4]. What only [4-q4] says: that the tiers are *exactly
three* — so **the count is ⚠️ single-source**, while "mechanical candidates then a narrow LLM adjudication" is
HIGH. ANN alone has known recall holes (hubness in high-dimensional embeddings) [3-q4].
⚠️ **[4-q4]'s authority caveat:** it is an unrefereed GitHub knowledge-base note
(`github.com/claude-dev-suite/knowledge_base/…/entity-resolution/algorithms.md`) — identifiable, not
authoritative. It is cited for the *shape* of the pipeline only; the numbers come from [5-q4], which is a
peer-reviewed venue (Wiley 10.1002/aaai.70062).

⚠️ **This finding is unadoptable as-is** — see the contradiction test below.

---

### Tech Stack Research

**[HIGH] Declared-ontology-first prompting + violation feedback** is the strongest documented pattern for
making a pack load-bearing: schema-conditioned extraction with **iterative refinement converting ontology
violations into feedback** [1]. ⇒ C3's proof should be run on a corpus that *exercises the pack's declared
hierarchy*, not a generic one (already the plan's requirement, now evidenced).

**[HIGH] Validation must be structural, not narrative.** The extracted-graph literature repeatedly finds
that strict schemas (Pydantic/RDF validation) are what keep output well-formed [4] and that
Turtle/RDF validation-plus-repair materially improves grounding [6]. ⇒ a `config/v4-mechanisms.yml` row with
`code:`/`symbols:` (the producer's) plus a `pins:` absence test (ours) is the right instrument shape.

**[⚠️ emerging]** Nothing found on **per-graph pack gating** (which pack applies to which graph) — our
`#2714`/`#2728` interim caveat (the catalog union leaks when no `:PackInstall` resolves) has no comparable to
check against. Unvalidated, and out of this epic's scope.

---

### Assumptions Register

| Assumption | Confidence | Source | Validation Plan |
|---|---|---|---|
| **A-a** `live-v4` baseline is accurate (0 object→object edges; 62.3 % `the <X>`; 26 Subjects) | **high** | owner-measured 2026-11-04, per-kind breakdowns | C1's extended census re-derives it |
| **A-b** packs declare part-whole object relations; schema + writers admit them | **high** | tree (`dev:142,148,193`; `edges.py:621-630`; ONTOLOGY §3.7) **and** the literature's declarative-ontology pattern [1], [4] | C3 |
| **A-c** the gap is the declaration→consumer seam, not a missing writer | **high** | zero `create_edge` in `extractor_v2.py`; `hierarchies` validated-only | C3 |
| **A-d** a corpus through the fleet's **transport** exists or is capturable | **medium** | registered corpus is a fixture; the committed arm drives the SDK, not `POST /v1/sessions`; **E1b's `m2` control has no committed artifact** | C1 + C2 |
| **A-e** `TAGGED: 0` is a gap | **rejected** | no extraction path writes `tags` | none — recorded so it never becomes a target |
| **A-f** E1–E5 needs a new instrument | **rejected** | `#9075` + `capstone_measure.py` + verdict gate, committed | C1 extends it |
| **A-g (NEW)** the rollup can be scored without a direct-part/transitive distinction | **rejected — new risk found** | ontology literature: transitive `has_part` + separate **direct** part is the recommended model; OWL cannot detect cycles [1], [4] | C3 must declare its reading and label ground truth the same way |

---

## Open decisions this research did not resolve

**1 · The TRANSPORT (assumption A-d).** §5's job (c): *"reconcile 'the registered corpus is a fixture' with
'the acceptance must run through the fleet's door'."* **Unresolved, and no external research applies** — it is
an internal choice with an evidentiary consequence, not a question about the field:

| option | what it proves | what it does not |
|---|---|---|
| the committed arm (SDK `capture_session` over `tests/eval/write_path`) | the **pipeline** behaves this way on a known corpus, reproducibly | nothing about the **hosted door**: `POST /v1/sessions`, its quota/auth gates, harness resolution, or the fleet's actual session shapes |
| a real fleet capture through `POST /v1/sessions` | the claim the epic actually makes — *the graph the fleet builds* | reproducibility (the corpus is whatever the fleet happened to do), so the before/after must be measured on the same captures |

**Recommendation carried into scope:** both, with **one named as the source of the A-row numbers**. The
fixture arm is the regression harness; the fleet capture is the claim. Scope must choose, because C1 registers
whichever it picks and C2 proves the door.

**2 · D3 / A4 — the contradiction the epic's own GREEN bar creates.** (`00-align` §4 records the REOPEN.) D3
(`docs/ONTOLOGY.md:975-984`, *"extraction emits `statement` only"*, option B 2026-08-12, `#1022` MERGED) versus
A4 (*"0.6 % typed claims is the ceiling the capstone must beat"*). What the research says that bears on it:

**[⚠️ single-source]** The category's shape is *declared **classes** + properties, then hierarchy, then query* [4],
and schema-conditioned extraction is what makes the declaration load-bearing [1]. A *statement-only* point
vocabulary is compatible with that (a `statement` Point carrying an object link is still typed at the object
layer) — **so the research neither confirms nor refutes D3**; it only says the object layer does not need the
point layer to be typed. ⇒ **D3 stays exactly where `00-align` put it: routed as a REOPEN, not decided, and
not closed by anything found here.** A4's target remains unset.

> ⚠️ **SUPERSEDED ON THE LAST CLAUSE ONLY.** "A4's target remains unset" was true at the research stage and is
> no longer: the owner ruling of 2026-11-09 set it — per-layer label coverage, bar = appears + labelled, the
> percentage reported and not chased. **Everything else above stands**, including that this brief neither
> confirmed nor refuted D3: the ruling moved A4 *off* D3 rather than settling D3. See `02-scope.md` *In Scope* item 5 for the bar, and
> `04-plan.md` §4.4 (`04-plan.md:446`) for its `#4432` locator.

---

## Cross-Phase Contradictions (research vs recorded decisions)

**⚠️ The tiered-linking finding contradicts D6 unless it is routed — and it is routed.**
`EXTRACTOR-V4-ARCHITECTURE.md:1400` records D6: *"Two distinct connection sets — within-batch and to-graph,
both **proposed by the model**"*, and `:1095` says *"D6 as ruled — the model proposes both sets"* (`:977` is the
`#5087` seam STATUS block, not D6). The field's convergent answer (tiered:
mechanical candidates + LLM only for borderline) is **not** D6. Per the **contradiction test** and the
Adoption Gate, a convergent standard that contradicts a recorded decision is **not adoptable at all** — the
route is a reopen with the evidence in front of the owner. ⇒ this finding is **input to the §13.8 Q1 ask**
(already routed in `00-align.md` §4), never a change C5 may make. C5 measures conformance to D6 and reports
which tier actually produced each edge.

**✅ No contradiction found** for: `#5199` option A (Object mention links unanchored by design — the
literature's "preserve provenance between graph relationships and source evidence" [4-of-q3] is about
*relations*, and our derived-node anchoring already covers `Point`/`Event`/`Document`); `#5213` option 3
(document the unanchored `create_edge` hatch); `#2817` (no separator/locale rule — untouched);
§16.3 **O2** (no volume target — nothing here proposes a node budget; A2's attach rate is a *distribution*
measure, not a volume floor).

---

## Source Confidence Summary

| Claim | Tier | Sources |
|---|---|---|
| relation prediction (not entity extraction) is the graph-level bottleneck | HIGH | [3-q1] + [2-q3] (two independent framings) |
| extracted triples remain disconnected without normalization/abstraction | HIGH | [2-q3] |
| ontology-constrained prompting + violation feedback improves compliance | HIGH | [1-q1] + [4-q1] |
| minting should be the *alternative*, not the default | HIGH | [2-q2] + the graph's own point `1a1a9915562-9062dfd3576d` |
| over-large blocks decrease F1; blocking is required for O(N²) | HIGH | [2-q4] + [3-q4] |
| cross-batch linking: mechanical candidates then a **narrow** LLM adjudication (8.3 % of triples, F1 86.2→88.4, +$0.008/triple) | HIGH | [5-q4] (numbers) + [4-q4] (shape) |
| the pipeline has exactly **THREE** tiers | ⚠️ single-source | [4-q4] only |
| transitive `has_part` must be paired with a **direct** part relation; OWL cannot detect cycles | HIGH | [1-q5] + [4-q5] + [2-q5] |
| "map of the business" is a category and comparables converge on declared-classes+hierarchy+query | ⚠️ single-source | [4-q1] only |
| the rollup is the query shape the category is sold on | ⚠️ hypothesis | none — inferred from [4-q1] + A1; **no product examined** |
| the precision/recall asymmetry "favours attaching" | ⚠️ hypothesis — **directional reasoning, not a finding** | neither cited source establishes the direction; the *reversibility* argument is the sourced part |
| profit magnitude is a thousands-class contract | ⚠️ hypothesis | none — `product/pricing.json` records $0/$9/$25/$149 |
| pack-manifest design as shipped has no direct comparable | ⚠️ single-source / speculative | none found — **unvalidated** |
| per-graph pack gating has no comparable | ⚠️ speculative | none found — **unvalidated**, out of scope |

---

## Recommendation

1. **A1's remedy is a declared consumer in the extraction loop** (pack declaration → prompt constraint →
   violation feedback → structural write), not a post-hoc backfill — and the **rollup** is the acceptance
   artefact, scored with an explicit direct-vs-transitive reading (A-g).
2. **A2's "attach instead of mint" is the field's practice and our inverse** — the *sourced* part of that
   sentence is the reversibility argument (a false merge is far harder to unpick than a bad mint); the
   *direction* ("the asymmetry favours attaching") is ⚠️ hypothesis, not a finding. The three-bucket record
   (attached / minted / **held**) **follows from that argument as our inference** — no source proposes a
   held bucket by name.
3. **A3 must report the tier that produced each edge**, because the field's answer is tiered and D6 records a
   different answer; the measurement is what makes the owner's §13.8 Q1 answer decidable.
4. **Do not adopt the tiered standard over D6.** It is the evidence for the reopen, filed as such.

## Required Evidence (for the unvalidated claims)

- A comparable that ships per-pack declared relations + a write-path verdict, to test "our manifest is
  unusually complete".
- A rollup ground truth labelled with direct-vs-inherited parts, to score C3 without conflating them.
- **For the profit magnitude:** the artefact that would move it off hypothesis is a beta customer stating that
  the *map* capability (not the claim catalogue) is what a priced contract is for — e.g. a pricing or
  beta-feedback record naming the tier. None exists today; `product/pricing.json` contradicts the magnitude.

---

## Raw Notes

Append-only ledger. Entries are `[date] [axis] finding — source`.

- `[2026-11-05] [preflight]` rung 1 `mcp_load seo-intelligence` → `Unknown MCP server 'seo-intelligence'` — the
  server is absent from this machine's `.mcp.json`; rung 2 (`web_search model="sonar"`) taken and named. The
  research skill's 2026-09-24 measurement of the same absence is **still true on 2026-11-05**.
- `[2026-11-05] [graph]` `1a1a9915562-9062dfd3576d` — *"A non-referential noun phrase is a MENTION, not an
  entity: the extractor must decline to mint it and record it on the cl[aim]"*. **Built on it**: this is the
  graph's own recorded policy for A2, and the literature independently reaches it.
- `[2026-11-05] [graph]` the capstone bundle's cite-drift history (`pt_0b69ff42…`, `pt_555f8325…`) — the
  `#10513`/`#10360` +1101-line re-point made every `extractor_v2.py` cite stale by +11; the bundle has a
  citation-rot guard. Context for C1: **do not hand-transcribe line numbers into A-rows**.
- `[2026-11-05] [graph]` `#9736`'s E3 re-registration and `blocker-states.json`'s V12 rule (a `blocked_by`
  pointer at CLOSED work is stale by construction) — the reason C1 must refresh it in the same diff.
- `[2026-11-05] [write-back BLOCKED]` `tortoise-graph.sh evidence … --to <id>` → `tortoise_create_operator`
  rejects `label=`; the point create returns `-32006 Team points limit reached (25000)`. **The graph is at
  quota**, so the mandated write-back cannot land. This is an operational finding, not a skipped step: it
  blocks every lane's write-back, and eviction/limits is `#10976`'s territory.
- `[2026-11-05] [tooling defect]` `tortoise-graph.sh get <id>` → `tortoise_get_point` wants `id`, the client
  sends `point_id`; the `evidence` verb sends `label` to `tortoise_create_operator`, which rejects it. **The
  farmed client is out of sync with the tool schema** — `get` cannot answer its own question.
- `[2026-11-05] [workflow]` `[2-q2]` **paraphrase, not a quotation** (the earlier version presented this in
  quotes; corrected): arXiv 1607.00992's Rule 8 states that *"the desired behavior in entity resolution
  systems is to add a new entity only when no other entity in the knowledge graph appears to match"*, and it
  discusses blocking as the way to bound the candidate search. The two phrasings are equivalent in substance
  and only the second is the paper's own.
- `[2026-11-05] [workflow]` `[5-q4]` hybrid routing: **8.3 %** of triples to an LLM, F1 **86.2 → 88.4**, cost
  **$0.008/triple**.
- `[2026-11-05] [tech-stack]` `[1-q5]` `is_part_of` transitive + inverse `has_part`, **plus a separate
  non-transitive direct-part relation**; OWL cannot express antisymmetry ⇒ accidental cycles undetected.
- `[2026-11-05] [strategy]` `[2-q3]` extracted triples *"often remain disconnected local structures"*;
  normalization/decomposition/abstraction create cross-triple connectivity.

### Sources

`[1-q1]` Springer 10.1007/978-3-032-40377-3_5 · `[2-q1]` HAL hal-05740878 · `[3-q1]` aclanthology 2026.kallm-1.21 ·
`[4-q1]` arXiv 2602.01276 · `[5-q1]` arXiv 2609.17291 · `[6-q1]` arXiv 2511.05991
`[1-q2]` kindatechnical ER · `[2-q2]` arXiv 1607.00992 · `[3-q2]` USC-ISI AAAI18 slides · `[4-q2]` arXiv 2608.25149 ·
`[5-q2]` Springer 978-3-031-79468-1_4 · `[6-q2]` PMC 7250616
`[1-q3]` Springer 10.1007/s10462-023-10465-9 · `[2-q3]` arXiv 2510.11297 · `[3-q3]` arXiv 2506.12367 · `[4-q3]` ACM 3331166 ·
`[5-q3]` Berg-Cross V107.3 · `[7-q3]` ACM 3618295
`[1-q4]` Springer 978-3-030-49461-2_23 · `[2-q4]` ar5iv 2602.05708 · `[3-q4]` dbs.uni-leipzig.de KG-DI 2023 ·
`[4-q4]` ⚠️ **identifiable but unrefereed** — `github.com/claude-dev-suite/knowledge_base/…/entity-resolution/algorithms.md`, a practitioner knowledge-base note. Cited for the pipeline's **shape** only; the numbers come from `[5-q4]` · `[5-q4]` Wiley 10.1002/aaai.70062 · `[6-q4]` FC23/25 preproc 74 (fc25.ifca.ai/preproceedings/74.pdf)
`[1-q5]` cs.man.ac.uk simple-part-whole v0.2 · `[2-q5]` semanticscholar mereological/meronymic · `[3-q5]` PMC 1885809 ·
`[4-q5]` libretexts ontology engineering · `[5-q5]` scispace part-whole taxonomy · `[9-q5]` SEP Mereology

> **[updated 2026-11-05 — appended, review cycles 2–3]**: re-sourced the tiered-linking claim (`[4-q4]` is
> identifiable but unrefereed — a practitioner note, cited for shape only; its numbers now come from
> `[5-q4]`; "exactly three tiers" re-tagged ⚠️ single-source); **re-attributed** the OWL/antisymmetry clause to
> `[1-q5]` after an over-correction in cycle 2; declared the **UX axis unresearched** rather than implying
> precedent; added the two chartered axes (**transport**, **D3/A4**); restated the `HOLD` bucket as **our
> inference** (no source proposes it by name) in the body and the Recommendation. ⚠️ *A process claim about a
> location re-stales, so precisely: the Source Confidence Summary states this correction by **omission** — it
> has no `HOLD`/bucket row, and none of its rows claims the bucket is sourced.*
> The five canonical sections were edited across these passes — this entry is their trace.
