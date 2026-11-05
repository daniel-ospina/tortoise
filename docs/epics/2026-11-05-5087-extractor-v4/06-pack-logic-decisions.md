---
title: "Pack connection logic — the granular decisions (#11083)"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#5087, #11083, packs/, ONTOLOGY.md"
---

# Pack connection logic — the granular decisions

**What this is.** The audit behind `#11083` found three classes of problem in the shipped packs. Each is a **granular decision for the owner**, because each either edits a pack's declared business meaning or edits canonical ontology — and both are owner-gated. Every option is stated with the canonical document's own words, and where the mechanical fix is **wrong**, that is said.

**What I checked against, so the verdicts are not opinion:**

- `docs/ONTOLOGY.md` §5 (the core kind vocabulary, incl. the commitment-state family) and §3.6 / §3.7 (the allowed edge topology);
- `tortoise/pack_registry.py` — `SEMANTICS_VALUES`, `SEMANTICS_DIRECTIONS`, `relation_write_verdict`;
- the packs themselves, verbatim.

---

## Class A — the `project-management` pack: 4 relations that bind no write path

All four are declared in the legacy `from`/`to` shape, so they carry **no `mechanism`** and `relation_write_verdict` returns `predicate-not-in-allowlist`. The registry warns on each at load. **The content is not junk** — each names a real project-management relation — but the question the owner asked is the right one: *add the missing mechanism, or is this ontology drift?* Verdict per row:

### PM-1 · `partOfSprint` — **drift. Retire.**

Declared **`Object → Object`** — both endpoints unconstrained, and the pack declares no `description` on it; *“part of a sprint”* is what the predicate name means, not the manifest's words. ONTOLOGY §3.7 states: *"`hasPart` … Inverse traversal (`<-[:hasPart]-`) covers 'part of' — **no separate `partOf` edge**."* A domain-specific "part of" predicate is exactly what the canonical model forbids, and `partOfSprint` cannot be validated because both endpoints are the unconstrained `Object`.

- **Option A:** retire it; composition is `hasPart` (`sprint --hasPart--> card`), inverse-traversed as "in sprint".
- **Option B:** keep the verb and constrain it (`fromKind: pm:sprint, toKind: pm:card`, semantics `hasPart`, mechanism `IMPL`).
- **Recommended: A.** The specificity belongs in the endpoint kinds, not the verb — the canonical doc says so in as many words, and a second "part of" spelling creates two answers to one question.

### PM-2 · `onBoard` — **drift. Retire, or mark declaration-only.**

Declared **`Object → Object`** — again both endpoints unconstrained, with no manifest `description`; *“card is on a board”* is the intended sense, not the declaration. A kanban board is a **view** over cards, not a container of them: the card's state is already carried by `cardMoved` events and the board is derived. Storing `card → board` writes a projection into the durable layer, and it goes stale the moment a card moves.

- **Option A:** retire — the board is recomputed from card state + `cardMoved`.
- **Option B:** keep as `declarationOnly: true` with that reason, so the word survives in the vocabulary without pretending to be writable.
- **Recommended: A**, with B as the fallback if a capture is ever observed asserting it.

### PM-3 · `assignedTo` — **real relation, wrong spelling. Map to `managedBy`.**

§3.6 has the canonical pair: `managedBy` (Entity → Subject, *operational responsibility*, RACI Responsible) and `ownedBy` (Entity → Subject, *accountability*). "Assigned to" is operational responsibility, i.e. `managedBy`.

- **Option A:** declare `managedBy` (predicate `managedBy`, fromKind `pm:card`, toKind `Subject`, mechanism `IMPL`) — its direction is `->` from the table, so **no `direction:` key is declared**: the manifest schema has no such field and a relation carrying one is silently ignored, which is the gap DM5 in `05-plan.md` names.
- **Option B:** keep the word `assignedTo` as the predicate with semantics `managedBy`.
- **Recommended: A.** One spelling per meaning; `assignedTo` stays a *synonym* in the kind's vocabulary, not a second edge predicate.

### PM-4 · `blocks` — **real relation, inverted direction. Do not simply add a mechanism.**

