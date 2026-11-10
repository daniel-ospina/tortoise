---
title: "Epic #7949 — Scope Boundaries, Customer Value Map, and high-level E2E"
type: engineering
domain: platform
doc_status: live
created: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: extractor v4, object layer, expansion packs, capstone evidence
issue: daniel-ospina/tortoise#7949
stage: epic-scope
---

# Epic Scope — #7949 extractor v4 CAPSTONE

**Input:** `00-align.md` (PROCEED, GREEN bar = E1–E5 + A1–A5) · `01-research-brief.md`.
**Produced by:** `epic-scope` (read in full). **Gate:** human approval (Step 5) → scope reviewer → separate
`duplication-architecture` reviewer.

---

## Step 0 — `### Axis Research Notes`

Two axes rate `medium+` (Architecture, Ontology — Step 3). For each, the skill requires a granular per-axis
note **or a justified skip citing the brief**. Both are **justified skips**, cited:

| medium+ axis | boundary question | disposition |
|---|---|---|
| **Architecture** — which transport, and does the door contract change the boundary? | is the acceptance's *door* in scope, or assumed? | **justified skip** — `01-research-brief.md` §Open decisions → the two-option transport table (what each proves / does not), plus §Tech Stack on declared-ontology prompting and structural validation. The boundary question is answered there; no further external query is warranted for an internal transport choice. |
| **Ontology** — how is `hasPart` read, and does the direct-vs-transitive distinction change what A1 can promise? | does the rollup need a direct-part relation ONTOLOGY lacks? | **justified skip** — `01-research-brief.md` §UX Pattern Research (the direct-vs-transitive finding, three sources) + §Assumptions Register **A-g (rejected)**. The finding is that ONTOLOGY §3.7's inverse-traversal model *can* answer "part of" but **conflates immediate with inherited parts**; the boundary consequence is recorded in §Scope Boundaries below (we do not add a `partOf` edge; we constrain the query's reading). |

> **Findings date:** 2026-11-05 — provenance: both notes derive from `01-research-brief.md` (queries of
> 2026-11-05), not from new queries. Dedup rule respected: no question is asked that the brief already answers.

**Two axes rate `low`** (UX, Accessibility) — no axis-research note required (skill: only `medium+`).

---

## Step 1 — `## Scope Boundaries`

### In Scope

1. **A1 evidence** — on a registered corpus, **object→object edges of the types declared by the graph's EFFECTIVE
   ONTOLOGY** exist, and a **rollup query answers** from the graph, scored against hand-labelled ground truth.
   ⚠️ **Amended 2026-11-05 by owner ruling:** the canonical source is **`core ∪ the packs installed on that graph`**
   — packs *expand* the core and toggle **per graph** — so the criterion is "the graph's effective ontology", not
   "one pack's declared types". `Theme→Epic→Issue` is the **first instance, not the whole claim**. The edge
   *producer* is `#11001`'s; this epic measures it and owns the rollup; the **derivation of the write vocabulary**
   from core+packs is **C10 (`#11484`)**.
2. **A2 evidence** — the **attach / mint / held** distribution on the definite-description population
   (`the <X>`), with per-candidate decisions recorded.
3. **A3 evidence** — the **chain-join rate** (statements with no IMPL and no NAND — *definition settled in
   §D-S5*), **both S5 jobs reported separately**, the **cross-batch link observed** in the graph with a
   dependent rollup answered, **and each emitted connection recording which of D6's two sets produced it**
   (within-batch vs to-graph). That last clause is not decoration: it is the evidence the owner's `§13.8 Q1`
   answer is waiting on (`01-research-brief.md` Rec. 3). ⚠️ **The set is DERIVED, not persisted** (F4): the
   write path already carries `batch_id` in `RelationUpsert.attrs` (`tortoise/record_projection.py:829-836`), and
   "this batch" vs "a previous batch" is exactly that distinction — so the tier is **reported from `batch_id`**
   and no new edge property is added. A persisted marker would have to update the five MAP-form writers and
   `sdk.py:17079`'s per-door `_honours` set in the same change, and a free-form `props` key is the branch this
   scope refuses (`STORAGE-ARCHITECTURE.md` §11.4/§13: an OPEN set no guard bounds).
4. **A5 evidence** — the `Subject` layer either populated (count + the four subject-edge families + the
   customer-visible readers) **or** the honest finding that it is not, with the reason — *"which of
   `#4889` / 'still empty' is true"*.
5. **A4 attribution only** — the provenance of today's typed-point population, typed/`statement`/`NULL` shares.
   **No target** (blocked on the D3 reopen).
