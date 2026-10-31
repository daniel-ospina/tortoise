---
title: "`how-to-use-tortoise` — the write-first rewrite (Step 2 draft)"
type: reference
domain: epistemic
doc_status: live
created: 2026-10-26
issue: 9675
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise, how-to-use-tortoise
---

# The write-first `how-to-use-tortoise` — Step 2 draft (#9675)

> **What this file is.** The authored text of the rewritten skill, produced from
> the measured failure list in [`how-to-use-tortoise-step1-eval.md`](how-to-use-tortoise-step1-eval.md)
> (`#9675` Step 1, landed as `#9695`). **The body is the `## Contents` section
> through the last line — 523 lines**, line for line: the port is a copy, not a
> second rewrite. The port adds only what `writing-skills` requires around it
> (see below).
>
> **How it is delivered.** The served artifact is a *derived* mirror: authored in
> `tortoise-skills-and-integrations`, pulled in with
> `uv run python tools/served_skills_parity.py --sync` (#4358 — hand-editing the
> mirror is refused by the parity gate, and `skills/` here is a machine-local
> symlink). So: port this body to
> `daniel-ospina/tortoise-skills-and-integrations@skills/how-to-use-tortoise/SKILL.md`,
> `--sync` here, then mirror to the fleet channel. Until that port lands the
> installed skill is unchanged: this file is the source of the change, not the
> change. The skill frontmatter the port must carry:
>
> ```yaml
> name: how-to-use-tortoise
> description: Write to the Tortoise graph — decide the layer (Subject/Object/Point/Event/Source), the kind, state vs occurrence, the edge type, truth vs relevance, supersede, and provenance. Use before ANY graph write; the failures this prevents are silent, or come back only as an advisory.
> domain: capability
> subjects.team: epistemic-team
> type: Workflow
> status: live
> tags: [tortoise, graph, epistemology, knowledge-graph, write, entity-type, pack]
> summary: "The write pipeline — the seven decisions (layer, kind, state vs occurrence, edge semantics, truth vs relevance, supersede, provenance), the pairwise layer discriminators, and the 20 inventoried traps."
> created: 2026-10-26
> updated: 2026-10-27
> allowed-tools: read write edit bash grep find
> ```
>
> ⛔ **The port is not the body alone.** `skills/writing-skills/SKILL.md`
> §"Mandatory Blocks" requires **two** blocks, *"no exceptions"*, each validated
> by a `grep -c` that must be ≥ 1: a gate warning after the frontmatter
> (`grep 'MUST be read in full'`) and a continuity directive at the bottom
> (`grep 'Continue following the workflow'`). Carry both — **in the mirror's own
> words, not the template's**: the served file reads
> `> ⛔ **This skill MUST be read in full before any Tortoise graph write.** Skipping corrupts the graph.`
> and closes with `> Continue following the workflow as mandated by this skill. Do not skip steps.`
> Rewriting a served line to match a template is a change nobody decided.
> `type: Workflow` additionally requires `allowed-tools`, in Pi tool names rather
> than Claude's. The mirror also carries an `# How to Use Tortoise` H1 above the
> body and `tags`/`summary`/`created`/`updated` — convention rather than a
> mandatory block, but carry them too: a port that drops any of this fails a
> contract the artifact it replaces already satisfies.
>
> **Why this draft names no verb.** `#9675`'s own recorded sequencing — the
> `owner-decision` line *"Sequenced after #4282 Phase 3 for any tool name"* —
> governs the rewrite, and Phase 3 has **not** landed: measured at `44f0e6b6a`,
> `tools/surface-target-check.py` still reports `81 MCP tools / 150 SDK methods`
> against the approved `27 / 40`, and 27 of the 40 approved method names still
> have no implementation. A document written against today's 81 would teach names
> about to be retired. It also would not earn its keep: **of the 13 P0 / 31 P1
> the Step 1 eval measured, not one is a call-syntax failure** — every one is a
> structural decision (layer, kind axis, occurrence-vs-belief, truth-vs-relevance,
> provenance). So this document teaches the decisions and names the operations;
> the verb for each is the one your own tool list offers. That is what makes it
> survive the rename untouched.

