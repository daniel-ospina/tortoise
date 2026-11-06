---
title: "Epic scope — #5087 Extractor v4"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#5087, #7949, #10994-#11002, #11021, #11083"
---

# 03 · Scope — #5087 Extractor v4

**Inputs:** `01-align.md` (PROCEED, sequencing corrected), `02-research-brief.md`, and the decomposition of record (`docs/plans/2026-11-04-5087-epic-decomposition.md`, PR #11005).

## Scope Boundaries

### In Scope

1. **A mention is not an entity** — the referentiality test at S2.2, and the mention channel S2.1 never had (`#11021`). **This item owns the arbiter probe** (the 25 owner-labelled rows plus the 12 most-referenced rows a name-pattern rule would have deleted); item 2 *consumes* that reading rather than re-measuring it, so an outcome cannot be credited to both.
2. **The selection arm runs on the fleet door** — mechanical predicate default ON, the reference question available to an armed arbiter, and the classify prune **withdrawn** (`#10996`): no candidate is classified that `classify_consolidation` will delete, because no `DELETE` verdict is producible (ADD/NOOP/UPDATE/MERGE only, D5) — the prune is not deferred, there is nothing to prune on; whoever introduces a producer owes it. The reference question remains `#11021`'s.
3. **Identity is a gate** — an unresolved lookup HOLDs, never mints; one transaction across S3/S5/S6 (`#10997`).
4. **The against-graph check fails closed** — an unchecked item is held, not committed (`#10998`).
5. **The batch's own story is wired** — an entity↔entity channel in the output contract, and E4 read off a real-extraction receipt (`#10999`).
6. **The pack's declared vocabulary widens, and gains the initiative level** — `subclassOf` reaches the classifier, and `dev:initiative` becomes a real kind between theme and epic (owner ruling 2026-11-05): `theme --hasPart--> initiative --hasPart--> epic --decomposesInto--> issue`, with `initiative` removed from `dev:theme`'s synonyms and the near-miss set reshuffled (`#11000` declares it; `#11001` materialises it).
7. **The declared business relations become edges** — replayable, from the pack's own declaration (`#11001`).
8. **The eight steps are actually the eight steps** — a bounded re-narrate, and S4's gap-filling relocated before `run_s4` goes (`#11002`).
9. **The epic can say what its own doors write** — the per-door census (`#10995`), and the integration-surface map (`#10994`).
10. **The packs' declared connection logic is constructed, and that is now an acceptance row** — per-pack declared → writable → written census over **both** declaration surfaces (`relations` and `hierarchies`), the four `pm` relations that bind no write path, and the Subject-rooted spine reported with an owner-approval route (`#11083`; sixth capstone row, owner ruling 2026-11-05). ⚠️ **The meanings are RULED, not aligned by find-and-replace:** `funds` and `produces` return as meanings (direction `->`/`IMPL` and `<-`/`IMPL`) with their `SEMANTICS_TRANSITION` entries removed under an **`OVERRIDES:` on `#7848`**; `ownedBy` targets a Subject **or** an Object with the per-edge `direction` carrying the meaning; and `producedBy` + the pack's own `Program → Asset` `hasPart` path are **both** written and are **not** a duplicate.

### Out of Scope

- **The existing 5,314-Object population** — defer to **`#5333`** (owner-gated relocation; a pass may add, no committed row is removed).
- **E2 value retention** — defer to **`#10360`** (upstream of selection) and `#9775` (closed render half).
- **E3's metric re-registration** — `#9736`, closed; the row needs a re-run, not work.
- **The every-point span widening** — **`#2684`** (own registry row; the value-bearing half is measured 101/112).
- **The sufficiency router / tier walk** — **`#5009`** (read path, not the write path).
- **The atomic point write** — **`#9879`** (a precondition of the against-store exact dedup, not of the S3 HOLD).
- **Narrative/raw persistence** — **`#10000`**, **`#10394`**.
- **The claim-class write path (L8)** — `#9417`/`#9727`/`#9819`, **all closed** (re-measured 2026-11-05).
- **Vector reaping** — **`#4997`** (closed), `#4998` (merged PR).
- **The legacy rule extractor / documents door** — **`#4432`** (owner dispatched it elsewhere).
- **The seam's element attribution** — **`#10176`**: a **precondition of the first *armed* scored run**, not of the epic (`02-research-brief.md`). Item 2's proof is externally gated on it.
- **The ~4,900-Object backfill** — **`#5059`**, beside `#5333`; it co-owns the population question and is not this epic's to run.
- **E4's receipt retrievability** — **`#9596`**, which item 5's "E4 read off a real-extraction receipt" leans on.
- **The EMBED gate** — `#5008`, **closed and `implemented`** (`config/v4-mechanisms.yml`); the residual is a rate measurement, not work here.
- **The fan-out / sufficiency bound** — `fanout_cap` is **`implemented`** at 200; the router half is `#5009`.
- **Any new MCP tool or public SDK method** — none is needed; if one becomes necessary it needs Daniel's approval first.

### Boundary Rationale

**One principle: this epic changes what is allowed to *enter* the graph and what *connects* once it is in — never what is already there.** That cut is forced by a recorded decision (`#5333`) and by the capstone's own measurement shape (E1 is scored on a graph the run minted). Everything that would rewrite committed rows, or that belongs to the read path, is deferred with a named owner.

## Customer Value Map

| Scoped capability | User-visible value |
|---|---|
| mention-vs-entity (#11021) | An answer cites `the parity harness` as something the team *has*, not as an entity called "the parity harness". |
| selection arm (#10996) | Search stops returning junk for entity queries; the entities a user sees are things, not turns of phrase. |
| identity gate (#10997) | The same thing has one node, so a question about it does not get three half-answers. |
| against-graph check (#10998) | A capture cannot silently contradict a belief the system already holds. |
| story wiring (#10999) | *"Why is it like this?"* has an answer, because the claims in one session support and attack each other. |
| `subclassOf` widening (#11000) | Asking about "work items" finds the issues, not just the ones literally typed `workItem`. |
| business hierarchy (#11001) | You can walk goals → initiatives → work items instead of reading a flat list of objects. |
| eight steps / re-narrate (#11002) | A session that was summarised badly is retried once instead of silently losing its content. |
| census + surface map (#10995, #10994) | **The operator** can say whether a change reached the fleet's own write path *before* it is believed — no improvement is claimed per door without a count, and a door nobody measured says so. |
| pack connection logic (#11083) | A company, its goals and its work are connected because the pack says they are — and if a pack says it badly, **the operator is told which declaration is missing** instead of the graph quietly staying flat. |

## Complexity Ratings

| Axis | Rating | Rationale |
|---|---|---|
| UX | **low** | no user-visible surface of its own; the changes surface as better recall and legible node names |
| Architecture | **high** | eleven children, seven touching the 13.6k-line pipeline body (`wc -l tortoise/extractor_v2.py` = 13,614 at HEAD), one transaction boundary across three steps, and a new channel through the prompt contract |
| Ontology | **high** | a mention/entity distinction the canonical document does not yet name, plus `hasPart`'s two declared topologies (Point→Point in §3.1, Object→Object in §3.7) — **and, since the 2026-11-05 rulings: a new pack kind (`dev:initiative`) with a near-miss reshuffle, an `ownedBy` topology widening (Entity → Subject **or** Object), two `SEMANTICS_VALUES` additions (one needing a `<-` direction no table value has ever produced), and two `SEMANTICS_TRANSITION` removals that reverse `#7848`** |
| Accessibility | **n/a** | no UI surface in scope |

### Axis Research Notes

*(issue #231 D11 — required for each `medium+` axis)*

> **Findings date:** 2026-11-05. **Source:** `02-research-brief.md` §Workflow Pattern Research and §Tech Stack Research (rung-2 external pass + the in-repo comparables record), plus §Assumptions Register.

- **Architecture (high):** covered by the brief's *Workflow Pattern Research* — the field's pipeline shape, the missing mention step, and the `c/ r` ordering rule that fixes CLASSIFY-before-RESOLVE. Boundary question (what the epic must and must not change) is answered there and needs no further query.
- **Ontology (high):** covered by *Workflow Pattern Research* (NIL / `NEW_ENTITY` / stoplist) and *Tech Stack Research* (where a mention can live). The one genuinely open boundary question — **where a mention is stored** — is a data-model decision carried by `#11021`'s `### Research Needed`, not an epic-boundary question.

## High-Level E2E Test Cases

### E2E-1: a turn of phrase does not become a thing

**Given:** a capture whose narrative says *"timeout is not on macOS"*
**When:** the pipeline runs end to end
**Then:** no `:Object` named `the timeout command` exists
**And:** the claim that carried it is still retrievable, with the mention recorded against it

### E2E-2: the same thing, twice, is one thing — a CONTROL

**Given:** two sessions that each mention `the parity harness`, and a graph that already holds one
**When:** the second capture resolves the **exact** name
**Then:** the second capture attaches to the existing node
**And:** no second node is created
⚠️ **This case passes on today's code and is kept deliberately as a control** — it proves the fix does not break the attach path. Its discriminating siblings are E2E-5 (the unresolved lookup, including the partial-prior-set form) and E2E-8 (an against-graph check that did not complete).

### E2E-3: one session's claims argue with each other

**Given:** a session whose narrative states that A changed because of B
**When:** the batch is committed
**Then:** an edge **of the pack-declared class** exists between A and B (operator vs structural is not interchangeable, and its recorded `direction` must run A→B as the story states — an inverted edge is a wrong claim, not a cosmetic one)
**And:** the planted operator edges for that session are wired and persisted, scored on a **real-extraction** receipt

### E2E-4: the pack's map is walkable

**Given:** a graph built from the frozen corpus with the dev pack installed
**When:** a user walks from a theme
**Then:** they reach its **initiatives**, each initiative's epics, and each epic's issues — the four-level path the 2026-11-05 ruling fixed (theme → initiative → epic → issue, `docs/epics/2026-11-05-5087-extractor-v4/06-pack-logic-decisions.md` §KIN-R)
**And:** a rebuild from the journal reproduces the same edges
**And:** a pair the packs do **not** declare — e.g. `dev:theme --hasPart--> dev:issue`, which no `dev` relation and no `dev` `hierarchies:` path declares — produces no edge. ⚠️ **The direct `theme → epic` hop is explicitly NOT in that set:** `packs/dev/manifest.yaml:148-153` declares it and `:192-194` lists it in `hierarchies:`, and no ruling retires it — deleting it would be an owner-gated pack-vocabulary edit (`#11000`), so it is named here rather than assumed away

### E2E-5: a failed lookup does not become a new belief

**Given:** a lookup that returns `unresolved` (degraded, or a partial prior set)
**When:** the run commits
**Then:** the affected candidates are held, not minted
**And:** a later run against the same graph commits them once the lookup resolves

### E2E-8: an unchecked item is not committed

**Given:** an item whose against-graph check did **not** complete (a partial Job-2 pass)
**When:** the run commits
**Then:** that item is **held**, not committed
**And:** held / committed / discarded are three distinct counts, so nothing is silently folded into one of them
*(Scope item 4. It previously had no case of its own — E2E-5 was doing both jobs with two different assertions.)*

### E2E-6: the graph can be asked what its doors write

**Given:** the frozen corpus
**When:** the census runs
**Then:** each write door reports its counts per step
**And:** a door nothing measured reads `UNMEASURED`, never zero

### E2E-7: the packs' declared business logic is the one we construct

**Given:** all **seven** shipped packs (`agent-ops`, `dev`, `investment-portfolio-manager`, `marketing`, `product-strategy`, `project-management`, `venture`), and their **two** declaration surfaces — `relations` and `hierarchies` (the four that declare hierarchy paths are `dev`, `marketing`, `product-strategy`, `venture`; `pm` contributes the four unwritable relations)
**When:** the pack census runs
**Then:** every declared connection reports **declared → writable → written**, and any declared connection the writer cannot produce is named rather than totalled away
**And:** the effective **direction** is reported per row, so a meaning that runs backwards is visible
**And:** a company reaches its goals and its work — or the census says exactly which declared connection is missing (today: no relation starts from a Subject, and no pack declares `goal` as an endpoint)

## Risks

| risk | why it matters | mitigation |
|---|---|---|
| **The arbiter is unmeasured** (top risk) | the whole mention-vs-entity lever rests on a judgement nobody has scored against our data | score it on the 25 owner-labelled rows **first**; E1's row at n≥2 arms after |
| **`#1026`'s "no name patterns"** may be read as forbidding the cue | adopting it quietly would reverse a recorded decision | `OVERRIDES:` on `#1026` (6648049935); **one narrow owner read requested** — it is the only decision this scope needs from you |
| seven children in one 13.6k-line file | merge collisions, serialized lane | one extractor lane, order fixed in the decomposition |
| the existing population stays junk | the epic improves new writes while the measured problem persists | stated, not smoothed; `#5333` owns it |
| E4's tenth miss (`wp02`) has no owner | E4 cannot reach 15/15 on this decomposition | stated in the decomposition §8; needs a re-registration or a content-level fix |
