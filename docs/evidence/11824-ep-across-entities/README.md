---
title: "EP across entity kinds — a measured audit of docs/ONTOLOGY.md"
type: evidence
domain: platform
doc_status: live
created: 2026-11-18
subjects.team: epistemic-team
aboutSubjects: EP belief propagation, entity kinds (Point, Operator, Object, Subject, Event), docs/ONTOLOGY.md
aboutObjects: "#11824, docs/ONTOLOGY.md, tortoise/ep.py, tortoise/live.py, tortoise/sdk.py"
---

# EP across entity kinds — a measured audit of `docs/ONTOLOGY.md`

**Branch:** `audit/ep-across-entities` · **Measured at:** `340d500491` · **Citations at this head**
(re-anchored after this branch integrated `main`) · **Tracker:** #11824

**Status: evidence only.** This change does **not** edit `docs/ONTOLOGY.md`, and it contains **no**
replacement ontology text. `docs/ONTOLOGY.md` carries:

> CRITICAL RULE: no modification to this file is allowed without explicit approval of the exact
> changes by Daniel Ospina.

So this document is the deliverable, and it is deliberately narrow: **one table** of how the engine
treats each entity kind, and **the list of doc sentences that contradict it**, each with a verdict —
*the doc is stale*, or **fix the code**. Writing the replacement text is a second step, on approval.

---

## 1. Method — what is read from code, what is measured

Every cell below is marked one of two ways:

| Mark | Means |
|---|---|
| **⟨code⟩** | read out of the engine at the `file:line` cited. No execution involved. |
| **⟨measured⟩** | observed on this branch, in this lane's own throwaway FalkorDB container, from a probe whose output is quoted. |

**Reproducing the measurements.** The two probes in `probes/` are the instruments. They build their
own throwaway graphs and print what they observe; they are not tests and they change no engine code.

```bash
uv run python tools/test_lane.py status          # prints  uri=docker://…:PORT/…
EP_AUDIT_DB_PORT=<that PORT> uv run python docs/evidence/11824-ep-across-entities/probes/measure_per_labels.py
EP_AUDIT_DB_PORT=<that PORT> uv run python docs/evidence/11824-ep-across-entities/probes/measure2_door_and_axis.py
```

⚠️ The port is read from `EP_AUDIT_DB_PORT`, never baked in — a hard-coded port points at whichever
lane happens to hold it. Both probes read it as a **required** variable (`os.environ[…]`, no fallback),
so a run that forgot it stops rather than quietly measuring a foreign container. **A probe cannot run
without the lane's container already up** (`test_lane.py run` starts one). Every **⟨measured⟩** cell
below was produced by the run recorded in §7.

**The engine files read:** `tortoise/live.py` (the currency predicate, the epistemic-label set, the
identity keys), `tortoise/ep.py` (factor assembly, the affected set, the two seed readers, the two
write paths), `tortoise/quadrature.py` (the three potentials), `tortoise/weights.py` (operator weight,
mitigation dampening), `tortoise/ranking.py` (`fetch_entity_signals`), `tortoise/derived_invalidation.py`
(the goal-retirement arm), `tortoise/sdk.py` (`_hydrate_evidence`, `set_point_baseline`,
`_apply_source_inheritance`, `create_operator`, `create_direct_edge`, `GOAL_STATE_VALUES`,
`compute_reputation`), `tortoise/pipeline.py` + `tortoise/lifecycle_vocabulary.py` (the operational
labels).

---

## 2. The table — how EP treats each entity kind

The four questions, per kind: **(1)** can it be an operator *source* / *target*? **(2)** does its
confidence *propagate*, or is it fixed/neutral? **(3)** what *excludes* it from belief? **(4)** is its
evidence *hydrated* with the same filter a Point's is?

