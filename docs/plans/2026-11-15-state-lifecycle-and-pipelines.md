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

**Issue of record:** #11824 · **Supersedes the design of:** the **status / lifecycle vocabulary** only — `docs/ONTOLOGY.md` §4.2 Subject status (`:423`), §4.3 Object status (`:441`), §5 Point status (`:972`), `goalState` (`docs/ONTOLOGY.md` §4.3, `:457`), and pack `statuses`/`folds` (`docs/ONTOLOGY.md` §4.3, `:449`, #2729). The **pipeline** slot model is **not** superseded: it is already shipped (`#2792`, `tortoise/pipeline.py`) and §4 restates it rather than redefining it.

**What is actually new here** is narrow, and worth stating plainly so a reader does not rebuild what exists — most of this machinery already ships. `tortoise/live.py` is the **single shared predicate** (#2901 / #2422): `is_terminal_status` and the Cypher twin `_terminal_excluded`, declared once and imported by the readers that skip non-current rows across `ranking`, `ep`, `sdk`, `why`, `projection`, `entity_identity`, `search_engine` and others, under its own recorded rule *"NO reader re-spells the predicate"* (the module deliberately states **no census**, because "the census grows with every reader … each number was stale within a release"). That module also already carries the **base-set-plus-delta seam**: `_terminal_excluded(clause, excluded=…)` takes a family's vocabulary (defaulting to the Point one) and `include_outdated_flag=` toggles the legacy conjunct for the Object family (#3301).

Adoption is not universal, and **where it stops is the point**: four modules still hand-write the narrower "not retracted" filter, so each **still serves superseded/archived/outdated/deprecated rows** — `tortoise/mcp_server.py`'s tag arm (`r.get("status") != "retracted"`; the gap its docstring records and **#12502** tracks); `hosted_api.py` on the public REST v1 list route (`:13719`, with a second identical append on the same route at `:13696`) and on the point-by-id read (`:13872`); `topic_summarization.py`'s three queries in `_retrieve_topic_neighborhood` — two seed queries and the Stage-2 expansion (four filter occurrences, `:231`, `:253`, `:289-290`); and `sdk.py:37120`'s `_hydrate_evidence`, where a superseded claim's ghost still votes in EP. That is this model's defect, live, in **four** modules rather than one — the very shape `live.py`'s own header records: *a hand-written subset is how `outdated` was omitted from three reader filters … and a superseded/outdated claim was then served as the CURRENT statement.* So what is genuinely new is (a) one **four-word lifecycle** vocabulary for every **knowledge kind** (Point, Object, Subject, Source), with `archived` retired and the successorless case moved onto `deprecated`; and (b) exactly **two new inputs** to the existing predicate — it reads neither the pipeline's `terminal` slot nor the object's durable `isClosed` marker, and it must read both.

> **OVERRIDES:** six recorded positions this model deliberately reverses, all named here so the departure is findable rather than read as history — (1) `docs/ONTOLOGY.md` §4.3 (`:449`, owner-approved text under that file's CRITICAL RULE at `:41`): *"a retirement with no successor is `archived`"*; (2) the same clause's *"pack-declared kinds validate against their own declared words"* as applied to the **status** vocabulary — `commit_ops.py:114-122` records that a single shared status set was **foreclosed** because `packs/venture` exercises its own words, and this model answers that by moving those words onto the position axis rather than by widening the status list (a pack still validates against its own declaration — the declaration changes axis, it is not dropped); (3) the extractor's recorded removal-from-recall target (`EXTRACTOR-V4-ARCHITECTURE.md:1084`, 2026-10-26: a journaled `restatus` to **`archived`**); and (4) `archived` in `SUBJECT_STATUS_VALUES` (`commit_ops.py:123-124`); (5) the same owner-approved sentence at `docs/ONTOLOGY.md:449` also accepts **`in_progress`** and **`completed`** as core Object words, and `status_folds.BUILTIN_CONNECTOR_FOLDS` (`:82-87`) writes both **today** (`pm:cardCreated` / `github.issue.open` / `github.issue.reopened` → `in_progress`; `pm:cardCompleted` / `github.issue.closed` → `completed`), so moving them onto the position axis changes what those folds target (§8); and (6) `docs/ONTOLOGY.md` §5 (`:1166`) declares **`retracted`** a terminal **Point** word with live writers (`retract_point`, `EventAPI.retract_point`), and the owner's recorded position on #11824 (2026-11-15) keeps it a Point concept — this model makes a retraction land on `deprecated`, so `retract_point`'s target changes and `retracted` survives only as a read delta. What replaces them: a thing with no successor ends by `deprecated`, and filing-away becomes a visibility **flag**. The reason is the collision §2 exists to remove — a word that means both *ended* and *hidden from a view*. **These are reversals of owner-approved text, so PR #12895's approval by the owner is the ruling**; until it is given, the recorded positions stand.

---

## 1. The problem in one paragraph

Today "status" answers at least three unrelated questions at once. One word can mean *does this claim still count as a belief*, or *what state is this customer record in*, or *did this goal work out*. Because the word is shared, the meaning is not: the **words** are declared per-kind and per-pack — which is correct, and a single shared list is foreclosed (`commit_ops.py`: "a shared `OBJECT_STATUS_VALUES` would refuse real words and narrow the door") — while the **rule** for what "finished" means exists in one canonical form for Points (`live.py`, #2901) and, outside that family, as eight or more sets of two different kinds: some are **legitimate declared deltas** (the Object and Subject vocabularies, and the resolver's deliberate narrowing, which §6 keeps), and some are **undisciplined re-statements that serve a dead row as current** — the `mcp_server.py` tag arm's bare `!= "retracted"` is the live example (#12502). Four of the sets are of sizes 1/5/4/2 — `assembly._RESOLVER_UNRESOLVABLE_OBJECT_STATUSES` (`{retracted}`, `:1668`), `live.TERMINAL_EXCLUDED_STATUSES` (5, `:104`), `commit_ops.OBJECT_TERMINAL_STATUSES` (4, `:69`), `derived_invalidation.RETIRED_GOAL_STATES` (2, `:174`) — and the rest do not all differ: `commit_ops.SUBJECT_TERMINAL_STATUSES` is recorded as **matching** `OBJECT_TERMINAL_STATUSES` (`:100-102`) and `_RECALL_OBJECT_EXCLUDED_STATUS` is a literal **alias** of it (`:77`), so a count of *declarations* overstates the number of *distinct* answers. Where a reader does not import the canonical predicate and does not declare a delta, anything with a word outside its own list is **treated as current**, which is the same as saying it never ends. Nine declarations across our packs use thirty distinct words; one of them (`draft`) is in a built-in **status** vocabulary, and three more (`active`, `met`, `unmet`) are built-in `goalState` values (`sdk.GOAL_STATE_VALUES`, `sdk.py:301-310`).

---

## 2. The four questions, and who owns each

Everything that looked like "status" is one of these. Three get a **state** field; visibility gets a flag, not a word.

| question | what it really asks | owned by |
|---|---|---|
| **1. Lifecycle** | Is this a current commitment, or is it over — and if over, how did it end? | the `status` word — one closed write set of four, every **knowledge kind** except events (§8) |
| **2. Position** | Where is this in its process? | a **pipeline**: stages declared per kind |
| **3. Achievement** | Did the goal actually work out? | its own field (`goalState`), already separate and working |
| **4. Visibility** | Is this tucked out of the default view? | a **flag**, not a word — and out of scope until a surface needs it |

Questions 1 and 2 are the two axes. They are separate because **they change for different reasons**: lifecycle changes when a thing is promoted, replaced or retired — rarely, and by decision. Position changes as work moves — often, and sometimes automatically from events. Mixing them means a routine progress update can silently retire a commitment.

Questions 3 and 4 are **not** lifecycle. Achievement is already its own axis (a goal can be `live` *and* `unmet` — both true at once, which is the proof they are different questions). Visibility is a property of a view, not of the thing.

---

## 3. The lifecycle word

Every **knowledge kind** — Point, Object, Subject and Source — carries **one** lifecycle word from a closed set of four. One of the four is not reachable for every kind yet: **a Source has no successor route**, so `superseded` is unavailable to a Source until a carrier exists — `_LIFECYCLE_CARRIER_LABELS` is `("Object", "Subject")` (`sdk.py:5543`), the `CORRECTS` edge is hard-coded `(a:Point),(b:Point)` (`projection/entities.py:4302`), and `docs/ONTOLOGY.md:803` records *"Source supersession is unreachable, not merely unimplemented"* — so a Source ends by `deprecated` today (§5). Events are the one exclusion, and §8 says why: an event is a dated fact, not a commitment, so it has no lifecycle to be in. (Operational node kinds — `:PipelineStage`'s `active`/`retired`, `:Batch`'s batch states — are not knowledge kinds and keep their own vocabularies; §10.)

**A pack's own words are not refused by this — they move axis.** A pack declares its words as that kind's **pipeline stages** (§4.2), each carrying a slot, so `prospective`, `invested`, `written-off` become stages of a funnel and the kind's `status` still carries one of the four. This is the one place the model deliberately changes a recorded constraint: `commit_ops.py:116-122` records that a shared status vocabulary was **foreclosed** because `packs/venture` exercises its own words, and the answer here is not a wider status list but the axis the words actually belong to. A pack still validates against its own declaration — the declaration just has a shape now (see the `OVERRIDES` line above).

| word | means | can move to |
|---|---|---|
| `draft` | created, not yet a commitment. Present in the graph; not counted as in force. | `live` — or straight to `superseded`, because a draft can end before it was ever promoted (`sdk.py`'s `_ALLOWED_TRANSITIONS`, the #432 decision). `deprecated` is a valid target in **this model** but not in today's table: `_ALLOWED_TRANSITIONS` (`sdk.py:4639-4653`) has no `deprecated` entry, so `draft → deprecated` and `live → deprecated` raise today and the change adds those entries |
| `live` | current: in force and counted. | `deprecated` (needs the `_ALLOWED_TRANSITIONS` entry above), `superseded` (ships) |
| `deprecated` | ended, and **nothing replaced it**. Final. | — |
| `superseded` | ended because something **replaced** it. Final. The replacement is named. | — |

```mermaid
stateDiagram-v2
    [*] --> live : created with no status (the ordinary case)
    [*] --> draft : created as a draft
    draft --> live
    draft --> deprecated : ended before promotion, no successor
    draft --> superseded : replaced before promotion
    live --> deprecated : ended, no successor
    live --> superseded : replaced by a named successor
    deprecated --> [*]
    superseded --> [*]
```

**The one rule that carries the meaning:** `superseded` names its successor. If nothing replaced it, the word is `deprecated`. That is what separates the two, and `superseded` additionally obliges the successor carriers (§5).

### 3.1 Verbs are not words

A *verb* is what someone does; a *word* is what the record then says. Several verbs land on the same word, and that is correct — it keeps the vocabulary small without losing distinctions in what happened.

| verb | resulting word |
|---|---|
| promote | `live` |
| retire, withdraw, retract, reject, invalidate | `deprecated` |
| replace, supersede, correct | `superseded` (successor named) |

This is why the current five extra words are not needed — `retracted`, `outdated`, `archived`, `in_progress`, `completed`. Four are names from **another axis** leaking into the state vocabulary — `retracted` a verb, `in_progress`/`completed` a position, `archived` a visibility choice (§3.2) — and are not lifecycle words at all; `outdated` is a former lifecycle word whose meaning `deprecated`/`superseded` already carry. Keeping verbs separate from states means a new verb never grows the vocabulary. `deprecated` is the one word in the base set that is not a removal: it is already declared for Objects and Subjects and already read for legacy Points, so promoting it introduces no word. (A family's **read delta**, §6, may still *retain* one of these as a word its readers must treat as ended — what is removed is its standing as a **write** word a verb can produce.)

### 3.2 What is deliberately not a lifecycle word

| not a word | what it actually is | where it lives |
|---|---|---|
| "archived" | a visibility choice about what you look at | a flag, if and when a surface needs it. Retroactively filing a rejected thing must not erase *why* it ended |
| "in-progress", "completed" | position in a process | a pipeline stage |
| "outdated" | the past name of the Point "no longer counts" mechanism | the `deprecated`/`superseded` words above |
| "blocked", "paused", "waiting" | an interruption, not an ending | a stage in the `suspended` slot — and for a **goal**, `blocked` is also one of its `goalState` values. The two do not collide: the stage is read by its **slot**, never its name (§4.1) |
| "met", "unmet", "abandoned" | how the **goal** worked out | `goalState` — which is meaningful only on `goal`/`target`/`strategy`/`plan`. On any other kind the word is progress, so it becomes a pipeline stage: `packs/venture`'s `condition` (`statuses: [unmet, met]`) is not a goal, and its words are stages |

---

## 4. The pipeline

A pipeline says where a thing is in a process. It is **not** a lifecycle: a stage in `entry`, `active` or `suspended` says *how far along*, never *whether the commitment still stands*. The one exception is the `terminal` slot, which is one of the two endings (§6) — and that is exactly why the slot exists rather than a stage name.

A pipeline is a short ordered list of **stages**. Each stage declares:

| field | means |
|---|---|
| `id` | a stable name for the stage |
| `label` | what a person sees |
| `order` | its place in the sequence |
| `slot` | which of four roles it plays — see below |
| `outcome` | for a terminal stage only: how it ended |

The **slot** is the field that carries the meaning — and **it is already shipped**, not proposed here: `tortoise/pipeline.py` (#2792, closed) declares `PIPELINE_SLOTS` and `PIPELINE_OUTCOMES`, validates both, and stores the current stage on the Object as `pipelineStageId` / `pipelineSlot` / `pipelineOutcome` / `pipelineStageSince` / `pipelineStageKey` / `isClosed` (`STAGE_PROPS`, `pipeline.py:65-69`), with a pack opting in through `kindDefs[<kind>].pipeline`. That module also states the separation this document depends on: *"`Object.status` keeps its existence-axis meaning (`live`/`superseded`); the pipeline stage is a separate property"*. This section therefore **restates the shipped shape rather than introducing it**, and cites the module so a reader does not rebuild it.

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

A pack declares pipelines for its own kinds. A graph with no pack still works: a core kind has no pipeline **until a default is declared for it**, and its state is the lifecycle word alone.

This is what makes the model customisable without becoming a configuration language: **the shape is fixed (four slots, ordered stages, one outcome on a terminal stage); the contents are declared per kind** — which is exactly the two-tier split `tortoise/pipeline.py` already ships (canonical slots in core, tenant stages declared per graph). A CRM funnel, a work item's flow and a goal's outcome are all the same shape with different stage names.

---

## 5. The data model

**Three state fields** — `status`, the pipeline stage (`pipelineStageId` + `pipelineSlot`, with `pipelineOutcome`, `pipelineStageSince`, `pipelineStageKey` and `isClosed` — the six shipped `STAGE_PROPS`, `pipeline.py:65-69`), and `goalState` — **plus the successor bookkeeping that the `status` word obliges when it is `superseded`.** No others, and the pipeline field names are the shipped ones (#2792) rather than new spellings.

| field | lives on | values | written by |
|---|---|---|---|
| `status` | every **knowledge kind** (Point, Object, Subject, Source) | `draft` · `live` · `deprecated` · `superseded` | a verb (§3.1) |
| `supersededBy`, `supersededAt` | **Object and Subject only** — a Point names its successor by the `CORRECTS` edge, not by a property (`ONTOLOGY.md` §4.7: a Point carries `status='superseded'` and has no `supersededAt`) | the successor's identity + when | the replace verb, in the same write as `superseded` |
| `pipelineStageId` (+ `pipelineSlot`) | kinds whose pack declares a pipeline | a stage `id` from that kind's declaration | the stage-advance verb, or an event the pack folds onto the stage |
| `pipelineStageSince`, `pipelineStageKey` | same | when the current stage was entered; the stage key `current_stage` answers with (`pipeline.py:70-79`) | the stage-advance write |
| `pipelineOutcome`, `isClosed` | same | `pipelineOutcome` is the `outcome` of the terminal stage reached; `isClosed` is a **boolean durable marker** — the authoritative record that the thing is closed, not a mirror of the slot | the stage-advance write (which sets `isClosed = stage.is_terminal`), and the generic entity surface (`create_object(..., isClosed=True)`, `update_entity(id, isClosed=True)`, the #2728 repair verb) |
| `goalState` | goals | `met` · `unmet` · `blocked` · `abandoned` · `active` · `guideline` · `superseded` | the goal's own writer (unchanged) |

`supersededBy`/`supersededAt` are not a fourth kind of state — they are what the `status` word obliges you to record. They bind to **`status`**, not to `goalState`, because a goal is an Object and the carriers are gated to Object and Subject (`sdk.py`'s `_LIFECYCLE_CARRIER_LABELS`) — and `sdk.py` refuses a lifecycle carrier on a node whose **resulting** status is not `superseded` — the status it reads is the one supplied in the write, else the one already stored, so adding a stamp to an already-superseded node stays writable. The carriers are bookkeeping, not a fourth field of state. **A Source has no carrier at all**: `_LIFECYCLE_CARRIER_LABELS` excludes it, the `CORRECTS` edge is Point-only, and `docs/ONTOLOGY.md:803` records Source supersession as *unreachable* — so `superseded` is not offered to a Source by this model until a successor route exists, and a Source ends by `deprecated` (§3).

**A goal that retires crosses both axes in one write.** `superseded` and `abandoned` are the two `goalState` words that mean the goal is no longer a pursued commitment (`derived_invalidation.RETIRED_GOAL_STATES`); the other five describe a *live* commitment. So a goal that retires also sets `status` in the same write — `superseded`, naming its successor, or `deprecated` where there is none. The axes stay independent for the other five words, and the coupling runs in one direction only: `goalState` never reads `status` back.

`superseded` is the one word that carries the same meaning on two axes, and that is correct rather than a collision: on both, it means *replaced by a named successor*. What differs is **what** was replaced — a claim, or a commitment — and saying which is the axis's job. If the two ever come to mean different things, that is when one of them must be renamed.

`blocked` and `active` are values that appear on two axes as well, and neither is the `superseded` case. `active` names a **slot role** (§4) and a `goalState` value; `blocked` is only ever a stage *name* and a `goalState` value. They cannot collide because **the slot field is its own namespace**, distinct from stage `id`, `label` and `order`: a reader resolves a stage by its `slot` (§4.1), never by any of the stage's names. `superseded` is the only word whose *meaning* has to be reconciled across two **fields a reader reads**, which is why it is the one that needs the explanation above.

Every field is **optional**. A thing with no `status` reads as `live` — created and current is the ordinary case, and it must not require a write. A thing with no pipeline has no position, and that is not a defect.

---

## 6. The one rule every reader follows

This is the whole architecture in one decision. Anything that needs to know whether a thing still counts as current asks **one** question, from **two** sources of meaning:

```mermaid
flowchart TD
    Q{Is this still<br/>served as current?}
    Q --> L{status in the ended set,<br/>or legacy outdated=true?}
    L -->|yes| NO[No — it is over]
    L -->|no| P{isClosed, or is its stage<br/>in the terminal slot?}
    P -->|yes| NO
    P -->|no| YES[Yes — it is current]
```

- **Lifecycle** answers it, because `deprecated` and `superseded` are ends.
- **Position** answers it too — by the object's **closed marker** *or* a stage in the `terminal` slot, and the marker is the half that makes the ending **durable**, so re-resolving the slot alone can never resurrect an object the graph records as closed. `pipeline.py:924-941` records that the durable marker is `isClosed` and that the two must **agree**: a pack release that reclassifies the stage an object sits in from `terminal` to an active slot takes effect immediately, yet must not resurrect an object the graph records as closed; and an object closed with **no** resolvable stage stays closed, because `isClosed` is also written through the generic entity surface (`create_object(..., isClosed=True)`, `update_entity(id, isClosed=True)`, and the #2728 repair verb). Both shapes are pinned — `tests/test_pipeline_2792.py::test_a_slot_reclassification_cannot_resurrect_a_closed_object` (closed while sitting in an `active` slot) and `::test_a_closed_object_with_no_stage_is_not_resurrected`.
- Nothing else in the model does. A stage in `active` or `suspended` is not *by itself* an ending (an object already marked closed is ended while sitting in one), and a **missing** stage is not an ending unless `isClosed` is set. The one implementation input outside these two axes is the legacy `outdated` flag, which the predicate must read (below) until it is migrated onto a word.

The question is **"is this still served as current?"**, deliberately not "is this counted as a belief?". Those are different, and `draft` is the difference: it is **current** (not retired — it is served, and a reader renders it) yet **not in force** (inert for belief computation, excluded from EP factor extraction). A reader that needs the second answer asks a second question; no reader is allowed to answer "not current" as a shorthand for "not in force", because that is exactly how a word becomes dead to one surface and alive to another.

**The implementation already exists. What do not are the position axis's two inputs.** `tortoise/live.py` declares the one shared predicate (#2901 / #2422) — `is_terminal_status` / `_terminal_excluded` — and its own header records the rule that keeps it one: *"NO reader re-spells the predicate … Do NOT re-declare a status set at a call site. A hand-written subset is how `outdated` was omitted from three reader filters … and a superseded/outdated claim was then served as the CURRENT statement."* It already takes the family as an input rather than a second implementation: `_terminal_excluded(excluded=…)` defaults to the Point vocabulary and the Object read surfaces pass their own (#3301), which is the delta seam in practice.

So the work is not to build a shared reader — that was built, and it is imported across most read surfaces, though not all: the four modules whose hand-written filters the preamble names still bypass it. The work is that **the predicate reads two sources and must read four inputs**: it answers from the terminal-status vocabulary **and** the legacy `outdated` flag (`_terminal_excluded` builds `(status_part AND coalesce(flag, false) = false)`, with `include_outdated_flag=False` for the Object family), while a thing ended by a `terminal` **slot** — or by the durable `isClosed` marker, which must agree with the slot (§6) — is invisible to it. Extending that shared predicate to read the position axis — the durable `isClosed` marker, which must agree with the resolved `terminal` slot (`pipeline.py:922-941`) — is the whole change. It is small *because* the implementation and its delta seam already exist — the same seam the `outdated` flag rides — but it is **not one function**: the Cypher twin is, while the Python mirror `is_terminal_status(status, outdated)` takes both inputs as arguments, so its 22 call sites across ten modules (`why`, `ranking`, `search_engine`, `decide_wiring`, `audit`, `ask_lane`, `commit_ops`, `sdk`, `time_aware`, `hosted_api` — most reading the pair straight out of a row, e.g. `ranking.py:1075`'s `is_terminal_status(row[7], bool(row[8]))`, and `hosted_api.py:22721`/`:22734` reaching it through an aliased import) must each fetch the stage they now have to answer with. `pipelineSlot` is read by no module but `pipeline.py` today.

The rule reads the **base** set plus any **delta** the family declares — it is not one collapsed list. Two families legitimately answer different questions, and #11824 recorded the shape this model must keep: *"one base set plus named, declared deltas"*, because *"collapsing them would be a reopen"*. `commit_ops.py:61-65` says the same — *"Do not unify the two blindly: they answer different questions"* — and `assembly.py`'s resolver narrowing is the live example: it declares a **narrower** set than the read-surface vocabulary on purpose, so that a superseded/deprecated/archived Object still **resolves** to render its own state, and its own comment warns that re-pointing it at the canonical set is what stops the current-state question firing. A delta is therefore **declared and named**, never a divergence discovered later.

A delta is a **read-side** declaration: the set of words a family's readers treat as **ended**. It may **widen** the base (adding a legacy word its stored data still contains) or **narrow** it (withholding a base ending from a particular reader, as the resolver does) — the direction is part of the declaration, not an accident of it. It **never adds a write word**: the write vocabulary stays one closed set of four (§3), so a delta cannot mint a state no verb can produce.

---

## 7. What this model guarantees

| guarantee | why it follows |
|---|---|
| **A word means one thing.** | One closed lifecycle set of four is the only **write** vocabulary, plus a named **read** delta per family where one is genuinely needed (§6). The same word cannot be an ending in one family and a current state in another without that delta declaring it. |
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
| **An event has no lifecycle.** An event is a dated fact; a later event does not rewrite an earlier one. | #11825 / #12108 | Events take no lifecycle word. If an event must be withdrawn, that is a claim about it, not a state on it. |
| **Achievement is its own axis.** Goal outcome is not promotion. | #7856 | `goalState` keeps its own closed set; it is never derived from `status`. The coupling runs one way only, for the two retirement words — a goal that retires also sets `status` in the same write (§5) — because the successor carriers and the reader rule are bound to `status`. |
| **A pack owns its kind's words.** | #2729; *Foreclosed* in `EXTRACTOR-V4-ARCHITECTURE.md` ("pack lifecycles own their words") | Packs declare stages; the engine never imposes stage names. A pack's declared status words are not refused — they become that kind's stage names (§4.2), so the declaration is preserved and only its axis changes. |
| **One base set, plus named declared deltas — never one collapsed set.** | #11824's contradiction test; `commit_ops.py:61-65` "Do not unify the two blindly: they answer different questions"; the `assembly.py` resolver narrowing; the Object/Point vocabulary divergence in #3308 / PR #6955 | The four words are the **base** and the only write vocabulary. A family that must answer a different question declares its delta explicitly and names it; a delta is never a quiet divergence, and never a list to be re-pointed at the base. |
| **The claim family's "no longer counts" flag is load-bearing.** | #2498 | The flag's *effect* is preserved as the `deprecated`/`superseded` words; the mechanism is not removed by fiat. |
| **What a reader hides is wider than what a writer may create.** | #2977 | Readers must still be able to treat a word as dead that no verb currently produces — the net stays wider than the door. |
| **A single shared list cannot cover pack words.** | #2729, and Foreclosed in `EXTRACTOR-V4-ARCHITECTURE.md` ("pack lifecycles own their words") | The shared **stage** list is the shipped four-slot vocabulary (#2792), and it covers only *roles*, never names — so no pack word is refused. Pack words live on the position axis. |
| **A word with no reader is not added.** | the withdrawal of `stale`, #11824 | This model introduces no new word and removes **five** — `retracted`, `outdated`, `archived`, `in_progress`, `completed` — leaving a base set of four. |
| **The built-in connector folds write `in_progress` / `completed` today.** | `status_folds.BUILTIN_CONNECTOR_FOLDS` (`:82-87`), owner-approved text at `ONTOLOGY.md:449` | Their targets move onto the position axis, so the removal step must either declare the core default pipeline those folds land in (§4.2) or map the folds to `deprecated`. Until then those two words remain the folds' targets — the model cannot delete them from the vocabulary while the folds still write them. |

---

## 9. Out of scope

Deliberately absent, and each has its own document:

- **How we get from today to this.** Migration, backfill and re-stamping.
- **Which stages each core kind's default pipeline ships with.** The model does not depend on those names; it depends only on the *shape* (§4.2), so this is a declaration to fill in, not a design question this document settles.
- **The implementation plan**, the code surfaces, and the test strategy.
- **Visibility/filing.** Named here because it must not become a status word; no surface needs it yet.
- **The deltas themselves.** §6 fixes the *shape* — one base set plus named, declared **read** deltas — but not each family's delta. Which delta a family needs, and what each contains, is part of #11824's ordered removal plan, not this document.

---

## 10. The model in one paragraph

Every **knowledge kind** — Point, Object, Subject and Source — has **one** lifecycle word from a closed set of four, and optionally a **position** in a pipeline its kind declares. Events have no lifecycle at all (§8), and operational node kinds keep their own vocabularies (`:PipelineStage`'s `active`/`retired`, `:Batch`'s batch states); they are not knowledge kinds and this model does not re-spell them. A family may declare a **read** delta over which words its readers treat as ended (§6), but never a new write word. The lifecycle word says whether the commitment still stands; a pipeline stage says how far along it is, **except** that a stage in the `terminal` slot is itself an ending. A goal's outcome is a third field of its own; visibility is a flag and never a word. Every reader asks one question — *is this still served as current?* — and answers it from the lifecycle word (plus any declared read delta), from whether the stage sits in a `terminal` slot or the object's `isClosed` marker is set, and — until the legacy `outdated` flag is migrated onto a word — from that flag. Words declare their meaning where they are declared, so a pack can invent any stage names it likes and nothing that reads the graph has to learn them.