6. **E1 + E2** — the registered volume criterion and the value-survival / no-fusion guard. ⚠️ **Corrected
   twice:** not "same run" (two lanes are involved) and **not "same corpus as 1–5"** — the E-row denominators
   are properties of the **fixture** (`value_bearing_units: 14`, `planted_units: 90`,
   `planted_operator_edges: 15`, `denominator_floors` of 30), and a curated payload corpus has **no planted
   units**, so E1b/E2a/E2b are unmeasurable there. **The row → corpus → run mapping is §D-S1's matrix.**
7. **E4 + E5** — re-derivation at this head (`= 15 of 15` operator edges on an unpatched leg; E5's raw arm on
   a reconciled population).
8. **The instrument and the door** — the pre-registered criterion set extended with the A-rows, and the
   acceptance run through a **named transport** (see §Scope Decisions).
9. **Reporting** — every number lands on `#7949` as evidence, with its corpus, command and head sha.

10. **E6 evidence** — the **sixth** acceptance criterion, registered on this epic by **`#11083`** (2026-11-07): for
   **each installed pack**, every declared relation whose endpoints both occur in the corpus is **materialised**, and
   a census reports **declared → writable → written** with the delta named. ⚠️ It was **missing from this pipeline's
   own artifacts** until 2026-11-07 (they were built on E1–E5 + A1–A5). Its *writable* column is `#11484`'s, its
   *written* column is the **wiring residual** (`extractor_v2` → `pack_hierarchy`), and it is **RED by construction**
   — before-arm **0 written on Object→Object** — for a wiring reason, **not** a pack-content one.

### Out of Scope

1. **The mechanisms** — implementing A1's edge producer (`#11001`), A2's HOLD gate (`#10997`), A3's S5 jobs
   (`#10998`/`#10999`), or the selection defaults (`#10996`). **Deferred to epic `#5087`'s lane.** *Exception:*
   A2's attach/resolve half is unowned, so **C4 builds it** (argued in `00-align.md` §4, two-branch rule).
2. **The MCP-side nudge** — telling agents to *use* the graph so they connect objects. Nothing implements it;
   a different surface from extraction. Deferred to `#5087` / `#10976` / `#5090`.
3. **Any volume target, recall floor, or node budget.** §16.3 **O2** — E1's `≤ 0.50 ×` is a pre-registered
   measurement, never an objective.
4. **E3's metric re-registration.** `#9736` CLOSED without making the bar reachable and owes two owner-gated
   lines. This epic **reports the figure and the 193-of-354 composition**; it does **not** re-cut the metric.
5. **Relocation / deletion of existing Objects** — `#5333`'s ruling. E1's row is BLOCKED below its floor;
   this epic moves it only via the **non-destructive** attach path, never by removing rows.
6. **The v3 fidelity programme** beyond the E2 guard arm (`#2453`/`#1534`/`#10360` own it).
7. **`#2817`** numeric/locale canonicalisation (CLOSED) — `value_normalizer`'s own rulings.
8. **Deciding `§13.8 Q1`** (cross-batch connection by model or mechanically) — **deferred to the owner**, routed
   by a comment on `#7949` naming the decision home (`EXTRACTOR-V4-ARCHITECTURE.md:1842`). This epic produces
   the **tier-provenance evidence** that makes the answer decidable (item 3); it does not decide it, and it
   does **not** adopt the mechanical standard over **D6**.
9. **The 25,000-point quota / cache eviction** — `#10976`'s root (the graph is at its ceiling; measured).
   ⛔ **But it is a BLOCKING PRECONDITION for every A-row, not merely a neighbour.** The capture door refuses
   with **HTTP 402** when `count + est > max_points` (`hosted_api.py:17368` (condition) / `:17378` (402)), counting non-episodic Points +
   Object + Subject — and the team was **measured at that cap** (`-32006 … limit reached (25000)`). So while the
   cap holds, **the acceptance capture is refused before it writes anything** and every A-row is unrunnable.
   The A-runs therefore name: **the org/graph they write to**, its **headroom**, the **point budget the A-corpus
   consumes — counted TWICE, because the negative control replays the same payloads against the same graph, and a 402 on the control is a different failure from a 402 on the acceptance capture**, and — if the cap still holds at acceptance — the A-rows report **`BLOCKED — capture refused 402`,
   never a zero and never a pass**. Raised limit / eviction / a different org is `#10976`'s decision.

