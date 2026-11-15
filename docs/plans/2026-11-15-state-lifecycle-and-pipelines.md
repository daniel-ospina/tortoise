---
title: "State, lifecycle and pipelines — the target model for how a thing says where it is"
type: engineering
domain: data
doc_status: draft
created: 2026-11-15
updated: 2026-11-15
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutObjects: tortoise-ontology
aboutSubjects: tortoise-state-model
governingAgreement: "#11824"
---

# State, lifecycle and pipelines

This is the **target model** for how anything in the graph that has a state says where it is. It states what we want, not what is built. It contains no migration, no changelog and no implementation plan — those are separate documents.

**Issue of record:** #11824 · **Supersedes the design of:** §4.3 Object status, §5 Point status, `goalState` (§4.3), pack `statuses`/`folds` (§9), pipeline stages.

---

## 1. The problem in one paragraph

Today "status" answers at least three unrelated questions at once. One word can mean *does this claim still count as a belief*, or *what state is this customer record in*, or *did this goal work out*. Because the word is shared, the meaning is not: the list of words lives in one place, and the rule for what "finished" means lives somewhere else — in four hardcoded lists that only contain built-in words. So anything with a word outside those lists is **treated as current by everything that reads it**, which is the same as saying it never ends. Nine declarations across our packs use thirty words; one of them is in the built-in list.

---

## 2. The four questions, and who owns each

Everything that looked like "status" is one of these. Three get a field; visibility does not.

| question | what it really asks | owned by |
|---|---|---|
| **1. Lifecycle** | Is this a current commitment, or is it over — and if over, how did it end? | the `status` word — one closed set, every kind except events (§8) |
| **2. Position** | Where is this in its process? | a **pipeline**: stages declared per kind |
| **3. Achievement** | Did the goal actually work out? | its own field (`goalState`), already separate and working |
| **4. Visibility** | Is this tucked out of the default view? | a **flag**, not a word — and out of scope until a surface needs it |

Questions 1 and 2 are the two axes. They are separate because **they change for different reasons**: lifecycle changes when a thing is promoted, replaced or retired — rarely, and by decision. Position changes as work moves — often, and sometimes automatically from events. Mixing them means a routine progress update can silently retire a commitment.

Questions 3 and 4 are **not** lifecycle. Achievement is already its own axis (a goal can be `live` *and* `unmet` — both true at once, which is the proof they are different questions). Visibility is a property of a view, not of the thing.

---

## 3. The lifecycle word

Every kind **except events** — claims, entities and sources — uses **one** closed set of four words. Events are the one exclusion, and §8 says why: an event is a dated fact, not a commitment, so it has no lifecycle to be in.

| word | means | can move to |
|---|---|---|
| `draft` | created, not yet a commitment. Present in the graph; not counted as in force. | `live` |
| `live` | current: in force and counted. | `deprecated`, `superseded` |
| `deprecated` | ended, and **nothing replaced it**. Final. | — |
| `superseded` | ended because something **replaced** it. Final. The replacement is named. | — |

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> live
    live --> deprecated : ended, no successor
    live --> superseded : replaced by a named successor
    deprecated --> [*]
    superseded --> [*]