Declared **`Object → Object`**. Blocking is not a meaning in `SEMANTICS_VALUES`; the nearest is `dependsOn` (direction `->`, source→target). ⚠️ **A blocks B means B depends on A** — so declaring `blocks` with semantics `dependsOn` would propagate confidence from blocker to blocked, the opposite of the truth.

- **Option A:** model the dependency from the dependent side: `pm:card --dependsOn--> pm:card` ("this card depends on that one"). No new vocabulary, direction correct.
- **Option B:** keep the verb `blocks`, declare `declarationOnly: true` and record that the canonical vocabulary has no inverse relation — the graph then holds the words without asserting a direction it cannot defend.
- **Option C:** add an inverse/`blocks` meaning to `SEMANTICS_VALUES` with direction `<-`. That is an ONTOLOGY §8 change — the vocabulary is **closed** by rule, so this needs owner approval and a direction argument.
- **Recommended: A**, with C named as the honest cost if the pack's authors insist the business says "blocked by".

---

## Class B — two packs declare a meaning core has RETIRED (four declarations, ONTOLOGY §8, #7852)

Warn-not-block, which is why they survived. **The mechanical replacement is safe for only one of the four** — the other three are direction-inverted or contradict their own declared mechanism. This is the granular set the owner asked to rule on.

| # | pack · relation | declared | §8 replacement | **is the replacement correct?** |
|---|---|---|---|---|
| **RET-1** | `product-strategy` · `competesWith` (`feature → competitor`) | `opposes`, mechanism `NAND` | `mutuallyExclusive` (`<->`) | ✅ **Correct and safe.** `mutuallyExclusive` is a NAND meaning per §8's own mechanism table, and the direction is symmetric. |
| **RET-2** | `venture` · `funds` (`fundingAgreement → program`) | `funds`, mechanism **`IMPL`** | `transacts` (`-`) | ⚠️ **Conflicts.** `transacts` declares **no confidence transfer** (`-`), while the relation declares `mechanism: IMPL` (raises the target). Keeping IMPL and adding `transacts` asserts both. Alternatives: **`causes`** (`->`, IMPL) — a funding agreement causes the program — which matches the declared mechanism and direction. |
| **RET-3** | `venture` · `producedBy` (`asset → program`) | `produces`, mechanism `IMPL` | `causes` (`->`) | ⚠️ **Direction-inverted.** The declared direction is `asset → program`, but the business says the **program produces the asset**. Applying `causes` as-is asserts the asset causes the program. Fix the **relation direction** (`program --produces--> asset`, `causes`) or use `hasPart` if the asset is an output component of the program. |
| **RET-4** | `venture` · `holds` (`investment → Subject`) | `holds`, mechanism `IMPL` | `ownedBy` (`->`) | ⚠️ **Direction-inverted.** §3.6 defines `ownedBy` as **Entity → Subject** = "the entity is owned by the subject". `investment → Subject` therefore asserts the investment is owned by the company — the opposite of what the pack's own comment says (*"the fund's stake in — and ongoing relationship with — a company it backs"*). Alternatives: `transacts` (`-`, drop `IMPL`), or flip to `Subject --ownedBy--> ?` which is not what the sentence means. |

**What I am asking for, per row:** approve / reject the replacement, and for RET-2/3/4 approve the **alternative I propose** (RET-2 `causes`; RET-3 flip the direction; RET-4 `transacts` with `IMPL` dropped, or keep `holds` as a distinct meaning if the business needs a word for shareholding).

⚠️ **Why this cannot be decided mechanically:** the #7852 retirement list names a *replacement value* per retired word, but the replacement is only correct if the relation's **direction and mechanism** also fit. Three of four do not. A lane that "aligns to §8" by find-and-replace would invert two meaning directions in the graph, silently.

---

## Class C — `goal`, `objective`, `initiative`: what exists, and what is actually missing

**Answering the question directly, from the canonical document:**

