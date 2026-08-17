---
title: "Extractor Pipeline v2 — 5-Stage Narrative-First Architecture (epic #909)"
type: log
domain: capability
subjects:
  team: epistemic-team
ownedBy: epistemic-team
doc_status: draft
created: 2026-08-14
governingAgreement: "#909, #946, #1272"
---

# Extractor Pipeline v2 — 5-Stage Narrative-First Architecture

**Status:** DESIGN — for owner review before implementation.

## 1. Problem (why v2)

The current production extractor (value_extractor.py: summarize → construct_graph →
ground) has three structural defects the calibration runs surfaced:

1. **It classifies-as-it-goes without the ontology.** `compile_value_brief()` (the
   closed vocabulary from the packs) exists but is dead code — no prompt uses it.
   The model invents kinds (minted kinds: "worktree", "test suite", "approach"),
   and the "decision is a type of event" collapse happens because there's no
   vocabulary to extract into.
2. **It destroys the story arc.** The summary reduces the conversation to
   state/decisions/logic lists — the *narrative* of how things connect (who
   decided what, what caused what, what depends on what) is lost. The story arc
   IS part of the memory.
3. **It conflates truth vs weight.** CONSTRUCT_SYSTEM wires "for" → IMPL and
   mitigations as a third thing without the ontology's distinction: NAND = claim
   is FALSE (truth attack on the point); MITIGATES = claim is TRUE but matters
   LESS (relevance attack on the edge, 0.10-0.50). Relevance lives on the
   OPERATOR, truth lives on the POINT.

## 2. The new architecture — 5 stages, one model, narrative-first

One capable model (deepseek-v4-flash) runs FOUR prompts in order, same
session (continue-context — no repeated re-reading of the raw conversation).
The fifth step (embed) is EXECUTION via the how-to-use-tortoise mechanics, not
a flash prompt. The solar-pro4 pre-processing tier is a LATER optimization —
parked, tested after the flash-only design is proven.

```
raw conversation
  │
  ▼
[S1] STORY SUMMARY — read the whole conversation, produce a NARRATIVE that
     preserves the logic: what happened, in what order, why. Map the 3 layers
     (State: subjects+objects · Epistemic: points + IMPL/NAND/MITIGATES ·
     Events: decisions/meetings/occurrences) with entities embedded and
     connections shown. Uses the master entity list (compile_value_brief).
  │
  ▼
[S2] MAP TO EMBED — using how-to-use-tortoise semantics, map the summary to
     the exact graph writes: objects/subjects → entities (with lifecycle),
     events → Event nodes, points → Points (statement), connections →
     IMPL/NAND/MITIGATES operators. Output the embed list.
  │
  ▼
[S3] SEARCH THE GRAPH — using the summary, search existing memory for related
     entities/points/events (same names, topics, prior decisions). Fetch what
     exists. [NEW CAPABILITY: the extractor reads the graph.]

     Backend: the REAL graph — FalkorDB via Docker (self-hosted) or the hosted
     API (TORTOISE_DB_URI). NOT FalkorDBLite (that is test/eval-only embedded).
     The graph-read call must resolve the active backend from the environment
     and run the same queries a client would (entity by name+kind, points by
     aboutObject/aboutSubject, events by entity).
  │
  ▼
[S4] REVIEW GAPS — look at the conversation + S1 summary + S3 search results:
     did we miss any key Points/objects/subjects/events that affect the world
     model? Add them. Output the COMPLETE embed list.
  │
  ▼
[S5] EMBED — EXECUTION (not a flash prompt): using how-to-use-tortoise
     mechanics, execute the writes: entities first → events (with aboutObject)
     → points (with aboutObject) → operators (IMPL/NAND/MITIGATES) → connect
     to EXISTING graph items.
```

### Chunking (long conversations, up to 1M tokens)

A conversation can far exceed one model window. The S1 story summary is
CHUNKED: split the raw conversation into bounded segments (e.g. 40-60 EDUs),
run S1 per chunk, then COMPILE the per-chunk narratives into one coherent
story (dedup entities, stitch the arc, preserve cross-chunk connections)
before S2. Only the compiled story + the master list go forward — S2-S5 run
on the compiled narrative, never on the raw.

### Why this fixes the three problems