---

## Contents

- [The one operation with epistemic consequences](#the-one-operation-with-epistemic-consequences)
- [The seven decisions](#the-seven-decisions)
- [1 · Layer — the decision nothing downstream can rescue](#1--layer--the-decision-nothing-downstream-can-rescue)
- [2 · Kind — from the declared vocabulary, on the layer's own axis](#2--kind--from-the-declared-vocabulary-on-the-layers-own-axis)
- [3 · State vs occurrence](#3--state-vs-occurrence)
- [4 · Structural vs epistemic edge](#4--structural-vs-epistemic-edge)
- [5 · Truth vs relevance](#5--truth-vs-relevance)
- [6 · Change — supersede, never edit](#6--change--supersede-never-edit)
- [7 · Provenance](#7--provenance)
- [Before the write](#before-the-write)
- [After the write](#after-the-write)
- [The checklist](#the-checklist)
- [Every measured failure, mapped to its step](#every-measured-failure-mapped-to-its-step)
- [What the reads inherit](#what-the-reads-inherit)
- [What this document does not teach](#what-this-document-does-not-teach)

## The one operation with epistemic consequences

**Writing pays for every read.** Confidence propagates over the operators you
wired. A wrong structure does not degrade gracefully — it computes something
wrong that looks authoritative, because the wrong shape is still a shape.

**Almost every failure in this document is silent.** The call returns, the node
lands, the response looks successful, and nothing tells you the structure is
wrong. The exceptions come back as an advisory a caller can ignore — they never
refuse the belief write. What *is* refused is the correction attempted through
the wrong door: the generic edge writer will not take a provenance predicate
(§7), and the mechanism slot takes a mechanism, not a meaning (§4). That
near-universal silence is the whole reason for the decisions below and for the
checklist at the end.

The layer decision cannot be recovered later. The kind classifier picks a kind
**inside a partition chosen before it runs** — entities get object+subject kinds,
events get event kinds, points get point kinds (`tortoise/kind_classifier.py`,
`TYPE_SECTIONS`). Hand it the wrong layer and it faithfully returns the wrong kind
from the wrong partition.

And the write path does not refuse. A kind that belongs to **another declared
layer** is now reported back to you (a `kind_other_layer` advisory); one declared
only on the sibling entity layer is *routed* to that layer, also with an advisory.
But a kind the vocabulary **does not declare at all** is silent — no advisory,
and on the entity doors not even a log line. And a mechanism that contradicts the
mechanism your declared meaning fixes produces a warning and then stores **the
mechanism you passed**. You are the only check in this pipeline.

## The seven decisions

| # | Decision | If wrong |
|---|---|---|
| 1 | **Layer** — Subject / Object / Point / Event / Source | Event→Object loses the timeline and the state cannot fold; Subject→Point makes the actor a claim, so "what do we know about this company" stops working; Object→Point means the same position re-mentioned becomes a second node, so nothing ever updates; Point→Object overwrites the reasoning with state; no Source makes the claim uncheckable |
| 2 | **Kind** — from the active vocabulary, on the layer's own axis | a kind nothing declares is a kind nothing routes to, finds, or classifies |
| 3 | **State vs occurrence** — Object holds *now*, Event records *when*, a fold maps one to the other | "has tranche 2 been released?" becomes unanswerable |
| 4 | **Structural vs epistemic edge** | context becomes a property instead of an edge — invisible to every traversal |
| 5 | **Truth vs relevance** — truth attacks the belief, relevance attacks the *operator* | a dampener is not used at all and the confidence numbers are wrong — not missing |
| 6 | **Change** — supersede with the old claim kept, never edit | "what did we believe then" is lost |
| 7 | **Provenance** — belief → Source, with `sourceKind` and a tier | nothing can be re-verified later |

**Answer these in order.** Each one narrows the next: the layer fixes the kind
vocabulary, the occurrence fixes the layer, and the edge type is a consequence of
what the two endpoints are.

## 1 · Layer — the decision nothing downstream can rescue

Layer is a decision about **the item**, not about the sentence that mentioned it.
The same word can be a Subject, an Object or a Point depending on what the world
is doing with it.

### Pairwise discriminators

Five layers — **Subject** (can act, or be acted upon), **Object** (has a state that
changes), **Event** (happened, when), **Point** (a belief, and why), **Source**
(what we read it from; a document is a Source, never an Object).

The failures are confusions between **adjacent** layers — an agent that saw the
right word and picked the neighbouring layer. A per-layer positive test does not
catch that. Test the pair.

| Pair | Ask | Left | Right |
|---|---|---|---|
| **Event vs Object** | did it happen at a moment, or does it hold now? | *"Acme released tranche 2 on 14 May"* → Event (`eventKind`) | *"the tranche's funded state is 2 of 3"* → Object state, folded from the events |
| **Subject vs Point** | is it an actor, or a belief about one? | *"Acme"*, *"role: CFO"* → Subject (`naturalPerson` / `organization` / `role` / `team` / `legalPerson`) | *"Acme is ISO 27001 certified"* → Point, wired to the Subject |
| **Object vs Point** | is it a state, or a claim about one? | *"ARR", "headcount", "the ship date"* → the Object's state | *"the plan will ship in Q3"* → Point, wired to the Object |
| **Point vs Event** | is it a belief, or a record that something occurred? | *"we believe the price is defensible"* → Point | *"we decided on 3 June to hold the price"* → Event (`eventKind: decision`), `aboutObject` → the state it resolved |

**A decision is an Event, not a first-class Point.** The decision happened on a
date and resolved state; that is the episodic layer. The reasoning that shaped it
is Points, wired to the decision Event and to the states it touched. `pointKind:
decision` remains a valid *write-compat* spelling and it is not what extraction
emits — prefer the Event.

### The subtler failure: two homes

A *missing* node is easy to notice. **Two homes** is worse: the right layer holds
the item **and** a second one does. No query can then answer what the state is,
because neither node is authoritative.

> **Measured:** the "request" in a decision-request input existed as both an Event
> naming it and a Point holding it; the ship date in a product-spec input existed
> as both a plan Object field and a target Point.

**The fix is not deletion.** Keep the state on its own layer and express the
second reading as a **claim about** the node — a Point wired to it, not a second
copy of it.

### Canonical example — one paragraph, all five layers

> *"On 14 May, Acme released tranche 2 of the Series B. In the 3 June board
> meeting we decided to hold pricing, on the reasoning that the round extends our
> runway to 20 months. Our source: the 3 June minutes."*

```text
Subject: Acme (organization) · role: CFO
Object:  Series B tranche state (funded 2 of 3) · pricing stance (hold)
Event:   tranche 2 released (2026-05-14, eventKind: disbursement)  # venture pack's kind; else occurrence
         board meeting (2026-06-03, eventKind: meeting)
         decision to hold pricing (2026-06-03, eventKind: decision,
                                   aboutObject -> pricing stance)
Point:   "the round extends runway to 20 months" (statement)
         wired: Point -IMPL-> the decision Event
Source:  the 3 June minutes (sourceKind: document, documentKind: meetingNotes)
         wired: Point -provenance-> Source
```

Every later read is now answerable: what is true now (Object state), what
happened (Event timeline), why (Points), says who (Source).

## 2 · Kind — from the declared vocabulary, on the layer's own axis

A kind is an **identifier from a declared vocabulary**, never a description you
invent at the call site. `docs/ONTOLOGY.md` §5 owns the vocabulary; this section
owns the decision. Read §5 for the lists, not a second copy of them.

**The layer and the kind have different axes.** Picking the right word on the
wrong axis is a real and measured failure — it looks declared and it still routes
nothing:

| Axis | Property | Example of getting it wrong |
|---|---|---|
| Point | `pointKind` | `incident` is an Object kind; on a Point it is legal to the flat registry and wrong for every reader |
| Subject | `subjectKind` | `person` — the declared spelling is `naturalPerson`; `company` → `organization` |
| Object | `objectKind` | `offering` — declare a product/offering kind, or use `other` deliberately |
| Event | `eventKind` | `tranche` — an Object kind; the release itself is a `disbursement` (the `venture` pack's kind — declare it, or write `occurrence`) |
| Source | `sourceKind` | `research` is **not** a `sourceKind`; it is a `documentKind` |
| Source genre | `documentKind` | a genre is not a credibility tier and not a source type |

**`sourceKind` and `documentKind` are two axes, not one.** `sourceKind` says what
*type* of thing you read (conversation, document, agentSession, a connector kind);
`documentKind` says what *genre* it is (`research`, `brief`, `meetingNotes`,
`handoff`, …). Credibility is a **third** thing, `credibilityTier` (T0–T4), and it
is not spelled either of them.

**How to choose.** In order: the core vocabulary for that layer in §5 → a kind
the SDK itself registers for decision parts → a kind a loaded pack declares,
spelled exactly as the pack declares it. If none has your item, the honest answer
is the declared generic (`other`, `statement`, `occurrence`) plus a pack change —
not a new word at the call site.

**What the surface will and will not tell you.** A kind another *axis* declares
and this one does not is reported (`kind_other_layer`, naming that layer); a
sibling-entity-layer kind is routed there. A kind this layer declares — or one no
layer declares — is silent: the vocabulary is deliberately open, so nothing
comes back. That is why the measured failures are what they are: `person`,
`company`, `offering` and `research` are all *undeclared*, so none produced a
word back.

> **Measured:** the single most common failure in the eval — 14 scored items
> across 5 of 10 inputs — is a kind no vocabulary declares on that layer
> (`subjectKind: person` ×6, `company` ×2, `investor`, `authorGroup`;
> `objectKind: offering`; `sourceKind: research`; two event kinds). Every one
> was accepted without complaint.

## 3 · State vs occurrence

**Object holds *now*. Event records *when*. A fold maps one to the other.**

`Object.status` is a **write-through cache over its event stream** — a
performance copy, never the truth. The events are the truth. That is why the
question "has tranche 2 been released?" is answered from the Event, and why
writing the state as a fresh Point instead produces a graph that can no longer
answer it.

Practical consequences:

- **Changing what is true now** is an occurrence (an Event) plus, if your write
  path folds it, a state update. Never overwrite history to record a change.
- **Reading what is true now** is the Object's state, derived. Do not store the
  derivation as a second claim — that is the two-homes failure.
- **Bundled writes can leave what you wrote invisible.** The ingest path defaults
  to a *gated* promotion policy: beliefs stay `draft`, and a draft contributes
  nothing to confidence. Promotion is explicit; an explicit live status under a
  gated policy is a violation, not a shortcut. If a write "worked" and moved no
  confidence, check this first.

## 4 · Structural vs epistemic edge

There are two kinds of edge, and which one you need is decided by **what the
edge claims**, not by what you prefer:

| | Structural (plain) | Epistemic (carried by an operator) |
|---|---|---|
| Claims | how things stand — `performs`, `produces`, `ownedBy`, `memberOf`, `hasPart`, `authoredBy` | this *supports* or *contradicts* that |
| Confidence | none (a fact) | yes — EP propagates over it |
| Mitigable | no | yes — the operator is the anchor a mitigation attaches to |

**The rule: an edge carries an operator iff it needs mitigation, or it is a
support/contradict between epistemic nodes.** Everything else stays plain. This
is `docs/ONTOLOGY.md` §8's reification rule; the plain alternative is not a
shortcut, it is the correct shape for a fact.

Two consequences that are easy to get wrong:

- **A fact stored as a property is invisible.** "This decision applies to Acme"
  written as a property is unreachable from every traversal. Make it an edge.
- **A support/contradict edge between beliefs must have an operator**, the only
  shape a mitigation can attach to. A bare support edge is permanently
  undampenable, and you find out when someone needs to qualify it.

**An edge has three layers.** The **mechanism** (`IMPL` / `NAND`) and the
direction it records; the **meaning** — the closed semantics vocabulary:
`addresses`, `supports`, `refutes`, `mutuallySupportive`, `mutuallyExclusive`,
`hasPart`, `related`, …; and the **predicate** — the specific verb (`fixes`,
`bearsOn`). Declare the meaning and the direction resolves from it. Do not hand a
meaning to the mechanism slot: a meaning is not a mechanism. If your mechanism
contradicts the mechanism your declared meaning fixes, you get a warning and the
write proceeds **with the mechanism you passed** — so an accidentally inverted
edge stores the inversion.

**Write a rationale, not a label.** Annotate why an edge exists, not what shape
it has: *"the minutes contradict the revenue figure because they cover a
different entity"* — not `evidence`. A label repeats the shape; a sentence
records the reasoning, the only part a later reader cannot reconstruct.

## 5 · Truth vs relevance

When a belief is challenged you have two acts, and they fix different things.
Choosing the wrong one is the failure the whole document exists for.

| | Attack the **belief** (truth) | Dampen the **operator** (relevance) |
|---|---|---|
| Says | "this is **false**" | "this is **true**, but bears less than it looks" |
| Dimension | correctness | relevance |
| Mechanism | a contradiction edge against the belief node | a **mitigation** attached to the operator |
| Effect on confidence | the contradiction propagates | the operator's weight is damped |

**Relevance lives on the operator; truth lives on the belief.**

### The dampener, exactly

A mitigation is **not a third edge kind and not a peer in the operator menu**. It
is a node attached to the operator it weakens. Its `strength` is a graded
**dampener** of that operator's effective weight:

```text
w_eff = w × (1 − strength)        strength ∈ [0.10, 0.50]
```

0.10 reads as a minor caveat and keeps 90% of the weight; 0.30 a significant
limitation, 70%; **0.50 the strongest sanctioned** — major counter-evidence,
50%. Never below 0.10 (negligible) and never above 0.50 — above that you are
inverting the claim, which is a contradiction's job. **A dampened contradiction is
still a contradiction, only weaker.** Strength is not how true the reason is; it
is the fraction of the operator's weight this reason removes. Several mitigations
on one operator compose by **max**, never by product — dampening never stacks
past 0.50.

**Retract, do not delete, to undo.** A retracted mitigation (any terminal status)
contributes nothing, so the operator's undamped weight returns. Deleting it is
unnecessary: the two differ in what they *remove*, never in whether EP stops.

### The fan-out trap — one mitigation, many edges

**An operator can carry several target edges, and it has ONE weight.** Mitigating
it damps *every* edge it carries. Wire a single operator fanning out to five
targets, attach one reasonable 0.30 mitigation, and you have silently damped all
five relationships.

> This is the "batch-connected mitigations cascade-nuke weights" failure. The
> remedy is not a smaller number: it is **one operator per relationship**, so a
> mitigation prices exactly the relationship you meant to price — and you can
> verify it.

### Annotation — the other weight mechanism

A second, independent lever **bands** an operator's weight rather than damping it.
Four dimensions, each `0.0–1.0`:

| Dimension | Reads as | Direction |
|---|---|---|
| `bias` | how one-sided the source's stake is | **inverted** — high bias reads *worse* |
| `precision` | how sharply the claim is defined | high reads better |
| `consistency` | how stable this relevance is across contexts | high reads better |
| `directness` | how directly the evidence bears on the claim | high reads better |

```text
q      = mean(1 − bias, precision, consistency, directness)
factor = 2 ** (2q − 1)          # 0.5 (worst) … 1.0 (neutral) … 2.0 (best)
```

**Unrated, or all four at 0.5, is exactly neutral.** Rating is a deliberate act,
never a by-product of writing something else. It is **symmetric** — for every
rating that halves a weight there is one that doubles it — so it is not a place
to express disagreement (that is a contradiction's job). It is **not source
credibility**: credibility is how much we trust the claim, from its sources'
tiers; annotation is how much this *connection* contributes. One asymmetry: on a
contradiction edge the clamp absorbs part of the top of the band, and on a
contradiction whose target is itself an operator it absorbs the strengthening
half entirely — diminishing still works.

### The decision tree

```text
A challenge arrives:
├─ Is the claim FACTUALLY WRONG?            → attack the belief (contradiction). Truth.
├─ Is the claim TRUE but OVERSTATED?        → mitigate the operator, 0.10–0.50. Relevance.
├─ Is the claim TRUE but IRRELEVANT here?   → contradict or mitigate the OPERATOR. Relevance.
└─ Is an OPTION a bad fit for a CRITERION?  → contradict/mitigate the operator between them.
                                              NEVER the option or the criterion.
```

**An option is an option and a criterion is a criterion — both are true by
definition.** "This option is a bad fit" is never a contradiction of the option;
it is a statement about the relationship. Contradicting the option itself
destroys the thing you were evaluating.

**Before adding a contradiction, look for the support edge that is already
there.** A contradiction with no support edge beside it is an orphan: it lowers a
number nobody raised, and the reader cannot see what it argues against.

## 6 · Change — supersede, never edit

**A belief that changed is two beliefs and a record of the change.** Editing in
place destroys the answer to "what did we believe then", which is the question
every audit eventually asks.

- **Supersede**, do not edit, when the content changes. The old node stays,
  marked superseded; the new one takes over its connections. **Transfer the
  edges as part of the supersede.** Never move edges by hand afterwards — except
  one. ⚠️ **The transfer is blanket; `docs/ONTOLOGY.md` §3.1 scopes it to
  restatement:** a rebuttal that *motivated* a correction must come off the
  successor explicitly — nothing does it for you, and removing it (a Point) destroys the refutation's own content.
- **Retract** when it stops being true with no replacement. Retraction is
  terminal: a re-assertion creates a new node, it does not revive the old one.
- **Superseding a claim does not supersede its operators.** An operator is a node
  with its own life: the transfer moves it onto the successor, so it prices the
  new claim; one left on the old node is dangling wiring the check reports. The
  ghost itself never moves — a superseded claim is excluded from the inference
  unconditionally (`retracted` / `superseded` / `archived` / `outdated` /
  `deprecated`, plus the legacy outdated flag), so an operator still pointing at
  it contributes **nothing**, and if the stripping leaves fewer than two
  participating inputs the operator's whole factor is skipped — disclosed as
  `skipped_factors` on the confidence result rather than in the graph. The edge
  does not price a ghost; it makes the operator's target set wrong and the
  reader's picture wrong — so check the operators that pointed at the claim you
  superseded, and unwire or supersede them.
- **Delete** is for a node that should never have existed — a mis-write, a
  duplicate. It destroys content, leaving a journal record of the destruction —
  not how you express a change of mind.

## 7 · Provenance

**Every belief written from something you read carries its source at write time.**

- **Stamp the source in the same write that mints the belief.** The predicate that
  carries a source on a claim is not in the structural edge allowlist — the
  generic edge writer raises on it — so an "I'll wire the source after" plan
  leaves the claim unsourced: the create returns a `no_source` advisory (unless
  you passed `credibility`) and the later edge write raises. Stamp it at the create.
- **The provenance predicates the edge writer *does* accept are a trap.** They
  land, and the one that anchors a version cannot get that anchor through this
  path — a bare edge also *pre-empts* the writer that would have stamped it, so
  the read of that edge is permanently `unknown`. If you need the anchor, use the
  provenance writer; if you came from a document, stamp on the create.
- **A Source with no belief→source edge is a scrapbook entry.** It proves you read
  something; it proves nothing about any claim.
- **Criteria and findings need provenance as much as conclusions do.** A criterion
  with no source is untethered from the need it serves, and the whole chain above
  it becomes unfalsifiable.
- **`sourceKind` is what it is, `credibilityTier` is how much it counts,
  `documentKind` is its genre.** Setting one and meaning another is a measured
  failure, not a technicality.

> **Measured:** the input that mattered most — a claim under direct contradiction
> — wrote **no Source at all** *and* dampened an operator instead of contradicting
> the belief. Two failures in one write: the second is silent, and the first
> returns only an advisory.

## Before the write

Three checks, all cheaper than the failures above:

1. **Confirm the graph you are writing to.** An unset or wrong connection target
   does not error — it opens an empty graph and every subsequent write lands on
   the wrong one. "The graph has no data on X, I'll create it" is usually a
   connection failure, not a fact about the graph. Confirm the target explicitly
   before the first write (`localhost` and `127.0.0.1` are not interchangeable in
   every client), and verify with the graph's own status read: zero totals on a
   graph you believe is populated means you are somewhere else.
2. **Never construct or guess an identifier.** Identifiers are not uniformly one
   format — canonical ULIDs, early hashes, legacy numerics and prefixed ids all
   exist in real graphs. Use the identifier the write returned and store it;
   resolve later references by content, never by re-deriving an id.
3. **Look before you create.** Read the target node's current edges first: you
   need to know whether the support edge already exists (step 5) and whether the
   belief is already there under another phrasing. A duplicate belief splits the
   evidence between two nodes, neither of which ever reaches a settled
   confidence. Search for the claim, or write with the idempotent dedup option,
   before minting a second node.

## After the write

**A write you did not verify is a claim about your own intent.** Verify the
structure, not the return value.

- **The edges exist and point where you meant.** Read the node back and check the
  endpoints. A support edge between the wrong two nodes satisfies "an edge
  exists" while saying the opposite of the item.
- **Nothing landed twice.** Look for the twin.
- **The confidence moved, in the direction, by roughly the magnitude you
  expected.** A mitigation that changed nothing is attached to something that is
  not an operator, or to a node whose state excludes it from the dampener. This
  is the most valuable check, because it is the one thing a
  structurally-correct-looking graph can still get wrong.
- **Run both integrity surfaces, and read what each covers.** They are not
  interchangeable. The **hygiene audit** reports nine classes, among them a
  superseded claim with no replacement edge (`superseded_no_edge`, high),
  evidence with no provenance (`missing_sourceKind`), a superseded claim that
  still has live `IMPL`/`NAND` edges into it (`superseded_active_edges`), and a
  keyword heuristic over `IMPL` edges that may have belonged to `NAND`
  (`impl_instead_of_nand`). The **chain-integrity check** validates the
  product-strategy chain rules only — dangling journey→use-case, workflow→JTBD
  and requirement→workflow references, orphaned use cases and drafts — and
  reports no provenance class at all. Neither finds an orphan contradiction, and
  neither finds a source edge pointing at a node that is gone: those are yours,
  at write time (§5, §7).

## The checklist

Copy this, tick it, and do not skip a line because the write "looks simple".

- [ ] I decided the **layer** of every item, as an item — not as a sentence.
- [ ] I ran the **pairwise** discriminator for each adjacent pair I was unsure of
      (Event↔Object, Subject↔Point, Object↔Point, Point↔Event).
- [ ] No item has **two homes** — a second reading is a claim *about* the node,
      not a second node.
- [ ] Every **kind** is declared, on that layer's own **axis**; nothing invented.
- [ ] `sourceKind` is not carrying a `documentKind`, and neither is carrying a
      credibility tier.
- [ ] Occurrences are **Events**; state is on the **Object**; nothing overwrites
      history to record a change.
- [ ] Structural facts are **edges**, not properties.
- [ ] Support/contradict edges between beliefs have an **operator** — so they can
      be mitigated later.
- [ ] The declared **meaning** agrees with the **mechanism** I passed.
- [ ] Every challenge is either a **contradiction of the belief** (truth) or a
      **mitigation of the operator** (relevance) — never a mitigation where a
      contradiction belongs.
- [ ] Every mitigation strength is inside **0.10–0.50**.
- [ ] Every operator I mitigated carries **one target**, so the dampening prices
      one relationship.
- [ ] I checked for an existing **support** edge before adding a contradiction.
- [ ] Nothing I contradicted is an **option or a criterion**.
- [ ] Annotation dimensions are set only **deliberately** — unrated means neutral.
- [ ] Edge rationale is a **sentence**, not a label.
- [ ] Changes are **supersessions**, not edits — and a refutation that *motivated* one is off the successor.
- [ ] Every belief carries its **Source**, stamped by the same write.
- [ ] Bundled writes are **promoted** (or I know they are drafts).
- [ ] I confirmed the **graph** before writing and did not construct any **id**.
- [ ] I verified the write: endpoints, no twins, confidence moved, integrity check
      run.

## Every measured failure, mapped to its step

| Failure | Step | Why it is silent |
|---|---|---|
| A state (ARR, headcount) written as a belief while no Object holds it | 1, 3 | only the wrong layer has it, so nothing folds it and "has tranche 2 been released?" is unanswerable |
| The right layer holds an item **and** a second node also holds it (the request, the ship date) | 1 | two homes; both answer, neither authoritatively |
| A structure (an organisation, a role) written as an Object | 1 | the Objects still traverse; only "what do we know about X" breaks |
| A decision written as a belief with no occurrence | 1 | the timeline is gone and nothing errors |
| A kind nothing declares on that layer (`subjectKind: person`) | 2 | no notice at all: the write succeeds |
| A kind from the wrong axis (`sourceKind: research`) | 2 | it reads as declared; it routes nothing |
| No contradiction on the one contradiction — the support operator was mitigated instead | 5 | the confidence is *wrong*, not missing |
| A mitigation where a contradiction belonged | 5 | the belief is now priced as merely less relevant |
| A contradiction with no support edge beside it | 5 | it lowers a number nobody raised |
| No source written at all | 7 | the claim is uncheckable; only a `no_source` advisory comes back |
| A source written with no edge to any belief | 7 | the Source exists; provenance does not |

In the order the eval measured them, not an order of importance. Steps with
**no** measured failure matter too: supersession and alias folding were done
correctly, unaided, in every input that exercised them.

## What the reads inherit

**Reading.** Answers are only as good as the structure above: a contradiction that
should have been written is a number too high; a mitigation that should not have
been is a number too low. When a read looks wrong, the first hypothesis is the
write, not the query.

**Deciding.** A decision is an Event plus the states it resolved, justified by
Points that trace through criteria to the needs they serve. Rank options by
propagated confidence over the support and contradiction edges you wired between
evidence and options. If that chain is not edges, the ranking is not a ranking.

**Auditing.** The graph can be audited for exactly what the write recorded: which
belief changed, when, on what source, against what evidence. An audit that cannot
answer "what did we believe then" is auditing a structure that was edited instead
of superseded.

**Maintaining.** Supersede, merge and retract are writes — steps 6 and 7, not a
job of their own.

## What this document does not teach

- **Configuration** — connections, credentials, keys, self-hosting. That is
  deployment, it is not a decision about structure, and it belongs in the setup
  documentation. The only configuration fact this document keeps is the one that
  corrupts a graph: confirming the target before the first write.
- **The vocabulary itself** — the kind lists, the predicate list, the tier
  definitions. `docs/ONTOLOGY.md` §2, §4, §5 and §8 own those, and this document
  points at them rather than restating them. Two ontologies that drift is a
  failure class we keep paying for.
- **The verb for each operation** — see *Why this draft names no verb* above; the decisions do not change when the names do.