1. **`goal` IS a core Object kind** — ONTOLOGY §5's commitment-state family: `strategy, plan, goal, target`, each with a `goalState` axis (`met`/`active`/`blocked`/`unmet`/`superseded`/`abandoned`/`guideline`). It is **not** a pack kind.
2. **`objective` is not a kind** anywhere, and the research says it need not be: open-okr's method spec treats *"goal" as synonymous with objective*, and Microsoft Viva Goals models **Objective / Key Result / Initiative**. Our `goal` is the objective layer.
3. **`initiative` is not its own kind** — it is declared as a **synonym of `dev:theme`** (`packs/dev/manifest.yaml:60-64`, alongside `strategic theme`, `workstream`, `pillar`). And **`goal` is declared as a `nearMiss` of `theme`** (`:67`) — i.e. the pack says "if it looks like a goal, it is *not* a theme".
4. **The Key Result layer has a home**: `dev:indicator` (*"a measured value of system behavior"*), and `target` is a core commitment-state kind. open-okr distinguishes exactly this (`key_results` with metric / maintain / milestone / baseline types).

**So the gap is not a missing kind. It is that `goal` has no connections at all.** No shipped relation declares `goal` as an endpoint, in any pack. The commitment-state layer exists, is validated, and is unreachable — which is why the graph has a flat catalogue and no business spine.

**Research (converged):** SAFe calls strategic themes *"portfolio-level business objectives"* and portfolio epics *"large enterprise initiatives"*; open-okr separates objectives (qualitative, owned, time-bounded), key results (verifiable), and initiatives (planned work **linked to** key results, many-to-many); Viva Goals models the same three. Our vocabulary maps as **goal ≈ objective**, **indicator ≈ key result**, **epic ≈ initiative** — with one inconsistency the sources do not support: **`initiative` as a synonym of `theme`**. An initiative is *work*; a theme is an *objective*.

### KIN-1 · Where does the word "initiative" belong?

- **Option A (recommended):** move the `initiative` synonym from `dev:theme` to `dev:epic` — an initiative is a body of work, and `epic` is the work-bundle kind. `theme` keeps `strategic theme` / `workstream` / `pillar`.
- **Option B:** leave it on `theme`, accepting that the same word then names an objective in our graph and a work-bundle in SAFe/OKR tools — the exact class of drift this audit is fixing.
- **Option C:** both, as a declared `equivalentTo`.

### KIN-2 · Add the missing connections (no new kinds, no new vocabulary)

The spine the owner described can be built from **canonical predicates that already exist**, with zero ontology additions:

| relation | why it is canonical | direction |
|---|---|---|
| `core:goal --ownedBy--> Subject` | §3.6 `ownedBy` = **Entity → Subject**, *accountability* (RACI Accountable) | `->` |
| `dev:epic --addresses--> core:goal` | §8 `addresses` = IMPL, epistemic | `->` |
| `dev:indicator --addresses--> core:goal` | §8 `addresses` = IMPL (a metric serves a goal) | `->` |
| *(existing)* `dev:theme --hasPart--> dev:epic`, `dev:epic --decomposesInto--> dev:issue`, `dev:issue --implements--> product-strategy:feature` | already declared and writable | ⚠️ **`dev:theme --hasPart--> dev:epic` is the direct hop the four-level path routes around** — and it is declared (`packs/dev/manifest.yaml:148-153`) and listed in `hierarchies:` (`:192-194`) **today**, with **no ruling retiring it**. If the four-level path is to be *exclusive*, retiring that declaration is a pack-vocabulary edit `#11000` owes; until it lands the edge stays writable, so `E2E-4`/T8's negative case may not use it as an undeclared pair |

That closes **company → goal → epic → issue → feature** — the spine the sixth acceptance row measures — using only words the canonical document already rules. **Option B** is to add `goal`/`objective` as *pack* kinds with relationships declared by the pack that owns the business vocabulary; **Option C** is to leave it and let the row stay red.

- **Recommended: Option A** for KIN-2 — because it adds three declarations and no vocabulary, which is the smallest change that makes the owner's goal measurable. Option B is defensible if the intent is that packs (not core) own commitment state, but it is a larger change and it duplicates `goal`.

---

## What is *not* in question

- **`pm`'s four relations are not junk** — three name real business facts and one (`onBoard`) names a view. The pack is not invalid; it is **un-migrated**, and two of its verbs are drift against the canonical topology.
- **The core vocabulary is closed.** Nothing here proposes a new meaning except PM-4 Option C, which is named with its cost.
- **ONTOLOGY §5 is untouched by this document.** KIN-2 uses kinds that already exist. If any option here would add or rename a kind, it needs Daniel's approval before a lane writes it.