1. **Ontology-driven**: every stage receives the master entity list (objects +
   subjects + points + events + pack kinds + chains) — no minted kinds, and the
   chains enforce the business logic ("customers connect to JTBDs/use-cases, not
   to architecture requirements").
2. **Narrative preserved**: S1 produces the story arc with entities embedded —
   the *how-it-connects* survives to S2-S5. The granularity is "decisions saved
   for the future based on objects/subjects"; process chatter is excluded unless
   it drives a lifecycle event or a point about State.
3. **Truth vs weight correct**: S2/S4 emit operators with the distinction —
   IMPL (support/veracity), NAND (attack/veracity, on the point OR operator),
   MITIGATES (relevance, on the edge, 0.10-0.50).

## 3. The master list — what compile_value_brief must return

The current function returns only core objects + pack kinds (and lacks subjects
entirely). The v2 master list:

```
objects   (core §5: Project, WorkItem, document, user, skill, tool, agent,
           workflow, agreement, standard, strategy, plan, goal, target, other)
subjects  (core: organization, team, role, legalPerson, naturalPerson)   ← ADD
points    (statement — the extraction write kind; hypothesis folded into confidence)
events    (decision, occurrence, deployment, review, meeting, experiment, friction, ...)
pack kinds (product-strategy: product, feature, customer, customerSegment, market,
           requirement, architecture, JTBD, useCase, userJourney, valueProposition
           · dev: epic, issue, code, api, database, infrastructure, deployment
           · marketing: campaign, content, channel, audience, keyword)
chains    (productDelivery: JTBD→useCase→feature→userJourney→workflow→requirement→
           architecture · epicToCode: epic→issue→code · campaignToChannel:
           campaign→content→channel)                                     ← ADD
+ description + nearMisses per kind
+ how the 3 layers connect (State=entities+lifecycle, Epistemic=points+operators,
  Events=timeline)
```

## 4. The condensed semantic core (from how-to-use-tortoise)

S2/S5 prompts carry the condensed semantic core, not the full 597-line skill:

- **Truth vs Weight** (§46-90): NAND = claim FALSE (truth attack on the point);
  MITIGATES = claim TRUE but matters LESS (relevance attack on the edge,
  0.10-0.50). Golden rule: relevance lives on the OPERATOR, truth lives on the
  POINT. Decision tree included.
- **Edge Types + Mitigation Ranges** (§29-45): IMPL/NAND; 0.10/0.30/0.50.
- **Supersession** (§118-124): lifecycle created/changed/superseded.
- **Link-Before-Create** (§234-240) + **Pre-Write Checklist** (§140-152).

Excluded: decision-comparison workflow, search modes, EP breakdown fields,
SDK props, retry tables, sourceKind taxonomy — operational detail, not extraction
semantics.

## 5. Model selection

- **S1-S5 all run deepseek-v4-flash** (owner decision: "no opus"; flash is
  capable + cheap + the production P3 from the earlier design).
- **solar-pro4** ($0.03/M) remains the candidate for a future cheap
  pre-processing tier IF the flash-only design needs cost optimization — but the
  user's current directive is the simpler single-model design; solar is parked.
- No max_tokens caps (owner decision); temperature 0.0 for determinism (owner
  to confirm).

## 6. The parity validation (before building the production path)

The question: does this 5-stage design preserve the logic vs the current
pipeline, at comparable or lower cost? Validate on the calibration windows
(w-design-bounded) BEFORE committing to the architecture:

- Path A (current): value_extractor summarize→construct→ground
- Path B (new): S1→S2→S3→S4→S5
- Compare: decision/state/logic set containment (no loss from A→B), story-arc
  quality (owner judgment), cost (measured tokens).
- A few runs (≥3) for reliability.

## 7. Open items (owner to confirm)

1. **Temperature 0.0** on flash for S1-S5 (deterministic runs) — keep or change?
2. **S3 graph-search capability**: the extractor currently never reads the graph.
   The runner must add a graph-query call (fetch existing entities/points by
   name/topic) and inject results into the prompt. Confirm the mechanism.
3. **Condensed semantic core** (as in §4) — confirm this is the right slice of
   how-to-use-tortoise for the prompts.
4. **The chains' enforcement**: WARN (owner confirmed) — flag a chain violation
   but do not hard-block. Additionally, S2/S5 should TRY TO FIX the mapping
   when the data is not directly against the chain: e.g. a customer entity
   connected to an architecture requirement is re-mapped toward the nearest
   valid chain position (customer → JTBD → useCase → ...) with a note, rather
   than dropped or blindly kept.

## 8. Scope classification (for routing)

- Touches: tortoise/value_extractor.py (re-architecture), new stage runner,
  compile_value_brief (master-list expansion), model registry, graph-read
  capability (S3), parity validation script, tests, docs.
- This is a **Standard**-scope engineering change (one system boundary: the
  extractor; multiple files but no schema migration). Routes to
  issue-creation → issue-scoping → writing-plans.