| Kind | (1) Operator source? target? | (2) Confidence: propagates, or fixed/neutral? | (3) What excludes it from belief | (4) Evidence hydrated like a Point's? |
|---|---|---|---|---|
| **`:Point`** | **source ✅ / target ✅** — **⟨measured⟩** `Point -> Point WROTE`. **⟨code⟩** operator discovery is `MATCH (o:Point)-[r:IMPL\|NAND]->(c)` (`ep.py:2012`), with the far endpoint widened by a per-label `epistemic_label_queries` sweep (`ep.py:2007-2008`), and the input-assembly leg states the widening directly, `epistemic_disjunction('c')` (`ep.py:2220-2222`) — so an input may carry any of the four labels. | **propagates.** Seed = `coalesce(posterior_alpha, ep_alpha, 1.0)` on the direct read (**⟨code⟩** `ep.py:801`); on the warm batched load it is **only** `coalesce(ep_alpha, 1.0)` — no `posterior_alpha` arm (**⟨code⟩** `ep.py:507`; see §6.6). A **declared** prior is writable and immutable: `set_point_baseline` (`sdk.py:40924`, write at `:41073`), honoured by the `keep_prior` / `baseline_set` CASE in both write paths (**⟨code⟩** `ep.py:629-635`, `ep.py:822-826`). | **four conditions on every Cypher leg:** the 5-word status set ∪ the legacy `outdated` flag ∪ `isClosed` ∪ a `terminal` pipeline slot (**⟨code⟩** `live.py:114` for the words, `live.py:159` `_terminal_excluded`, `live.py:372` the Python mirror, `live.py:588` `_live_only`). **But only two of the four in the Python participation test for an operator *input*** — **⟨code⟩** `ep.py:2258`; **⟨measured⟩** §5 F1. `goalState` never excludes (see `:Object`). | **yes — and it is the only kind that is.** `_hydrate_evidence` is `MATCH (n:Point) WHERE n.baseline_set = true … AND {_terminal_excluded('n.status')}` (**⟨code⟩** `sdk.py:40848`), so it gets the *same* predicate as every other read. **⟨measured⟩** the hydrated evidence set held 4 ids and **no** non-Point. |
| **`:Operator`** (`:Point {is_operator: true}`) | It **is** a Point, so it is addressable by an id — but an operator-shaped node is **stripped out of another operator's inputs**: `is_operator_shaped` is tested by identity against `None`/`False` and never participates, under **both** `include_draft` modes (**⟨code⟩** `ep.py:2257-2260`). **⟨measured⟩** every operator minted over an `:Object`/`:Event`/`:Subject` endpoint is `['Point'], is_operator=True, op_type=IMPL, status=live`. | **it holds no propagated belief of its own.** It supplies a *mechanism* and a *weight*: the factor potential is chosen from `op_type` (**⟨code⟩** `ep.py:47-52` → `quadrature.py:90`/`:124`/`:143`), and a mitigation scales the weight as `w_eff = w × (1 − strength)` (**⟨code⟩** `weights.py:232`, `weights.py:65`). Its **inputs'** beliefs are what move. | Same as `:Point` (it is one). Additionally the mechanism census counts live **and** draft operators (**⟨code⟩** `sdk.py:13620-13624`), so a draft operator still occupies its pair. | **n/a** — an operator is not evidence, and never enters `_hydrate_evidence` (it has no `baseline_set`). |
| **`:Object`** | **source ✅ / target ✅** — **⟨measured⟩** `Object -> Point WROTE` and `Point -> Object WROTE`; as a target EP wrote `posterior_alpha 1.7769 / posterior_beta 0.9561 / confidence 0.6502` onto it. | **propagates, but the seed is vacuity.** With no prior of its own the seed is `Beta(1,1)` → mean **0.5**. The stored `confidence` property is **never an EP input** — `n.confidence` appears in `ep.py` only inside `SET` clauses (**⟨code⟩** `ep.py:632`, `:822`, `:2976`), and the one place it is read is that clause's own `THEN n.confidence` (`ep.py:2977`), which *preserves* the mirror rather than consuming it; neither posterior seed (`ep.py:507`, `:801`) mentions it. **⟨measured⟩** an Object created `confidence=1.0` was *served* at 1.0, **seeded at 0.5**, and then had its 1.0 **overwritten to 0.5** by the run. There is **no declared-prior writer**: `set_point_baseline` refuses a non-Point (**⟨code⟩** `sdk.py:41073` + refusal at `:41157-41161`), and the refusal names **#9063** as the open belief-model decision. | The **Point** vocabulary words ∪ `isClosed` ∪ terminal stage — but note the vocabulary mismatch: `completed` / `in_progress` are *not* endings for EP (**⟨measured⟩** a `status='completed'` Object **participated** — the probe prints the target's posterior and a participated/dropped suffix, not a comparison against a control). On the Cypher legs the position axis is read (**⟨measured⟩** the direct-edge leg **dropped** an `isClosed` and a `terminal`-slot endpoint, `0.0 / 0.0`); in `ep.py:2258` it is **not** (**⟨measured⟩** a closed Object with a `(9,1)` posterior **contributed**). `goalState` **never** excludes (**⟨measured⟩** §2 consequence 3, and §5 F4). | **no.** `_hydrate_evidence` is `:Point`-anchored (**⟨code⟩** `sdk.py:40848`), and `set_point_baseline` refuses (**⟨code⟩** above). An Object is filtered by a *different* population, not a looser one. |
| **`:Subject`** | **source ✅ / target ✅** — **⟨measured⟩** `Subject -> Point WROTE`; as a target it took `1.7769 / 0.9561`. | Same as `:Object` — **⟨measured⟩** as an operator input a Subject read `posterior [0.5, 1.0, 1.0]`, i.e. it participated from vacuity and was written. | Same as `:Object`. `SUBJECT_TERMINAL_STATUSES` is a subset of the Point set (**⟨code⟩** `live.py:654-658`). Both position arms apply on the Cypher legs; `ep.py:2258` omits them as for `:Object`. | **no** — `:Point`-anchored hydration; `set_point_baseline` refuses. |
| **`:Event`** | **source ✅ / target ✅** — **⟨measured⟩** an `Event → Point` direct edge **wrote**, and an Event in the **target** slot took `1.7769 / 0.9561`. | Same as `:Object` — **⟨measured⟩** the Event's own belief after a run read `confidence 0.5 / posterior (1,1)`, i.e. seeded at vacuity and then written. | **nothing.** An Event has **no lifecycle at all**: the write door **refuses** a `status` on it (**⟨code⟩** `live.py:961` `refuse_event_lifecycle_state`, #13100) and `STATUS_CARRYING_EPISTEMIC_LABELS` is `{Point, Subject, Object}` (**⟨code⟩** `live.py:683`). The NULL-status arm of `_live_only` therefore reads it LIVE (**⟨code⟩** `live.py:588`). Its identity is `id`-or-`eventId` (**⟨code⟩** `live.py:1204`). `goalState` never applies. | **no** — `:Point`-anchored hydration. **⟨measured⟩** no Event reached the evidence set. |
| **`:Source`** | ❌ **refused, both slots.** **⟨measured⟩** `Source -> Point` → `ValueError: Cannot create operator: Points ['…'] do not exist` (**⟨code⟩** raised at `sdk.py:29001`), because the endpoint probe sweeps `EPISTEMIC_LABELS` only (**⟨code⟩** `sdk.py:28984-28987`). | **not an EP node**, so it has no belief of its own. A Source reaches belief **only** as a Beta prior written onto an extracted **Point**: `_apply_source_inheritance` (**⟨code⟩** `sdk.py:41282`, its `MATCH (n:Point)-[:extractedFrom]->(s:Source)` at `:41449`). | n/a for EP. The Point-side inherited prior is then filtered by the *Point's* currency predicate when it is hydrated. | n/a — a Source is never a member of the evidence set, only its provenance for a Point. |
| **`:PipelineStage`** | ❌ **not an epistemic node.** Not in `EPISTEMIC_LABELS = ("Point", "Subject", "Object", "Event")` (**⟨code⟩** `live.py:667`), so no EP leg can address it (`epistemic_disjunction` / `epistemic_label_queries`, `live.py:1263`/`:1277`). | **none.** It reaches belief only as the **slot an entity sits in** — the position axis, `TERMINAL_STAGE_SLOT = "terminal"` (**⟨code⟩** `live.py:127`) over `PIPELINE_SLOTS = ("entry", "active", "suspended", "terminal")` (**⟨code⟩** `pipeline.py:56`). It is a *source of an ending*, never a belief node. | n/a — but it **causes** an exclusion: a row whose stage resolves to the `terminal` slot is ended on the position axis (**⟨code⟩** `live.py:212-214`). | n/a. |
| **`:Batch`** | ❌ **not an epistemic node** (same declaration as above, `live.py:667`). | **none — no EP role at all.** `lifecycle_vocabulary.py` states it explicitly: *"operational node kinds (`:PipelineStage`, `:Batch`) keep their own vocabularies … neither is reached by it"* (**⟨code⟩** `lifecycle_vocabulary.py:63-65`). `mining.py:57-58` calls a `:Batch` *"operational metadata … not ONTOLOGY"* (**⟨code⟩**). | n/a — nothing to exclude, because nothing is priced. | n/a. |