```

**The one rule that carries the meaning:** `superseded` names its successor. If nothing replaced it, the word is `deprecated`. That is the only difference between the two, and it is the rule every comparable system uses.

### 3.1 Verbs are not words

A *verb* is what someone does; a *word* is what the record then says. Several verbs land on the same word, and that is correct — it keeps the vocabulary small without losing distinctions in what happened.

| verb | resulting word |
|---|---|
| promote | `live` |
| retire, withdraw, archive, retract, reject, invalidate | `deprecated` |
| replace, supersede, correct | `superseded` (successor named) |

This is why the current five extra words are not needed: each of them is a verb's name leaking into the state vocabulary. Keeping verbs separate from states means a new verb never grows the vocabulary.

### 3.2 What is deliberately not a lifecycle word

| not a word | what it actually is | where it lives |
|---|---|---|
| "archived" | a visibility choice about what you look at | a flag, if and when a surface needs it. Retroactively filing a rejected thing must not erase *why* it ended |
| "in-progress", "completed" | position in a process | a pipeline stage |
| "outdated" | the past name of the Point "no longer counts" mechanism | the `deprecated`/`superseded` words above |
| "blocked", "paused", "waiting" | an interruption, not an ending | a pipeline stage |
| "met", "unmet", "abandoned" | how the goal worked out | `goalState` |

---

## 4. The pipeline

A pipeline says where a thing is in a process. It is **not** a lifecycle: a stage says *how far along*, never *whether the commitment still stands*.

A pipeline is a short ordered list of **stages**. Each stage declares:

| field | means |
|---|---|
| `id` | a stable name for the stage |
| `label` | what a person sees |
| `order` | its place in the sequence |
| `slot` | which of four roles it plays — see below |
| `outcome` | for a terminal stage only: how it ended |

The **slot** is the field that carries the meaning, and it is the piece today's model is missing:

| slot | means | examples of the role |
|---|---|---|
| `entry` | not started yet | a backlog item before work begins |
| `active` | in progress | being worked on |
| `suspended` | paused but not ended — can resume | blocked, on hold, awaiting input |
| `terminal` | **ended**; the process is over | done, failed, cancelled, written off |

```mermaid
flowchart LR
    E[entry<br/>not started] --> A[active<br/>in progress]
    A --> S[suspended<br/>paused, resumable]
    S --> A
    A --> T[terminal<br/>done / failed / cancelled]
```

**A stage in a `terminal` slot is what makes a pipeline's ending real.** Without it, a list of stage names is decoration: nothing can tell that `done` means over.

### 4.1 Why the slot, and not "just mark done"

Because a stage's *name* is the most customisable thing in the system — a pack can call it `released`, `written-off`, `signed`, `shipped`. A **role** does not change when the name does. So the meaning lives in the slot and the name lives in the label, and a pack can rename everything without breaking a single reader.

### 4.2 Who declares a pipeline

A pack declares pipelines for its own kinds. A graph with no pack still works: core kinds have no pipeline, and their state is the lifecycle word alone.

This is what makes the model customisable without becoming a configuration language: **the shape is fixed (four slots, ordered stages, one outcome on a terminal stage); the contents are declared per kind.** A CRM funnel, a work item's flow and a goal's outcome are all the same shape with different stage names.

---

## 5. The data model

**Three state fields** — `status`, `pipelineStage` (with its `pipelineOutcome`), and `goalState` — **plus the successor bookkeeping that one of them obliges.** No others.

| field | lives on | values | written by |
|---|---|---|---|
| `status` | every kind **except events** | `draft` · `live` · `deprecated` · `superseded` | a verb (§3.1) |
| `supersededBy`, `supersededAt` | wherever `superseded` is set | the successor's identity + when | the replace verb, in the same write as `superseded` |
| `pipelineStage` | kinds whose pack declares a pipeline | a stage `id` from that kind's declaration | the stage-advance verb, or an event the pack folds onto the stage |
| `pipelineOutcome` | same | the `outcome` of the terminal stage reached | the same write that moves into a terminal stage |
| `goalState` | goals | `met` · `unmet` · `blocked` · `abandoned` · `active` · `guideline` · `superseded` | the goal's own writer (unchanged) |

`supersededBy`/`supersededAt` are not a fourth kind of state — they are what the `superseded` word obliges you to record. The three state fields are `status`, `pipelineStage` (with its `outcome`) and `goalState`.

`superseded` is the one word that appears on two axes, and that is correct rather than a collision: on both, it means *replaced by a named successor*. What differs is **what** was replaced — a claim, or a commitment — and saying which is the axis's job. If the two ever come to mean different things, that is when one of them must be renamed.

Every field is **optional**. A thing with no `status` reads as `live` — created and current is the ordinary case, and it must not require a write. A thing with no pipeline has no position, and that is not a defect.

---

## 6. The one rule every reader follows

This is the whole architecture in one decision. Anything that needs to know "does this still count" asks **one** question, from **two** sources of meaning:

```mermaid
flowchart TD
    Q{Does this still count?}
    Q --> L{status is<br/>deprecated or superseded?}
    L -->|yes| NO[No — it is over]
    L -->|no| P{is its pipeline stage<br/>in the terminal slot?}
    P -->|yes| NO
    P -->|no| YES[Yes — it is current]