**⛔ Registry statement (required by the architecture review, F3).** C4 builds a **write-path mechanism**
(A2's attach/resolve half) and `config/v4-mechanisms.yml` — *"the ONE PLACE the declared set lives"* (`#5064`),
13 rows, guarded by `tests/test_v4_declared_mechanisms.py` — has **no row for it**. `failure_policy` owns only
the HOLD half and is `not-implemented`. So after C4 the write path would implement a mechanism the registry
cannot see: the `#5064` defect running **inverted**, in the epic whose own instrument extends that registry.
**The scope requires one of two statements in C4's child, not silence:** (a) C4 adds a row (a new `s3_attach`,
or a `preconditions:` edge onto `failure_policy`), or (b) C4 records **why** the registry cannot express it,
citing `00-plan.md:356`'s criterion ("only if the registry's contract can express it"). Silence is the defect.

### Boundary Rationale

**The cut is *evidence vs mechanism*.** `00-align.md` §Decision established that the sibling epic `#5087`
charters the implementations (filed 2026-11-05T00:30–00:31Z) while **the proof of A1–A5 has no other owner**.
A capstone that implements is a second implementation with its own opinion; a capstone that proves is the only
thing that can turn a RED row green with evidence.

**The second cut is *the object layer*, not all of v4.** E1–E5 already have an instrument and a measured
verdict (`#9075`/`#9080`/`#9089`); this epic **re-derives** two of them and **guards** the rest rather than
re-scoping them. Anything that would require re-negotiating a registered criterion is out.

**A third cut is forced by research:** we do **not** add a `partOf` edge to satisfy the rollup. ONTOLOGY §3.7
says inverse traversal covers "part of", and adding a second edge type would duplicate a vocabulary defined
elsewhere (`duplication-architecture`'s question, answered here in advance). Instead **C3 must declare which
reading its rollup uses** — direct-only, or transitive with an explicit cycle guard — and label ground truth
the same way. That is a *constraint on the proof*, not a scope addition.

---

## Step 2 — `## Customer Value Map`

⚠️ **Who the "user" is, stated so the map is not theatre.** This is an internal capstone, so "user-visible" is
deliberately interpreted as **the person or agent consuming the graph** (the beta customer asking rollups, the
agent answering from memory) and, for the evidence rows, **the owner making a ship/no-ship call**. A row whose
only value is "easier for us" is marked as such and earns no place.

| Scoped Capability | User-Visible Value |
|---|---|
| A1 — pack declared types become object→object edges **+ the rollup** | An agent can answer *"which work items serve this goal, and what evidence supports them"* from the graph, with the evidence attached — instead of the user reading 23,814 loose claims. |
| A2 — attach instead of mint, with attach/mint/held recorded | The graph stops carrying nodes for **non-referential mentions** (`the timeout command`, `the staging constraint`), so recall returns the things that **exist** instead of phantoms. ⚠️ *Corrected: not "duplicates" — the baseline has **zero duplicate names**; the defect is writing things that were never entities (`#4894`).* |
| A3 — batch story + cross-batch link | A claim made in one session can join work recorded in another, so the user's question is answered across sessions instead of per-session. |
| A5 — Subjects populated | *"What does this agent/team own and manage"* returns an answer; today the subject field and org structure are structurally empty, so the question fails silently. |
| A4 — typing attribution (no target) | The owner can decide the `statement`-only question on measured provenance rather than on a docs sentence — a decision-grade input, not a feature. |
| E1+E2 — volume + the no-fact-loss guard | When the graph gets smaller, the user can trust it got smaller by dropping *bookkeeping*, not facts — the difference between a cleaner map and a quieter one. |
| E4+E5 — re-derived at this head | The owner's ship call rests on a number measured at the current head, not on a closed issue's claim — the difference between evidence and a citation. |
| **Instrument + door** | **Internal — no user-visible value.** Recorded as such: it exists so the other seven rows' numbers are reproducible. It is in scope because *without* it every other row is an unverifiable claim, not because it helps a user. |

**Rule applied:** eight rows for nine in-scope items; item 9 (*Reporting*) is the delivery mechanism of the
first eight and is not given a value line of its own.

---

## Step 3 — `## Complexity Ratings`

| Axis | Rating | Rationale |
|---|---|---|
| **UX** | **low** | No UI. The only user-facing surface is a **query result** (rollup / subject answer), and its UX is a data-correctness property, not an interface one. |
| **Architecture** | **high** | ⚠️ **Raised from medium after review.** The scope carries an **owned mechanism build** (C4 builds S3's attach/resolve half — an `00-align` §4 exception) through the extraction path, plus a pinned registration to extend without drifting its checker, two lanes to reconcile, and a proof that must run through the fleet's real door. "No new service" under-rates it: a mechanism build on the write path is the epic's own definition of cross-system, and **five of the nine children already ship as `complex`** on Architecture or Ontology. |
| **Ontology** | **medium** | Object→Object structural edges under ONTOLOGY §3.7's inverse-traversal model, the direct-vs-transitive rollup reading (research A-g), and an **anchor obligation that cannot be met by the writer we use** (`#5213` option 3, `#5199` option A) — a boundary to respect, not to change. |
| **Accessibility** | **low** | No interactive surface. |

**Overall tier: `complex`** (Architecture = high). ⚠️ Corrected from `standard`: the children's own ratings and the owned mechanism exception both demand it, and the skill's consequence is concrete — downstream gates scale on this tier.

---

## Step 4 — `## High-Level E2E Test Cases`

Written **before** journeys, as the skill requires. Behavioural, not presentational — each is verifiable
without knowing an interface.

### E2E-1: the declared hierarchy reaches the graph, and a rollup answers

**Given:** a registered corpus that exercises one pack's declared hierarchy (dev's `Theme→Epic→Issue`), and a
before-census showing **0** object→object edges of any type
**When:** the whole pipeline runs through the named transport and the pack is installed on the graph
**Then:** object→object edges of the pack's **declared types** exist, with endpoints of the **declared kinds**
**And:** the rollup query *"which work items serve this goal, and what evidence supports them"* returns a
non-empty answer that matches **hand-labelled ground truth**
**And:** the answer states whether it walked **direct** or **transitive** part-whole, and contains no inherited
part where direct is claimed

### E2E-2: attaching instead of minting, with the facts intact

**Given:** a corpus whose statements reference things by definite description (`the <X>`)
**When:** the pipeline runs
**Then:** each candidate is recorded as **attached / minted / held**, and the `the <X>` share of Objects falls
**And:** the registered verbatim-value criterion (E2a) does **not** regress — no value observable in the source
is absent from the graph without a recorded refusal

### E2E-3: a claim joins a chain, and crosses a batch boundary

**Given:** two sessions where the second refers to something the first recorded
**When:** both run through the same graph
**Then:** the statement from the second session carries an IMPL or NAND (it is not one of the unlinked 9,910)
**And:** the cross-batch link is observable, and a rollup that depends on it answers
**And:** S5's **two jobs are reported separately** — job 1 (this batch's own story) cannot stand in for job 2
**And:** each emitted connection records **which of D6's two sets produced it**, so the `§13.8 Q1` answer has its
evidence