**Row count: 8.** (`:Point`, `:Operator`, `:Object`, `:Subject`, `:Event`, `:Source`,
`:PipelineStage`, `:Batch`.)

### Three consequences that are not visible in any single row

1. **An entity's belief is a *stored* posterior, not a function of the claims attached to it.** EP
   writes the same columns onto an entity that it writes onto a Point (**⟨measured⟩** an Object target
   read `1.7769 / 0.9561 / 0.6502`), and the *ranking* surface computes something else entirely — the
   **mean over the claims attached to it by an `about*` edge** (**⟨code⟩** `ranking.py:838`, the
   `sum(confs)/len(confs)` at `:879`). Both are live and they can disagree. See §3 C2.
2. **Two currencies read an entity, and only one of them reads the position axis.** The affected set
   (**⟨code⟩** `ep.py:1455-1467`, filtered by `live_c`/`live_o` from `_live_only`) and the direct-edge
   leg (Batch 3, `ep.py:2087-2092`) both filter on **both** axes, read from their own Cypher — that half
   is **not** measured: **⟨measured⟩** only the direct-edge leg's effect exists (`0.0 / 0.0` for a
   position-ended endpoint), and one untouched-target posterior cannot separate the two legs. The
   **factor-input** leg reads neither position condition (`ep.py:2258`) — **⟨measured⟩**. See §5 F1.
