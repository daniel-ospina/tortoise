# EP across entity kinds — audit of `docs/ONTOLOGY.md` against the engine

**Issued with this ask** (quoted verbatim from the brief that commissioned this audit — **not** from the body of #11824, where a search finds no such sentence): *"make sure ontology.md is aligned with the way EP works across entities."* The brief also directed that the proposal be posted as a comment on **#11824**; that issue is **closed**, and is the tracker this work was pointed at, not the issue the audit was filed from.
**Branch:** `audit/ep-across-entities`
**Code defect surfaced:** filed as **#13450** (see §7) — the audit found one arm where the engine does not meet the doc's own rule.

**Status:** **PROPOSAL — `docs/ONTOLOGY.md` is NOT edited by this change.**

`docs/ONTOLOGY.md` carries:

> CRITICAL RULE: no modification to this file is allowed without explicit approval of the exact changes by Daniel Ospina.

So this document is the deliverable: the audit, the measured evidence, and the **verbatim replacement text** for the owner to approve or reject, one item at a time. Applying an approved item is a separate change.

The doc's own framing governs how each item is judged:

> **⭐ If this document and the code disagree, THIS DOCUMENT IS RIGHT and the code has a defect.** The single exception is a *factual* error — the model itself being wrong — which is corrected here and recorded in `ONTOLOGY-CHANGELOG.md`.

So an item below is one of three things, and each is labelled: **(a) stale** — the doc describes a mechanism that has since changed, so the doc is a *factual* gap; **(b) target** — the doc states a rule the engine does not meet, so the **code** is the defect and the text may stay; **(c) factual error** — the model itself says a thing that is not true of any version of the engine.

---

## 1. Method — what is read from code, what is measured

| | |
|---|---|
| **Read from code** | `tortoise/live.py` (the currency predicate, the epistemic-label set, the direction vocabulary), `tortoise/ep.py` (factor assembly, the affected set, cache load/write, contested scan), `tortoise/quadrature.py` (the three potentials), `tortoise/weights.py` (operator weight, mitigation dampening), `tortoise/ranking.py` (`fetch_entity_signals`), `tortoise/search_engine.py` (`belief_value`), `tortoise/derived_invalidation.py` (the goal retirement arm), `tortoise/sdk.py` (`_hydrate_evidence`, `set_point_baseline`, `_apply_source_inheritance`, `create_direct_edge`, `create_operator`, `GOAL_STATE_VALUES` / `GOAL_ENDING_STATES`, `compute_reputation`), `tortoise/pipeline.py` + `tortoise/lifecycle_vocabulary.py` (the operational labels) |
| **Measured** | a private lane's own FalkorDB container (`tools/test_lane.py`), on throwaway graphs built by the two probes in `probes/`. Every claim marked **MEASURED** below has a probe output that produced it. |

Two probes, both re-runnable:

```
docs/evidence/11824-ep-across-entities/probes/measure_per_labels.py   # wiring, entity seed, goalState, Event, contested
docs/evidence/11824-ep-across-entities/probes/measure2_door_and_axis.py  # door guards, position axis per leg, per-label inputs, targets
```

Run (`tools/test_lane.py uri` first, then export the four seam variables):

```
uv run python docs/evidence/11824-ep-across-entities/probes/measure_per_labels.py
uv run python docs/evidence/11824-ep-across-entities/probes/measure2_door_and_axis.py
```

The measured tree is `340d500491` (this branch's base).

---

## 2. Measured behaviour, per label

"Feeds propagation" asks: does the node's stored belief enter the factor EP prices? "Excluded by" asks: what makes EP stop counting it? "Hydrated" asks: does its stored prior join the SDK's persistent evidence set the way a Point's does?

| Label | Operator **source**? | Operator **target**? | Confidence feeds propagation? | What excludes it from belief | Evidence hydrated like a Point's? | `goalState` read for belief? |
|---|---|---|---|---|---|---|
| **`:Point`** | ✅ yes | ✅ yes | ✅ seed = `coalesce(posterior_alpha, ep_alpha, 1.0)` (`ep.py:790`, `ep.py:485`); a **declared** prior is writable and immutable (`sdk.py:40833`) | the terminal status words ∪ the legacy `outdated` flag ∪ **`isClosed`** ∪ a **`terminal` pipeline stage** — on every Cypher leg (`live.py:159`, `live.py:372`, `live.py:127`), **but not in the Python participation test for an operator *input*** (`ep.py:2258` — see D1) | ✅ the only label `_hydrate_evidence` loads (`sdk.py:40736`, `MATCH (n:Point)`) | — |
| **`:Operator`** (`:Point {is_operator:true}`) | it is a Point, so it is addressable — but an operator-shaped node is **stripped** from another operator's inputs (`ep.py:2257`) and excluded from claim surfaces | same | its own posterior is not a claim's belief | same as `:Point` | ❌ not a claim | — |
| **`:Object`** | ✅ **MEASURED** written | ✅ **MEASURED** written | ✅ — but the seed is **vacuity**: `Beta(1,1)` ⇒ mean 0.5. The stored `confidence` property is **never read** by EP (MEASURED: an Object created `confidence=1.0` contributes 0.5 and EP then **overwrites** the 1.0), and there is **no declared-prior writer**: `set_point_baseline` refuses a non-Point (MEASURED) | the **Point** status vocabulary (so `completed` / `in_progress` are *not* endings for EP, while `superseded` / `deprecated` / `archived` / `retracted` are) ∪ `isClosed` / terminal stage — on the Cypher legs only, **not** in `ep.py:2258` (MEASURED: a closed Object with a `(9,1)` posterior still moved its target) | ❌ `_hydrate_evidence` is `:Point`-only | ❌ never read by EP and never read by the currency predicate |
| **`:Subject`** | ✅ **MEASURED** written | ✅ written (same machinery) | ✅ same as `:Object` (measured vacuous seed) | `SUBJECT_TERMINAL_STATUSES ⊂` the Point vocabulary, plus both position axes on the Cypher legs | ❌ | — |
| **`:Event`** | ✅ **MEASURED** written (must carry `id`) | ✅ **MEASURED** — EP writes `posterior_*` and `confidence` onto the Event | ✅ same as `:Object` | **nothing**: an Event has no lifecycle at all (`live.py:670`; the declaration implementing it is `live.py:683`; `status` is refused at the write door), and its identity is `id`/`eventId` (`live.py:1204`) | ❌ | — |
| **`:Source`** | ❌ **REFUSED** — *"Cannot create operator: Points ['…'] do not exist"* (MEASURED) | ❌ refused | ❌ not an EP node. A Source reaches belief **only** as a Beta prior on an extracted **Point**: `_apply_source_inheritance` (`sdk.py:41191`) runs `MATCH (n:Point)-[:extractedFrom]->(s:Source)` (`sdk.py:41358`) | n/a | n/a | — |
| **`:PipelineStage` / `:Batch`** | ❌ not epistemic nodes — `EPISTEMIC_LABELS = ("Point","Subject","Object","Event")` (`live.py:667`); `:PipelineStage` is its own operational label (`pipeline.py:52`) and both keep their own vocabularies (`lifecycle_vocabulary.py:63`) | ❌ | a `:PipelineStage` participates in belief only as the **slot** an entity sits in (the position axis) — never as a belief node | n/a | n/a | — |

Three consequences that matter and are not obvious from the prose:

1. **An entity's belief is its own stored posterior, not a function of the claims attached to it.** MEASURED: an `:Object` used as an operator **target** was written `posterior_alpha 1.7769 / posterior_beta 0.9561 / confidence 0.6502` — the same columns a Point gets (`ep.py:809`). The *ranking* surface computes something different: `fetch_entity_signals` (`ranking.py:838-902`) reports an entity's confidence as the **mean over the claims attached to it** by an `about*` edge. Both are live, and they can disagree.
2. **Two currencies read the entity, and only one reads the position axis.** `_hydrate_evidence` and the affected-set/factor Cypher legs read both axes; `ep.py:2258`'s participation test reads the lifecycle word only.
3. **`goalState` is not an EP input at any point.** MEASURED: a goal `:Object` with `goalState='abandoned'` and `status='live'` **still participates** in EP (only `status` decides), while `derived_invalidation` retires its derived caches on the `goalState` word (`derived_invalidation.py:181`, `:421`). The two surfaces disagree on exactly the legacy shape the write boundary now refuses to create — the finding behind D5.

---

## 3. Discrepancy table

`§`/line numbers are `docs/ONTOLOGY.md` at this branch's base.

| # | Doc line | The doc says | The engine does | Verdict |
|---|---|---|---|---|
| **D1** | §10.5, L1505-1508 | *"excludes terminal claims via the shared live.py predicate (status ∈ {retracted, superseded, outdated, archived, deprecated} OR `outdated=true`)"* | The predicate reads **two sources**: the lifecycle word **and** the position axis — `isClosed` (`live.py:159`, `:130`) or a stage in the `terminal` slot (`live.py:127`, `pipeline.py:56`) | **(a) stale** — the sentence names one of two axes. It also silently *omits* the arm where EP does not apply the axis (`ep.py:2258`), so the corrected sentence is a rule the engine does not fully meet — a code defect, filed separately |
| **D2** | §2, L94-96 | *"State confidence is derived at read time from the attached Points' EP confidence (§11) — never stored independently on the Object."* | An entity carries its **own** stored EP posterior + `confidence` mirror, written by EP (`ep.py:809`; MEASURED). `why` (`why.py` per-label, `:600`), `get_confidence` (`sdk.py:41153`) and `get_contested_claims` (`ep.py:2479`) all serve that stored value. *Derivation at read time over attached claims* is a **different** read, used by ranking (`ranking.py:838`) | **(a) stale / imprecise** — both reads are real; the sentence names one and denies the other exists |
| **D3** | §2, L105 | *"Facts = confidence 1.0."* | Nothing seeds a node at 1.0 from being a fact. A node's EP seed is `coalesce(posterior_alpha, ep_alpha, 1.0)` = vacuity 0.5 when unmeasured; a node's stored `confidence` is never an EP input; a structural edge carries no belief at all | **(c) factual error** as written — and if the *intent* is a target ("a fact should be believed at 1.0") then the doc is right and the **code** is the defect; either way the current sentence cannot stand, because it describes a mechanism that does not exist |
| **D4** | §4.1 L417, §4.3 L457, §5 L1056-1059 | `goalState` vocabulary = `met`, `active`, `blocked`, `unmet`, `superseded`, `abandoned`, **`guideline`** | `GOAL_STATE_VALUES` is **six** words (`sdk.py:378`) — `guideline` was removed by owner ruling (`sdk.py:361-370`), and `GOAL_LIVE_STATES` is *derived* from the six so a hand-written copy cannot survive | **(a) stale** — the doc describes a vocabulary that has since changed. Because the change is the **reversal of a recorded ruling**, the reversal's own record belongs in `ONTOLOGY-CHANGELOG.md` (see P4) |
| **D5** | §5, L1051-1061 | *"achievement is a SEPARATE axis"*, and the two axes are only described as separate | Two of the six words are the **lifecycle's endings spelled on the achievement axis** (`sdk.py:392`: `abandoned` IS `deprecated` for a goal); a write that ends on one axis and leaves the other running is **refused** in either direction (`sdk.py:6186-6205`); and no reader answers currency from `goalState` — the predicate never reads it (`live.py:159`; pinned by `tests/test_13034_currency_rule_reads_position.py::test_the_predicate_does_not_read_goal_state`) | **(a) incomplete** — the doc says the axes are separate but not which one *ends* a record, nor that the write boundary holds them in agreement precisely because no reader reconciles them |
| **D6** | §4.1 L402 | `op_type` = `IMPL / NAND (operator Points only)` | Three mechanisms: `_MECHANISM_OP_TYPES = ("IMPL", "NAND", "coherence")` (`sdk.py:13555`), `_MECHANISM_POTENTIALS` + `MECHANISM_REL_TYPES` (`ep.py:47-86`), `phi_coherence` (`quadrature.py:143`). §8 documents the third mechanism; this field row and §2's summary do not | **(a) incomplete** — lower priority; the canonical treatment is §8 |

**Count: 6 discrepancies** across **8 textual sites** (D4 alone is 3 sites). **One** is a factual error (D3); **five** are stale or incomplete relative to the shipped engine — D1, D2, D5, D6 (D6 merely an enumeration gap) and D4, a vocabulary that changed under the doc.

The proposal list below is **9 hunks**, not 8: P2 carries a second hunk (P2b) which is an **insertion** into a table that has no belief row today, so it has no before-text of its own.

---

## 4. Proposed replacement text

Each item is independent. Approve or reject them one at a time. "Why" is one line as required.

### P1 → fixes D1 (§10.5, the terminal-exclusion sentence)

**Before** (L1505-1508, verbatim):

```
Every contested computation (annotate_ep_batch, rankers, `get_contested_claims`,
`_review_prune`, why, analyze) excludes terminal claims via the shared
live.py predicate (status ∈ {retracted, superseded, outdated, archived,
deprecated} OR `outdated=true`).
```

**After** (verbatim):

```
Every contested computation (annotate_ep_batch, rankers, `get_contested_claims`,
`_review_prune`, why, analyze) excludes ended nodes via the shared live.py
predicate, which reads BOTH sources of an ending — the **lifecycle word**
(status ∈ {retracted, superseded, outdated, archived, deprecated}, or the
legacy `outdated=true` flag) and the **position axis** (`isClosed`, or a stage
in the `terminal` pipeline slot; `pipeline.py`). Either one alone is an
ending, and the position axis is a different SOURCE of the answer, not a
narrower vocabulary: a caller may choose which status words end a thing, and
may not opt out of `isClosed`/the slot. `include_draft` re-includes drafts
only, never an ended node.
```

*Why:* the predicate has read both axes since the position axis landed (`live.py:22-31`, `:130`, `:159`, `:372`), and a doc that names one axis is how a future reader re-derives the half-predicate.

### P2 → fixes D2 (§2's state-confidence sentence, plus the §4.3 belief row it points at)

**P2a — Before** (§2, L94-96, verbatim):

```
> lifecycle writes on Objects. State confidence is derived at
> read time from the attached Points' EP confidence (§11) — never stored
> independently on the Object.
```

**P2a — After** (verbatim):

```
> lifecycle writes on Objects. An Object or Subject has **two belief reads**,
> and they answer different questions. Its **own EP belief** is a stored
> posterior (`confidence`, `posterior_alpha`/`posterior_beta`) that EP
> propagates onto it exactly as it does a Point — it is what `why`, the
> contested scan and the confidence read serve (§4.3). Its **state
> confidence** is computed at read time as the mean of the confidence of the
> claims attached to it by an `about*` edge, and that is what the rankers
> order on (§11). Neither is a substitute for the other: a reader that asks
> "what do we believe about this object" must say which one it means.
```

**P2b — Insertion** (§4.3's table has **no** belief row today, and neither does §4.2's; this row is to be **inserted** between `passes_frequency_gate` and `goalState`, L455-457):

There is currently **no** belief row in §4.3 (and none in §4.2). Proposed **insertion** between `passes_frequency_gate` and `goalState`:

```
| `confidence` / `posterior_alpha` / `posterior_beta` | float 0..1 / float / float | — | — | An entity is an EP node: when an operator (or a direct `IMPL`/`NAND` edge) has it as an endpoint, EP writes the same belief columns a Point carries — `confidence` is the served mean, the pair is the posterior behind it. An entity no factor reaches has **no** persisted belief and reads `absent` in the measurement-state vocabulary (§11's three states); EP's arithmetic still seeds it at `Beta(1,1)` vacuity, which is a lack of evidence, not a belief. A **declared** prior is a Point-only concept — there is no declared-prior writer for an entity — so an entity's belief is always computed, never authored |
```

*Why:* the engine writes and serves a stored posterior on Objects/Subjects/Events (MEASURED), and no §4.x table admits it — a reader of §4.3 currently cannot learn that an entity carries a belief at all.

### P3 → fixes D3 ("Facts = confidence 1.0")

**Before** (§2, end of L105, verbatim span):

```
Reputation is derived at query time. Facts = confidence 1.0.
```

**After** (verbatim):

```
Reputation is derived at query time. A **fact** is a structural edge: it
carries no belief, and nothing reads one as evidence. Belief enters a node as
a **declared prior** (a baseline set on the claim, or one inherited from a
`Source` through `extractedFrom`) or through an operator or a direct
`IMPL`/`NAND` edge (§8); a node with neither has no **measured** belief — it is
absent, not certain.
```

*Why:* "confidence 1.0" describes a mechanism that exists nowhere in the engine — no writer seeds a fact at 1.0, and the level at which an unwired node sits is vacuity (`Beta(1,1)`, mean 0.5), which is the opposite of certainty. (Belief *can* enter with no edge at all, via a declared prior — which is exactly why the sentence has to name it rather than say "only through an edge".)

> ⚠️ **Owner decision needed if the intent was a target.** If the model *is* "a fact is believed at 1.0", then the doc is right and the code is the defect: nothing seeds an entity, and an entity's `confidence` property is not an EP input. Say which you mean and I will either take P3 as text, or file the code defect and leave the sentence.

### P4 → fixes D4 (`guideline` at three sites)

**P4a — Before** (§4.1, L417, the differing span; unique on the line):

```
(`met`, `active`, `blocked`, `unmet`, `superseded`, `abandoned`, `guideline`)
```

**P4a — After** (verbatim):

```
(`met`, `active`, `blocked`, `unmet`, `superseded`, `abandoned`)
```

**P4b — Before** (§4.3, L457, the same span):

```
(`met`, `active`, `blocked`, `unmet`, `superseded`, `abandoned`, `guideline`)
```

**P4b — After** (verbatim):

```
(`met`, `active`, `blocked`, `unmet`, `superseded`, `abandoned`)
```

**P4c — Before** (§5, L1056-1059, verbatim):

```
> boundary (`GOAL_STATE_VALUES` in `sdk.py`): `met`, `active`, `blocked`, `unmet`,
> `superseded`, `abandoned`, and `guideline` (directional, no pass/fail — the
> owner's #6792 form ruling, which is why `<10 min of CI` is a guideline and
> not an absolute target). The two must not be conflated: a goal that reads
```

**P4c — After** (verbatim):

```
> boundary (`GOAL_STATE_VALUES` in `sdk.py`): `met`, `active`, `blocked`, `unmet`,
> `superseded`, `abandoned`. Whether a goal is *directional* is a question
> about **priority**, not about outcome, so it is not an achievement word and
> has no place in this vocabulary. The two must not be conflated: a goal that reads
```

*Why:* `GOAL_STATE_VALUES` is six words (`sdk.py:378`); `guideline` was a real owner-ruled word that a later owner ruling **removed** (`sdk.py:361-370`), and a model list that keeps a deleted word is the exact failure the closed vocabulary exists to prevent.

> ⚠️ **This one is a reversal, so it is recorded twice, not once.** The Before text carries the pointer *"the owner's #6792 form ruling"* — a reference to **where a decision lives**, which the file header keeps ("A reference that records where a term or a decision lives … is not history and stays"). A bare deletion would erase the pointer and leave the next reader unable to tell a ruling from an accident, so the change should **also** add a line to `ONTOLOGY-CHANGELOG.md` naming the ruling that superseded `#6792`. If you would rather the pointer survive *inside* this block, append to the After: *"— a later ruling removed it, because directionality is a question about priority rather than outcome (§5, `ONTOLOGY-CHANGELOG.md`)"*.

### P5 → fixes D5 (which axis ends a goal)

**Insertion point:** §5's goal-state block, at the **end** of the blockquote — immediately **after** *"The canonical representation of a goal remains an **Object**; the born-live rule is the legacy-Point-leg safety net."* (L1066). Inserting at the *`goalState`/`status` sentence* would split a paragraph mid-thought: that sentence ends at L1061 and the block then carries the EP-inertness argument and the born-live rule. Pure insertion; nothing is removed.

**After** (verbatim, to be inserted as a new paragraph continuing the existing `>` blockquote):

```
> The two axes are separate QUESTIONS, not two ways to end a record. Two of the
> six words — `superseded` and `abandoned` — are the **lifecycle's endings
> spelled on the achievement axis** (`abandoned` IS `deprecated` for a goal,
> and `superseded` keeps its spelling on both), so a write that ends the goal
> on one axis and leaves it running on the other is refused in either
> direction, and a `superseded` still names its successor. The reader rule
> answers from `status` alone: `goalState` is never a currency input (§10.5).
> That is *why* the axes are held in agreement at the write boundary — nothing
> reconciles them at read time, so an incoherent pair (a legacy row carrying
> `goalState='abandoned'` beside `status='live'`) is current to every reader
> and ended to none.
```

*Why:* the engine's rule is `status` ends it and `goalState` never does (`sdk.py:392`, `:6186-6205`, `live.py:159`), and the doc currently states only that the axes are separate — which reads as "either can express an ending".

### P6 → fixes D6 (`op_type` omits the third mechanism) — *optional, lowest priority*

**Before** (§4.1, L402, verbatim — the whole row):

```
| `op_type` | string | — | — | IMPL / NAND (operator Points only) |
```

**After** (verbatim):

```
| `op_type` | string | — | — | The **mechanism**: `IMPL` (support), `NAND` (attack) or `coherence` (necessary condition) — operator Points only. A bare direct edge carries its mechanism as its edge TYPE, and there are two (`IMPL`, `NAND`); `coherence` is reachable only as an operator (§8) |
```

*Why:* `create_operator` accepts three mechanisms (`sdk.py:13555`, `ep.py:47-86`) and §8 documents all three; only this field enumeration still says two. (If you approve this one, §2's *"Operator (IMPL/NAND + label + EP confidence)"* on L68 has the same gap — I have left it alone to keep the diff minimal.)

---

## 5. Verified correct — checked, no change proposed

Listed so the owner can see what was actually examined rather than assumed.

| Doc claim | Where | Verified against |
|---|---|---|
| The operator node is **always** `:Point {is_operator:true}`; only its children/targets may carry any of the four labels | §8 reification rule; §5 L999 | **MEASURED** — operators minted over `:Object`, `:Event`, `:Subject` sources are all `['Point'], is_operator=true, op_type=IMPL, status=live` |
| The write path admits all four epistemic labels as operator endpoints | §2 L68, §3.1, §8 `OVERRIDES` | **MEASURED** — as operator **sources**: `Point`, `Object`, `Subject`, `Event`; as operator **targets**: `Point`, `Object`, `Subject`, `Event` (each target read back a written posterior of `1.7769 / 0.9561`); and as a direct edge: `Event → Point` wrote |
| `:Source` is **not** an epistemic endpoint | (implied: never listed) | **MEASURED** — refused; and `.sdk._INGEST_ENDPOINT_LABELS = frozenset(EPISTEMIC_LABELS)` (`sdk.py:30818`) documents the same set |
| `IMPL`/`NAND` between any two plain epistemic nodes propagates with no operator | §8 "Operator-less propagation" | code: `ep.py` Batch 3 admits both endpoints through `epistemic_disjunction` + `_non_operator`, and **MEASURED** (a live direct edge moved its target; the same edge wired-then-ended stopped being priced on both axes) |
| `MITIGATES` is not an operator kind; a mitigation is a Point on `(op)-[:mitigated_by]->(m)` and contributes no confidence of its own — it scales the operator's weight `w_eff = w × (1 − strength)` | §2, §3.9, §8 | code: `weights.py:232` (`compute_operator_weight`) — the mitigation is a single multiply, priced from the mitigation's **live** state; `ep.py` never admits `(m)-[:IMPL]->(op)` as a factor (`_affected_claims`: reverse-only IMPL forms no factor) |
| Three mechanisms, closed set, support can *lower*, coherence never raises its dependent | §8 | code: `quadrature.py:124` (`phi_impl`, difference coupling), `:90` (`phi_nand`), `:143` (`phi_coherence`, one-corner product) |
| `:Event` targets are epistemic; decisions stay non-first-class Points | §2, §8 `OVERRIDES` | code + **MEASURED** — an `:Event` source participates and EP writes a posterior onto the Event |
| Reputation's shape is `subject -[:performs]-> Event -[:IMPL|NAND]-> Point` | §11 | code: `sdk.compute_reputation` (`sdk.py:59110`) matches the documented shape exactly (it is `:Point`-only, which the doc also says) |
| An entity ended only by `isClosed` is not served as current by the *retrieval* legs | §2 procedural row / §10.5 | code: `live.py:159` composed into every retrieval leg; **MEASURED** for the direct-edge and affected-set legs |

---

## 6. What I could not determine, and what is out of scope

1. **Whether "Facts = confidence 1.0" is text-with-a-defect or a target** — that is an owner ruling, not a measurement. P3 states the choice (see its ⚠️ note).
2. **What an `:Event`'s `ep_alpha` seed *should* be.** Measured: an Event's seed is vacuity 0.5, and after one EP run EP writes `(1.0, 1.0)` as its `ep_alpha` — an Event therefore can never acquire a non-vacuous prior except by propagation. Whether a dated occurrence should carry a level of its own is a belief-model question (the class reserved to the owner), so I did not propose doc text for it.
3. **`:Batch`'s role.** I confirmed it is not epistemic and keeps its own vocabulary (`lifecycle_vocabulary.py:63`), but I did not find any writer that gives a `:Batch` an EP role, so the per-label row above says only "not a belief node".
4. **`Object.status='completed'` / `'in_progress'`.** The connector fold writes these, the Object read legs exclude on `OBJECT_TERMINAL_STATUSES` (`commit_ops.py:88` — a four-word subset), and EP uses the **Point** vocabulary, so neither is an ending for EP (MEASURED: a `completed` Object still participates). Whether a *completed* work item should stop voting is a model decision I did not take.
5. **The doc's frontmatter.** `updated:` and the version string are not touched by this proposal; applying an approved item is the change that should move them.

---

## 7. A code defect this audit surfaced (not a doc item)

Filed as **#13450**; recorded here so the doc proposal is not read as describing a satisfied rule.

**A node ended on the position axis still votes when it is an operator *input*.** `ep.py:2258` decides participation in Python from the two columns Batch 2 projects — `status` and `coalesce(c.outdated,false)`:

```python
is_terminal = status in TERMINAL_EXCLUDED_STATUSES or bool(outdated)
```

The Cypher predicate it is meant to mirror reads **four** conditions (`live.py:159`: status word, `outdated` flag, `isClosed`, `pipelineSlot != 'terminal'`). Batch 2's own comment (`ep.py:2244-2247`) says its status set is *"derived from `TERMINAL_EXCLUDED_STATUSES` so the two cannot drift"* — and that holds, for the **words**: the position axis is not a vocabulary item, it is a second *source* of the same answer, and it never reached this arm. MEASURED, all three ended shapes contributed to a live target, at exactly the live control's posterior:

| input | target posterior (live control: `1.6391 / 1.0327`) |
|---|---|
| `status='superseded'`, prior `(8,2)` | stripped (different: the status half works) |
| `isClosed=true`, `status='live'`, prior `(8,2)` | **`1.6391 / 1.0327` — contributed** |
| `pipelineSlot='terminal'`, prior `(8,2)` | **`1.6391 / 1.0327` — contributed** |
| `:Object` `isClosed=true` with posterior `(9,1)` | **`1.7769 / 0.9561` — contributed** |

Two related halves, measured, same defect class (the write doors do not read the position axis either):

- `create_operator` has **no** terminal endpoint guard at all — it wrote an operator over a `status='superseded'`, an `isClosed=true` and a `pipelineSlot='terminal'` source.
- `create_direct_edge`'s guard (`sdk.py:35948`) reads `status in TERMINAL_EXCLUDED_STATUSES or outdated` — the same two of four conditions — so it refuses a superseded endpoint but not a closed one (the read side then ignores the edge, so the damage is a dangling factor rather than a wrong vote).

Also measured, for completeness: writing `isClosed=True` through `update_knowledge` leaves the stored posterior untouched (`0.9 / (9,1)` before and after), whereas `retract_point` decays to vacuity `0.5 / (1,1)` — i.e. the `#2490` decay is bound to the *lifecycle-word* writers only, so a position-axis ending has no decay path. That is stated in §10.5 as a property of "every terminalizing write", and an `isClosed` write is not one.

---

## 8. Evidence index

| File | What it holds |
|---|---|
| `probes/measure_per_labels.py` | section A (wiring per label, `:Source` refusal), B (entity `confidence=1.0` seed + overwrite, `set_point_baseline` refusal), C (position axis on operator inputs; the contributed/dropped verdict is **derived** from the posterior's asymmetry, never typed), D (`goalState` shapes vs `is_terminal_status`), E (Event endpoints, `:Event`/`:Subject`/`:Object` in the **target** slot, `_hydrate_evidence` universe), F (contested scan per label) |
| `probes/measure2_door_and_axis.py` | 1 (`isClosed` through the door: no decay), 2 (`retract_point`: decay control), 3 (direct-edge position axis + door guards per axis), 4 (per-label operator inputs), 5 (`completed` Object input), 6 (Object as target: posterior + `confidence` written), 7 (operator node shape per endpoint label), 8 (entity never reaches the evidence set) |

Suite run for this change (via `tools/worktree_report.py run`):

```
uv run python tools/test_lane.py run -- tests/test_13034_currency_rule_reads_position.py -q
→ exit 0, 113 passed
```

That file is the pin for the currency rule this audit cites (D1/D5); it passes at this base, which is why D1 is a **doc** defect and the `ep.py:2258` gap is a **code** defect that file does not cover.
