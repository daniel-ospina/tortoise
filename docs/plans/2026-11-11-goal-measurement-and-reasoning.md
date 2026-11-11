---
title: "Goal measurement and the reasoning loop — indicators, targets, readings, and what makes a change take effect"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-11
updated: 2026-11-11
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutObjects: tortoise-ontology, tortoise-ep
aboutSubjects: tortoise-value-layer, tortoise-reasoning
governingAgreement: "#7860"
---

# Goal measurement and the reasoning loop

**Issue of record:** #7860 · **Related:** #2782, #11822–#11827, #11926–#11928, #11081 (waves 1 and 6)

> This is the design of record for a capability, not a status report. It states what the loop must do,
> what each part does today (measured 2026-11-11), and the decisions that govern the build. The build
> record belongs in a changelog; the decisions live on **#7860**, which its 2026-11-11 decision record
> names as the **holding pen** — *"`docs/DECISION-LEDGER.md` does not exist yet (owner is setting it up
> 2026-11-12) … When the ledger lands, these entries lift into it verbatim"*. (The graph is not the
> destination either: writes are refused today, §9 Q1.)
>
> **Scope, so #7860 is not closed on this alone:** this is a capability design of record. It does **not**
> deliver #7860's `ONTOLOGY.md` indicator vocabulary — `grep -c -i '\bindicator\b' docs/ONTOLOGY.md` is
> still **0** at this head, so the issue's criteria 1 and 3 remain unmet and **#7860 stays open**.

---

## 1. The capability, in the owner's words

> *"we want readings to supersede old readings automatically and most importantly that they 'trigger' the
> graph so e.g. CI was above 10 means the goal was red and CI below 10 (new reading) then turns the goal
> green and hence maybe a supra-goal gains in confidence … That's the cybernetic mechanism (the 'Reasoning'
> part of our system)."*

And the generalisation that governs the design:

> *"changes in parts of the graph can affect lifecycles in others … with superseded points where adding a
> new data point should supersede the old one hence the old one should stop counting."*

The second sentence is the requirement. The first is one instance of it.

---

## 2. The four primitives

Every reaction to change in this system is one of four things. A "hybrid engine" is not a new kind of
engine — it is these four, kept separate, with one dependency direction.

| # | primitive | what it means | owner |
|---|---|---|---|
| 1 | **Detect** | something changed: a reading arrived, a target was revised, a reading was corrected | the write door |
| 2 | **Decide** | what state does that imply — met / not met / unknown | a declared decision table (§5) |
| 3 | **Replace** | the old state stops counting, the new one counts, the window is recorded | the engine, uniformly for all entity kinds |
| 4 | **Weigh** | how confident are we, given what still counts | EP (unchanged) |

Two requirements cut across all four: **survive rebuild** (the journal is the reconstruction source) and
**be explainable** ("why is this goal green?").

---

## 3. What exists today, capability by capability

Measured at `32c0f39d4b` (with the two `search_engine.py` citations re-anchored there; the rest hold at
`0bdb08dbdb` / `372f926cd9` too), 2026-11-11. ✅ exists · 🟡 partial or decided-but-unbuilt · ⛔ absent.