3. **`goalState` is not an EP input at any point.** **⟨measured⟩** a goal `:Object` with
   `goalState='abandoned'` and `status='live'` **still participates**, while the same goal with
   `status='deprecated'` is dropped; `is_terminal_status` returns `False` for the first and `True` for
   the second. Separately, `derived_invalidation` *does* retire derived caches on the `goalState` word
   (**⟨code⟩** `derived_invalidation.py:181`, `:421`). The two surfaces disagree on exactly the legacy
   shape the write boundary now refuses to create. See §5 F4.

---

## 3. The doc sentences that contradict the table

Line numbers are `docs/ONTOLOGY.md` **at this head**. The measurements were taken at `340d500491`; between
that base and this head the engine moved only in `tortoise/sdk.py` (whose cited symbols are re-anchored
here) and `tortoise/mining.py` (whose cited lines did not move), so a `file:line` below still lands on
the text it describes instead of on the line that text used to occupy. Verdicts are one clause; **"fix
the code"** means the model is right and the engine does not meet it — not that the sentence should be
reworded to describe old behaviour.

⚠️ **Two items were absorbed by `main` while this PR was open** — **C4** and **C5**. Both say so in
place, with the head's line numbers, so a reader does not go and fix a sentence that is already fixed;
they are kept as the recorded reason the change was made, not as outstanding work to do.

### C1 — §10.5, L1511-1513: the terminal-exclusion sentence names one axis of two

> `_review_prune`, why, analyze) excludes terminal claims via the shared`
> `live.py predicate (status ∈ {retracted, superseded, outdated, archived,`
> `deprecated} OR `outdated=true`).`

- **The five words are exactly right — and at this head the doc says why they survive.**
  `TERMINAL_EXCLUDED_STATUSES = TERMINAL_STATUS_VALUES | {"deprecated"}` =
  `{retracted, superseded, outdated, archived, deprecated}` (**⟨code⟩** `live.py:97`, `:108`, `:114`).
  The §4.1 row has since been re-cut to a **four-word** lifecycle (`draft`/`live`/`deprecated`/
  `superseded`) with the other three declared **read-side legacy** over stored rows
  (**⟨code⟩** `docs/ONTOLOGY.md:403`, #12855) — which is exactly *why* the currency predicate must keep
  honouring them, and it settles the vocabulary half. The **axis** half below is unaffected.
- **The predicate is one axis short.** It reads the lifecycle word **and the position axis** —
  `isClosed`, or a stage in the `terminal` slot (**⟨code⟩** `live.py:212-214`, `:372`, `:588`).
- **Verdict: the doc is stale — the code is ahead.** The Cypher legs already read both axes; the
  sentence is how a future reader re-derives the half-predicate.
- ⚠️ **But the corrected sentence is a rule one arm of the engine does not meet:** `ep.py:2258` reads
  two of the four conditions. On that arm the code is behind the model — **fix the code** (§5 F1).

### C2 — §2, L94-96: "state confidence is derived at read time … never stored independently"

> `> lifecycle writes on Objects. State confidence is derived at`
> `> read time from the attached Points' EP confidence (§11) — never stored`
> `> independently on the Object.`

- **Both reads exist, and the sentence denies one.** The derived read is real (`ranking.py:838`); the
  **stored** read is also real — EP writes `confidence` / `posterior_alpha` / `posterior_beta` onto the
  entity (**⟨code⟩** `ep.py:632`, `:822`; **⟨measured⟩** an Object target took all three).