### E2E-4: the subject layer answers, or the reason it cannot is named

**Given:** a corpus with agent/team activity
**When:** the pipeline runs
**Then:** `Subject` nodes exist and at least one of `aboutSubject` / `memberOf` / `ownedBy` / `managedBy` is
non-zero
**And:** the customer-visible readers (search `subject`, `get_org_structure`) return non-empty
**Or:** the run reports *why* — the binder did not run, the corpus has no bindable slots, or the graph served
is not the graph measured — and `#4889` is confirmed or **reopened** accordingly

### E2E-5: the numbers are reproducible from the door

**Given:** the pre-registered criterion set, extended with the A-rows
**When:** the census runs twice from the same head, same corpus, same transport
**Then:** every **deterministic census** quantity is **identical** across the two runs — edges by type and
endpoint kind, the attach/mint/held split, the `pointKind` typed/statement/`NULL` split, the subject-family
counts, and A4's attribution
**And:** the **model-proposed** arms (the rollup answers, S5's connections) are **not** required to be
run-invariant — D6 makes both connection sets model-proposed, and this repo has measured gold swinging
`0,1,1,1,2 of 4` on **identical code** (`PRE-REGISTRATION.md` §1.4). They are reported against a **stated repeat
policy**: two runs reported with their disagreement, plus a pinned recorded-proposal artefact the re-run
replays — and a disagreement **above** that policy is a **failing run**, not a footnote
**And:** a run handed a **different** corpus is **refused**, not scored — ⚠️ **and the check must exist on the lane
it protects.** G0–G6 live in the SDK-capture probe (`probes/capstone_run.py`), while the A-rows run through
`POST /v1/sessions`; so **E2E-5's mismatch claim applies to the E-rows only** unless the A-lane adds its own
check (a hash of the **posted payload set** compared to that row's pin — the same check, one line). The scope
requires the A-lane check; without it the row carries **no** G1 gate and says so rather than borrowing one from
another lane.
**And:** the same corpus through the **other** transport is shown to **disagree** — the negative control

### E2E-6: the volume win cannot be bought with lost facts

**Given:** the A1/A2/A3 mechanisms are **shipped by their owners** (`#11001`/`#10997`/`#10998`+`#10999`) and armed
in the run
**When:** the registered volume and guard criteria are scored on the run named by D-S1's matrix
**Then:** the `^the <X>` share falls (or E1 is honestly reported BLOCKED)
**And:** E2a `≥ 13 of 14` and E2b `≤ 0.05` hold, and a per-claim discard audit exists
**And:** if E2a fails, the epic is **blocked** even though every other number improved

### E2E-7: the two closed claims re-derive at this head

**Given:** the planted-operator corpus and the **unpatched** extractor (the existing test monkeypatches
`extract_session_v2`, so today no proof exists)
**When:** the operator measurement runs at this head
**Then:** the row reports **`15 of 15` (GREEN)** or **`N/15` reported RED** with the gap **attributed**
(selection vs endpoint recall) — **attribution is the diagnosis, never a pass**
**And:** endpoint refs are read **back from the graph**, and **both** tree figures are named (`2/15` in the
shipped verdict table, `5/15` corrected by `E1-E5-PROOFS.md`) so the stale one can never be used as the bar
**And:** E5's raw arm is measured on the **reconciled** population — `≥ 0.80` met, or **blocked-by `#5087`** by
id with the ratio/floor asymmetry recorded (never reported as green)

### E2E-8: the typed population is attributable

**Given:** today's typed `pointKind` population (109 on `live-v4`)
**When:** attribution runs
**Then:** **100 % of typed points are attributed to a named writer**, and typed / `statement` / **`NULL`** are
reported **separately** (a "typed share" without its `NULL` denominator is not a measurement this epic accepts)
**And:** **no numeric target is asserted** — the case asserts *attribution and reporting*, never a count, because
the count is the D3 reopen's to set

⚠️ **A4 has no numeric target**, not "no verifiable surface": E2E-8 asserts attribution and reporting, which
presuppose nothing about D3 and would red if the population went unattributed.

---

## Step 5 — Scope Decisions taken here (not deferred)

**D-S1 · The transport, the corpus, and the environment.** *(Rewritten after review: the first version named
only "a real fleet capture", which cannot satisfy the E2E cases it is scored by.)*

- **The A-corpus is CURATED AND PINNED, not whatever the fleet happened to do.** It is a set of real session
  payloads **posted to `POST /v1/sessions`** (the fleet's actual door), committed to
  `docs/evidence/7949-capstone/a-corpus/` and **hash-pinned** exactly as `preregistration.json` pins its
  fixture (`pinned_by: fixtures_hash`). Curated, so E2E-1's *"a corpus that exercises dev's `Theme→Epic→Issue`"*
  and E2E-3's two-session cross-reference are **constructible**; real, so it runs through the hosted door's
  quota/auth/harness path rather than the SDK's in-process arm.
- **The negative control is the SAME A-corpus payloads replayed through `TortoiseSDK.capture_session`** — the
  path `tests/eval/write_path` drives — and it must **measurably disagree** (E2E-5). ⚠️ *Corrected: naming the
  write_path fixture as the control varied **two** variables at once (different corpus **and** different door),
  so a disagreement would have been uninformative. The fixture is the **E-row** harness; it is not the A-row
  control.*
- **The environment is named, not implied.** The run names **the org and graph it writes to**, their
  **headroom** (`#10976`: the team was measured AT the 25,000-point cap, and the door refuses with 402 —
  out-of-scope item 9), and **who installs the `dev` pack on that graph**. Per-graph pack gating is out of
  scope (`#2714`/`#2728`), so the installation is an explicit run step owned by C2, not an assumption.
- **"Same corpus, two runs"**: A-rows on the **v2** door, E1b on the **`m2`** control lane. The corpus is the
  invariant; "same run" across two lanes is not achievable and is not claimed.

**D-S1 matrix — which corpus carries which row, through which door.** *(Added after review: the first version
asserted one corpus for all rows, which the registered denominators refute.)*

| row | corpus | door / run | why that corpus |
|---|---|---|---|
| **A1, A2, A3, A5** | the **A-corpus** (curated real payloads, committed + hash-pinned, §below) | hosted `POST /v1/sessions` (v2 lane) | these rows are about *the graph the fleet builds*; their evidence must come from a real capture through the real door |
| **A4** | the **fleet graph** at C1's head | read-only census over `live-v4` | the 109-typed-point population is a property of the fleet's graph, not of any corpus |
| **E1, E2a, E2b** | the **fixture** (`tests/eval/write_path`, already pinned) | the committed arm | the denominators (14 value-bearing, 90 planted, floors 30) exist only there, and E1's threshold basis was calibrated on that fixture's own labelled names |
| **E1b** | the **fixture** | deterministic **`m2`** lane | it is an anti-gaming control on a deterministic lane by construction |
| **E4, E5** | the **fixture** (planted-operator corpus) | **unpatched** extractor leg | the registered bars (`15 of 15`, E5's raw ratio) are fixture-denominated |

**Two corpus pins, one record.** The A-corpus is a **second pin inside the same registration** — its own
`corpus_id`, its own `fixtures_hash`, its own derived facts — and **C1's amendment extends G0/G1 and
`tools/capstone_preregistration_check.py` from one `corpus.root` to a per-row corpus map**. That is an
extension of an existing check, **not a new gate**. Consequently `E2E-5`'s mismatch rule is scoped to *"a
corpus other than **that row's own** pin"* — a run may not be scored against the wrong corpus, and the A-corpus
run is not refused by the fixture's pin.
⚠️ **Consistency fix:** out-of-scope 5's "E1's row is BLOCKED below its floor" refers to the **fixture** run —
E1 is not scored on the A-corpus at all, so there is no contradiction with item 6.

*Override available:* if you would rather the fixture arm carry the A-row numbers (cheaper, fully
reproducible, weaker claim about the fleet's graph), say so and I will swap the source and the control.

**D-S2 · The rollup's reading.** *(Rewritten after review: a direct-part default cannot answer the owner's
question.)* **Both readings are scored, against ground truth labelled both ways** — which is exactly what
research A-g asks for. **The transitive reading, with an explicit cycle guard, carries A1's verdict**, because
*"which **work items** serve this goal"* is **two hops** over the declared hierarchy
(`packs/dev/manifest.yaml:148,193`: `Theme→Epic`, `Epic→Issue`, both `hasPart`) — a direct-part walk from a goal
returns Epics, not work items, so a direct default could go green on an answer that is not the question asked.
The **direct** reading is reported as the corroborating arm (it is what makes the transitive answer
falsifiable: a claimed inherited part must be reachable through declared intermediates).
Adding a `partOf` edge remains **out** of scope — it would duplicate ONTOLOGY §3.7's vocabulary.

**D-S3 · A5 is measured here; a real mechanism gap is filed under `#5087`.** *(Corrected after review: the
first version reversed `00-align.md` §4's operative sentence — "if C6 finds the binder genuinely broken, the
mechanism issue is filed **under `#5087`'s epic** and C6 keeps only the evidence" — while claiming to follow
the same rule. Reversing a decision in its operative sentence while restating it in a summary is the exact
failure this lane's align gate spent six cycles on, so the align's sentence stands.)*
The gap is **disputed** (`bind_point_subjects` exists; `#4889` records the Subject half "was never implemented,
at any commit"), so the two-branch rule sends it to **measurement first**. C6 measures; if the binder is
genuinely broken, **C6 files the mechanism issue under `#5087` and keeps the evidence**, and only if that lane
declines does the mechanism come back here — recorded as a new decision, not as a quiet expansion.

**D-S4 · The A-row verification rules — floors, and what "below floor" means.** *(Owed by this stage: C1 and
C5 both hand these up, and C1's own warning is that "an A-row with no floor can pass on three objects".)*
C1 **registers these verbatim**; it may not re-derive or tighten them after a scored run.

| row | metric | denominator | floor (and what KIND of floor) | **threshold** |
|---|---|---|---|---|
| **A1** | edges of the declared types present **and** rollup precision vs labelled truth | the **transitive** label set (D-S2 gives it the verdict) | **≥ 30** labelled pairs — *sample sufficiency*: the registration's own constant (30), chosen because ~10 pp is this repo's recorded resolution limit and fewer pairs cannot separate a fix from variance (`EXTRACTOR-V4-ARCHITECTURE.md:1197`) | **precision ≥ 0.80** on the transitive set **and** every claimed inherited part reachable through declared intermediates (the direct set) |
| **A2** | attach / mint / held distribution; `the <X>` share | definite-description candidates in the A-corpus | **≥ 30** candidates — *sample sufficiency* (same constant) | **attach share > mint share**, and attached+minted+held = 100 % classified |
| **A3** | unlinked-statement share **+ both S5 jobs** | statements written by the run | **≥ 30** statements — *sample sufficiency* | share **strictly lower** than the before-arm **AND** D-S5's component arm positive |
| **A4** | attribution of the typed population; typed/statement/`NULL` reported separately | the **typed population of the named graph at C1's head** | **n > 0** — *non-vacuity* (100 % of 0 is not attribution) | **100 % attributed to a named writer**; **no count target** (D3's) |
| **A5** | `Subject` count + the four edge families + the two customer readers | bindable subject slots in the A-corpus | **≥ 5** slots — *constructibility*, presence question (not a statistic) | non-zero families **and** both readers non-empty — **or** the reported reason, which is the finding |

**⛔ The ground truth is sealed BEFORE the rollup exists.** Both label sets (direct and transitive) are committed
under `docs/evidence/7949-capstone/a-corpus/gold/` and **hash-pinned in C1's amendment before any rollup is
written**, on the record's own `written_before_any_scored_run` basis. **The labeller is not the author of C3's
rollup** — a second person/agent records the gold, because a gold written by the same lane that writes the query
can be fitted to the answer, and A1 is the epic's headline claim. ⚠️ **And the rule needs an ARTIFACT, not just
prose** (P2, advisory review): a hash proves **when** the gold existed, never **who** wrote it. The gold artefact
therefore **records the labeller's identity** beside the hash, and the sealing reuses the repo's existing
`verify_manifest` / `compute_fixtures_hash` over `fixtures/` + `gold/` (`tests/eval/write_path/corpus.py:120`)
rather than restating a second sealing mechanism.

**`REFUSED` is neither a pass nor a fail** — the registration's own vocabulary. A refused row reports
`REFUSED (below floor: n of N)` and the epic cannot close on it.

**D-S5 · The chain-join definition.** *Decided:* the **verdict metric** is *"the statement carries no IMPL and
no NAND"* — the cheap reading, chosen **because it is the one the owner's baseline was measured with**
(9,910 of 15,534), so the delta is comparable. The **anti-gameable corroborating arm** is *connected-component
membership of ≥ 2 including a node written by a previous batch*: it is reported alongside, and a run whose
cheap metric improves while the component arm does not **is reported as not having joined anything**, regardless
of the cheap metric.

---

---

## `duplication-architecture` review — verdicts recorded (advisory, never blocking)

Ran as a **separate** dispatch after the scope reviewer, per the skill. Result: **`ISSUES — DEGRADED`** —
degraded because the Tortoise registry was unreachable (`mcp_load tortoise` → `401`, the known empty-key
expansion in a long-lived pi process), so **any claim about the component registry is LOW CONFIDENCE**. The
repo search is the load-bearing source for every finding below and was not degraded. Recorded per the skill's
disposition table; **no fix loop entered**.

| # | finding | verdict | disposition recorded here |
|---|---|---|---|
| **F1** | *"the pre-registered criterion set" is **seven** declarations, not one — `preregistration.json` ids, `PRE-REGISTRATION.md` §7, `capstone_preregistration_check.py:370/373`, the **closed enum** at `capstone_verdict_check.py:135` (refuses any other row id, `:821`), `capstone_measure.py:242/455`, `capstone_run.py:184` `OWNERS`, and `blocker-states.json` `cited_by` — and the only consistency assertions bind a tuple to a copy of itself | `unify-contract-keep-drivers` | **Folded into C1's contract:** the A-rows are added to the record's `criteria[].id` and the verdict gate's enum is **derived** from it (or a test asserts `record ids == verdict enum` minus declared sub-rows); `capstone_run.OWNERS` needs an A-row entry or the V12 `cited_by` shape has no owner pointer. **Two schemas move, not one** (`capstone-preregistration/v1` **and** `capstone-verdict-v1`). |
| **F2** | the **corpus pin** already carries **three incompatible shapes** (`fixtures_hash` in the record; `corpus.sha256`/`items` demanded by `capstone_run.py:1177`, which **fail-closes** against the committed record; a derived `e1e2_corpus.json`), five readers, and the **sibling bundle `5087-hypothesis` shares the same schema** — so "a second pin inside the same registration" is a **schema fork in effect**, not an extension | `unify-contract-keep-drivers` | **Folded into C1:** unify the pin **contract** (one required key set, one hash-pin helper reusing `compute_fixtures_hash`/`verify_manifest`, a per-corpus derivation registered *beside* the one it replaces), and reconcile the `sha256`/`items` vs `fixtures_hash` split that already makes the run probe refuse the record. The A-corpus is a genuinely **different kind** (no planted units), so its derivation is **named**, not implied. |
| **F3** | C4 builds a write-path mechanism with **no `config/v4-mechanisms.yml` row**; the scope dropped the registry its own plan carried | *(architecture — no duplication verdict)* | **Folded into the boundary** (§Registry statement): C4 must state (a) a row, or (b) why the registry cannot express it, citing `00-plan.md:350`. Silence is the defect. |
| **F4** | tier-provenance is an **edge attribute** proposal that never says so, over an existing `RelationUpsert.attrs` vocabulary that already carries `batch_id` | `unify-contract-keep-drivers` | **Folded into in-scope item 3:** the set is **DERIVED from `batch_id`** and reported, not persisted; no new property, no `_honours` change, no free-form `props`. |
| **F5** | **G0–G6 live in the SDK probe; the A-rows run through `POST /v1/sessions`** — so E2E-5's "G1 mismatch rule" enforces **nothing on the lane it was quoted to defend** | `unify` | **Folded into E2E-5:** the mismatch claim is scoped to the **E-rows**, and the **A-lane must add its own check** (hash of the posted payload set vs the row's pin) or state that it carries none. |
| **P2 (suppressed)** | the sealed-gold rule proves **WHEN**, never **WHO**; and it restates a sealing mechanism the repo already has | `unify` | **Folded into D-S4:** the gold records the **labeller's identity** beside the hash, and sealing **reuses** `verify_manifest`/`compute_fixtures_hash` rather than restating one. |

**Verdicts refused a place:** the **`partOf` edge** (`keep separate` — the scope's own refusal, correct);
**D-S5's chain-join metric** (`keep separate` — genuinely a different unit from `STORAGE-ARCHITECTURE.md` §15's
M6 and from `factor_edges`; one line in D-S5 names the aggregate so the two are never conflated);
**A5/C6's mechanism** (`keep separate` — D-S3 conforms to the align's two-branch rule; no second subject
mechanism).

**Coherence opinion (the reviewer's, recorded):** the epic **stays a capstone**, conditional on F1–F3. Its
one-line risk statement, worth carrying into the plan: *"the instability is in **four declarations that live
outside the epic bundle** — the verdict gate's closed enum, the corpus pin's three shapes, `RelationUpsert.attrs`'
writer set, and the registry's 13 rows. Each forks cleanly; none was named in the scope. That is the whole risk,
and it is cheap to close."*

---

## Step 5 (gate) — Ready for approval

```text
## Epic Scope Ready for Review

**Scope:** IN — A1/A2/A3/A5 evidence, A4 attribution (no target), E1+E2 guard, E4+E5 re-derivation,
         the instrument and the door, reporting.
         OUT — the mechanisms owned by #5087's children, the MCP-side nudge, any volume target,
         E3's metric re-registration, relocation/deletion (#5333), the v3 programme, #2817, the quota root.
**Customer value map:** 8 capabilities mapped (item 9 "Reporting" is the delivery mechanism, not a row)
**E2E test cases:** 8 drafted (E2E-1…E2E-8), behavioural; A4 has no *numeric target* but does have a case
         (E2E-8: attribution + typed/statement/NULL reporting)
**Complexity:** UX low · Architecture **high** · Ontology medium · Accessibility low → overall `complex`
**Decisions taken at scope (override available):** D-S1 A-corpus = curated, hash-pinned payloads posted to
         `POST /v1/sessions`, **the same payloads replayed through `TortoiseSDK.capture_session`** as the negative
         control, environment named · D-S2 rollup = BOTH
         readings scored, transitive-with-cycle-guard carries A1's verdict · D-S3 A5 measured here, a real
         mechanism gap filed under #5087 · D-S4 per-row floors + REFUSED rule · D-S5 chain-join = baseline
         metric as verdict, connected-component as the anti-gameable arm
**Test-design gate:** satisfied by `03-test-design.md`, filed as **#11454** — 17 surfaces, each with a
         test layer AND a run tier (`CI` / `Tier E` / `milestone`); children must cite it for their tier.
**Two corpus pins:** the **A-corpus** (curated real payloads, hash-pinned, second pin in the SAME record) and
         the existing **fixture** — with a **row → corpus → run** matrix in D-S1, extended G0/G1, and sealed
         gold committed before any rollup exists (labeller ≠ rollup author).
**Blocking precondition:** `#10976` — the capture door returns 402 while the 25,000-point cap holds, so the
         A-rows report `BLOCKED — capture refused 402`, never a zero and never a pass.
```

**Do NOT proceed until the human confirms.** On confirmation: dispatch the scope reviewer, then (separately)
the `duplication-architecture` reviewer, and record both verdicts here.
