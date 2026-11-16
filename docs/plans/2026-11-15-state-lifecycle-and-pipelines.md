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

**Issue of record:** #11824 · **Supersedes the design of:** the **status / lifecycle vocabulary** only — `docs/ONTOLOGY.md` §4.1 Point status (`:403`, the six-word row this model reduces to four, with its per-word rows at `:1166-1169`), §4.2 Subject status (`:423`), §4.3 Object status (`:441`), §5 Point status (`:972`), `goalState` (`docs/ONTOLOGY.md` §4.3, `:457`), and pack `statuses`/`folds` (`docs/ONTOLOGY.md` §4.3, `:449`, #2729). The **pipeline** slot model is **not** superseded: it is already shipped (`#2792`, `tortoise/pipeline.py`) and §4 restates it rather than redefining it.

**What is actually new here** is narrow, and worth stating plainly so a reader does not rebuild what exists — most of this machinery already ships. `tortoise/live.py` is the **single shared predicate** (#2901 / #2422): `is_terminal_status` and the Cypher twin `_terminal_excluded`, declared once and imported by the readers that skip non-current rows across `ranking`, `ep`, `sdk`, `why`, `projection`, `entity_identity`, `search_engine` and others, under its own recorded rule *"NO reader re-spells the predicate"* (the module deliberately states **no census**, because "the census grows with every reader … each number was stale within a release"). That module also already carries the **base-set-plus-delta seam**: `_terminal_excluded(clause, excluded=…)` takes a family's vocabulary (defaulting to the Point one) and `include_outdated_flag=` toggles the legacy conjunct for the Object family (#3301).

Adoption is not universal, and **where it stops is the point**: four modules still hand-write the narrower "not retracted" filter, so each **still serves superseded/archived/outdated/deprecated rows** — `tortoise/mcp_server.py`'s tag arm (`r.get("status") != "retracted"`; the gap its docstring records and **#12502** tracks); `hosted_api.py` on the public REST v1 list route (`:13719`, with a second identical append on the same route at `:13696`) and on the point-by-id read (`:13872`); `topic_summarization.py`'s three queries in `_retrieve_topic_neighborhood` — two seed queries and the Stage-2 expansion (four filter occurrences, `:231`, `:253`, `:289-290`); and `sdk.py:37120`'s `_hydrate_evidence`, where a superseded claim's ghost still votes in EP. That is this model's defect, live, in **four** modules rather than one — the very shape `live.py`'s own header records: *a hand-written subset is how `outdated` was omitted from three reader filters … and a superseded/outdated claim was then served as the CURRENT statement.* So what is genuinely new is (a) one **four-word lifecycle** vocabulary for every **knowledge kind** (Point, Object, Subject, Source), with `archived` retired and the successorless case moved onto `deprecated`; and (b) exactly **two new inputs** to the existing predicate — it reads neither the pipeline's `terminal` slot nor the object's durable `isClosed` marker, and it must read both.

> **OVERRIDES:** eight recorded positions this model deliberately reverses, all named here so the departure is findable rather than read as history — (1) `docs/ONTOLOGY.md` §4.3 (`:449`, owner-approved text under that file's CRITICAL RULE at `:41`): *"a retirement with no successor is `archived`"*; (2) the same clause's *"pack-declared kinds validate against their own declared words"* as applied to the **status** vocabulary — `commit_ops.py:114-122` records that a single shared status set was **foreclosed** because `packs/venture` exercises its own words, and this model answers that by moving those words onto the position axis rather than by widening the status list (a pack still validates against its own declaration — the declaration changes axis, it is not dropped); (3) the extractor's recorded removal-from-recall target (`EXTRACTOR-V4-ARCHITECTURE.md:1084`, 2026-10-26: a journaled `restatus` to **`archived`**); and (4) `archived` in `SUBJECT_STATUS_VALUES` (`commit_ops.py:123-124`); (5) the same owner-approved sentence at `docs/ONTOLOGY.md:449` also accepts **`in_progress`** and **`completed`** as core Object words, and `status_folds.BUILTIN_CONNECTOR_FOLDS` (`:82-87`) writes both **today** (`pm:cardCreated` / `github.issue.open` / `github.issue.reopened` → `in_progress`; `pm:cardCompleted` / `github.issue.closed` → `completed`), so moving them onto the position axis changes what those folds target (§8); and (6) `docs/ONTOLOGY.md` §5 (`:1166`) declares **`retracted`** a terminal **Point** word with live writers (`retract_point`, `EventAPI.retract_point`), and the owner's recorded position on #11824 (2026-11-15) keeps it a Point concept — this model makes a retraction land on `deprecated`, so `retract_point`'s target changes and `retracted` survives only as a read delta; and (7) the **Point** vocabulary's `outdated` (`docs/ONTOLOGY.md:1168`, live writers `invalidate_point` / `supersede_point`) and `archived` (`:1169`, declared "reserved"), both members of `POINT_STATUS_VALUES` (`sdk.py:275`) and both terminal in `_ALLOWED_TRANSITIONS` (`sdk.py:4651-4652`) — `outdated`'s meaning is carried by `deprecated`/`superseded` and `archived` by the reader's filter, so both survive only as read deltas. What replaces them: a thing with no successor ends by `deprecated`, and filing-away becomes a reader's **filter**. The reason is the collision §2 exists to remove — a word that means both *ended* and *hidden from a view*; and (8) `docs/ONTOLOGY.md:417`/`:457` declare `goalState` a **CLOSED** vocabulary that includes **`guideline`**, and `:1057-1058` records it as *"the owner's #6792 form ruling, which is why `<10 min of CI` is a guideline and not an absolute target"* — this model removes `guideline` from that set (§5), because directionality is a question about **priority**, not outcome, and §9 puts it out of scope. **These are reversals of owner-approved text, so PR #12895's approval by the owner is the ruling**; until it is given, the recorded positions stand.

---

## 1. The problem in one paragraph

Today "status" answers at least three unrelated questions at once. One word can mean *does this claim still count as a belief*, or *what state is this customer record in*, or *did this goal work out*. Because the word is shared, the meaning is not: the **words** are declared per-kind and per-pack — which is correct, and a single shared list is foreclosed (`commit_ops.py`: "a shared `OBJECT_STATUS_VALUES` would refuse real words and narrow the door") — while the **rule** for what "finished" means exists in one canonical form for Points (`live.py`, #2901) and, outside that family, as eight or more sets of two different kinds: some are **legitimate declared deltas** (the Object and Subject vocabularies, and the resolver's deliberate narrowing, which §6 keeps), and some are **undisciplined re-statements that serve a dead row as current** — the `mcp_server.py` tag arm's bare `!= "retracted"` is the live example (#12502). Four of the sets are of sizes 1/5/4/2 — `assembly._RESOLVER_UNRESOLVABLE_OBJECT_STATUSES` (`{retracted}`, `:1668`), `live.TERMINAL_EXCLUDED_STATUSES` (5, `:104`), `commit_ops.OBJECT_TERMINAL_STATUSES` (4, `:69`), `derived_invalidation.RETIRED_GOAL_STATES` (2, `:174`) — and the rest do not all differ: `commit_ops.SUBJECT_TERMINAL_STATUSES` is recorded as **matching** `OBJECT_TERMINAL_STATUSES` (`:100-102`) and `_RECALL_OBJECT_EXCLUDED_STATUS` is a literal **alias** of it (`:77`), so a count of *declarations* overstates the number of *distinct* answers. Where a reader does not import the canonical predicate and does not declare a delta, anything with a word outside its own list is **treated as current**, which is the same as saying it never ends. Nine declarations across our packs use thirty distinct words: `draft` is in the built-in **knowledge-kind** status vocabularies, `open` / `closed` / `pending` / `active` collide with the built-in shared connector vocabulary (`status_folds.SHARED_CONNECTOR_STATUS_WORDS`, `:95-97` — the collision the registry warns on at `:190-200`), and `active` / `met` / `unmet` are also built-in `goalState` values (`sdk.GOAL_STATE_VALUES`, `sdk.py:301-310`).

---

## 2. The four questions, and who owns each

Everything that looked like "status" is one of these. Three get a **state** field; visibility gets **nothing on the record at all** — it is a filter at read time.

| question | what it really asks | owned by |
|---|---|---|
| **1. Lifecycle** | Is this a current commitment, or is it over — and if over, how did it end? | the `status` word — one closed write set of four, every **knowledge kind** except events (§8) |
| **2. Position** | Where is this in its process? | a **pipeline**: stages declared per kind |
| **3. Achievement** | Did the goal actually work out? | its own field (`goalState`), already separate and working |
| **4. Visibility** | Is this tucked out of the default view? | the **reader** — a filter at read time (a tool parameter or a setting), never a field on the record and never a word. Out of scope until a surface needs it |

Questions 1 and 2 are the two axes. They are separate because **they change for different reasons**: lifecycle changes when a thing is promoted, replaced or retired — rarely, and by decision. Position changes as work moves — often, and sometimes automatically from events. Mixing them means a routine progress update can silently retire a commitment.

Questions 3 and 4 are **not** lifecycle. Achievement is already its own axis (a goal can be `live` *and* `unmet` — both true at once, which is the proof they are different questions).

Visibility is a property of a **view**, not of the thing, and that is why it needs no field. Nothing is hidden *in* the graph: a record that is `deprecated` or `superseded` is already fully described, and a reader that does not want to see ended things filters on the word it already has. So hiding is not a state to record — it is a **choice a reader makes**, and different readers may legitimately make different choices: a retrieval leg serving an agent's working context may drop everything ended, an audit view must show it, an export may keep only what counts. Today's defect is not that those readers differ — it is that they differ **silently**, from undeclared lists. The fix is that each reader states its filter, not that the record grows a hiding field. §6's rule is what a reader filters *on*; this is the setting that says whether it does.

---

## 3. The lifecycle word

Every **knowledge kind** — Point, Object, Subject and Source — carries **one** lifecycle word from a closed set of four. One of the four is not reachable for every kind yet: **a Source has no successor route**, so `superseded` is unavailable to a Source until a carrier exists — `_LIFECYCLE_CARRIER_LABELS` is `("Object", "Subject")` (`sdk.py:5543`), the `CORRECTS` edge is hard-coded `(a:Point),(b:Point)` (`projection/entities.py:4302`), and `docs/ONTOLOGY.md:803` records *"Source supersession is unreachable, not merely unimplemented"* — so in this model a Source ends by `deprecated` — but that ending needs a declared field, a writer and a reader that do not exist yet: `docs/ONTOLOGY.md` §4.6 declares no `status` on a Source, and today it is reachable **only through non-lifecycle doors** — the caller passthrough `_SOURCE_EXTRA_PROPS` (`projection/entities.py:2114-2129`) and the generic `update_entity` surface, because `status` sits in the declared node-property set `_SOURCE_NODE_PROPS` (`:2088`) and not in `_SOURCE_SERVER_MANAGED_PROPS` (`:7342`); no lifecycle verb and no reader that filters on it exist. Events are the one exclusion, and §8 says why: an event is a dated fact, not a commitment, so it has no lifecycle to be in. (Operational node kinds — `:PipelineStage`'s `active`/`retired`, `:Batch`'s batch states — are not knowledge kinds and keep their own vocabularies; §10.)

**A pack's own words are not refused by this — they move axis.** A pack declares its words as that kind's **pipeline stages** (§4.2), each carrying a slot, so `prospective`, `invested`, `written-off` become stages of a funnel and the kind's `status` still carries one of the four. This is the one place the model deliberately changes a recorded constraint: `commit_ops.py:116-122` records that a shared status vocabulary was **foreclosed** because `packs/venture` exercises its own words, and the answer here is not a wider status list but the axis the words actually belong to. A pack still validates against its own declaration, but the declaration is **rewritten**, not merely re-shaped: its `statuses` list becomes that kind's pipeline stages, and each `folds[].status` target moves with it. That is not free — `status_folds.py:215-219` refuses a kind whose `folds` has no `statuses` (*"a fold target must be one of the kind's declared status values"*), `:294-297` requires the fold's `status` to be a member of that list, and `VALID_FOLD_KEYS` (`:100`) admits only `event` and `status`, so every pack fold (`packs/venture/manifest.yaml:201-204`, `:225-228`) needs a stage target in the same change (§8; see the `OVERRIDES` line above).

| word | means | can move to |
|---|---|---|
| `draft` | created, not yet a commitment. Present in the graph; not counted as in force. | `live` — or straight to `superseded`, because a draft can end before it was ever promoted (`sdk.py`'s `_ALLOWED_TRANSITIONS`, the #432 decision). `deprecated` is a valid target in **this model**, but on a **Point** it is not writable today at all: the word is deliberately absent from `POINT_STATUS_VALUES` (`sdk.py:275`; `live.py:90-98`), so the vocabulary gate raises *before* the transition table, and `_ALLOWED_TRANSITIONS` (`sdk.py:4639-4653`, read only by the Point arm at `:48898`) has no `deprecated` entry either — the change adds the **word and** the entry. On an **Object or Subject** `deprecated` already writes: it is a declared word (`ONTOLOGY.md:449`; `commit_ops.py:123-124`) and their arm refuses leaving a dead state (`sdk.py:49529-49544`) — with one measured exception, an un-supersede that clears every lifecycle carrier (`:49143-49147`, #12786) |
| `live` | current: in force and counted. | `deprecated` (a Point needs the word **and** the entry above; an Object or Subject already writes it), `superseded` (ships) |
| `deprecated` | ended, and **nothing replaced it**. Final. | — |
| `superseded` | ended because something **replaced** it. Final, with one measured exception: an Object/Subject un-supersede is admitted when the write clears every lifecycle carrier (`sdk.py:49143-49147`, #12786) — the Point arm has no such door. The replacement is named. | — |

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
| retire, withdraw, retract, reject, invalidate, abandon, drop | `deprecated` |
| replace, supersede, correct | `superseded` (successor named) |

This is why the current five extra words are not needed — `retracted`, `outdated`, `archived`, `in_progress`, `completed`. Four are names from **another axis** leaking into the state vocabulary — `retracted` a verb, `in_progress`/`completed` a position, `archived` a collision of *this ended* with *do not show me this* (§3.2) — and none of them is a lifecycle word of its own; `outdated` is a former lifecycle word whose meaning `deprecated`/`superseded` already carry. Keeping verbs separate from states means a new verb never grows the vocabulary. `deprecated` is the one word in the base set that is not a removal: it is already declared for Objects and Subjects and already read for legacy Points, so promoting it introduces no word. (A family's **read delta**, §6, may still *retain* one of these as a word its readers must treat as ended — what is removed is its standing as a **write** word a verb can produce.)

### 3.2 What is deliberately not a lifecycle word

| not a word | what it actually is | where it lives |
|---|---|---|
| "archived" | **two different intentions wearing one word**: *this ended* (which writes `deprecated`/`superseded`) or *do not show me this* (which writes nothing at all — it is a reader's filter). The model makes the writer say which they meant. Retroactively filing a rejected thing must not erase *why* it ended | the `deprecated`/`superseded` words above — or, for the second intention, nowhere on the record at all: a filter the reader applies at read time (§2, question 4) |
| "in-progress", "completed" | position in a process | a pipeline stage |
| "outdated" | the past name of the Point "no longer counts" mechanism | the `deprecated`/`superseded` words above |
| "blocked", "paused", "waiting" | an interruption, not an ending | a stage in the `suspended` slot — and for a **goal**, `blocked` is also one of its `goalState` values. The two do not collide: the stage is read by its **slot**, never its name (§4.1) |
| "met", "unmet" | how the **goal** worked out | `goalState` — which is meaningful only on `goal`/`target`/`strategy`/`plan`. On any other kind the word is progress, so it becomes a pipeline stage: `packs/venture`'s `condition` (`statuses: [unmet, met]`) is not a goal, and its words are stages |
| "abandoned", "dropped" | the **verb** for ending with nothing replacing it | the `deprecated` word above. `goalState` echoes it as `abandoned`, and §5 makes that echo carry the lifecycle's rules rather than its own |

---

## 4. The pipeline

A pipeline says where a thing is in a process. It is **not** a lifecycle: a stage in `entry`, `active` or `suspended` says *how far along*, never *whether the commitment still stands*. The one exception is position: a `terminal` slot — or the durable `isClosed` marker — is an ending (§6), and that is exactly why the slot exists rather than a stage name.

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

### 4.3 Why a goal's outcome is not a stage

The position axis is the obvious home for "how is the goal doing", and it is the wrong one. Two reasons, one mechanical and one about meaning.

**Mechanical: an achievement must be revocable, and an ending must not be.** The value of the `terminal` slot is that it latches — `isClosed` makes an ending durable, so re-resolving a slot can never resurrect a thing (§6). But a goal can be met and then stop being met. A hygiene goal — uptime, a churn ceiling, "no P1 incidents" — is met today and unmet tomorrow, and the flipping *is* its content: it is a standing condition, not a hurdle cleared once. Put the outcome on the position axis and both choices fail. Make the stage non-terminal and nothing on that axis ever ends, which is not using the slot model at all. Make it terminal and a goal that was ever met can never be unmet again, which is exactly wrong for the kind of goal a business actually runs.

**Meaning: a stage is moved; an outcome is concluded.** Somebody advances a stage — "this is in review" — and nothing outside the record has to agree. An outcome is a claim about the world that evidence settles: `met` and `unmet` follow from the goal's readings against its target (the comparison is #11822's), and `blocked` from a dependency that either holds or does not. If `met` were a stage, moving a goal into it would be indistinguishable from a measurement, which is the failure the measurement model exists to prevent.

Two consequences follow. The six `goalState` values are **not one path**: `met`, `unmet`, `blocked` and `active` classify the present and can change back, while `superseded` and `abandoned` end the commitment — which is why those two also set `status` in the same write (§5). And outcome and pursuit are **orthogonal**: a pipeline on a goal kind says how the *pursuit* is going, a goal in delivery can be `unmet`, and a goal that has barely started can already be `met`.

---

## 5. The data model

**Three state fields** — `status`, the pipeline stage (`pipelineStageId` + `pipelineSlot`, with `pipelineOutcome`, `pipelineStageSince`, `pipelineStageKey` and `isClosed` — the six shipped `STAGE_PROPS`, `pipeline.py:65-69`), and `goalState` — **plus the successor bookkeeping that the `status` word obliges when it is `superseded`.** No others, and the pipeline field names are the shipped ones (#2792) rather than new spellings.

| field | lives on | values | written by |
|---|---|---|---|
| `status` | every **knowledge kind** (Point, Object, Subject, Source) | `draft` · `live` · `deprecated` · `superseded` | a verb (§3.1) |
| `supersededBy`, `supersededAt` | **Object and Subject only** — a Point names its successor by the `CORRECTS` edge, not by a property (`ONTOLOGY.md` §4.7: a Point carries `status='superseded'` and has no `supersededAt`) | the successor's identity + when | the replace verb, in the same write as `superseded` |
| `pipelineStageId` (+ `pipelineSlot`) | kinds whose pack declares a pipeline | a stage `id` from that kind's declaration | the stage-advance verb, or an event the pack folds onto the stage |
| `pipelineStageSince`, `pipelineStageKey` | same | when the current stage was entered; which pipeline it belongs to (`current_stage` returns it as `pipelineKey` — the **pipeline** key, not the composite `stageKey` of the `:PipelineStage` node; read key at `pipeline.py:76`, applied in `current_stage` at `:425-429`) | the stage-advance write |
| `pipelineOutcome`, `isClosed` | same | `pipelineOutcome` is the `outcome` of the terminal stage reached; `isClosed` is a **boolean durable marker** — persisted from the terminal stage on every stage move (`pipeline.py:958-959`) **and** independently writable through the generic entity surface, so a reader treats a node as closed if **either** the marker is set **or** the resolved stage is terminal (`pipeline.py:940-941`); the marker being set is what makes the closing durable, so a slot reclassification cannot reopen it, while a terminal slot still closes a node whose marker was never set | the stage-advance write (which sets `isClosed = stage.is_terminal`), and the generic entity surface (`create_object(..., isClosed=True)`, `update_entity(id, isClosed=True)`), plus a repair verb that #2728 plans rather than ships |
| `goalState` | goals | `met` · `unmet` · `blocked` · `abandoned` · `active` · `superseded` | the goal's own writer for `met`/`unmet`/`blocked`/`active`; a lifecycle verb for the two endings, which also sets `status` (the alignment below) |

`supersededBy`/`supersededAt` are not a fourth kind of state — they are what the `status` word obliges you to record. They bind to **`status`**, not to `goalState`, because the carriers are gated to Object and Subject (`sdk.py`'s `_LIFECYCLE_CARRIER_LABELS`): a goal that is an **Object** is the case those carriers serve, and a goal that is a **Point** — the `goal`/`target`/`strategy`/`plan` pointKinds (`ONTOLOGY.md` §4.1) — names its successor by the `CORRECTS` edge as any Point does, so neither kind's successor bookkeeping is a field of `goalState`, and `sdk.py` refuses a lifecycle carrier on a node whose **resulting** status is not `superseded` — the status it reads is the one supplied in the write, else the one already stored, so adding a stamp to an already-superseded node stays writable. The carriers are bookkeeping, not a fourth field of state. **A Source has no carrier at all**: `_LIFECYCLE_CARRIER_LABELS` excludes it, the `CORRECTS` edge is Point-only, and `docs/ONTOLOGY.md:803` records Source supersession as *unreachable* — so `superseded` is not offered to a Source by this model until a successor route exists, and a Source ends by `deprecated` (§3).

**A goal's two ending words are the lifecycle's endings, spelled on the achievement axis.** `superseded` and `abandoned` mean the goal is no longer a pursued commitment (`derived_invalidation.RETIRED_GOAL_STATES`); the other four — `met`, `unmet`, `blocked`, `active` — describe a *live* commitment. The two endings are not a second mechanism, and this model aligns them with the lifecycle's terminology and its rules rather than letting them grow their own:

| alignment | the rule |
|---|---|
| **One meaning** | `abandoned` **is** `deprecated` for a goal — ended, and nothing replaced it. `superseded` is `superseded` on both axes. One event, one meaning. `superseded` keeps its spelling on both axes; `abandoned` is `deprecated`'s meaning under another name. |
| **One source of truth** | `status` ends the goal. `goalState` never ends it alone: the write that sets either ending also sets `status` to `deprecated` (for `abandoned`) or `superseded` (with its successor), and the model **refuses** a write where the two disagree — in either direction, so a goal cannot be ended on one field and left running on the other. |
| **Same successor obligation** | A superseded goal names its successor exactly as any other ending does — `supersededBy`/`supersededAt` on an Object goal, the `CORRECTS` edge on a Point goal (`ONTOLOGY.md` §4.7). Never a property of `goalState`. |
| **Same transitions, same latch** | An ending is final, with the single measured exception that an Object/Subject write clearing every carrier can un-supersede (`sdk.py:49143-49147`, #12786); a Point goal has no such door. That door is a `status` mechanism and clears no `goalState` (`goalState` is vocabulary-checked only, `sdk.py:7742-7748`), so on a goal it must lift `goalState` off its ending in the same write — the "One source of truth" row above refuses the write otherwise, and that refusal is the row's, not a new rule. So a goal at `abandoned` cannot return to `active`, and `goalState` has no transition table of its own — the lifecycle's applies. |
| **Same reader** | No reader asks `goalState` for currency **except** `derived_invalidation`'s retirement arm (`derived_invalidation.py:385`, which returns `its goalState is …` as the **not-live reason** — it must keep doing so until the legacy split rows it exists for are migrated): the shared predicate (§6) reads its **four** inputs — `status` (plus any declared read delta), a stage in the `terminal` slot, `isClosed`, and the legacy `outdated` flag. |
| **Same writers** | The judgement cache writes only `met`/`unmet` (`goal_judgement.GOAL_STATE_CACHE`); `blocked` and `active` are stated by a person; the two endings arrive with a lifecycle verb (§3.1). |

**This alignment is a live defect, not a tidiness rule.** Nothing cross-checks the two fields today — `sdk.py:7742-7748` validates `goalState`'s vocabulary and never its consistency with `status` — while `derived_invalidation.py:385` retires a goal's caches on `goalState in RETIRED_GOAL_STATES`. So a goal written `goalState='abandoned'` with `status='live'` is **retired to invalidation and current to every reader**: the "dead to one surface, alive to another" shape §1 names, still in the graph for these two words.

`guideline` leaves this set. Whether a goal is directional — whether it carries a pass/fail at all — is a question about **priority**, not about outcome, and it was never an achievement: a goal does not "work out" as a guideline. Priority is out of scope here and needs its own design (§9).

`superseded` is the one **spelling** that carries the same meaning on two axes, and that is correct rather than a collision: on both, it means *replaced by a named successor*. What differs is **what** was replaced — a claim, or a commitment — and saying which is the axis's job. If the two ever come to mean different things, that is when one of them must be renamed. The `abandoned`/`deprecated` pair is the same meaning under two spellings, which is why the alignment above pins them together instead of letting them drift into two words.

`blocked` and `active` are values that appear on two axes as well, and neither is the `superseded` case. `active` names a **slot role** (§4) and a `goalState` value; `blocked` is only ever a stage *name* and a `goalState` value. They cannot collide because **the slot field is its own namespace**, distinct from stage `id`, `label` and `order`: a reader resolves a stage by its `slot` (§4.1), never by any of the stage's names. `superseded` is the single spelling that has to be reconciled across two **fields a reader reads**, which is why it needs the explanation above; `abandoned`/`deprecated` needs the same pinning across two spellings, and the alignment table above is where it gets it.

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
- **Position** answers it too — by the object's **closed marker** *or* a stage in the `terminal` slot, and the marker is the half that makes the ending **durable**, so re-resolving the slot alone can never resurrect an object the graph records as closed. `pipeline.py:924-941` records that the durable marker is `isClosed` and that the two must **agree**: a pack release that reclassifies the stage an object sits in from `terminal` to an active slot takes effect immediately, yet must not resurrect an object the graph records as closed; and an object closed with **no** resolvable stage stays closed, because `isClosed` is also written through the generic entity surface (`create_object(..., isClosed=True)`, `update_entity(id, isClosed=True)`), and a repair verb that #2728 plans rather than ships would also write it. Both shapes are pinned — `tests/test_pipeline_2792.py::test_a_slot_reclassification_cannot_resurrect_a_closed_object` (closed while sitting in an `active` slot) and `::test_a_closed_object_with_no_stage_is_not_resurrected`.
- Nothing else in the model does. A stage in `active` or `suspended` is not *by itself* an ending (an object already marked closed is ended while sitting in one), and a **missing** stage is not an ending unless `isClosed` is set. The one implementation input outside these two axes **that the shared predicate reads** is the legacy `outdated` flag, which it must read (below) until it is migrated onto a word. One **reader** also reaches outside the predicate: `derived_invalidation`'s retirement arm reads a goal's `goalState` (`derived_invalidation.py:385`) until the rows written with `status` and `goalState` out of step are migrated (§5). That is a legacy arm in a reader, not a fifth input to the shared rule — the predicate does not read `goalState` — and it is retired with those rows.

The question is **"is this still served as current?"**, deliberately not "is this counted as a belief?". Those are different, and `draft` is the difference: it is **current** (not retired — it is served, and a reader renders it) yet **not in force** (inert for belief computation, excluded from EP factor extraction). A reader that needs the second answer asks a second question; no reader is allowed to answer "not current" as a shorthand for "not in force", because that is exactly how a word becomes dead to one surface and alive to another.

**The implementation already exists. What do not exist yet are the position axis's two inputs.** `tortoise/live.py` declares the one shared predicate (#2901 / #2422) — `is_terminal_status` / `_terminal_excluded` — and its own header records the rule that keeps it one: *"NO reader re-spells the predicate … Do NOT re-declare a status set at a call site. A hand-written subset is how `outdated` was omitted from three reader filters … and a superseded/outdated claim was then served as the CURRENT statement."* It already takes the family as an input rather than a second implementation: `_terminal_excluded(excluded=…)` defaults to the Point vocabulary and the Object read surfaces pass their own (#3301), which is the delta seam in practice.

So the work is not to build a shared reader — that was built, and it is imported across most read surfaces, though not all: the four modules whose hand-written filters the preamble names still bypass it. The work is that **the predicate reads two sources and must read four inputs**: it answers from the terminal-status vocabulary **and** the legacy `outdated` flag (`_terminal_excluded` builds `(status_part AND coalesce(flag, false) = false)`, with `include_outdated_flag=False` for the Object family), while a thing ended by a `terminal` **slot** — or by the durable `isClosed` marker, which must agree with the slot (§6) — is invisible to it. Extending that shared predicate to read the position axis — the durable `isClosed` marker, which must agree with the resolved `terminal` slot (`pipeline.py:922-941`) — is the whole change. It is small *because* the implementation and its delta seam already exist — the same seam the `outdated` flag rides — but it is **not one function**: the Cypher twin is, while the Python mirror `is_terminal_status(status, outdated)` takes both inputs as arguments, so its 22 call sites across ten modules (`why`, `ranking`, `search_engine`, `decide_wiring`, `audit`, `ask_lane`, `commit_ops`, `sdk`, `time_aware`, `hosted_api` — most reading the pair straight out of a row, e.g. `ranking.py:1075`'s `is_terminal_status(row[7], bool(row[8]))`, and `hosted_api.py:22721`/`:22734` reaching it through an aliased import) must each fetch the stage they now have to answer with. `pipelineSlot` is read by no module but `pipeline.py` today.

The rule reads the **base** set plus any **delta** the family declares — it is not one collapsed list. Two families legitimately answer different questions, and #11824 recorded the shape this model must keep: *"one base set plus named, declared deltas"*, because *"collapsing them would be a reopen"*. `commit_ops.py:61-65` says the same — *"Do not unify the two blindly: they answer different questions"* — and `assembly.py`'s resolver narrowing is the live example: it declares a **narrower** set than the read-surface vocabulary on purpose, so that a superseded/deprecated/archived Object still **resolves** to render its own state, and its own comment warns that re-pointing it at the canonical set is what stops the current-state question firing. A delta is therefore **declared and named**, never a divergence discovered later.

A delta is a **read-side** declaration: the set of words a family's readers treat as **ended**. It may **widen** the base (adding a legacy word its stored data still contains) or **narrow** it (withholding a base ending from a particular reader, as the resolver does) — the direction is part of the declaration, not an accident of it. It **never adds a write word**: the write vocabulary stays one closed set of four (§3), so a delta cannot mint a state no verb can produce.

---

## 7. What this model guarantees

| guarantee | why it follows |
|---|---|
| **A word means one thing.** | One closed lifecycle set of four is the only **write** vocabulary, plus a named **read** delta per family where one is genuinely needed (§6). The same word cannot be an ending in one family and a current state in another without that delta declaring it. |
| **Every ending is real.** | An ending is a lifecycle word, a `terminal` slot, or the object's durable `isClosed` marker (which ends a closed object that has no resolvable stage) — all three are declarations, not names a reader has to recognise. |
| **Customising names cannot break readers.** | Readers read slots and words, never stage names. |
| **Adding a domain does not touch the engine.** | A new domain is a declared pipeline, not new code. |
| **The vocabularies stay small.** | Four lifecycle words. Verbs do not grow them. A second question gets a second field, not a bigger list. |
| **Nothing is lost when a thing is filed away.** | Hiding is a reader's filter, so the record keeps the word that says *why* it ended — nothing is erased to achieve invisibility. |
| **A second axis is the answer to a second question.** | `goalState` already proves it: a goal is `live` and `unmet` at the same time, and neither field overwrites the other. |

---

## 8. Requirements this model must not break

These are recorded decisions, not preferences. The model above is designed to sit *inside* them.

| requirement | source | how this model respects it |
|---|---|---|
| **An event has no lifecycle.** An event is a dated fact; a later event does not rewrite an earlier one. | #11825 / #12108 | Events take no lifecycle word. If an event must be withdrawn, that is a claim about it, not a state on it. |
| **Achievement is its own axis.** Goal outcome is not promotion. | #7856 | `goalState` keeps its own closed set; its **outcome** values (`met`/`unmet`/`blocked`/`active`) are never derived from `status` — the two **endings** are, because `status` is their source of truth (§5). For the two retirement words the coupling is **not** one-way: an ending on either field requires the matching ending on the other, and the model refuses a write that ends the goal on one axis while leaving it running on the other (§5) — because the successor carriers and the reader rule are bound to `status`. For the other four words the axes stay independent, and `goalState` never drives currency. |
| **A pack owns its kind's words.** | #2729; *Foreclosed* in `EXTRACTOR-V4-ARCHITECTURE.md` ("pack lifecycles own their words") | Packs declare stages; the engine never imposes stage names. A pack's declared status words are not refused — they become that kind's stage names (§4.2), so the declaration is preserved and only its axis changes. ⚠️ This is a **partial reversal** of the recorded position, not compliance with it: a pack kind's own `status` is now constrained to the four-word base, which is why the change awaits the owner's approval of PR #12895 — see `OVERRIDES` (2). |
| **One base set, plus named declared deltas — never one collapsed set.** | #11824's contradiction test; `commit_ops.py:61-65` "Do not unify the two blindly: they answer different questions"; the `assembly.py` resolver narrowing; the Object/Point vocabulary divergence in #3308 / PR #6955 | The four words are the **base** and the only write vocabulary. A family that must answer a different question declares its delta explicitly and names it; a delta is never a quiet divergence, and never a list to be re-pointed at the base. |
| **The claim family's "no longer counts" flag is load-bearing.** | #2498 | The flag's *effect* is preserved as the `deprecated`/`superseded` words; the mechanism is not removed by fiat. |
| **What a reader hides is wider than what a writer may create.** | #2977 | Readers must still be able to treat a word as dead that no verb currently produces — the net stays wider than the door. |
| **A single shared list cannot cover pack words.** | #2729, and Foreclosed in `EXTRACTOR-V4-ARCHITECTURE.md` ("pack lifecycles own their words") | The shared **stage** list is the shipped four-slot vocabulary (#2792), and it covers only *roles*, never names — so no pack word is refused. Pack words live on the position axis. ⚠️ Same **partial reversal** as the row above: the *status* axis is narrowed to four words, and the pack's declaration survives only on the position axis (see `OVERRIDES` (2)). |
| **A word with no reader is not added.** | the withdrawal of `stale`, #11824 | This model introduces no new word and removes **five** — `retracted`, `outdated`, `archived`, `in_progress`, `completed` — leaving a base set of four. |
| **The built-in connector folds write `in_progress` / `completed` today.** | `status_folds.BUILTIN_CONNECTOR_FOLDS` (`:82-88`), owner-approved text at `ONTOLOGY.md:449` | Their targets move onto the position axis, so the removal step must declare the core default pipeline those folds land in (§4.2). **Neither closing fold may write a lifecycle ending**: a `deprecated` ending is final and no verb clears it — `sdk.py:4639-4653` is the Point arm, and `:49529-49544` refuses leaving a dead state, the one recoverable exception being an Object/Subject `superseded` whose write clears every carrier (`:49143-49147`, #12786) — and a closed issue has no successor, so its fold target would be `deprecated`, which is not clearable. An old issue closed at import would therefore be retired with no way back. The two closing folds therefore land on a **terminal stage**, which `github.issue.reopened` clears with `advance_stage(..., reopen=True)` (`pipeline.py:805`, `:832`) — that clears a terminal-stage guard, never a `status` word. Of the three opening folds, `pm:cardCreated` and `github.issue.open` fire when a card is *created*, and `github.issue.reopened` fires on closed→open (`github_map.py:299-302`); a first ingest of an already-closed issue mints `github.issue.closed`, not `reopened` (`:294-297`). |
| **A pack's `folds` can only target a declared `status`.** | `status_folds.py:215-219`, `:294-297`, `VALID_FOLD_KEYS` (`:100`); live examples `packs/venture/manifest.yaml:201-204`, `:225-228` | The word move is not one-sided: a kind that declares `folds` without `statuses` is **refused today**, and a fold target must be a member of that list. Each pack fold therefore needs a stage target in the same change — `disbursement → released` and `conditionMet → met` are the two live cases. |

---

## 9. Out of scope

Deliberately absent, and each has its own document:

- **How we get from today to this.** Migration, backfill and re-stamping.
- **Which stages each core kind's default pipeline ships with.** The model does not depend on those names; it depends only on the *shape* (§4.2), so this is a declaration to fill in, not a design question this document settles.
- **The implementation plan**, the code surfaces, and the test strategy.
- **Visibility/filing.** The rule is fixed — hiding is a filter at read time, per reader, never a field and never a word — but which surfaces expose the choice, and what each one hides by default, are not designed here.
- **Priority.** How much a goal matters, and whether it carries a pass/fail at all. This is where `guideline` went when it left `goalState`; it is a question about importance, not about outcome, and it needs its own design.
- **The deltas themselves.** §6 fixes the *shape* — one base set plus named, declared **read** deltas — but not each family's delta. Which delta a family needs, and what each contains, is part of #11824's ordered removal plan, not this document.

---

## 10. The model in one paragraph

Every **knowledge kind** — Point, Object, Subject and Source — has **one** lifecycle word from a closed set of four, and optionally a **position** in a pipeline its kind declares. Events have no lifecycle at all (§8), and operational node kinds keep their own vocabularies (`:PipelineStage`'s `active`/`retired`, `:Batch`'s batch states); they are not knowledge kinds and this model does not re-spell them. A family may declare a **read** delta over which words its readers treat as ended (§6), but never a new write word. The lifecycle word says whether the commitment still stands; a pipeline stage says how far along it is, **except** that a stage in the `terminal` slot is itself an ending. A goal's outcome is a third field of its own; visibility is a reader's filter and never a word. Every reader asks one question — *is this still served as current?* — and answers it from the lifecycle word (plus any declared read delta), from whether the stage sits in a `terminal` slot or the object's `isClosed` marker is set, and, while their legacy spellings last, from the `outdated` flag until it is migrated onto a word — while one reader, `derived_invalidation`'s retirement arm, still reads a **goal**'s two `goalState` retirement words **outside** the shared rule, until the rows carrying them out of step with `status` are migrated (§5). Words declare their meaning where they are declared, so a pack can invent any stage names it likes and nothing that reads the graph has to learn them.