| # | capability | primitive | state | evidence |
|---|---|---|---|---|
| 1 | a reading records value + unit + source + as-of | detect | 🟡 | value + unit + as-of ✅ via `create_event` and the extras lane (`startedAt` is the as-of slot, `ONTOLOGY.md` §4.7); **`:Source` provenance ⛔** — `create_event` cannot set it and `link_source_to_event` is a second write (#11081 wave 5), though #7860's **D10** makes it mandatory for a machine reading |
| 2 | a reading is never mutated; a correction is a new reading that retires the old | replace | ⛔ | an `:Event` cannot be superseded (`LIFECYCLE_FANOUT_QUERY` is `MATCH (n:Point {id:$id})`, `live.py:386`) — and it **can** be marked: `update_entity(event, status=…)` is accepted and `ep.py:2128` **honours** it (the node is stripped from factors), while **no retrieval leg filters it** (`_STATUS_FILTERED_LABELS` omits `Event`) — **#11825** |
| 3 | a target states comparator + threshold (+ a due, **proposed — see §6**) | decide | ⛔ | no comparator field on any target — the shipped `portfolio:allocationTarget` (a `target` subclass, `packs/investment-portfolio-manager/manifest.yaml:60`) carries a declared percent value and **no comparison** |
| 4 | **compare the current reading to the target → a state** | decide | ⛔ | no goal-scoped comparator or threshold exists. The word appears in 7 lines under `tortoise/` — all unrelated commentary, on no goal or target (`rg -c '\bcomparator\b' tortoise/*.py tortoise/pi-hooks/*.ts`): tolerance policy `reconciliation.py:75`, search ordering `search_engine.py:4388`, NaN ordering `:5095`, provider AST `model_adapters.py:596`, stored-form drift `stored_form.py:19` and `:42`, and one test comment `tortoise/pi-hooks/tortoise-capture.test.ts:1827`. The nearest neighbour is `battery/report/classify.py:61-69` with `battery/config/thresholds.yaml` (`classification-delta`, `surfaced-rate`), which does compare a measured value against a declared bar — though for an eval cell, not a goal — and `tools/capstone_preregistration_check.py:419`'s `COMPARATORS` operator set, which reads a preregistration cell's threshold. **None exists in the graph or in `tortoise/`.** The one target still wired to a verdict is `~/.pi/agent/scripts/objective.py:3543` (`waiting < TARGET_PRS` — queue depth, named by the owner). The CI minute figure in the same file is **not** a comparator to cite as precedent: `:3527` prints `GUIDELINE_CI_MIN`, which the owner demoted from target to **guideline** on **#6792** and which `:3540-3542` says "must not be able to flip this word" — the demotion §5 and §6 rest on, and itself an illustration of why a target needs a declared comparator rather than a hardcoded constant |
| 5 | a state is a claim with a confidence **and** a validity window | replace + weigh | ✅ | `supersede_point`: contiguous `validFrom`/`validTo`, `expiredAt`, `CORRECTS` |
| 6 | when the state changes the old one **stops counting everywhere** | replace | ⛔ | search omits `Event` from `_STATUS_FILTERED_LABELS`; an Object supersede leaves operators attached and EP silently drops the factor (**#11825**, **#11826**) |
| 7 | the current state is what belief reads | weigh | ✅ | measured `0.347 → 0.639` on a goal **Object** with no stored confidence of its own, because the claim feeding it changed (the #7860 D12 ruling) |
| 8 | revising the **target** re-derives the state and retires the old | detect + decide | ⛔ | no invalidation path |
| 9 | changing the **definition** makes older readings non-comparable | detect | ⛔ | two readings under different definitions look like a trend |
| 10 | one indicator, several targets; some with none | decide | 🟡 | decided (D4), no code |
| 11 | achievement is per-goal; **no roll-up** | decide | 🟡 | decided (D5 on #7860), nothing enforces it |
| 12 | a goal's lifecycle affects its targets and sub-goals | replace | ⛔ | no propagation of a goal's own retirement |
| 13 | deeper cascades converge | weigh | 🟡 | 6 levels then a **silent** stop (**#11823**) |
| 14 | all of it survives a rebuild | cross-cutting (replay) | 🟡 | journal uniform for supersede; read gates are not (**#11824**) |
| 15 | "why is this goal green?" | cross-cutting (explain) | 🟡 | `why.py`; blind to Object/Event endpoints |

**Read the "state" column, not the rows:** **no primitive is fully healthy.** `weigh` is the only one
with no *absent* capability, and it carries one bounded partial — row 13, where a shallow pass reads as
converged (#11823, step 4). `decide` does not exist, `detect` has two capabilities that do not exist
(rows 8 and 9; row 8 is shared with `decide`), and
`replace` is the largest source of inconsistency in the codebase (`replace`: 11
terminal-vocabulary declarations, 5 fold implementations, 2 replay engines behind 4 entrypoints, and 621
test functions across 167 files pinning the asymmetry — #11824's body states these as 11 / 5 / 4 replay
engines / 632 tests in 166 files, and its own verification comment **re-measured and corrected** them: 11
reproduces exactly, 5 as 5 semantic models / 6 functions, 4 engines as **2 engines / 4 entrypoints**, and
`632 / 166` as **621 / 167**. The corrected set is used here; quote the verification comment, not the
issue body, which still carries the superseded numbers). That is why this
question has repeatedly collapsed into "how does EP get triggered" — EP is the only part that already
works — but note that `weigh` is not unblemished either: row 13 is its own bounded partial.

**Rows 10 and 11 have no step of their own.** Both are 🟡 *decided, nothing enforces it* — row 10 is
D4's 0..n shape, which is the shape step 1's table reads, and row 11 is D5's no-roll-up rule, which is a
constraint on whatever computes achievement rather than a build item. Every ⛔ row does have a step; these
two do not, so a builder working §7 in order will not be told how to close them, and should not read their
🟡 as scheduled.

---

## 4. The load-bearing distinction: readings append, states replace

The owner's sentence is exactly right **for states** and wrong for measurements, and the difference is
what the whole loop depends on.

- **A measurement is true for its moment.** Last week's 12.4 minutes did not stop being true. Readings
  therefore **append**: a reading's *value* is never mutated, and a reading is never replaced in place.
  (Its lifecycle **mark** is a different matter — it can be set today by `update_entity(event, status=…)`
  and no read leg filters it: that is the gap in §3 row 2 / **#11825**, not the design.) A *correction* is a new
  reading that retires the old — a different mutation from a state being replaced, and **not yet built**
  (§3 row 2, **#11825**) — which is why #11825/#11826 sit in step 0 rather than after it.
- **A state is true only until replaced.** "The CI sub-goal is met" stops counting the moment it is not.
  States therefore **replace**, with a validity window — the window `supersede_point` writes: *the
  **contiguous Graphiti (Zep) window intent: the old fact stops being true when the new one starts being
  true*** (`ONTOLOGY.md` §4.7, quoted verbatim). Note what that section itself pins: exact contiguity
  needs a **dated** successor (`validFrom`), an undated one makes the windows **overlap**, and which
  reading of the closed interval is canonical is an open decision `ONTOLOGY.md` §4.7 still anchors at
  **#7818** — although #7818 is **CLOSED** with no live tracker, a false attribution #11824 records — so
  the closed-vs-half-open question remains undecided. That is therefore the window's **intent**, not a
  claim that every supersede lands on a shared instant.

Two independent bodies of practice draw the same line:

- **Wikidata ranks** — a value *correct for its period* gets start/end-time qualifiers; **deprecation is
  reserved for values that were wrong**. Correct-for-its-period is not deprecated.
- **SCD Type 2 / dbt snapshots** — every version is kept; **current = valid_to IS NULL**; a
  point-in-time join is `BETWEEN valid_from AND valid_to`. Thirty years of the same mechanism.

**Consequence:** "the old one stops counting" is the **replace** primitive, and it belongs to states and
to corrected readings — not to readings themselves. The two are different objects with different
lifecycles.

---

## 5. Decision: the comparison is a declared decision table; replace and weigh are engine-owned

### The question

Where does the rule *"what state does this reading imply given this target"* live?

### The options and the evidence

| option | shape | evidence |
|---|---|---|
| **A** hardcode the comparator | one function per use case | cheapest to write, and every new use case adds a function. The ECA literature's own caution lands here: undeclared effects force a case analysis to be redone per situation (*"does it work if someone is hired? fired? changes department? department deleted? … eventually more triggers are needed"*) |
| **B** declare the decision; engine owns the plumbing | a **complete decision table** (reading × target → state), consumed by one engine step | **OMG DMN** (Decision Model and Notation)'s discipline is exactly this: *"the decision table contains all (and only) the inputs required to determine the output"* and *"a complete table contains all possible combinations of input values"*. Completeness is reviewable; a hardcoded function's missing case is invisible |
| **C** a general rule engine (ECA/triggers) | arbitrary conditions and arbitrary actions, cascading | the classic warning, verbatim: *"very powerful mechanism: small statement => massive behavior changes … rope for programmer … Make sure indefinite triggering or undesired cascading triggering cannot happen. Avoid using triggers unless really needed"* |

### The decision (good > easy)

**The decision is declared; the plumbing is not.**

1. **Declared:** the comparison, as a **complete decision table** — the finite set of
   (comparison, threshold, reading) cases and the state each yields. Finite, enumerable, reviewable, and
   testable for completeness. That is `decide`.
2. **Engine-owned and uniform:** what happens *after* a state is decided — the previous state stops
   counting, the new one counts with a window, and belief re-weighs. **No kind declares this**; declaring
   it per kind is how you get cascading triggers.
3. **No general rule engine.** No arbitrary conditions, no arbitrary actions, no chains of effects.

**Why not A:** a hardcoded comparator's *missing* case is invisible, and this design already depends on
completeness — "target revised" and "definition changed" (capabilities 8 and 9) are cases a table forces
you to enumerate and a function lets you forget.

**Why not C:** the ECA warning is not theoretical — it is the failure mode this repo has already lived
through once, as five different supersession implementations that nobody chose (§3's summary paragraph, which
carries #11824's re-measured figures — *"5 as 5 semantic models / 6 functions"* — rather than the issue
body's superseded count).

**OVERRIDES:** the industry default of a general rule/trigger engine for declarative business logic
(active databases, workflow engines) — we declare only the **decision**, because the literature that
built those engines also records their failure mode ("rope for programmer"), and because the effect half
must stay uniform or the loop inherits the supersession blind spots.

**Seam this reuses:** pack manifests already declare a change→state effect —
`kindDefs.<kind>.folds: [{event: …, status: …}]` with `statuses` (#2729, `status_folds.py`). That surface
is currently scoped to status words and pack kinds; the generalisation is *"a kind declares what state a
change produces"*, read by one engine step. **It has no home for the general case yet.**

---

## 6. The four shapes, and why each

| entity | what it is | why this shape |
|---|---|---|
| `goal` | a qualitative commitment, with `goalState` (#7856) | the objective is the owner's, never a number |
| `indicator` | the **definition**: what is measured, unit, direction of good, source, cadence | convergence: SRE's SLI, QUDT's quantity-kind (*"length is a quantity kind while the height of a rocket is a specific quantity of length"*), FHIR `ObservationDefinition` vs `Observation` |
| `target` | comparator + threshold(s) (**+ `due`, proposed**), **linked to both** the goal and the indicator, 0..n | SRE: *"one SLI can feed more than one SLO (two marks on the same ruler). Some SLIs never get an SLO"* — only a reified target allows 0..n *and* two targets on one goal. The field list is #7860's **Layer 2** (`comparator`, `threshold`/`range`, `baseline`, `window`, `asOf`, `direction`, **plus a validity window (§4.7)** — seven fields), with `asOf` retired by **#11827**. ⚠️ `due` is **not** in that list — it enters only from the FHIR `Goal.target` comparables — so it is a **proposed field awaiting a decision**, not the decided shape. (D4 rules only that a target is a separate Object linked to both; it names no fields) |
| reading | a `:Event` carrying the value + verbatim + `startedAt` | one reading = one value (history is table stakes in every comparable — ClearPoint sells "full history retained — no overwritten cells"); a reading carries no confidence and no NAND-ability |

**Where the `indicator` kind lives — decided, not open.** #7860 records the owner decision (2026-10-13)
*"promote `indicator` to a core Object kind — option (a)"*, carried as that issue's **D1**: *"`indicator` is
a core Object kind; packs subclass it."* Today the kind exists only as a `packs/dev` objectKind scoped to
DORA/DRIVE metrics — which is exactly why a product-strategy goal cannot reach it, the issue's acceptance
criterion 3 — and the promotion is **not** a one-line doc edit: it collides with the `dev` pack. The table
above describes the *shape*; the *home* is core.

**On `direction of good`.** The indicator row copies #7860's **D2** (*"what is measured, its unit, direction
of good, source, cadence"*). That sits in tension with the same issue's Layer-1 prose (*"No evaluative
orientation, no pass/fail"* (#6792) — *"metric polarity belongs to the target"*) and with its Layer 2,
which lists `direction` as a **target** field. **D2 is the numbered decision and carries its own
`OVERRIDES:` line, so it is the record this design follows**; the prose is not re-decided here. A builder
must not treat `direction` as belonging to both, or as absent from both — reconcile the two with the owner
before the table is implemented.

And the **state** those produce: a `statement` Point marked `claimClass: 'derived'`, superseded when it
flips, wired `IMPL direction='->'` into the goal. That shape is chosen because it is the only one that
gets a persistent prior (`set_point_baseline` is Point-only, #9063), automatic dirty marking
(`_mark_dirty`'s reverse BFS is Point-seeded), and automatic supersession — **without reopening #9063**.

`goalState` is a **cache of the state**, never an input to belief — and the two are disjoint with
nothing reconciling them: `goalState` is a plain field, while a goal **Object** has no **stored prior**
of its own (`set_point_baseline` is Point-only, #9063) — its `confidence` is only the posterior EP writes
onto it from linked claims (the flush sweeps `EPISTEMIC_LABELS`, which includes `Object`) — so the cached
state and the propagated belief can disagree silently. The divergence is asserted on #7860; **no
measurement artifact for it is recorded in the tree**, so it is the first thing to instrument rather than
a measured fact.

**Reconciled with the placement ruling (owner, 2026-10-06).** `docs/architecture/STORAGE-ARCHITECTURE.md`
ruled that *a state/numeric value is a STRUCTURAL FIELD ON AN ENTITY, not a `Point`* — *"a belief can be
argued with, an attribute cannot"*. It also records a **narrower, separate** exception — *"where a
sentence ASSERTS a figure … that assertion is a belief and may be a `Point`"*, which it calls *"the
separate, smaller prose question, not this placement"* — so that carve-out is not what this design rests
on, and is not invoked here. The design sits on the ruling's own line instead: the **reading's value** is
a structural field on the `:Event` (no confidence, no NAND-ability, no embedding), while the **derived
state** — *"the CI sub-goal is met"* — is an assertion the machine must be able to argue with, so it is
a claim; #7860's **D11** is that decision (*the judgement is a `statement` Point, `claimClass:
'derived'`*), not a re-reading of the placement ruling. Nothing is reified into a node that the ruling
puts in a field.

---

## 7. Sequencing

| step | what | why here |
|---|---|---|
| **0** | the **replace** primitive works uniformly across entity kinds (**#11824**, **#11825**, **#11826**) | everything above it inherits its blind spots. This is a **prerequisite of the reasoning mechanism**, not ontology hygiene. Note #11824's decision **was** recorded (owner, 2026-11-11 — *"the target model, the order, and four corrections"*), so step 0 is dispatchable; that decision's own next step is a scoping issue per change, then implementation lanes |
| **1** | the comparison as a declared decision table (**#11822**) | needs only the reading, the target, and a place to write the state. ⚠️ **Step 1's gate is also closed:** #7860's **D13 — "what triggers the loop?" — is not ruled** (it sits under that issue's *"OPEN — the owner has two questions outstanding"* with a recommendation only), and **#11822's scope waits on that ruling**. Do not dispatch #11822 on the strength of §5 alone |
| **2** | invalidation when inputs change — target revised, definition changed, goal retired (**capabilities 8/9/12** — **#11926**) | the cases a complete table forces you to enumerate |
| **3** | the effect-declaration surface generalised from `folds` (**§5 seam** — **#11927**) | only after 0, or a declared effect cannot retire the old state reliably |
| **4** | deeper cascades without the silent stop (**#11823**) | measured: 6 levels then stop; 4-level cascades already work |
| **5** | the explain path reaches Object/Event endpoints (**capability 15** — **#11928**) | usability, not correctness |

**Nothing here is blocked on the value shape** (#2782) except the reading's field list, which that lane
owns. The value's **placement** is not open: it is the owner's 2026-10-06 ruling in
`docs/architecture/STORAGE-ARCHITECTURE.md` (structural field on the entity), and §6 shows the design
sitting on it — #2782 is the money-typed instance of that ruling, not an alternative to it.

---

## 8. Out of scope

- **Being a BI engine.** Aggregation — grain, group-by, joins over a warehouse — is a different product.
  The seam is that an indicator names where its reading comes from, and readings are pushed in.
- **The analytics ingestion path** (an event write door; `:Source` provenance for machine readings) —
  wave 5 on #11081, deliberately not prioritised (#11081's own wave 6 names it as *wave 5*).
- **Causal inference and simulation.** Gartner's decision-intelligence platforms do Monte Carlo and
  counterfactuals and are criticised for it (*model-rich, adoption-poor*). Our `IMPL`/`NAND` +
  mitigations are *attribution with confidence*, not arithmetic decomposition; that difference is a
  feature.
- **Roll-up.** Achievement is computed per goal; a sub-goal never gates its parent (D5, recorded on #7860).

## 9. Open

| id | question | recommendation |
|---|---|---|
| Q1 | the graph is the decision record, but writes are refused — `Team points limit reached (25000)` | raise the cap or prune (owner call) |
| Q2 | does this sequencing (0 → 5) hold? | yes: step 0 is a prerequisite, not a side quest |
| — | D12 (confidence on non-claims) | **ruled: no** — recorded on #7860 |
| — | D13 (what triggers the loop?) | **recommended on #7860 — NOT yet ruled**, and it sits under that issue's *"OPEN — the owner has two questions outstanding"*: *the judgement write is the trigger* (per D11), the only option where the trigger and the answer are the same write. **#11822's scope waits on the owner ruling this**, so the gate is still closed: #11822 must not be dispatched as if the trigger were settled. Marking that write does **not** make a deeper cascade converge (#11823) — this is the trigger, not the convergence fix |
| — | where does the **effect** rule live? | **decided here: §5** (declared decision table; engine-owned effect; no general rule engine) — a design decision of this doc, relayed to #7860 |
| O1 | does this doc deliver #7860's acceptance criteria? | **No.** It is a capability design of record: `grep -c -i '\bindicator\b' docs/ONTOLOGY.md` is still **0**, so criteria 1 and 3 remain unmet and **#7860 stays open** |
| Q3 | the **§5** quotations (the ECA "rope for programmer" / "hired? fired? …" and the OMG DMN passages) name no pinned source — document, version or link. (The §6 SRE / QUDT / FHIR / ClearPoint quotations are **not** in this class: they are reproduced verbatim from #7860's D2/D3/D4.) | pin the §5 quotations before this doc is cited as evidence — they are reproduced from the design pass's research, not from a tracked document, and grep confirms neither string exists on #7860. One earlier quotation was withdrawn as unpinnable rather than left looking sourced |