- **The citation is dead.** §11 (L1537-1553) describes `compute_reputation` only; it says nothing about
  an Object's state confidence.
- **Verdict: the doc is stale — the code is right.** The model requires the stored belief (#7813 /
  #7853 rule that confidence propagates across all four labels; an entity that holds no belief cannot
  propagate one), and the engine implements it. The sentence must name **both** reads and say which
  question each answers.

### C3 — §2, L105: "Facts = confidence 1.0"

> `… Reputation is derived at query time. Facts = confidence 1.0.`

- **No mechanism seeds a node at 1.0 from being a fact.** The only seed is
  `coalesce(posterior_alpha, ep_alpha, 1.0)` (**⟨code⟩** `ep.py:801`) — and there the two `1.0` are
  **α and β**, i.e. `Beta(1,1)`, mean **0.5**: vacuity, the opposite of certainty.
- **A caller-set `confidence: 1.0` is inert.** It is not an EP input at all (**⟨code⟩** `ep.py` never
  reads `n.confidence`), and the run **overwrites** it — **⟨measured⟩** created at 1.0, served at 1.0,
  seeded at 0.5, rewritten to 0.5.
- **It also contradicts §2's own table** three lines above (L102), which records a structural edge's
  confidence as **"None (factual)"**.
- **Verdict: as written, the DOC is wrong** — this is a *factual* error, not a stale mechanism: no
  version of the engine has ever done this, and the same section says the opposite.
- ⚠️ **If the intent is a target** ("a fact *should* be believed at 1.0"), then it is **fix the code**,
  and only the owner can say which. This is the one item in this audit that is an owner ruling rather
  than a measurement — see §6.1.

### C4 — the `guideline` word at three sites (L417, L457, L1056-1057 at the measured base): a vocabulary that was removed

> At the measured base:
>
> `(met, active, blocked, unmet, superseded, abandoned, guideline)` — §4.1 L417 and §4.3 L457
> `> superseded, abandoned, and guideline (directional, no pass/fail — the` — §5 L1057

- `GOAL_STATE_VALUES` is **six** words and does **not** contain `guideline` (**⟨code⟩** `sdk.py:379`).
  `guideline` was **REMOVED by owner ruling** on 2026-11-16 under #13035 (**⟨code⟩** the comment at
  `sdk.py:371-372`), because *"whether a goal is directional is a question about PRIORITY, not about
  outcome"*.
- **Verdict at the measured base: the doc was stale — the code was ahead.** Three sites, one
  vocabulary.