```

- **Lifecycle** answers it, because `deprecated` and `superseded` are ends.
- **Position** answers it too, because a `terminal` slot is an end.
- Nothing else does. A stage in `active` or `suspended` is never an ending; a missing stage is never an ending.

There is **one** implementation of this rule, and every reader uses it: search, retrieval, ranking, belief, audit, export. Today four different lists implement it with four different contents, which is why a word can be dead to one reader and alive to another.

---

## 7. What this model guarantees

| guarantee | why it follows |
|---|---|
| **A word means one thing.** | One lifecycle set for every kind that has a lifecycle. The same word cannot be an ending in one place and a current state in another. |
| **Every ending is real.** | An ending is either a lifecycle word or a `terminal` slot — and both are declarations, not names a reader has to recognise. |
| **Customising names cannot break readers.** | Readers read slots and words, never stage names. |
| **Adding a domain does not touch the engine.** | A new domain is a declared pipeline, not new code. |
| **The vocabularies stay small.** | Four lifecycle words. Verbs do not grow them. A second question gets a second field, not a bigger list. |
| **Nothing is lost when a thing is filed away.** | Visibility is a flag, so the reason something ended survives it. |
| **A second axis is the answer to a second question.** | `goalState` already proves it: a goal is `live` and `unmet` at the same time, and neither field overwrites the other. |

---

## 8. Requirements this model must not break

These are recorded decisions, not preferences. The model above is designed to sit *inside* them.

| requirement | source | how this model respects it |
|---|---|---|
| **An event has no lifecycle.** An event is a dated fact; a later event does not rewrite an earlier one. | #2725, enforced by #12108 | Events take no lifecycle word. If an event must be withdrawn, that is a claim about it, not a state on it. |
| **Achievement is its own axis.** Goal outcome is not promotion. | #7856 | `goalState` stays separate, with its own closed set, and nothing reconciles it into `status`. |
| **A pack owns its kind's words.** | #2729 | Packs declare stages; the engine never imposes stage names. |
| **The claim family's "no longer counts" flag is load-bearing.** | #2498 | The flag's *effect* is preserved as the `deprecated`/`superseded` words; the mechanism is not removed by fiat. |
| **What a reader hides is wider than what a writer may create.** | #2977 | Readers must still be able to treat a word as dead that no verb currently produces — the net stays wider than the door. |
| **A single shared list cannot cover pack words.** | #3308 / PR #6955, and Foreclosed in `EXTRACTOR-V4-ARCHITECTURE.md` | There is no shared *stage* list. Only the lifecycle set is shared, and it is small enough to be closed. |
| **A word with no reader is not added.** | the withdrawal of `stale`, #11824 | This model adds no word and removes four. |

---

## 9. Out of scope

Deliberately absent, and each has its own document:

- **How we get from today to this.** Migration, backfill and re-stamping.
- **What the built-in default pipeline for each core kind is.** Worth a research pass on its own; the model does not depend on which stages we ship.
- **The implementation plan**, the code surfaces, and the test strategy.
- **Visibility/filing.** Named here because it must not become a status word; no surface needs it yet.
- **Per-type lifecycle sets.** The model says one set for every kind that has a lifecycle. If a kind genuinely needs a different lifecycle, that is a decision to reopen, not a variation to add quietly.

---

## 10. The model in one paragraph

Everything in the graph that has a lifecycle — everything **except events** (§8) — has **one** lifecycle word from a set of four, and optionally a **position** in a pipeline its kind declares. The lifecycle word says whether the commitment still stands; the pipeline stage says how far along it is; a goal's outcome is a third field of its own; visibility is a flag and never a word. Every reader asks one question — *does this still count?* — and answers it from exactly two places: the lifecycle word, and whether the stage sits in a `terminal` slot. Words declare their meaning where they are declared, so a pack can invent any stage names it likes and nothing that reads the graph has to learn them.
