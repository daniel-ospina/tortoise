---
title: "Extractor v4 — Architecture Changelog"
type: engineering
domain: platform
doc_status: live
created: 2026-10-15
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: EXTRACTOR-V4-ARCHITECTURE.md, extractor pipeline, decision protocol, review cycles
---

# Extractor v4 — Architecture Changelog

> **What this is.** The record of *how the extractor design got where it is* —
> the decision-protocol runs, the review cycles and what they found, and the
> corrections applied to the document. It is process and change history.
>
> **What `EXTRACTOR-V4-ARCHITECTURE.md` is.** The design. It states the target
> architecture and the decisions that fix it; it does not narrate the process
> that produced them or keep a running status of the work.
>
> **Moved out of the design document on 2026-10-15** (issue #8542). The material
> is moved verbatim; nothing is deleted. The design document keeps a pointer at
> §16 so its numbering — and the cross-references into §16.3/§16.4 — still
> resolve.

**The decisions themselves stay in the design document** (§16.3, §16.4). Only
the record of *running the protocol* and *what each review cycle did* is here.

---

## 16. Decision protocol run (AGENTS.md, 2026-09-23)

Each open decision was run through the mandated sequence: **contradiction test first → research (does it actually need a human?) → the required artifact.**

| decision | contradiction test | needs the owner? | artifact / action |
|---|---|---|---|
| **Keep contention + EP confidence** (ruled Option 1) | **No contradiction** — it *is* the recorded decision (`ONTOLOGY.md` §3.1). The field converges **against** it. | ruled | ✅ **`OVERRIDES:` posted on `#2730`** — the field's default is resolve/erase; we hold in tension |
| **Granularity** | The **merge-ban** was withdrawn (O4, §16.3); **the fuse prohibition was re-affirmed** by the same owner comment. ⚠️ **GraphRAG's consolidation STILL contradicts that position** — §9 REFUSES it (*"Directly destructive of the epistemic layer"*), and §16.4 authorises `MERGE` only *"when nothing distinguishing is lost"* and **never across a negation or a condition**, which is exactly what *"resolve the contradictions and provide a single, coherent summary"* does. | **merge-ban withdrawn**; **fuse re-affirmed** | ⛔ **The `OVERRIDES:` on `#4899` is WITHDRAWN** (comment `2026-09-24T04:33:23Z`); **near-duplicate `MERGE` is authorised** (bounded by §16.4, arbitrated by Jev) — and **`FUSE` REMAINS FORBIDDEN**: the admissible set is `KEEP` / `NOOP` / `DISCARD` / `MERGE` |
| **D6: LLM proposes to-graph connections** | **Contradiction** — Hindsight's mechanical cross-batch contradicts D6, so **not adoptable**. Route = reopen. | **still open** | reopen in front of the owner (§13.8). **No `OVERRIDES:`** — LLM edge extraction *is* the field default, so D6 is not against the grain |
| **`aboutObject`: stored edge vs derived join** | No contradiction — no decision mandates materialization | **no** | ✅ **MEASURED + RULED: KEEP the stored edge.** The primary search path **does not** read it, but **four CUSTOMER-CALLABLE sites do** (`belief_timeline`, `topic_summarize`, `recall_subgraph`, `search(order_by="graph")` — §13.8 question 2) plus two default-ON ingestion sites → deriving would silently break four public surfaces. **Owner 2026-09-23: the remaining unknown — *are those tools ever called?* — is unanswerable pre-launch, so the question is DEFERRED, not decided.** Keep for now; optimise with users. |
| **Fan-out caps** | No contradiction; the field converges (Hindsight caps at **200** *and* carries a timeout that drops the whole arm); ⚠️ **the bound is explicit rather than implicit** | **no** | ✅ **ADOPTED at 200** (owner, 2026-09-23: *"fine on adding the cap"*). **Above our current worst hub (123), so it binds nothing today and cannot lose data now** — a guard rail against runaway fan-out, **not** an optimisation. Refine with real usage. See `STORAGE-ARCHITECTURE.md` §11.5. |
| **Keep the epistemic + entity connection layer** | No contradiction — it *is* the product. The field's cost-driven default (Hindsight derives instead of storing) points the other way. | ruled | ✅ **KEPT — owner, 2026-09-23.** Cheap because edges are **property-free** and Postgres holds them **on disk** (~3 MB ≈ $0.0004/mo). **`OVERRIDES:` posted on `#4333`** — never cut this layer to save money. See `STORAGE-ARCHITECTURE.md` §11. |
| **D3: raw vs narrative** | No contradiction (D3 stays provisional) | **no** | ✅ The owner already approved the measurement. **The measurement decides — not a gate** — and it has: **STILL PROVISIONAL, 2026-10-18**, because the run cannot test the falsifier as built and its decisive arm ran partially (`#7947`; `EXTRACTOR-V4-ARCHITECTURE.md` §9 + §10). **No owner verdict was needed, and none was taken** |

**Deliberately NOT marked:** our class-based `Source` deviates from PROV (§9.2 of the storage doc), but modelling a source/document as a node is **not** against the industry default — GraphRAG, Graphiti and Hindsight all anchor to a document node. Deviation noted; **no `OVERRIDES:`** because it is not a departure from practice.

**⚠️ The "decision ledger" does not exist in this repo.** `docs/plans/2026-09-22-2814-rebuild-config-durability.md:316` records it: *"the 'decision ledger' earlier drafts named does not exist in this repo (`rg -il 'decision ledger'` matches only this plan)"*. **The `OVERRIDES:` comment on the issue is the marker's only home** — do not cite a ledger that isn't there.

### 16.1 The review cycle — outcome recorded (2026-09-23)

**Two fresh-context reviewers per document, complementary lenses; full consolidation in `REVIEW-CONSOLIDATED-2026-09-23.md` (that review's own summary: 6 P0 · 34 P1 · 18 P2).** This section records **what was done with each category**, so a later lane can tell a correction from an omission.

| category | count | disposition |
|---|---|---|
| **P0 — a false load-bearing claim, or two documents that contradict each other** | **6** | **All 6 corrected in place — A4 only after §16.2 caught it** (A1 vector-index model · A2 truth/derived test · A3 `#2453` landed · A4 VET/S3 circular dependency — `MERGE-INTO-EXISTING` removed from VET's outputs, restoring S3 as the sole authority · A5 the 10× cannot reproduce itself · A6 *"an Object is a name"*). **The corrections are marked in place — most with `⚠️ CORRECTED`, some with the error named directly in the row (A1 *"REWRITTEN TWICE"*, A3 *"THIS ROW WAS WRONG"*, A6 *"DO NOT WRITE"*).** |
| **P1 — code-evidence, citation, or arithmetic defects** | **34** | **All corrected.** Arithmetic recomputed (B1.1 §1, B1.2/B1.3/B1.5/B1.19, §5's table); citations fixed or the claim deleted (B1.16 `#1509 §9`/`E2`, B1.18 `MITIGATES`, B2.9 `llm_tail`, B2.18 `#3011`); **B2.1's constraint 4 kept visible with the evidence that falsifies it**, so it cannot be re-derived. |
| **P2 — completeness gaps** | **18** | **Recorded, not silently dropped** — this document's §4.3 (G1–G5) + `STORAGE-ARCHITECTURE.md` §5.1/§14/§15. |
| **Genuinely a decision, not a correction** | **6** | ⛔ **Escalated to the owner — §16.3. These are NOT applied and NOT decided.** |

**⚠️ The rule applied throughout, and it is the one that matters:** where a claim about **process** had no artifact to check it against, it was **DELETED rather than reworded** (the `#3011` thresholds, *"carries most of the retrieval value"*, the `#2453` reading). **Rewording a self-referential claim just generates the next review round; deleting it ends the question.**

### 16.2 ⛔⛔ THE SECOND REVIEW CYCLE FOUND THAT THE *FIRST REVIEW* WAS WRONG — and this is the most important lesson in this document

**Cycle 2 (2026-09-24, four fresh-context reviewers + two gap analysts) checked the *corrections*, not the original text. Five of them were false. Three trace to the same source: `REVIEW-CONSOLIDATED-2026-09-23.md` — the cycle-1 review itself — whose findings were applied verbatim without re-verification.**

| what the doc now said | what the primary source says | where the falsehood came from |
|---|---|---|
| *"`#3011`'s thresholds were embellished 3 of 3; REMOVED"* | the frozen doc **carries all three** (`H1 ≥15`, `H2 ≥10`, `H3 −5`) | **cycle-1 B2.18** — conflated *"also in H4 / also in the falsifiers"* with *"not in H1/H3"* |
| *"the lookup does not need kinds → constraint 4 evaporates"* | `_find_existing_entity` **uses `kind` as its exact-match key** | **cycle-1 B2.1** — read the query builder and called it the lookup |
| *"there is no `:Document` node and none is created"* | ✅ **NOW TRUE** — the `:Document` NODE label is **retired** (PR `#5127`, D10); the **`documents` cap** counts document-bearing `:Source` nodes (the node cap does not) | not from cycle 1 — a **lane inference** from "D30 puts raw outside the graph" |
| *"All 6 P0s corrected in place"* | **at the time, A4 was NOT corrected** — `MERGE-INTO-EXISTING` was still in VET's outputs, so the VET/S3 circularity stood | **cycle-1 A4's disposition was claimed, not verified** · **✅ NOW FIXED** — `MERGE-INTO-EXISTING` is removed from VET's outputs (§S2.2) and §16.1 is corrected |
| *"the narrowing is PROPOSED… ✅ Recorded as such in §9 (D4)"* | §9 D4 says **`DECIDED (narrowed)`** | **cycle-1 B2.20** — the "fix" replaced one two-ways status with another · **✅ NOW FIXED** — the ⚠️ pending line is deleted and §S3 states `DECIDED (narrowed)` |

**⇒ THE MECHANISM, STATED PLAINLY: a review finding is a CLAIM, not a fact.** Applying one is a **change to the artifact**, and it must be verified against the same primary source the finding cites — **otherwise the review becomes a propagation vector for its own errors**, and the `⚠️ CORRECTED` marker makes the wrong text *more* credible than the right text it replaced. **That is exactly what happened here: the doc's markers became evidence for the wrong reading.**

**⇒ THE RULES THIS PRODUCES, and they are binding on future cycles:**

1. **Verify a finding against its cited primary source before applying it.** A line range, a frozen doc, a `gh` output — **open it.**
2. **A `⚠️ CORRECTED` marker is not evidence.** It says the text was *changed*, not that the change was *right*.
3. **Correct the review record too.** `REVIEW-CONSOLIDATED-2026-09-23.md` B2.1 and B2.18 are **themselves wrong** and are the propagation vector; a corrected artifact with an uncorrected review leaves the next lane free to re-apply the error.
4. **"Also present elsewhere" is not "absent here".** Two of these five are that single misreading.

**⚠️ And the honest counter-note, so this does not become an argument against reviewing:** cycle 2 also found the things cycle 1 got **right** and this document now depends on — the `#4997` index drift, the four-layer placement, the four dedup keys, the `c/r` ordering rule and the sequence of the ten `!`-corrections that hold. **The failure was not review; it was unverified application.**

---

## Removed wording, by section (moved from the design document, 2026-10-15, #8542)

Every line this pass removed from the design document, keyed to the section it sat
in. Emphasis markers are dropped from the recorded wording so the record renders, and a
line longer than ~190 characters is shortened with an ellipsis — the moved sections
above are verbatim; this list is an index to them, not a second copy.
The **substance** each carried — a measurement, a rule, an open question, a
decision — is stated in the design document's rewritten sentence; what is not
carried anywhere is the delivery state and the account of the passage's own
revision, which is what this file is for.

### (front matter)

- `EXTRACTOR-V4-ARCHITECTURE.md:14` — Status: design in progress, 2026-09-23. Home: `#4894` (write-path redesign). Predecessor: `#1509` (extractor v3, approved 2026-08-20; ⚠️ epic CLOSED `NOT_PLANNED` 2026-10-10 — the v4 desi…
- `EXTRACTOR-V4-ARCHITECTURE.md:17` — Related: `docs/architecture/STORAGE-ARCHITECTURE.md` · `#1026` (pack slots) · `#2281` (ask/answer over distilled knowledge) · `#2730` (entity attachment) · `#4333` (volume/economics) · `#…

### ⛔ USAGE IS DENOMINATED IN TOKENS, AND OVERAGE IS PURCHASED — decision, owner, 2026-09-26 (`#4495`)

- `EXTRACTOR-V4-ARCHITECTURE.md:37` — reason to prefer measurable steps over inferred ones: §16.2's rule (a claim must be checkable) is now also a billing

### v2 → v3 → v4

- `EXTRACTOR-V4-ARCHITECTURE.md:92` — | v3 (approved; ⚠️ epic CLOSED `NOT_PLANNED` 2026-10-10 — the remainder is cancelled, but E2/E4 LANDED and are live) | did we extract it correctly? (dates, verbatim state values, atomic p…
- `EXTRACTOR-V4-ARCHITECTURE.md:95` — v3 has no discard, trim or gate anywhere. It is a fidelity workstream. v4 is a selection workstream. ⚠️ The ordering obligation this passage used to state — "it must land with, or before,…

### ⚠️ Storage rule for the first two tiers — they are OUTSIDE the graph (owner, 2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:121` — Neither raw nor narrative belongs in the graph. Both live in Postgres. ⚠️ The owner's wording is "store in supabase" — and "supabase" there means the DATABASE, not the object bucket. The …
- `EXTRACTOR-V4-ARCHITECTURE.md:128` — - It is far cheaper than the graph — the storage research measured ~580× on disk-resident data (⚠️ not the "~1,000×" an earlier draft stated — that number was unsourced; 580× applies to t…
- `EXTRACTOR-V4-ARCHITECTURE.md:133` — ⇒ D1 restated, correctly: the narrative is not a graph node and not graph metadata — it is Postgres-stored text referenced by its `Source` (the owner's "supabase" is the DATABASE, not the…

### ⭐ The experiment already exists — `#3011`, pre-registered and frozen 2026-09-11

- `EXTRACTOR-V4-ARCHITECTURE.md:158` — ⚠️ ⛔ THE THRESHOLDS WERE DELETED ON A FALSE PREMISE — RESTORED FROM THE FROZEN DOCUMENT (2026-09-24). This block previously said the earlier draft "rendered" the hypotheses wrongly and th…
- `EXTRACTOR-V4-ARCHITECTURE.md:170` — The earlier draft was correct about all three. The cycle-1 review's premise was wrong: `−5` appears in both H3 and H4, and `15` appears in H1 and H5's falsifier — "also present elsewhere"…

### The mechanism is also already filed — `#2684`

- `EXTRACTOR-V4-ARCHITECTURE.md:196` — Related, and not to be duplicated: `#2405` CLOSED (paraphrase-tolerant survival leg — the deformation measurement) · `#2542` family (value-fidelity discipline, cited by `#2684`) · `#2104`…
- `EXTRACTOR-V4-ARCHITECTURE.md:200` — ⚠️ Storage consequence, harmonised with `STORAGE-ARCHITECTURE.md` §9.1: the raw fact is Postgres text, not a graph node (the owner's "supabase" = the DATABASE, not the object bucket; dest…

### 2.4.4 ⛔ Redaction and the append-only layer — the G4 reconciliation (`#8496`)

- `EXTRACTOR-V4-ARCHITECTURE.md:350` — ✅ R1–R4 request no new mechanism, and it is worth saying why. `#4911` already landed the control at
- `EXTRACTOR-V4-ARCHITECTURE.md:379` — `#5007` shipped the span schema, so a `Point` can carry `span_start`/`span_end`, but the raw-fact layer

### ⭐ D10 and the version model — what the extractor must respect

- `EXTRACTOR-V4-ARCHITECTURE.md:467` — ⭐ CORRECTED 2026-09-24 (owner): a document is a SOURCE, not a graph node. "I am suggesting making them sources so our entity layer can be extracted from them… That way our entity layer be…
- `EXTRACTOR-V4-ARCHITECTURE.md:470` — ⇒ D10 is LANDED — both halves. Ontology: `ONTOLOGY.md` v3.15 (PR `#5022`, issue `#5013`). Code: PR `#5127` retired the `:Document` NODE label, and the `:Source` replay key is `url` (`tort…
- `EXTRACTOR-V4-ARCHITECTURE.md:480` — | Q5 | Fold + change the replay key + migrate — ✅ DONE 2026-09-25 (`#5026`/PR `#5127`) | The window closed as predicted and the work landed inside it: zero `:Document` nodes and the commi…

### ⚠️ This REOPENS D4 — and narrows it along its own reasoning

- `EXTRACTOR-V4-ARCHITECTURE.md:551` — ✅ STATUS: `DECIDED (narrowed)` — the owner ruled. The narrowing is in force: the lookup sits after extraction, and it is consumed by the salience judgment and by S3 only. Extraction does …
- `EXTRACTOR-V4-ARCHITECTURE.md:553` — > ✅ Superseded and deleted (reviewer B2.20, then §16.2). This line used to say the narrowing was "PROPOSED and awaiting the owner's confirmation" while §9 recorded D4 as `DECIDED (narrowe…
- `EXTRACTOR-V4-ARCHITECTURE.md:555` — > ✅ Note — `#4511` is CLOSED, and it no longer gates anything. It was the blocker when the lookup sat before extraction (`_fts_rows` read `r.get("kind")` while the callee emits `point_kin…

### Level 1 — per-candidate (many, cheap, evaluated in parallel)

- `EXTRACTOR-V4-ARCHITECTURE.md:581` — > This is where the pack stops being prose. `packs/dev/manifest.yaml` already declares `memory_granularity` — "Durable: problem-family reasoning … Ephemeral: issue/PR numbers, CI status, …

### ⚠️ Owner question: if the narrative is poor, should we send it back? (2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:609` — ⚠️ `MERGE-INTO-EXISTING` is deliberately NOT a VET output — and `MERGE` and `MERGE-INTO-EXISTING` are not the same thing. VET may emit the verdict `MERGE` — "fold into another candidate" …
- `EXTRACTOR-V4-ARCHITECTURE.md:613` — ⚠️ There is no S4 — do not restore one. A previous draft listed a separate "S4 — granularity review" and folded it back in here. It is not a step: its purpose was identical to question 2 …
- `EXTRACTOR-V4-ARCHITECTURE.md:626` — > ⚠️ `TORTOISE_S4_DELTA=0` does NOT disable S4 — it selects the legacy complete-list contract. There is no S4-specific off switch: S4 is reached whenever the v2 pipeline runs a story, and…
- `EXTRACTOR-V4-ARCHITECTURE.md:638` — > pipeline". ⚠️ An earlier version of this marker said `S2.3` had no code counterpart because the
- `EXTRACTOR-V4-ARCHITECTURE.md:639` — > string `S2.3` is absent from `extractor_v2.py`. The string is absent; the mechanism is not. ⚠️ An earlier version of this marker claimed
- `EXTRACTOR-V4-ARCHITECTURE.md:640` — > `S3` and `S5` were absent from the code as well; that was wrong — `S3` and `S5` appear 51 and 22 times
- `EXTRACTOR-V4-ARCHITECTURE.md:644` — > stands: the symbol name is authoritative and the line number is a convenience. ⚠️ For THIS passage that rule is now mechanical, not a promise: `tests/test_extractor_v4_s4_stage_claims.p…

### The five correctness constraints — and ONE of them was FALSE

- `EXTRACTOR-V4-ARCHITECTURE.md:761` — ⚠️ Constraint 4 stands, and it is the basis of the S2.3-before-S3 order. CLASSIFY runs after the gate (so only survivors are classified) and BEFORE S3 — because `_find_existing_entity` (`…

### ✅ Jev for classification is cheap — but ⚠️ the seam does NOT yet exist

- `EXTRACTOR-V4-ARCHITECTURE.md:808` — ⚠️ And a hard constraint this ordering must respect: ⚠️ `#4899`'s Phase-1 mechanical DISCARD has known false positives — "a bare `#\d{3,}` in a working note is discardable; a PR number a …

### 4.3 ⚠️ What the steps above do NOT yet cover (review-provided, all unaddressed)

- `EXTRACTOR-V4-ARCHITECTURE.md:816` — | G1 → `#5064` (open remainder; `#5007` CLOSED `COMPLETED` via PR `#5108` — the schema + passthrough shipped, but `extractor_v2.py` emits no `span_start` (the repo's document extractor do…
- `EXTRACTOR-V4-ARCHITECTURE.md:824` — Where the five gaps live in the tracker (checked 2026-10-13): G1 `#5064` (the open remainder — `#5007` closed `COMPLETED` via PR `#5108`, which shipped the span schema + passthrough; what…

### 4.4 Per-step FAILURE POLICY (G3) — ✅ declared 2026-10-14

- `EXTRACTOR-V4-ARCHITECTURE.md:870` — | I3 | A committed run is ALL-OR-NOTHING, and the journal is the truth (`derived = replay(journal)` — `#4240` restored this at the lifecycle layer, `#4716` at the operator layer; both CLO…

### ✅ The carve-out DID NOT cover decision-relevant numbers — measured, and FIXED (`#2453`, PR `#2456`)

- `EXTRACTOR-V4-ARCHITECTURE.md:1014` — #### ✅ The carve-out DID NOT cover decision-relevant numbers — measured, and FIXED (`#2453`, PR `#2456`)
- `EXTRACTOR-V4-ARCHITECTURE.md:1016` — `#2453` root-caused this: the carve-out is scoped to user-personal state (personal bests, schedules, preferences) and explicitly tells the mapper to DROP process metrics as "mechanics tok…
- `EXTRACTOR-V4-ARCHITECTURE.md:1018` — ⇒ There are TWO value classes. ⚠️ At the time of measurement the policy kept one and discarded the other; the table below records that this was FIXED (commit `4a690d0be`, 2026-09-07, PR `…
- `EXTRACTOR-V4-ARCHITECTURE.md:1022` — | Personal state | "personal best 5K = 27:12" | retained (E2 carve-out — `#1534`, now CLOSED) | retained |
- `EXTRACTOR-V4-ARCHITECTURE.md:1023` — | Operational / decision values | "p99 latency 480 ms at 3k rps" | ⚠️ RETAINED — THE FIX ALREADY LANDED (commit `4a690d0be`, 2026-09-07, PR `#2456`) | retained |
- `EXTRACTOR-V4-ARCHITECTURE.md:1025` — Operational values are DURABLE. `#2453` landed and extended `STATE_VALUE_CARVE_OUT` (`extractor_v2.py:145-171`) with an operational-value paragraph — "a concrete value that is the SUBJECT…
- `EXTRACTOR-V4-ARCHITECTURE.md:1029` — ⚠️ And the mechanical DISCARD (`#4899`, §16 — ✅ LANDED, `value_gate.py`) would previously have inherited this bug — had it been defined against the old, stale reading, it would have disca…

### Where numeric facts should live (owner, 2026-09-23) — structured, so they are never re-processed

- `EXTRACTOR-V4-ARCHITECTURE.md:1039` — - ⚠️ Harmonise, do not duplicate. `#2453` (retain operational values verbatim) · `#1534` CLOSED (E2 state-value Tier-A extraction) · `#2817` (no numeric/locale canonicalisation exists — P…

### 8. Already in the code — do not rebuild

- `EXTRACTOR-V4-ARCHITECTURE.md:1052` — | S2/S4 merge | `merge_embed_lists` (def `extractor_v2.py:2284`; called at `:4845` — ⚠️ an earlier draft cited `:4838`, which is `try:` (`stage_stats` is `:4837`)) |

### ⛔ REFUSED — contradicts a recorded decision ⇒ a REOPEN, not an adoption — ✅ **this reopen is CLOSED, 2026-10-06; see the block below**

- `EXTRACTOR-V4-ARCHITECTURE.md:1199` — ### ⛔ REFUSED — contradicts a recorded decision ⇒ a REOPEN, not an adoption — ✅ this reopen is CLOSED, 2026-10-06; see the block below
- `EXTRACTOR-V4-ARCHITECTURE.md:1205` — > ✅ `OVERRIDES:` posted on `#2730` — for this ruling the marker lives on the issue, per §16's rule (below); the issue comment carries the full text. ⚠️ The rule is not yet true file-wide …

### 13.8 What this does to our design

- `EXTRACTOR-V4-ARCHITECTURE.md:1304` — | connection fan-out | bounded everywhere, with a timeout that drops an arm | ✅ Cap adopted AND implemented — `PER_ENTITY_FANOUT_CAP = 200` (`tortoise/fanout.py`), consumed by `assembly.p…
- `EXTRACTOR-V4-ARCHITECTURE.md:1306` — ⇒ One of the three questions below is still the owner's (question 1). Question 2 is measured, ruled and deferred; question 3 was adopted and is CLOSED — `#5010` (`PER_ENTITY_FANOUT_CAP = …

### 16. Decision protocol run (AGENTS.md, 2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:1329` — ## 16. Decision protocol run (AGENTS.md, 2026-09-23)
- `EXTRACTOR-V4-ARCHITECTURE.md:1331` — Each open decision was run through the mandated sequence: contradiction test first → research (does it actually need a human?) → the required artifact.
- `EXTRACTOR-V4-ARCHITECTURE.md:1333` — | decision | contradiction test | needs the owner? | artifact / action |
- `EXTRACTOR-V4-ARCHITECTURE.md:1334` — |---|---|---|---|
- `EXTRACTOR-V4-ARCHITECTURE.md:1335` — | Keep contention + EP confidence (ruled Option 1) | No contradiction — it is the recorded decision (`ONTOLOGY.md` §3.1). The field converges against it. | ruled | ✅ `OVERRIDES:` posted o…
- `EXTRACTOR-V4-ARCHITECTURE.md:1336` — | Granularity | The merge-ban was withdrawn (O4, §16.3); the fuse prohibition was re-affirmed by the same owner comment. ⚠️ GraphRAG's consolidation STILL contradicts that position — §9 R…
- `EXTRACTOR-V4-ARCHITECTURE.md:1337` — | D6: LLM proposes to-graph connections | Contradiction — Hindsight's mechanical cross-batch contradicts D6, so not adoptable. Route = reopen. | still open | reopen in front of the owner …
- `EXTRACTOR-V4-ARCHITECTURE.md:1338` — | `aboutObject`: stored edge vs derived join | No contradiction — no decision mandates materialization | no | ✅ MEASURED + RULED: KEEP the stored edge. The primary search path does not re…
- `EXTRACTOR-V4-ARCHITECTURE.md:1339` — | Fan-out caps | No contradiction; the field converges (Hindsight caps at 200 and carries a timeout that drops the whole arm); ⚠️ the bound is explicit rather than implicit | no | ✅ ADOPT…
- `EXTRACTOR-V4-ARCHITECTURE.md:1340` — | Keep the epistemic + entity connection layer | No contradiction — it is the product. The field's cost-driven default (Hindsight derives instead of storing) points the other way. | ruled…
- `EXTRACTOR-V4-ARCHITECTURE.md:1341` — | D3: raw vs narrative | No contradiction (D3 is provisional) | no | ✅ The owner already approved the measurement. The measurement decides — not a gate |  ⚠️ **the live row now reads "D3 stays provisional"**: the measurement ran and could not decide it (`#7947`; the section below), so the *authority* half of this row still stands and its *action* half changed on 2026-10-18.
- `EXTRACTOR-V4-ARCHITECTURE.md:1343` — Deliberately NOT marked: our class-based `Source` deviates from PROV (§9.2 of the storage doc), but modelling a source/document as a node is not against the industry default — GraphRAG, G…
- `EXTRACTOR-V4-ARCHITECTURE.md:1345` — ⚠️ The "decision ledger" does not exist in this repo. `docs/plans/2026-09-22-2814-rebuild-config-durability.md:316` records it: "the 'decision ledger' earlier drafts named does not exist …

### 16.1 The review cycle — outcome recorded (2026-09-23)

- `EXTRACTOR-V4-ARCHITECTURE.md:1347` — ### 16.1 The review cycle — outcome recorded (2026-09-23)
- `EXTRACTOR-V4-ARCHITECTURE.md:1349` — Two fresh-context reviewers per document, complementary lenses; full consolidation in `REVIEW-CONSOLIDATED-2026-09-23.md` (that review's own summary: 6 P0 · 34 P1 · 18 P2). This section r…
- `EXTRACTOR-V4-ARCHITECTURE.md:1351` — | category | count | disposition |
- `EXTRACTOR-V4-ARCHITECTURE.md:1352` — |---|---|---|
- `EXTRACTOR-V4-ARCHITECTURE.md:1353` — | P0 — a false load-bearing claim, or two documents that contradict each other | 6 | All 6 corrected in place — A4 only after §16.2 caught it (A1 vector-index model · A2 truth/derived tes…
- `EXTRACTOR-V4-ARCHITECTURE.md:1354` — | P1 — code-evidence, citation, or arithmetic defects | 34 | All corrected. Arithmetic recomputed (B1.1 §1, B1.2/B1.3/B1.5/B1.19, §5's table); citations fixed or the claim deleted (B1.16 …
- `EXTRACTOR-V4-ARCHITECTURE.md:1355` — | P2 — completeness gaps | 18 | Recorded, not silently dropped — this document's §4.3 (G1–G5) + `STORAGE-ARCHITECTURE.md` §5.1/§14/§15. |
- `EXTRACTOR-V4-ARCHITECTURE.md:1356` — | Genuinely a decision, not a correction | 6 | ⛔ Escalated to the owner — §16.3. These are NOT applied and NOT decided. |
- `EXTRACTOR-V4-ARCHITECTURE.md:1358` — ⚠️ The rule applied throughout, and it is the one that matters: where a claim about process had no artifact to check it against, it was DELETED rather than reworded (the `#3011` threshold…

### 16.2 ⛔⛔ THE SECOND REVIEW CYCLE FOUND THAT THE *FIRST REVIEW* WAS WRONG — and this is the most important lesson in this document

- `EXTRACTOR-V4-ARCHITECTURE.md:1360` — ### 16.2 ⛔⛔ THE SECOND REVIEW CYCLE FOUND THAT THE FIRST REVIEW WAS WRONG — and this is the most important lesson in this document
- `EXTRACTOR-V4-ARCHITECTURE.md:1362` — Cycle 2 (2026-09-24, four fresh-context reviewers + two gap analysts) checked the corrections, not the original text. Five of them were false. Three trace to the same source: `REVIEW-CONS…
- `EXTRACTOR-V4-ARCHITECTURE.md:1364` — | what the doc now said | what the primary source says | where the falsehood came from |
- `EXTRACTOR-V4-ARCHITECTURE.md:1365` — |---|---|---|
- `EXTRACTOR-V4-ARCHITECTURE.md:1366` — | "`#3011`'s thresholds were embellished 3 of 3; REMOVED" | the frozen doc carries all three (`H1 ≥15`, `H2 ≥10`, `H3 −5`) | cycle-1 B2.18 — conflated "also in H4 / also in the falsifiers…
- `EXTRACTOR-V4-ARCHITECTURE.md:1367` — | "the lookup does not need kinds → constraint 4 evaporates" | `_find_existing_entity` uses `kind` as its exact-match key | cycle-1 B2.1 — read the query builder and called it the lookup |
- `EXTRACTOR-V4-ARCHITECTURE.md:1368` — | "there is no `:Document` node and none is created" | ✅ NOW TRUE — the `:Document` NODE label is retired (PR `#5127`, D10); the `documents` cap counts document-bearing `:Source` nodes (t…
- `EXTRACTOR-V4-ARCHITECTURE.md:1369` — | "All 6 P0s corrected in place" | at the time, A4 was NOT corrected — `MERGE-INTO-EXISTING` was still in VET's outputs, so the VET/S3 circularity stood | cycle-1 A4's disposition was cla…
- `EXTRACTOR-V4-ARCHITECTURE.md:1370` — | "the narrowing is PROPOSED… ✅ Recorded as such in §9 (D4)" | §9 D4 says `DECIDED (narrowed)` | cycle-1 B2.20 — the "fix" replaced one two-ways status with another · ✅ NOW FIXED — the ⚠️…
- `EXTRACTOR-V4-ARCHITECTURE.md:1372` — ⇒ THE MECHANISM, STATED PLAINLY: a review finding is a CLAIM, not a fact. Applying one is a change to the artifact, and it must be verified against the same primary source the finding cit…
- `EXTRACTOR-V4-ARCHITECTURE.md:1374` — ⇒ THE RULES THIS PRODUCES, and they are binding on future cycles:
- `EXTRACTOR-V4-ARCHITECTURE.md:1376` — 1. Verify a finding against its cited primary source before applying it. A line range, a frozen doc, a `gh` output — open it.
- `EXTRACTOR-V4-ARCHITECTURE.md:1377` — 2. A `⚠️ CORRECTED` marker is not evidence. It says the text was changed, not that the change was right.
- `EXTRACTOR-V4-ARCHITECTURE.md:1378` — 3. Correct the review record too. `REVIEW-CONSOLIDATED-2026-09-23.md` B2.1 and B2.18 are themselves wrong and are the propagation vector; a corrected artifact with an uncorrected review l…
- `EXTRACTOR-V4-ARCHITECTURE.md:1379` — 4. "Also present elsewhere" is not "absent here". Two of these five are that single misreading.
- `EXTRACTOR-V4-ARCHITECTURE.md:1381` — ⚠️ And the honest counter-note, so this does not become an argument against reviewing: cycle 2 also found the things cycle 1 got right and this document now depends on — the `#4997` index…

### 16.3 ✅ ALL SIX DECISIONS CLOSED (owner, 2026-09-24)

- `EXTRACTOR-V4-ARCHITECTURE.md:1390` — | O2 | ⛔ WITHDRAWN — the owner rejected the framing: "we're not optimising stupidly for a number… this is not a corporate OKR setting exercise. We need to balance multiple things at each …

## Removed wording — the design-language pass (2026-10-15, `#8542`)

The copy pass that removed the implementation-state and change-narration
wrappers from the design document, leaving the design statements. Every
removed line is recorded here, keyed to the section heading in effect in
the pre-change file. Emphasis markers are dropped so the record renders,
and a line longer than ~190 characters is shortened with an ellipsis — the
moved material above is verbatim; this list is an index to it, not a
second copy. Section labels are bullets, not headings, so a removed line
that was itself a heading cannot collide with this file's own outline.

- **Extractor v4 — Architecture**
  - Status: design. Home: `#4894` (write-path redesign). Predecessor: `#1509` (extractor v3, approved 2026-08-20; ⚠️ the v4 design does not wait on it — no further v3 work is coming. But v3 i…
  - ⚠️ Code citations — revision pin. Every `file.py:NNNN` reference here is pinned to commit `c79ba1cf2` — except where a passage names a later pin. ⚠️ Every citation added to this document …

- **⛔ USAGE IS DENOMINATED IN TOKENS, AND OVERAGE IS PURCHASED — decision, owner, 2026-09-26 (`#4495`)**
  - \**✅ Where the measurement is — PRESENT (this paragraph said PLANNED; re-measured 2026-10-11, `#3840`).** The
  - capture lane **does** record token counters on `main` now: the per-org ledger carries `capture_tokens_in` /
  - analytics row carries — `#5045` / PR #5697, **merged** (commit `d3a0851a0`). The **ask** lane separately records
  - "tokens per team per month" is written but not yet readable from code (the `#4493` lesson: a recorded dimension

- **v2 → v3 → v4**
  - | **v2** (live) | did we extract it? | baseline |
  - | v3 (approved; ⚠️ the epic is cancelled — but E2 and E4 are live) | did we extract it correctly? (dates, verbatim state values, atomic points, supersession) | ⬆️ increases it — E2 preser…
  - v3 has no discard, trim or gate anywhere. It is a fidelity workstream. v4 is a selection workstream. ⚠️ No ordering obligation holds against v3 — no further v3 work is coming. But v3 is n…
  - ⚠️ Part of v3 has already landed. Its E4 (`"S4 merges, not replaces"`) is live — `merge_embed_lists(embed_list, s4)` runs at `extractor_v2.py:4845` and S2 is preserved. v3's premise there…

- **2. Three tiers — and where each one lives**
  - \**The architecture has three tiers with different cost/quality trade-offs. Earlier drafts conflated the first two, and named the third wrongly.**
  - ⚠️ Correction (owner, 2026-09-23): the third tier is `Entities`, not "Claims". Calling it "Claims" hid the fact that the graph also holds `Source`, `Object`, `Subject` and `Event` — and t…

- **⚠️ Storage rule for the first two tiers — they are OUTSIDE the graph (owner, 2026-09-23)**
  - \- It is far cheaper than the graph — the storage research measured ~580× on disk-resident data (⚠️ 580× is the disk-resident leg, not the vector index — do not quote a "~1,000×" figure, w…
  - ⇒ D1 restated, correctly: the narrative is not a graph node and not graph metadata — it is Postgres-stored text referenced by its `Source` (the owner's "supabase" is the DATABASE, not the…

- **⭐ The experiment already exists — `#3011`, pre-registered and frozen 2026-09-11**
  - ⚠️ ⛔ The thresholds stand as the frozen document has them. The claim that they were "rendered" wrongly is false: `−5` belongs to H4 as well as H3, and `15` belongs to H1 and to H5's falsi…
  - ⭐ That correction was itself WRONG, and it deleted three accurate numbers. Verified against the frozen file (`docs/experiments/2026-09-11-abc-context-assembly-experiment.md`, §2 "Hypothes…
  - \**All three values hold:** `−5` appears in **both** H3 and H4, and `15` appears in H1 **and** H5's falsifier — **"also present elsewhere" is not "not present here"**. **⇒ Restored.**
  - ⚠️ Still read them from the frozen doc before using them: `docs/experiments/2026-09-11-abc-context-assembly-experiment.md`. The falsifier's ROLE is the claim to keep, and it survives: a r…

- **⛔ What the epistemic subgraph is actually FOR (owner, 2026-09-23) — and it is NOT retrieval**
  - ⚠️ **`#3011` is OPEN — the experiment has not run.** So the layer is **evidenced externally and unmeasured on our stack.**

- **The mechanism is also already filed — `#2684`**
  - ⚠️ COORDINATION NOT TO MISS (reviewer B2.23): §2.4 WIDENS `#2684`'s scope — from value-bearing `Point`s to all `Point`s — without recording it. It is a deliberate widening, but `#2684` is…

- **2.4.2 Lifecycle and superseding in the raw-fact layer**
  - ✅ Adoptable, and it applies to the GRAPH side (Hindsight, no contradiction with any recorded decision): invalidation by RELOCATION rather than a flag. They move superseded rows to a separ…

- **2.4.3 Who carries the span? `Point`s only — measured, not assumed**
  - \- `extractedFrom` is a `Point`→`Source` edge (`projection/entities.py:655`, `:1864`) — not an `Object`→`Source` edge. (An earlier reading of this document assumed `extractedFrom` hung off…
  - (An earlier revision of this section reasoned from "the only edge on a `Source`" to "so `Point` is
  - the only possible carrier" — that does not follow, and the premise was an edge-type over-claim; the
  - carrier choice rests on the argument above, not on the edge census. #5007 review.)

- **⭐ ✅ THE ENTITY-PROVENANCE DECISION — research returned, and it changes the claim above**
  - \#### ⭐ ✅ THE ENTITY-PROVENANCE DECISION — research returned, and it changes the claim above
  - Researched 2026-09-23 because the owner asked; findings in §16. Seven comparables were examined: entity-level provenance is the FIELD NORM — 5 of 7 have it (GraphRAG `Entity.text_unit_ids…

- **2.4.4 ⛔ Redaction and the append-only layer — the G4 reconciliation (`#8496`)**
  - \**G4 (`§4.3`) is the one gap in this document that is not a missing step: it is two correct rulings made
  - in different issues, at different times, where neither could see the other's constraint.** `#4911`
  - (redaction) closed `COMPLETED` on **2026-09-26**; `§2.4.2` (append-only) was ruled **2026-09-23**. §2.4.2
  - makes the raw-fact layer immutable *by design* — a raw fact that can be rewritten is not evidence — and a
  - control that removes a credential is, read naively, a rewrite. **`#8496` asks for the reconciliation to be
  - STATED, not implied. It is stated here, as four decisions.**
  - \**⚠️ R3 corrects the premise `#8496` was filed on — the partial-scan caveat was DISCHARGED by its own
  - issue.** `#4911`'s *body* reported a scan of **10,000 of 19,960** turn nodes (FalkorDB's default result
  - cap), and `#8496` inherited that as an open gap. `#4911`'s own **posted re-scan (2026-09-25)** read
  - \**21,281 of 21,281** turn nodes to the host's `hasMore=false`, `include_retracted=True`, and found **0**
  - anchored matches (the *"170 nodes contain `sk-`"* figure is prose — `risk-`/`disk-`/`task-`). **So the
  - coverage question is not "was the store scanned?" — it was — but "does every future clean claim carry
  - its denominator?", which is what R3 pins. Two rules follow and they are the mechanism the item asked
  - for: the scan **must page to `hasMore=false`** (a default-capped read is a partial scan wearing a clean
  - verdict), and it **must run the same table as the write path** (`redact_secrets` / `CREDENTIAL_KINDS`) — a
  - parallel pattern list is a second source of truth for what a credential *is*, and the two would drift
  - \**⚠️ And the layer G4 governs does not exist yet — so R1–R4 bind a design, and R2 is a procedure for a
  - store that today holds no raw facts.** The raw-fact persistence step is still open (`§4.3` G1 — `#5064`;
  - The span *schema* exists (`#5007`), so a `Point` can carry `span_start`/`span_end`, but the raw-fact layer
  - is not written); and `#4911`'s own measurement says there is nothing to erase today. **This subsection
  - records the design so that the layer is built WITH the control — which is precisely what `#8496` asked
  - for and what designing them in separate issues, at separate times, could not deliver.**
  - overridden and no reopen is required. (`#4911` reached the same conclusion for its own scope, and this
  - line carries it forward so the next lane holding a vendor's append-only page finds the ruling here.)

- **3. Step 0 is not the only source-varying step**
  - The wrong assumption, corrected (owner, 2026-09-23): "An asana task completed has a very specific format, or a github issue completed… I imagine we need to optimise some of the other step…
  - ⇒ A source type declares a MAPPING, and that mapping configures steps S0, S1, S2 and S5. The `Source` type vocabulary exists and packs already declare `extraction.sourceTypes` (§8). What …
  - ⚠️ AND IT IS NOT MERELY UNDECLARED — THE S0 HALF IS NOT BUILT. Confirmed in code: `_edus_from_conversation(conversation)` + `chunk_transcript(edus, target=chunk_size)` are transcript-only…

- **4.0 What each step is FOR — the purpose test**
  - ⇒ EIGHT steps: S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6. ⚠️ The gap in the numbering is cosmetic and should be closed (S1–S8) once the design settles — but do not renumber yet: `#4894`…

- **4.1 The step list**
  - Eight steps. No S4 and no S1.5 — both were deleted by the purpose test in §4.0. ⚠️ The count is the TOP-LEVEL steps (S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6): `S0a`/`S0b` are sub-step…

- **⭐ D10 and the version model — what the extractor must respect**
  - ⇒ D10 holds in both halves — ontology and code. Ontology: `ONTOLOGY.md` v3.15 (PR `#5022`, issue `#5013`). Code: PR `#5127` retired the `:Document` NODE label, and the `:Source` replay ke…
  - | Q2 | Two classification axes, kept separate: `sourceKind` (what kind of source) + `documentKind` (what genre of document) | This is S2.3's target vocabulary. ⚠️ An intermediate reading …
  - | Q5 | Fold + change the replay key + migrate (`#5026`/PR `#5127`) | The fold and the `coalesce(title, name)` → `url` rekey cost nothing: zero `:Document` nodes and the commit lane had ne…

- **⭐ The version model — and the one thing S6 must do (owner ruling, 2026-09-24)**
  - ⚠️ Scope of the anchor — no longer Point-only (resolved 2026-09-25). `sourceVersion` rides on `extractedFrom`, which is declared `Point → Source`. Derived Events and Documents reach their…
  - ⚠️ Interval-closing is part of the WRITE, not a later repair. An unclosed `validTo` reads as "still true" indefinitely — the field names this as the #1 production bug in temporal knowledg…

- **S1.5 — ⛔ REMOVED (owner review, 2026-09-23)**
  - \### S1.5 — ⛔ REMOVED (owner review, 2026-09-23)
  - \**There is no S1.5.** It was two things, and **both have now been removed for different reasons** — the review half as a defect, the lookup half as a **mis-ordering**.

- **(a) The review half — deleted as a defect**
  - \#### (a) The review half — deleted as a defect
  - "Was S1 too coarse?" was asked twice (here and as S2.2's question 3), on the wrong artifact (atomicity is a property of the items, not of the prose), and blind to the failure it targeted …

- **(b) The lookup half — deleted as a mis-ordering (owner, 2026-09-23)**
  - \#### (b) The lookup half — deleted as a mis-ordering (owner, 2026-09-23)
  - ⇒ The lookup moves into S3, and becomes keyed on candidates. Doing it earlier was looking up words from prose, not entities — a strictly weaker operation that also could not cover S3's ke…
  - The correctness argument is now in ONE place. The old design split the dedup guarantee between "extraction knew A existed" and "S3 resolved the candidate" — two places that can disagree. …

- **⚠️ This REOPENS D4 — and narrows it along its own reasoning**
  - \#### ⚠️ This REOPENS D4 — and narrows it along its own reasoning
  - ✅ STATUS: `DECIDED (narrowed)` — the owner ruled. The narrowing is in force: the lookup sits after extraction, and it is consumed by the salience judgment and by S3 only. Extraction does …
  - \> **✅ D4 is `DECIDED (narrowed)` (owner).** There is no pending confirmation, and no third phrasing.
  - \> ✅ Note — the lookup bug `#4511` does not gate this design. It bit when the lookup sat before extraction (`_fts_rows` read `r.get("kind")` while the callee emits `point_kind`, so every p…

- **S2.1 — EXTRACT**
  - \> ⚠️ Vocabulary — "claim" is NOT a thing (owner, 2026-09-23). `docs/ONTOLOGY.md` v3.14 (`#4369`) declares it: "claim" is the sanctioned user-facing gloss for a belief `Point` — "It is not…
  - ✅ FINDING (2026-09-23) — the live code ALREADY hands extraction the raw alongside the story. The call is `run_s2(model, story, master, session_date=…, edus=edus, …)` (`extractor_v2.py` :4…
  - \> ⚠️ What this does to D3. The open question is no longer "may extraction consult the raw?" — it already does. It becomes the much smaller "what does the narrative add that the raw does n…

- **Level 1 — per-candidate (many, cheap, evaluated in parallel)**
  - \> This is where the pack stops being prose. `packs/dev/manifest.yaml` already declares `memory_granularity` — "Durable: problem-family reasoning … Ephemeral: issue/PR numbers, CI status, …

- **Level 2 — per-batch: did this batch come out right? (a batch verdict, not a per-item one)**
  - \1. Coverage: did we account for everything the narrative said? If the narrative stated three things and extraction emitted one, that is a loss — and it is only visible by comparing the it…

- **⚠️ Owner question: if the narrative is poor, should we send it back? (2026-09-23)**
  - Outputs: `KEEP` · `NOOP` · `DISCARD` · `MERGE` (the VERDICT — a flag, §16.4) · `SPLIT` (for a fused item — extraction may then split it from the raw, which it already has) · `RENARRATE` (…
  - ⚠️ `MERGE-INTO-EXISTING` is deliberately NOT a VET output — and `MERGE` and `MERGE-INTO-EXISTING` are not the same thing. VET may emit the verdict `MERGE` — "fold into another candidate" …
  - ⚠️ There is no S4 — do not restore one. A previous draft listed a separate "S4 — granularity review" and folded it back in here. It is not a step: its purpose was identical to question 2 …
  - \> **⛔ DRIFT MARKER — this sentence is about the v4 PROPOSAL, not the shipped code (checked 2026-10-13).**
  - \> | S4 | *"There is no S4"* | `merge_embed_lists(s2, s4)` def `extractor_v2.py:2673`, **consumed at `:7414`**; `run_s4` called **`:7396`** |
  - \> | the S4 flag | — | ⚠️ `TORTOISE_S4_DELTA` selects the **delta vs complete-list contract of S4's OUTPUT**, **not** whether S4 runs (`:689-710`) |
  - \> | stage order | the eight steps above | `run_s1` `:7290` → `run_s2` `:7314` → `S2.2 VET` `:7331` → `S3 search_graph` `:7375` → **`run_s4` `:7396`** → `S5` |
  - \> ⚠️ `TORTOISE_S4_DELTA=0` does NOT disable S4 — it selects the legacy complete-list contract. There is no S4-specific off switch: S4 is reached whenever the v2 pipeline runs a story, and…
  - \> docstring `:10-29`), with `S2.2 VET` present in the production path (`:7331`). **`S0` and `S6` have no code
  - \> classify pass is `kind_classifier.classify_items` (`kind_classifier.py:189`), wired at
  - \> `extractor_v2.py:7356-7358` / `:7487`, gated by `TORTOISE_CLASSIFY_LATER` — **unset today = "the LEGACY
  - \> ⚠️ Currency of these line numbers: the figures above are the numbers at `4c801fb18` — ⚠️ not the tree this marker landed on, so they are not a description of the tree at the commit that…
  - \> stands: the symbol name is authoritative and the line number is a convenience. ⚠️ For THIS passage that rule is now mechanical, not a promise: `tests/test_extractor_v4_s4_stage_claims.p…

- **S2.3 — CLASSIFY (assign the pack kind — after the gate, on SURVIVORS only)**
  - \**✅ It exists — and it is already built, already flag-gated, and already positioned after the gate. It is switched OFF.**
  - \**⇒ So the answer is: we HAVE it, it is `TORTOISE_CLASSIFY_LATER=1`, and it is OFF.** Three things are wrong with its current state, and they are the actual work:
  - \1. It is OFF, so classification happens INLINE — inside S2.1's big call. When the flag is on, `core_only=classify_later` is passed to S2 (`:4782`) so the extractor only assigns core kinds…
  - \2. The owner's improvement is real and specific: it must run on SURVIVORS. ⚠️ `_collect_classify_items` collects from `embed_list` — the whole candidate set — and `classify_consolidation`…
  - ⚠️ **Owner confirmation pending** — *"maybe part of S2.2? or not sure if we already have it"* — **the evidence says: we have it, it should not be a substep, and it belongs after the gate.**

- **⛔ What the code does TODAY — the order is inverted**
  - \#### ⛔ What the code does TODAY — the order is inverted
  - Resolved by call graph, not by line number:

- **The five correctness constraints — and ONE of them was FALSE**
  - \#### The five correctness constraints — and ONE of them was FALSE
  - | 4 | ✅ CONSTRAINT 4 IS TRUE — AND AN EARLIER "CORRECTION" BROKE IT. "CLASSIFY needs no graph; the LOOKUP needs KINDS → CLASSIFY before RESOLVE" | ⛔ RESTORED 2026-09-24. The 2026-09-23 co…
  - ⚠️ Constraint 2 is what fixes the near-duplicate stage's SITE — it is not merely compatible with it. Three passages in this document used to disagree: this table put a `MERGE` at `S2.2a-β…
  - The fusion cannot precede the gate: §S2.2 reserves new-vs-existing to S3, and the against-store leg needs the lookup anyway (constraint 5) — the fusion needs a destination, which only S3 …

- **✅ Jev for classification is cheap — but ⚠️ the seam does NOT yet exist**
  - \#### ✅ Jev for classification is cheap — but ⚠️ the seam does NOT yet exist
  - And ⚠️ `llm_tail` is NOT a ready-made Jev seam — that was over-read. It is a BOOLEAN (`kind_classifier.py:93`); the line cited as a seam is `:522`, the offline-eval CLI disabling it. The …
  - ⚠️ And the "structural enforcement" argument is weaker than it reads. `closed_vocab_rejects` already exists (the closed vocabulary is enforced today), so Jev's `Choice` does not add a new…
  - ⚠️ And a hard constraint this ordering must respect: ⚠️ `#4899`'s Phase-1 mechanical DISCARD has known false positives — "a bare `#\d{3,}` in a working note is discardable; a PR number a …

- **4.3 ⚠️ What the steps above do NOT yet cover (review-provided, all unaddressed)**
  - \### 4.3 ⚠️ What the steps above do NOT yet cover (review-provided, all unaddressed)
  - The step list is the SHAPE, not a complete specification. Five gaps were identified by review and are recorded here rather than silently implied by the diagram. These are gaps to close, n…
  - | G1 → `#5064` (open remainder; the span schema + passthrough exist (PR `#5108`), but `extractor_v2.py` emits no `span_start` (the repo's document extractor does — `docs_miner.py:373`)) ·…
  - | G2 → `#5008` | No EMBEDDING-DECISION step. `STORAGE-ARCHITECTURE.md` §12.2a decides the `EMBED` gate exists ("a row can be worth KEEPING but not worth EMBEDDING", "the lever is what get…
  - | G3 → `#8495` ✅ DECLARED | Per-step FAILURE POLICY — ✅ declared in §4.4 (2026-10-14): every step states fail-open or fail-closed, the invariant that survives, and its retry/transaction b…
  - | G4 → `#8496` | ⛔ `#4911` (no secret redaction) + an append-only immutable raw layer = IMMORTAL SECRETS. ✅ RECONCILED IN `§2.4.4` (R1–R4): redact BEFORE the append; the already-persisted…
  - Where the five gaps live: G1 `#5064` (the open remainder — PR `#5108` carries the span schema + passthrough; what is still missing is that `extractor_v2.py` emits no `span_start` — the re…

- **S3 — RESOLVE (find what exists · disambiguate · attach)**
  - \**S3 owns the lookup** — moved here from S1.5 (§4.2). The lookup is keyed on **candidates**, and candidates only exist **after** extraction.
  - ⚠️ The narrative is an INPUT to this step. The old S3 listed only "candidates + what exists" — leaving the disambiguation context out of the step that needs it most. Owner, 2026-09-23: "w…

- **4.4 Per-step FAILURE POLICY (G3) — ✅ declared 2026-10-14**
  - \### 4.4 Per-step FAILURE POLICY (G3) — ✅ declared 2026-10-14
  - Why this section exists. §4.3's G3 recorded that only two of the eight steps named what they do on failure — S2.2 ("fail-open — keep") and S2.3 ("never raises") — and that an unstated def…
  - `HOLD` is a run-level disposition, not a new VET outcome. It is the set of candidates a run did not commit and did not discard — carried forward for a later run against the same graph. ⚠️…
  - | S2.2 VET (Jev) | FAIL-OPEN (already declared) — a failed Level-1 question returns `KEEP`; a failed Level-2 verdict triggers no re-narrate and the batch proceeds. | "a wrong keep is nois…
  - | S2.3 CLASSIFY (Jev + kNN) | FAIL-OPEN, never raises (already declared) — the candidate keeps `unclassified`; the run records it in its census. | A wrong kind is worse than no kind: `_fi…
  - | S6 COMMIT (mechanical) | FAIL-CLOSED, ATOMIC. One run = one journalled commit; a failure leaves the journal — and the derived graph — unchanged. | I3. A half-applied run is a duplicate/…
  - \**The re-narrate loop — bounded to once, and now with a deterministic tie-break.**
  - §S2.2 already bounds the loop ("once, then accept") and names the trigger (a poor batch verdict, e.g. a keep-rate below a threshold). What was unstated is which narrative is kept when the…
  - ⚠️ State of the shipped code — this section is a SPEC CHANGE, not a description of what runs. The load-bearing instance is S3. The shipped `search_graph` degrades instead of raising (corr…

- **5a. Supersession — we already have it. The gap is DETECTION, not mechanism**
  - \## 5a. Supersession — we already have it. The gap is DETECTION, not mechanism
  - \**⚠️ The premise is wrong in a useful way: the system already exists, is fully wired, journaled, and tested.** Measured on the live graph (2026-09-23):

- **⛔ "The more recent fact wins" is REFUSED — contradiction test**
  - There is a recorded decision on exactly this, and it says no. `#2354` (OPEN, epistemic-team) — "uncertainty verdict layer — close/keep/reject on correction conflicts via quarantine+promot…
  - \2. It is the wrong mechanism for us, and the reason is knowable. Recency-precedence is what Hindsight uses — `"the fact with the LATEST mentioned_at is authoritative"` — and they use it b…

- **✅ The owner's second option is what we already have — and it is the right one**
  - "the fact attached to the right entity / the entity lifecycle" is the built design, not an alternative: the fact attaches via `aboutObject`, and the Object's lifecycle decides live-ness —…
  - ⚠️ One trap to design around, which follows from §5's correction: a fact that caused A's death must not die with A. Blind propagation of staleness down `aboutObject` would kill the reason…

- **Atomicity is required by the epistemic layer — NOT an optimisation**
  - Operators connect only epistemic targets (any of `:Point`/`:Subject`/`:Object`/`:Event`; see `ONTOLOGY.md` §8 `OVERRIDES`) — you cannot `NAND` a paragraph. The `:Subject`/`:Object` operat…

- **State values and numeric facts — the carve-out is NARROWER than it looks**
  - v3's E2 requires state-value facts be preserved verbatim ("personal best 5K time = 27:12, as of <date>") rather than filtered as "counts are noise." `STATE_VALUE_CARVE_OUT` exists in `_gr…

- **✅ The carve-out does NOT cover decision-relevant numbers**
  - \#### ✅ The carve-out does NOT cover decision-relevant numbers
  - `#2453` root-caused this: the carve-out is scoped to user-personal state (personal bests, schedules, preferences) and explicitly tells the mapper to DROP process metrics as "mechanics tok…
  - \**⇒ There are TWO value classes, and the policy must keep BOTH** (commit `4a690d0be`, PR `#2456`) — the table below states which:
  - | **Operational / decision values** | *"p99 latency 480 ms at 3k rps"* | **RETAINED** (commit `4a690d0be`; PR `#2456`) | retained |
  - Operational values are DURABLE (the extension is `#2453`). `STATE_VALUE_CARVE_OUT` (`extractor_v2.py:145-171`) with an operational-value paragraph — "a concrete value that is the SUBJECT …
  - ⇒ The design requires ENFORCEMENT BY DEFAULT — the mechanism is not the gap. The S2.2b identifier-only predicate is the shipped rule in `tortoise/value_gate.py`: `value_gate._REFERENCE` (…
  - ⚠️ And the mechanical DISCARD (`#4899`, §16 — `value_gate.py`) would otherwise have inherited this bug — had it been defined against the old, stale reading, it would have discarded operat…

- **Where numeric facts should live (owner, 2026-09-23) — structured, so they are never re-processed**
  - ⚠️ PLACEMENT IS DECIDED (owner, 2026-10-06): a state/numeric value is a STRUCTURAL FIELD ON AN ENTITY, not a `Point` (`STORAGE-ARCHITECTURE.md` §7, whose head reads "RETAIN is decided and…

- **8. Already in the code — do not rebuild**
  - \## 8. Already in the code — do not rebuild
  - | **S2/S4 merge** | `merge_embed_lists` (def `extractor_v2.py:2284`; **called at `:4845`** — ⚠️ not `:4838`, which is `try:` (`stage_stats` is `:4837`)) |
  - | **The narrative prompt slot** — S1's granularity rule is already injected | `_granularity_text()` |

- **10. The narrative-first hypothesis — tested, and the better argument**
  - \- ⛔ Zero precedents for summarise-then-extract. The one apparent candidate was miscited and corrected — it is the reverse direction (input = raw long document, output = a compressed graph).

- **✅ Update 2026-09-23 — a STATED reason for raw extraction now exists**
  - \### ✅ Update 2026-09-23 — a STATED reason for raw extraction now exists
  - \**⇒ Option 4 is no longer merely hedged — it is evidence-backed.** The narrative stays the cheap searchable tier and a *focus*; extraction keeps access to raw as the source.

- **⛔ REFUSED — contradicts a recorded decision ⇒ a REOPEN, not an adoption**
  - \> ✅ `OVERRIDES:` posted on `#2730` — for this ruling the marker lives on the issue, per the rule below; the issue comment carries the full text. ⚠️ The rule is not yet true file-wide — th…
  - \> ⚠️ **Two claims, and only one is established — stated so the ruling cannot be overclaimed later.**
  - The five challenges that were put to the owner, kept for the record:

- **13.6 ⭐⭐ THE FINDING THAT BITES US: they materialised entity edges — then DELETED them**
  - ⚠️ This does not automatically transfer — their `/graph` endpoint does derive the edges on demand, so the capability is preserved — and the measurement this sentence used to demand as "th…

- **13.8 What this does to our design**
  - | S5 CONNECT — two sets, within-batch and to-graph (D6, decided) | LLM does within-batch only, max 2, backwards; to-graph is mechanical (ANN + entity join) | ⚠️ A direct challenge to D6. …
  - | connection fan-out | bounded everywhere, with a timeout that drops an arm | ✅ The cap is adopted — `PER_ENTITY_FANOUT_CAP = 200` (`tortoise/fanout.py`), consumed by `assembly.py` / `sub…
  - \**⇒ One of the three questions below is still the owner's (question 1). Question 2 is measured, ruled and deferred; question 3 is adopted — `PER_ENTITY_FANOUT_CAP = 200` — see below:**
  - \2. Should `aboutObject` be a stored edge or a derived join? ✅ MEASURED 2026-09-23 — the answer is split; RULED (owner, 2026-09-23): KEEP the stored edge; DEFERRED to post-launch (not open…

- **14. Not established — do not treat as known**
  - \- Whether the neighbourhood lookup changes extraction's output is UNMEASURED — and the new ordering makes it moot. The old design put the lookup before extraction so extraction would emit…

- **15. Open questions**
  - Cross-batch connection — by LLM or mechanically — is open (§13.8). The other two questions this document raises are settled: hard fan-out caps apply (200, `PER_ENTITY_FANOUT_CAP`, §13.8 q…

- **16. Decisions and where their record lives**
  - Six open decisions were run through the mandated sequence — **contradiction test
  - first → research (does it actually need a human?) → the required artifact**. The
  - decisions themselves are §16.3 and §16.4; the record of how they were reached,
  - and of the two review cycles that followed, is in
  - \**The decisions it produced are below, and they are the design.** §16.3 records
  - the six answers; §16.4 states what `MERGE` means on near-duplicates.

- **16.3 ✅ ALL SIX DECISIONS CLOSED (owner, 2026-09-24)**
  - \### 16.3 ✅ ALL SIX DECISIONS CLOSED (owner, 2026-09-24)
  - \**These were escalated per the AGENTS.md protocol. They are now answered — recorded so no future lane re-opens them.** Full detail: `STORAGE-ARCHITECTURE.md` §14.1.
  - | O1 | The conflict was mine. `#2826` A2 (the real decision row) recommended the journal be authoritative; `#2881` reversed it by recommendation; the doc recorded the reversal and flagged…
  - | O2 | ⛔ Not a question — the owner rejected the framing: "we're not optimising stupidly for a number… this is not a corporate OKR setting exercise. We need to balance multiple things at …
  - | **O3** | ✅ **ADOPTED — emit the DECLARED outcome word (`DISCARD`), matching the engine's existing classifier.** | S2.2's outputs use the declared vocabulary. |
  - | O4 | ✅ Merge IS authorised — with Jev as arbiter over the claim plus narrative/raw data. ⛔ This supersedes the `OVERRIDES:` ruling on `#4899` ("never merging two claims into one"). | Th…
  - | O5 | ✅ Neither — draft and iterate: "we draft something (a prompt, a step of the extraction workflow, etc) and run it and see the result then refine and run again, until good." | The wo…

- **16.4 ⭐ O4 applied — what "merge near-duplicates" means here, bounded by the evidence**
  - \- ⛔ A merge must NEVER happen across a difference in: a number or quantity, a named entity, a language, a negation, a condition, a load-bearing connective whose role changes across the pa…

---

## D3 measured, and it stays provisional (2026-10-18, `#7947`)

**What changed.** `D3` was measured twice and **remains PROVISIONAL**. The two runs reach that by different
routes: **run 1 (the run the registration fixed, written before any data) lands in branch 3 by the rule's own
terms** (Δ = −60 pp is neither "fired" nor "near parity"), and **run 2 meets branch 2 literally but cannot
certify it** (its decisive raw arm is `partial`). The registration has no branch for "parity met,
uncertifiable" — inventing one and calling it branch 3 would misdescribe the rule, so the scorer records
`branch 2 met, not certifiable; branch 3 not reached` and only branch 3's *effect* is adopted. The design statements are in `EXTRACTOR-V4-ARCHITECTURE.md` §9 (the D3 row) and §10 (both
runs, the limits, and what would settle it); this is the record of how the verdict was reached.

**Why no owner verdict was needed.** The protocol run above (2026-09-23) already settled that: *"D3: raw vs
narrative … needs the owner: **no** … The measurement decides — not a gate."* A question raised on `#7947`
asking for the owner's reading of run 1's 32 pp pooled gap was **sent back** by the owner (`#7947` comment
`6103751497`, 2026-10-11) with the standing rule *"if the research settles it, ACT on it and do not ask"*. The
canon settles the **authority** question in that protocol row; the verdict is the **measurement's** to give, and
what it gave is *"not answerable as designed"* — a statement about the registered design (the first reason
below), not a judgement attributed to the instrument.

**Two runs, and the verdict does not depend on picking one.** Run 1 (2026-10-10) put the holdout Δ(loss) at
−60 pp, which the registration's third branch (*anything else → stays provisional*) absorbs — so the
pre-registered run already returns "provisional" with no extra rule. The run was therefore
**repeated** — identical protocol, corpus, gold, pinned model, chunking — on 2026-10-18, this time committing
per-session arm receipts so the scoring is re-runnable offline. The repeat put the holdout Δ at **+10.0 pp**
with Δprecision **+0.8 pp**, which *literally* satisfies branch 2 (|Δ| ≤ 10 pp, P/R within ±5 pp).

**⛔ Three independent reasons that literal reading is not a settlement, each sufficient:**

1. **The REGISTRATION specified a design that cannot test the falsifier** — this is a defect of the question,
   not a discovery about the run. §S2.1 had recorded on 2026-09-23 that the code passes the raw alongside the
   story, and the registration therefore compares *narrative + raw* against *raw alone* with the same `edus`:
   `render_s2_prompt()` appends the full raw transcript to the prompt for **both** arms, so they differ only in
   the `story` slot. A narrative that dropped a hedge would still
   have it in the transcript. The harness measures *what the narrative adds to raw* — D3-Option 4's shipped
   shape — and **not** whether a narrative could replace raw.
2. **The decisive arm ran partially.** `wp07` raw carries `partial: true` (rung-4 partial-accept, tail dropped)
   and supplies 3 of the holdout's 6 raw losses; one qualifier there turns +10.0 pp into +20 pp and fires
   branch 1. A branch cannot be certified on an extract the extractor marks incomplete.
3. **n = 25 (10 holdout) is narrower than the effect it must resolve,** and the bound-qualifier subset flips
   sign between halves (+16.7 pp on the holdout, −10.0 pp on tuning). Repeating the run does not settle it either: the
   registration fixes ONE run (and it is run 1, whose holdout Δ is −60 pp), so a repeat is evidence rather
   than a replacement — and in any case the two runs return **opposite** statuses from the same rule.

**The replication's separate finding is about magnitude, and it is real.** The falsifier did not fire on the
registered metric in either run — *and as built it could not have: both arms carry the raw transcript, so a
narrative-borne loss has nowhere to show up* — and the narrative's pooled loss was identical both times (48.0%, 12/25) — but
that pooled match is a NET (the narrative's per-session loss moved in 6 of 7 sessions, −1,−1,−1,0,+1,+1,+1), and
the **raw** arm moved 28 pp on the same corpus (80.0% → 52.0% loss; 54.4% → 78.9% recall), so run 1's 32 pp gap
does not replicate. ⚠️ **Confound declared, not hidden:** `main` advanced 8 days between the runs, so the
run-to-run swing cannot be attributed to sampling alone; the *within-run* comparison is unaffected because both
arms ran the same code.

**Registered work that did not happen, recorded rather than dropped:** the judge-arbitration pass over the
decisive `flattened` cases; the repeat-run variance check; the **secondary fleet-session arm** (§2 of the
registration, "drawn and listed by id at first run"); the six-outcome taxonomy reduced to three (so
`weakened`/`strengthened` score as `preserved`); and the environment pins — which the harness now persists into each receipt, but which **these two runs' receipts do not carry**, so their environment is testimony, not artifact.

**What would settle it:** two arms that differ only in the **carrier** — narrative-with-transcript-suppressed
vs raw-with-transcript-suppressed — on clean (non-`partial`) receipts, with ~100 annotated **bound** qualifiers
**and** independent sessions (a run-level shift, not qualifier sampling, is what moved 28 pp here). Recorded in
§10.

**Not touched.** Raw stays the source. Its argument is independent of qualifier loss (a summary cannot see
prior state; re-extraction is always from raw, §10) and §2.4's raw-fact layer is untouched by this result.

### What the review cycles corrected in the measurement's own instruments (2026-10-18/19)

Recorded here because each one changed a CLAIM about the numbers, not a number: no committed figure was
re-derived (run 2 still regenerates byte-identically — now pinned by a test, not by prose).

- **The lexical instrument's error direction was stated one-sidedly, and it is two-sided.** The module
  docstring and the emitted `instrument.note` both said the marker test "can only under-credit preservation".
  It can also **over-credit** it: `marker_present` is an unbounded substring test, so `"commonly"` satisfies
  scope's `"only"` and a `flattened` cell reads as `preserved` — understating loss, and not arm-neutrally (the
  arm emitting more claim text gets more chances to contain a mid-word hit). Corrected in the module, in the
  emitted note (both committed score files carry the corrected note; run 1's by hand, since its receipts are
  gone), and in §10's limits, with a test asserting no committed `preserved` cell rests on a mid-word hit.
  Word-bounding the table would remove the class, but that is a REGISTERED-instrument change, so it belongs in
  a newly registered run rather than a silent re-scoring of these two.
- **S1 failures were written into the receipt and read by nothing.** `build_inputs()` swallows a per-chunk S1
  raise into `errors` and carries on, and `run_s1` was called without `stats`, so a length-capped summary
  (S1's documented tail-drop) left no trace at all — a narrative story missing a chunk could therefore move
  the decision cell while `branch_2_certified` stayed true. The harness now records `s1_stats` and prints the
  S1 errors, and the scorer consumes both **on the narrative arm only** (S1 builds that arm's input; the raw
  arm's is the transcript). This is the same "the flag was in the receipt, unread" defect the cycle before
  fixed for S2, one stage earlier.
- **The rule's own tests reached no CI surface.** `test_score_qualifiers.py` was registered nowhere
  (`manifest-integrity` reds on drift), and because `tests/eval/write_path/` was not a `SOURCE_PATTERNS`
  entry, a `score_qualifiers.py`-only change selected `core` — which registers none of the tests pinning the
  rule, the corpus counts or the artifact. Both halves are now registered (`eval`, plus `core` via
  `CORE_ALSO`) and pinned by `test_write_path_measurement_source_edit_selects_eval`.
- **A delta that does not EXIST was read as a delta that PASSES — and, before that, crashed the scorer.** Two
  defects on the same input (an arm that emits no claims): `parity` accepted a missing P/R delta
  (`all(d is None or abs(d) <= tol)`), so Δloss 0 with precision/recall `None` — every qualifier `absent` on
  both sides — certified branch 2 and `verdict_label` recorded D3 as **settled** off two empty extracts, with
  no contamination flag tripped (`partial`/`truncated`/`s2_error` are clean: the output is EMPTY, not
  incomplete); and the decision-input print formatted the three deltas unconditionally, so the same run raised
  `TypeError: unsupported format string passed to NoneType.__format__` *before* the artifact was written.
  `main()` now refuses a split with no precision delta (exit 2, message), every delta renders `n/a`, and a
  missing delta makes the verdict `no branch matched — scoring input incomplete`. No committed figure changes:
  run 2 still regenerates byte-identically (the artifact pin is what proves it), and the fix is pinned by
  four tests that fail on the pre-fix code (two of them on the `TypeError`, verified by reverting the module).