- ✅ **Absorbed by `main` while this PR was open — measured at this head.** v3.36 (#13404,
  owner-approved) removed `guideline` from both `goalState` rows — L417/L457 now read *"`guideline` is
  **not** an achievement: whether a goal is directional is a question about **priority**, and it is
  unassigned (#11824)"* — and replaced the §5 list at L1056-1057 with the six-word vocabulary plus a
  status note at **L1059-1068** that keeps the #6792 form ruling *and* says where it went. That is
  exactly the pointer-preserving reversal the warning below asks for, so **no further edit to
  `docs/ONTOLOGY.md` is needed for this item**; it is recorded here as the reason the change was made.
- ⚠️ The base L1057 span also carried a **pointer to where a decision lives** (*"the owner's #6792 form
  ruling"*). This is a **reversal of a recorded ruling**, so deleting the word silently would erase the
  pointer and leave the next reader unable to tell a ruling from an accident. The reversal belongs in
  `ONTOLOGY-CHANGELOG.md` — and at this head it landed with one.

### C5 — "achievement is a SEPARATE axis" (L1051, and the "Separate from `status`" clause at L417 / L457)

- True, but **incomplete in a way that changes behaviour.** Two of the six words are the **lifecycle's
  endings spelled on the achievement axis** — `GOAL_ENDING_STATES = {abandoned, superseded}`
  (**⟨code⟩** `sdk.py:393`; `abandoned` *is* `deprecated` for a goal) — and the write boundary
  **refuses** a goal that ends on one axis while leaving the other running, in either direction
  (**⟨code⟩** `_refuse_inconsistent_goal_ending`, `sdk.py:6384`).
- As written ("separate", "orthogonal"), the doc reads as *either axis can express an ending*. It
  cannot: `status` ends the record, `goalState` never does (**⟨code⟩** `live.py:159` reads
  status/outdated/isClosed/slot and nothing else).
- **Verdict: the doc is incomplete — the code is right** on the write boundary; the doc must say which
  axis ends a record, and why the two are held in agreement.
- ✅ **Absorbed by `main` while this PR was open — measured at this head.** Both `goalState` rows now
  carry *"The two endings are the lifecycle's endings on this axis — `abandoned` is `deprecated` for a
  goal, `superseded` is `superseded` — and a write whose ending disagrees with `status` is refused"*
  (L417/L457), which is what the verdict above asks the doc to say; **no further doc edit is needed for
  this item**.

### C6 — "`goalState` is never `draft`/`live`" (L1065-1066) vs. `derived_invalidation` reading `goalState` as an ending

- `RETIRED_GOAL_STATES = {"superseded", "abandoned"}` (**⟨code⟩** `derived_invalidation.py:181`) and its
  reason arm at `:421` retire a goal's derived caches **on the `goalState` word** — a surface that *does*
  answer currency from `goalState`.
- **⟨measured⟩** the same goal shape is still counted by EP: `goalState='abandoned'`, `status='live'` →
  the target moved (participated), and `is_terminal_status(...)` = **False**. With
  `status='deprecated'` the target was untouched and `is_terminal_status` = **True**.
- **Verdict: fix the code.** The doc's rule — `goalState` is not a currency word — is the model; the
  engine has one surface that disagrees. The code's own note (`derived_invalidation.py:179-180`) claims
  *"this arm and the readers that ask `status` now answer about the same goals"*, which is false for a
  legacy row that the write boundary can no longer create but must still read.

### C7 — §10.5's decay claim (L1501-1509): "every terminalizing write … decays to VACUITY"

> `**Terminal posterior vacuity decay (#2490):** every terminalizing write`
> `(retract/supersede/invalidate/assess_source + the rebuild folds) decays the`
> `terminal claim to VACUITY — confidence=0.5, posterior (1,1) — atomically`

- **⟨measured⟩** `retract_point` decays exactly as documented: `0.9 → 0.5`, `(9,1) → (1,1)`.
- **⟨measured⟩** an ending on the **position axis** does **not**: writing `isClosed=true` through the
  door left `confidence 0.9 / posterior (9,1)` **untouched**. So a position-ended node keeps a
  full-strength pre-terminal posterior indefinitely — which is precisely the #2490 shape this paragraph
  says cannot happen ("*an include-terminal surface never shows a frozen pre-terminal posterior*").
- **Verdict: fix the code.** The doc enumerates the *word-writers* as "every terminalizing write", but
  the position axis is a second source of the same ending, and the decay is bound to the writers rather
  than to the predicate.

### C8 — verified correct, no contradiction (checked sentence by sentence)

Listed so the owner can see what was examined rather than assumed:

| Doc site | Claim | Verdict |
|---|---|---|
| §2 Epistemic row, L68 | Operators connect `:Point`/`:Subject`/`:Object`/`:Event`; the write path admits all four | **correct** — **⟨measured⟩** all four writable in both slots; **⟨code⟩** `live.py:667` |
| §3.1 operator rows, L117-118 (`IMPL`/`NAND` from/to all four labels), L225 (§3.8) | same, plus the four-value direction vocabulary | **correct** |
| §8 Reification rule, L1302-1303 | "The operator node itself is always a `:Point {is_operator:true}`; its children/targets may carry any of the four labels" | **correct** — **⟨measured⟩** every operator minted over an Object/Event/Subject endpoint is `['Point'], is_operator=true` |
| §8 operator-less propagation, L1329-1331 | a direct `IMPL`/`NAND` between any two plain epistemic nodes propagates | **correct, in the strong sense** — the direct-edge leg reads **both** currency axes: **⟨measured⟩** it dropped an `isClosed` and a `terminal`-slot endpoint |
| §8 `OVERRIDES`, L1306-1309 | pre-#7813 "write-only" rule is reversed; an Event target is epistemic and propagates | **correct** — **⟨measured⟩** an Event source *and* an Event target both received a posterior |
| §11, L1537-1553 | reputation is `Subject -[:performs]-> Event -[:IMPL]-> Point`, derived not stored, Point-only | **correct** — **⟨code⟩** `sdk.py:59698`, `MATCH (e)-[:IMPL]->(p:Point)` |
| §10, L1467-1474 | recency modulation of inherited priors; no blunt time decay | **no contradiction found** with the table |

---

## 4. What the table does *not* say

Deliberately out of scope for this pass, listed so absence is not read as a verdict:

- **No replacement text.** No before/after hunks, no proposed wording. Approving the table and the
  verdicts is the gate; the wording is the next change.
- **No code change.** The four defects in §5 are named with a reproduction, not fixed here.
- **No claim about what the model *should* be.** Where the question is a belief-model decision, it is
  in §6 as undeterminable, not decided here.

---

## 5. Where the code is behind the model — **fix the code**

Four defects. Each is a place where the doc's own rule (or `live.py`'s own canonical predicate) is not
met, so the remedy is the engine, not a sentence describing old behaviour. **F1 is the one that affects
belief correctness**: it lets an ended node vote.

### F1 — an ended node still votes when it is an operator *input* (belief correctness)

`ep.py:2258` decides participation in Python from two columns, and Batch 2's projection
(`ep.py:2228`) does not even **fetch** the other two:

```python
is_terminal = status in TERMINAL_EXCLUDED_STATUSES or bool(outdated)
```

The Cypher predicate it is meant to mirror reads **four** conditions (**⟨code⟩** `live.py:159`:
status word, `outdated` flag, `isClosed`, `pipelineSlot != 'terminal'`). Batch 2's own comment
(`ep.py:2243-2246`) says its status set is *"derived from `TERMINAL_EXCLUDED_STATUSES` so the two
cannot drift"* — true for the **words**; the position axis is a second *source* of the same answer and
never reached this arm. **⟨measured⟩** — every ended shape contributed, the two Point shapes landing
on the live control's posterior and the `:Object` shape on its own `(9,1)` prior:

| operator input | target posterior (live control: `1.6391 / 1.0327`) |
|---|---|
| `status='superseded'`, prior `(8,2)` | dropped — the status half works |
| `isClosed=true`, `status='live'`, prior `(8,2)` | **`1.6391 / 1.0327` — contributed** |
| `pipelineSlot='terminal'`, prior `(8,2)` | **`1.6391 / 1.0327` — contributed** |
| `:Object` `isClosed=true`, posterior `(9,1)` | **`1.7769 / 0.9561` — contributed** |

The direct-edge leg **drops** a position-ended endpoint (**⟨measured⟩** `0.0 / 0.0`), and the affected
set filters on the same two axes in its own query (**⟨code⟩** `ep.py:1455-1467` — read from the Cypher,
**not** separately observed: an untouched target prints `0.0 / 0.0` whether the affected set excluded
the node or the direct-edge leg skipped the factor), so within one EP run a node can be too dead to be
re-measured and alive enough to vote.

**Fix direction:** project `isClosed` and `pipelineSlot` on Batch 2's statement and call
`live.is_terminal_status(status, outdated, is_closed=…, stage_slot=…)` — the keyword-only position arms
already exist for exactly this caller (`live.py:372`).

### F2 — the write doors do not read the position axis (one has no guard at all)

- **`create_operator` (`sdk.py:28417`) has no terminal-endpoint guard whatsoever.** **⟨measured⟩** it
  **wrote** an operator over a `status='superseded'` source, an `isClosed=true` source, and a
  `pipelineSlot='terminal'` source. The endpoint probe reads only `id` and `claimClass`
  (**⟨code⟩** `sdk.py:28984-28987`).
- **`create_direct_edge`'s guard reads two of four conditions**: `sdk.py:36039` is
  `if status in TERMINAL_EXCLUDED_STATUSES or outdated:` — so it refuses a superseded endpoint but not
  a closed one.

The damage differs by arm and is worth naming exactly: for the **word** arm the outcome is a dangling
factor — the input is stripped at `ep.py:2258`, so the factor is skipped and the run records why
through `_last_skipped_factors` (**⟨code⟩** `ep.py:2308-2311` for the log line, `:2332-2344` for the record, `:415` for the attribute; the probe run logs `Operator …: source stripped — factor skipped`). For the **position** arm there is no strip at all: the operator is
live **and** the input still votes — i.e. F2 hands F1 a shape F1 then mis-prices.

### F3 — a position-axis ending has no decay path

**⟨measured⟩** `isClosed=true` written through the door leaves `confidence 0.9 / posterior (9,1)`
unchanged; `retract_point` on the control decays to `0.5 / (1,1)`. The #2490 decay is bound to the
lifecycle-**word** writers, so the second source of an ending has no decay route — and, combined with
F1, a closed node keeps both its full-strength posterior and its vote.

### F4 — `derived_invalidation` and the currency predicate disagree about `goalState`

**⟨code⟩** `derived_invalidation.py:181` / `:421` retire a goal's derived caches on
`goalState ∈ {superseded, abandoned}`; **⟨code⟩** `live.py:159` never reads `goalState`;
**⟨measured⟩** EP still counts a `goalState='abandoned'`, `status='live'` goal. So the same record is
*ended* to its derived caches and *live* to belief. The write boundary now refuses to create that shape
(`sdk.py:6384`), but stored rows predate it and the reader rule is what must be coherent.

---

## 6. Undeterminable in this pass

1. **Whether "Facts = confidence 1.0" (L105) is a stale sentence or a model target.** A measurement
   cannot settle it: if the model *is* "a fact is believed at 1.0", the code is the defect. This is the
   one owner ruling the audit needs (C3).
2. **What an `:Event`'s seed *should* be.** An Event seeds at vacuity, and after a run EP writes
   `(1,1)` into both its `posterior_*` pair and its `ep_alpha`, so an Event can never acquire a
   non-vacuous prior except by propagation (**⟨measured⟩** Event belief after a run: `confidence 0.5 /
   posterior (1,1)`). Whether a dated occurrence should carry a level of its own is a belief-model
   question, not a measurement.
3. **Whether a `completed` / `in_progress` `:Object` should stop voting.** EP uses the Point
   vocabulary, so neither is an ending for belief (**⟨measured⟩** a `completed` Object participated),
   while the Object read family excludes a four-word subset (**⟨code⟩** `commit_ops.py:88`:
   `{superseded, deprecated, archived, retracted}`). Whether a completed work item should stop being
   evidence is a model decision; I did not take it.
4. **Declared priors for entities.** Their absence is **deliberate**, not an oversight: the refusal at
   `sdk.py:41157-41161` names **#9063** as *"the open belief-model decision"*. Owner-owned.
5. **`:Batch`'s intended role.** It is not epistemic today and I found no writer that gives it an EP
   role; `mining.py:57-58` classifies it as operational metadata, not ONTOLOGY. I cannot say what it
   *should* be, so the table says only "no EP role".
6. **Whether the two seed readers diverging is intentional.** The warm batched load
   (`ep.py:507`) reads `coalesce(ep_alpha, 1.0)`; the direct read (`ep.py:801`) reads
   `coalesce(posterior_alpha, ep_alpha, 1.0)`. For a `baseline_set` node these are **different values**
   by design — `ep_alpha` is deliberately the immutable prior (`ep.py:629-633`) while
   `posterior_alpha` is the last posterior — so the same node seeds from its prior on one path and from
   its posterior on the other. Whether that is the intended reading is not determinable from the code,
   and I did not measure a baseline'd claim through both paths. **Flagged, not asserted.**
7. **The probes' own reach.** They exercise the ordinary run path (`_load_cache`). They do not drive the
   cold `_read_node` path, the `warm_start` path, or `analyze`'s BFS. A claim about those paths here is
   **⟨code⟩**, never **⟨measured⟩**.

---

## 7. Evidence index

**Measured on this branch, this lane's container (`fdb-lane-783bdc9798`, port 16416), base
`340d500491`.** Both probes completed with `DONE` and identical verdicts on repeated runs.

⚠️ **`measure_per_labels.py` can die mid-run with a raw `redis.exceptions.TimeoutError`** — observed
once, at the `:Subject` target-slot write in section E (the loop's second iteration). That is a
**client read timeout** — a query slower than the client's socket timeout on a loaded host — not a
failed assertion and not lost data: re-issuing the same run exits `0` and prints `DONE`.
**Re-run it before concluding anything from a traceback.**

| File | What it holds |
|---|---|
| `probes/measure_per_labels.py` | A — wiring per label (`:Point`/`:Object`/`:Event`/`:Subject` writable in both slots, `:Source` refused, `:PipelineStage`/`:Batch` not epistemic, `EPISTEMIC_LABELS`); B — the `confidence=1.0` entity seed, its overwrite, and the `set_point_baseline` refusal; C — the position axis on operator inputs (the contributed/dropped verdict is **derived** from the posterior's asymmetry, never typed by hand); D — `goalState` shapes against `is_terminal_status`; E — `:Event` source and target, and the `_hydrate_evidence` universe; F — the contested scan per label |
| `probes/measure2_door_and_axis.py` | 1 — `isClosed` through the door (no decay); 2 — `retract_point` decay control; 3 — direct-edge position axis **and** the door guards per axis; 4 — per-label participation as an operator input; 5 — a `completed` Object input; 6 — an Object as **target**; 7 — operator-node shape per endpoint label; 8 — an entity never reaches the evidence set |

Ports, graphs and the run recipe: §1. The probes build throwaway graphs and never touch the engine,
the docs, or any shared container.