---

## Round 2 — the owner's rulings, and what the research changed

Five answers came back, four of which asked for research. The research **changed three verdicts** and found one gap in the census itself.

### SEM-1 · "we need something to denote movement of funds … maybe `funds` comes back"

**Research (REA2 formalisation; a core ontology for economic exchanges):** money movement is modelled as an **event** — a *transfer* — with `from` / `to` agents, `provide` / `receive` participation relations, and `inflow` / `outflow` resource flows. The sender's and recipient's perspectives are *cash-disbursement* and *cash-receipt*.

**And the pack already does exactly that.** `packs/venture/manifest.yaml:75-83` declares the **`disbursement` event kind**: *"Events carry NO state. A release of funds is a `disbursement` event; the tranche Object keeps `pending → conditions-met → released`"*, with the kindDef at `:271-275` — *"the dated movement of money against a tranche — a release of funds"*. Two relations are also already there (`holds`, `funds`).

So **the movement of funds has a home**; what is missing is the *durable provision* relation's meaning.

- **Proposal:** `funds` comes back **as a meaning in its own right** — REA's `provide`: *source provides economic resources to target* — with direction `->` and mechanism `IMPL`.
- ⛔ **It must not be `transacts`**, which declares direction `-` (no confidence transfer) and therefore contradicts the relation's own `mechanism: IMPL`.
- **Granular choice:** name the meaning **`funds`** — **DECIDED 2026-11-05: `funds`.**
- **What does *not* follow:** no new relation for *movement*. A movement is an event; adding a durable `movedFunds` edge would duplicate `disbursement` and drift from the pack's own state/event split.

### SEM-2 · `competesWith` → `mutuallyExclusive` — **approved as-is.**

### SEM-3 · "not sure production and causation are similar enough" — **you are right, and they are not**

**Research:** W3C PROV defines generation as *"the completion of production of a new entity by an activity"* — `prov:wasGeneratedBy` links **an entity to an activity**, and the spec is explicit that it does **not** assert a general causal relation. The BFO/RO mapping goes further: `prov:wasGeneratedBy` maps to *participation in a process*, while `prov:wasDerivedFrom` is what maps to a **causal-influence** relation between entities. A survey of causation in ontologies notes that *"produces" is often used as causal vocabulary* but **should not automatically be treated as equivalent to causation**.

**So `causes` was the wrong replacement.** And the declaration fits neither canonical shape:

| what the business may mean | canonical form | fits the declared `asset → program`? |
|---|---|---|
| the program *produced* the asset (generation) | `prov:wasGeneratedBy`: **entity → activity** | ⚠️ only if `program` is an Event/activity — it is an **Object** (`Project` subclass) |
| the asset is *derived from* the program | §3.7 `wasDerivedFrom` (prov) | ✅ Object→Object |
| the asset is *part of* the program | §3.7 `hasPart` | ✅ **already declared** — `packs/venture/manifest.yaml:98-99`: `hierarchies: [{path: Program → Asset, type: hasPart}]` |
| the program *causes* the asset | §8 `causes` (entity→entity, IMPL) | ❌ research above; and the declared direction is inverted |

- **Proposal (revised): retire `producedBy`.** The pack declares the same fact twice — once as a relation, once as its own `hasPart` hierarchy — and the relation's meaning is neither a core meaning nor causation. **Option B:** if the business means *derivation* rather than *containment*, declare §3.7 `wasDerivedFrom` (`asset → program`) instead. **Option C (`causes`) is withdrawn.**

**⛔ ADMINISTRATIVE RULING 2026-11-05, and it overrides the paragraph above: `producedBy` STAYS.** The owner's reason, verbatim: *"producedBy sounds correct because it's not membership (hasPart) but causation/production."* Accepted — containment and production are different assertions, so the two declarations are **not** duplicates and both stay. What the ruling requires instead is a **meaning that exists**:

- **Add `produces` to `SEMANTICS_VALUES`** (ONTOLOGY §8) — *"the target brought the source into existence"*, aligned to the PROV generation family but at **entity → entity** level, which is what this declaration is (both ends are Objects; `prov:wasGeneratedBy`'s activity form does not apply).
- **Its direction is `<-`** (target → source): the declared edge is `asset → program`, and production runs `program → asset`. `SEMANTICS_DIRECTIONS` already admits `<-`, so this needs no new machinery — but the value must be pinned there, and the table's pin (`SEMANTICS_VALUES` ≡ `SEMANTICS_DIRECTIONS` keys) makes that a test-visible edit, not a quiet one.
- ⚠️ **One value, one direction.** Because direction is recorded per *value*, the predicate spelling must follow: `producedBy` (`asset → program`) is consistent with `produces`/`<-`. A pack wanting `program --produces--> asset` would need direction `->` and therefore a **different** spelling. §3.8's structural `produces` predicate is a different layer and does not conflict — but note what it admits: **any → Object or Point** (`schema:result`, `docs/ONTOLOGY.md:220`), where *"Event→artifact is the encouraged pattern"*; §3.5's row at `:189` puts the same point more strongly — *"the event→artifact shape is the encouraged pattern, not a restriction"*.
- ⚠️ **What this accepts:** `producedBy` and the `Program → Asset` `hasPart` path will both be written where both are true. That is now intentional rather than drift, and the census will show two edges where an earlier reading expected one — the census must not treat that as a duplicate.

### SEM-4 · "`ownedBy` is the right one, we just need the proper direction"

**Agreed — and that is exactly the problem.** §3.6 defines `ownedBy` as **Entity → Subject**, *"Accountability, data boundary"*, RACI Accountable. The declaration is `investment → Subject`, which under that definition says **the investment is owned by the subject**. Whether that is right depends on **which** subject, and the manifest does not say:

- **If the intended subject is the FUND/owner** — the declaration is already directionally correct and needs only `mechanism: IMPL` + `semantics: ownedBy`. ✅
- **If the intended subject is the COMPANY the fund backs** — then `toKind: Subject` is the *target of the stake*, not the owner, and  `ownedBy` would be wrong in substance (not just direction). That link needs its own relation: §3.8's `produces` admits **any → Object or Point** and never a Subject, so the Object→Subject options are `ownedBy` (accountable), `managedBy` (responsible), or `transacts` (`-`).

**Granular question, and it is one word: is `venture:investment --ownedBy--> Subject` the fund that owns the investment, or the company it invests in?**

- **DECIDED 2026-11-05 — left deliberately OPEN:** *"ownedBy Subject or Object, we leave it open because it can be quite varied (a person can own something, or a person can own a company that owns something)."* So the **target may be a Subject or an Object**, and the pack does not have to pick one.
- **What that requires of the canonical document (ONTOLOGY §3.6/§3.7):** `ownedBy` today is defined as **Entity → Subject** only. The ruling widens it to **Entity → (Subject | Object)**, which is a topology change and therefore needs exactly the approval just given.
- **How the ambiguity is resolved mechanically — the edge's own `direction`, not the predicate:** `->` means the **target** owns the source (`investment --ownedBy--> fund` = the fund owns the investment); `<-` means the **source** owns the target (`investment --ownedBy--> company` with `<-` reads *the investment has an ownership stake in the company*). The value `ownedBy` therefore stays single-direction-agnostic at the table level and the per-edge direction carries the meaning.
- ⚠️ **The risk this accepts, stated so it is not discovered later:** with an open target and a per-edge direction, `ownedBy` no longer distinguishes *"A owns B"* from *"A is a stake in B"* by its predicate alone — a reader must read the direction field. `venture:holds` therefore resolves to `ownedBy` **with the direction inverted** (`<-`, the investment has an ownership stake in the company) — the direction carries the sense. **Polarity check, because these are two different claims and the record is easy to misread:** the owner's ruling **keeps the word** — *"`venture:holds` stays as its own predicate for the ownership-interest sense (FIBO draws the same distinction)"* (`#11083` comment `6667121779`, ruling 3) — and what changes is its **meaning**: `SEMANTICS_TRANSITION` resolves `holds` → `ownedBy`, and `ownedBy`'s table direction is `->`, so the stake sense has to ride the **per-edge** `direction` as `<-` — which is ruling 3's own words, *"the direction field carries the meaning, not the predicate"*. ⚠️ **The table cannot produce that `<-` by itself:** `SEMANTICS_DIRECTIONS['ownedBy']` is `->`, and no `SEMANTICS_VALUES` member yields `<-` today (`sdk.py:24321`, *"no ruled semantics produces `<-`"*), so resolving `holds` through the table alone writes the inversion this section warns about, silently — see the per-edge field `05-plan.md` DM5 records as owed. Round 2's "keep `holds` as its own predicate" therefore **stands**; it was never superseded, and **no `OVERRIDES:` entry is owed for it** — `#7848`'s marker states that `holds` is *unchanged by this ruling* and keeps its entry. The census reports direction per row so a mis-signed edge is visible rather than plausible.

### KIN-R · "is theme bigger than epic?" — **yes, and the level in between is what is missing**

*(`R` for the ruling on `KIN-1` above; the owner's answers are numbered `SEM-1`… and `KIN-1`/`KIN-2` as they were asked.)*

**Research (Atlassian's own scaling-agile material, Jira Align/Portfolio docs, Tempo's comparison):** the common chain is

```text
Strategic theme  →  Initiative / Portfolio epic  →  Epic  →  Feature  →  Story
```

- A **strategic theme** sits **above** the portfolio epic and expresses a *portfolio-level business objective* (SAFe's wording).
- An **initiative** is the level **above** a Jira epic: *"initiatives group multiple epics and can span multiple teams"*; Jira Align commonly calls the same level a **portfolio epic**, and organisations use "initiative" as its synonym — so **theme > initiative ≡ portfolio epic > epic > story**.
- Themes are often *labels or strategic categories* rather than a delivery level; the delivery hierarchy starts at initiative/portfolio epic.

**Our chain is `theme → epic → issue`.** That is three levels where the common model has four *if* the business distinguishes portfolio-level from team-level bodies of work. Our `dev:epic` is *"a large body of work decomposed into **issues**"* — i.e. it decomposes into work items, not into other epics, so **our `epic` sits at the Jira-epic level and the initiative / portfolio-epic level is absent.** That is why the word "initiative" had nowhere to go and was filed under `theme`.

| option | what it means | cost |
|---|---|---|
| **A · `initiative` becomes a synonym of `dev:epic`** | collapse: our three levels are theme → (initiative=epic) → issue | no new kind; **loses** the portfolio/team distinction — and a theme, which is a strategic objective, then directly groups work bundles |
| **B · `dev:initiative` as its own kind between theme and epic** | matches the researched chain and your own phrasing ("goals → initiatives → work items"); `theme --hasPart--> initiative --hasPart--> epic --decomposesInto--> issue` | one pack kind + one hierarchy declaration; **no ONTOLOGY §5 change** (pack kinds are the pack's own vocabulary) |
| **C · status quo** (`initiative` = synonym of `theme`) | — | contradicts the research in one direction only: a theme is *bigger* than an initiative |

**Recommend B**, because your spine names initiative as a level and the packs currently have none; **A is defensible** if the business never distinguishes portfolio-level from team-level epics, in which case `initiative` should move to `epic` and the word "theme" should stop being used for grouping work.

✅ **DECIDED 2026-11-05: option B.** `dev:initiative` becomes a kind between theme and epic, and `initiative` stops being a synonym of `theme`. The declared path becomes **theme --hasPart--> initiative --hasPart--> epic --decomposesInto--> issue**. Notes:

- **No ONTOLOGY §5 change** — this is a *pack* kind in the pack's own namespace, which is the layer packs exist to own.
- ⚠️ **`theme`'s `nearMisses` must drop `epic`** and gain `initiative`'s neighbours, or the classifier will resolve an initiative as an epic. `dev:theme` currently lists `feature`, `goal`, `epic` as near-misses (`packs/dev/manifest.yaml:66-68`); with a real `initiative` kind the disambiguation set changes, and leaving it unchanged would make the new kind unreachable in practice.
- ⚠️ **`goal` is still declared a `nearMiss` of `theme`** (`:67`), while KIN-2 now wires `epic --addresses--> goal`. That is coherent only if a goal is a *commitment object* and a theme is a *strategic area* — which is true (§5's commitment-state family), so the near-miss stays and the distinction is documented rather than removed.

**KIN-2 stays approved and unblocked** — the three canonical connections (`goal --ownedBy--> Subject`, `epic --addresses--> goal`, `indicator --addresses--> goal`) work under either KIN-1 answer; only option B adds `initiative` into the middle of that path.

### 🔺 Gap found while doing this research — and it affects the census (#11083's own scope)

**`hierarchies:` is a second declaration surface, and the census only counted `relations`.** **Four** packs declare `hierarchies:` paths — `dev`, `marketing`, `product-strategy`, `venture` (`agent-ops`, `investment-portfolio-manager` and `project-management` declare none):

```text
dev               Theme → Epic, Epic → Issue                        (hasPart)
marketing         Campaign → Content                                 (hasPart)
product-strategy  Product → Feature                                  (hasPart)
venture           Investment → Program, Program → Asset              (hasPart)
```

**6 declared hierarchy paths across 4 packs — but they are not 6 *extra* connections.** Measured against `relations`, five of the six restate a connection the pack already declares: `dev/Theme → Epic` is `hasPart`; `dev/Epic → Issue` is `decomposesInto` (semantics `hasPart`); `marketing/Campaign → Content` is `contains`; `product-strategy/Product → Feature` is `contains`; and `venture/Investment → Program` is `partOf`, written `program → investment`, which `hasPart` inverse traversal covers. **Exactly one is a connection `relations` does not carry: `venture/Program → Asset` (`hasPart`)** — the containment assertion that sits beside `producedBy`'s production assertion, and it is what lets the census **see** both. ⚠️ **It must not call that pair a duplicate:** the owner ruled the two are different assertions (production vs containment), and both are written. So the declared population is **37 distinct connections** (36 relations + that one path), and a census that reads only `relations` misses **1 of 37**. ⚠️ **This corrects the relayed record rather than silently diverging from it:** `#11083` comment `6667121779` states *"9 paths across 4 packs"* and an under-count of *"a third"*; the tree holds **6** paths across **4** packs, and the miss is one connection. `#11083`'s scope is corrected accordingly, and the implementing lane cites this measurement against the relay's number.

---

## Round 3 — what the plan review found *behind* the rulings

**`funds` and `produces` are not missing from the vocabulary — they are RETIRED from it.** `tortoise/pack_registry.py`'s `SEMANTICS_TRANSITION` maps `funds → transacts` and `produces → causes`, and `sdk.py` applies the rename **before** resolving direction, so a write declaring `funds` lands as `transacts` (direction `-`, no mechanism), and `produces` lands as `causes` (`->`). Both shipped declarations are therefore *currently warned as retired* on every load.

**That changes what "the ruling" costs, and it is why this is recorded rather than absorbed:**

- **`funds` is safe to restore** — it needs an entry in `SEMANTICS_VALUES` (`->`) and `SEMANTICS_MECHANISMS` (`IMPL`), and its removal from the transition map.
- **`produces` needs a direction the table has never produced.** `<-` is valid per edge (`live.py`'s `DIRECTION_VALUES`) but no `SEMANTICS_VALUES` member yields it; the owner's own words were that this is production, not causation, and the declared edge runs product→producer. So the table gains a `<-` value.
- **Both removals reverse a recorded decision** (`#7848`'s alignment), so they carry an **`OVERRIDES:` line on that issue** (comment `6668793463`) and the owner's approval, cited by the implementing lane. A lane that "aligns to the core vocabulary" by find-and-replace would otherwise delete the ruling it is implementing.
- ⚠️ **`ownedBy` + `IMPL` and `transacts` + `IMPL` are NOT errors.** **ONTOLOGY §8's mechanism column** gives those four values (`related`, `transacts`, `ownedBy`, `managedBy`) **no** mechanism (`—`), so *either* spelling is permitted over them — `SEMANTICS_MECHANISMS` omits them by design — and the registry **compares and warns, never gates** (#1934). A census that flagged them would red on correct behaviour. The genuine inversion is `IMPL` over `refutes` or `NAND` over `addresses` (#9963).

**Not affected:** `#1026`'s "no name patterns" ruling. That one is about manifest filter keys, and nothing here touches it.
