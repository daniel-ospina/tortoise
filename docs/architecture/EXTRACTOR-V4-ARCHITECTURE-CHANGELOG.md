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
| **Keep the epistemic + entity connection layer** | No contradiction — it *is* the product. The field's cost-driven default (Hindsight derives instead of storing) points the other way. | ruled | ✅ **KEPT — owner, 2026-09-23.** Cheap because edges are near-bare: two ids, the binder's `confidence` on a *bound* link, the record projection's `idx` ordinal on a record-projected edge, and — on that same edge — the record row's placement columns plus an open-set `props` tail whose names the map-form writers supply at runtime and which are not statically bounded. ⚠️ **Corrected 2026-10-20 (`#9397`):** the earlier "property-free" was read from a search that cannot see a `SET r.<prop>` writer, and the second name is written in a THIRD form that no `SET`-clause search can see at all — the inline pattern map in `cache_projection.relation_statement`. Postgres holds them **on disk** (~3 MB ≈ $0.0004/mo). **`OVERRIDES:` posted on `#4333`** — never cut this layer to save money. See `STORAGE-ARCHITECTURE.md` §11. |
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
- `EXTRACTOR-V4-ARCHITECTURE.md` §13.8, question 2 — **CORRECTED 2026-10-20 (`#7948`): TEN sites are customer-callable, behind EIGHT doors — the four counted here missed the whole state-recall path, and a later pass added three more readers that no module-level census could see.** Moved here verbatim on 2026-10-21: at that pass the design document stated only the count (*"TEN sites are customer-callable, behind EIGHT doors"*) — a figure **since superseded, twice** (SEVENTEEN sites / TWENTY doors at the 2026-10-21 fifth pass; §13's table is the authority, and neither number should be re-read as current here) — because a dated `CORRECTED` stamp and the narrative of what an earlier pass missed are change history, not design — and `tests/test_extractor_v4_citation_rot.py` pins that class in the design documents. What the count moves from and why: the paragraph counted FOUR customer-callable sites; re-deriving it at `26aff163c` found eight behind five doors (the whole `recall_state` path was missing, on four code paths), and a second pass found three more readers that sat INSIDE modules the table already named — `navigation._hop_query`, `mcp_server._preview_supersede` and `projection.FalkorProjection._rebuild_all_replay` — which is the drift a module-level census cannot see, and why `STORAGE-ARCHITECTURE.md` §13's census is now keyed on `module.py::symbol`. The ruling itself is unchanged: KEEP the stored edges, deferred to post-launch.

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

## §13.6 / §13.8 question 2 — the `aboutObject` premise is SCOPED to the files it measured (`#9399`)

The removed wording, and why it went. Two passages in the design document opened
with an unqualified claim about *"the primary search path"*, but the command they
quote as proof measures exactly three modules — and `tortoise/ranking.py`, which
holds both product ranking read paths and therefore the reads behind the default
recall door (`tortoise_recall` → `sdk_method="recall_state"`), is not one of them.

**Removed (verbatim), §13.6:**

> **The primary search path does not read the edge; four *customer-callable* sites DO.**
> ⇒ **Hindsight's premise holds for the primary search path only, so the comparable's conclusion does NOT transfer.**

**Removed (verbatim), §13.8 question 2:**

> The primary search path does **not** read it (`grep -in aboutObject
> tortoise/retrieval.py tortoise/search_engine.py tortoise/session_reinjection.py`
> → exit 1, case-insensitive) — so Hindsight's premise holds *there*.

The measurement was never wrong; the **scope** was unstated, so both passages read
as claims about the whole product and a reader checking only the quoted command
concluded the opposite of what the section rules on. The design document now
states the lane the three files bound and names the module and the door outside
it; `tests/test_9399_search_path_claim_scope.py` guards this document *and*
`STORAGE-ARCHITECTURE.md` §13 — one `CLAIMS` entry per document, re-running each
quoted scan over the files that sentence names — so the qualifier cannot be
dropped from either copy, and a second unscoped copy inside the same document
reds as an absence.

## §13.8 question 2 — the read-site correction's narration moved here (2026-10-20, `#7948`)

The design document states the fact; the story of how the figure moved belongs here, per the rule the
design-language pass (#8542) established and `tests/test_extractor_v4_citation_rot.py` pins.

**The removed stamp, verbatim:**

> ⚠️ **CORRECTED (2026-10-20, `#7948`): TEN sites are customer-callable, behind EIGHT doors — the four
> counted here missed the whole state-recall path, and a later pass added three more readers that no
> module-level census could see.**

**What it recorded.** §13.8 question 2 previously counted four customer-callable sites (`belief_timeline`,
`topic_summarize`, `recall_subgraph`, graph-ordered `search`). That count missed the whole state-recall
path — `recall_state` alone reads the edge on four code paths, three of them through an `about*`
alternation CONSTANT — and a later pass added three readers that no module-level census could see: an
all-edge walk in `navigation.py` behind two further MCP tools, the supersede dry run in `mcp_server.py`,
and the replay delete-leg in `projection/__init__.py`. The document stated only the resulting fact at that pass
(**TEN customer-callable read sites, behind EIGHT distinct doors**); the authoritative enumeration is
`STORAGE-ARCHITECTURE.md` §13's table, resolved against the code by
`tests/test_7948_about_object_read_sites.py`.

**And the guard that forbids this shape had a false PASS, now closed.** Its date arm was pinned to the
space-separated form (`CORRECTED 20xx-`), so the parenthesized stamp above — the form this document's own
corrections actually take — read as clean. The separator now accepts a short non-digit run, and the
instrument's own both-directions test carries the parenthesized form as a `must_flag` fixture.

**Two citations in §13.8/§13 named something else at HEAD, and were re-pointed:**
`create_about_edge` (`projection/edges.py:344` → its `def`, at `:1458`) and, in `STORAGE-ARCHITECTURE.md`
§13, `_connect_issue_objects` (`sdk.py:20154` → the symbol, `sdk.py::TortoiseSDK._connect_issue_objects`).
§13's prose now states no line numbers at all, matching its own sentence that it carries none; the
sibling `tests/test_extractor_v4_citation_rot.py` asserts every remaining quoted-symbol citation resolves.

## §13's remaining line citations symbol-keyed, and the LINE asserted where the document keeps one (2026-10-21, `#7948`)

The entry above records that *"§13's prose now states no line numbers at all"*. It still carried two, and
both had rotted: `projection/entities.py:785+` (line 785 sits inside `_connector_content_hash`'s docstring,
not the replay — the fold is `projection/entities.py::_EntityHandlers._fold_entity_linked`) and
`test_index_restore.py:373-403` (the unjournalled-`Document` drop, not the `aboutObject` count, which is
`tests/test_index_restore.py::test_t12_instantiates_edges_rebuild_drop_and_restore`). Both are now
symbol-keyed, so the sentence is true and a line that MOVES cannot falsify it.

Two guards, so neither class survives a later edit.
`tests/test_7948_about_object_read_sites.py::test_section_13_carries_no_line_citations` holds §13 to its own
sentence — the document states it carries no line numbers, so carrying one reds. §13.8 was symbol-keyed as
well, in the entry below, which is the guard that SHIPS: no line is pinned anywhere in either section, and
`tests/test_extractor_v4_citation_rot.py::test_section_13_8_carries_no_line_citations_and_its_symbols_resolve`
asserts both halves (none present, each name resolves). The intermediate span assertion
(`test_the_rederived_read_site_citations_land_inside_their_own_symbol`, which required each §13.8 citation to
resolve *inside* the symbol it was attached to) was replaced in that same pass because it was over-blocking
and under-detecting at once: one inserted line above a reader's `def` moved the span and reddened a `core`
test on a PR that touched no reader, while a mid-body citation only had to sit somewhere inside the symbol.
That is why the two citations that had drifted — `_state_related_objects` and `_state_related_points`, both
landing in a `mitigated_by` reader instead of their own bodies — are now symbol-keyed rather than re-pointed.

## §13.8 symbol-keyed (no line pinned), its count made to hold its own enumeration, and the door figure derived (2026-10-21, `#7948`)

**The §13.8 line guard was over-blocking and under-detecting at the same time.** Ten of its eleven
`file.py:NNNN` citations named a reader's `def` line, so inserting ONE line above
`def _preview_supersede` in `mcp_server.py` moved the symbol's span and reddened a `core`-registered
test on a PR that touched nothing in this document — and the one mid-body citation
(`_preview_delete_edges`) was only bounded by "somewhere inside the symbol", so it could point at a
different statement and stay green. That is the rot `STORAGE-ARCHITECTURE.md` §13 retired in this same
PR, so §13.8 now takes the same resolution: **every reader is cited as `module.py::symbol`, and no line
is pinned anywhere in the document.** `tests/test_extractor_v4_citation_rot.py::test_section_13_8_carries_no_line_citations_and_its_symbols_resolve`
replaces the line-span test and asserts both halves (none present, each name resolves), and the
whole-document quoted-symbol floor drops 10 → 6 because §13.8's eleven citations no longer match that
form. Non-vacuity measured: restoring one citation as ``(`mcp_server.py:6529`)`` reds the new test.

**The count and the enumeration under it had also come apart.** §13.8 read *"TWELVE sites are
customer-callable"* and then listed thirteen, the thirteenth being `sdk.TortoiseSDK.supersede_point` —
which `STORAGE-ARCHITECTURE.md` §13 classes `write path` (row 20), its read half being
`mcp_server._preview_supersede` (row 21). The numeral therefore agreed with §13 while the list under it
did not, and nothing could see it: the agreement guard compared only the number. Two fixes:

- §13.8 now enumerates exactly the twelve customer-callable readers, and states the write half
  separately — `supersede_point` IS reachable from `tortoise_supersede(dry_run=False)` and is a public
  SDK method, so it is a door into the edge, but it MOVES the edges rather than reading them to answer
  a customer, which is why the count stays twelve;
- `tests/test_7948_about_object_read_sites.py::test_the_extractor_document_agrees_on_the_count` now
  asserts **membership in both directions** (every customer-callable row named, no reader of another
  reach named) and reads BOTH word orders the document states the count in — `N sites are
  customer-callable` (§13.8) and `N *customer-callable* sites` (§13.6), the second of which the old
  pattern never matched, so one copy could be edited while the other stayed stale and green.

Measured non-vacuity: putting `supersede_point` back in the enumeration reds the membership arm naming
it and §13 row 20; editing §13.6's `TWELVE` to `THIRTEEN` reds the word-order arm (under the pre-fix
pattern it stayed green).

**And the blast-radius DOOR figure in the same sentence was a third hand-count that nothing read.** It said
*"TWELVE distinct doors"* while the 12 customer-callable reach cells in `STORAGE-ARCHITECTURE.md` §13 name
**sixteen** distinct door identifiers; the folding rule was applied inconsistently (graph-ordered `search` and
`tortoise_fts_query` folded into one door, the three `dry_run` delete tools left as three); and the sentence
justifying it — *"TWELVE sites behind those twelve doors, because `recall_state` alone reads the edge on FOUR
code paths"* — requires doors to be FEWER than sites while asserting they are equal. At that pass both documents stated
SIXTEEN, the rule was stated (*a door is one independently callable entry point — an MCP tool, a public SDK
method, or a served REST route — counted once per site that reaches it* — ⚠️ a rule since RETRACTED: it yields
cell occurrences, not distinct doors), and `test_the_stated_door_count_matches_the_table` derived the total and
the per-kind breakdown (then nine MCP tools, five SDK methods, two REST routes) from the reach cells and
asserted it in BOTH documents. ⚠️ These figures are SUPERSEDED — the census carries whatever §13's table states, so the present counts live in §13.8 and §13 of the two
design documents and are never restated here. Measured before the
fix: rewriting the figure to `four` here, or `TEN` in the sibling, left all ten tests green.

## §13.8 question 2 — the count moves to SEVENTEEN sites / TWENTY doors, because the ALL-EDGE class was still a hand pass (2026-10-21, `#7948`)

The guard over this passage now DERIVES the untyped-walk class from the tree, so this entry records what the
derivation moved and why the predecessor kept missing it.

**What was wrong.** Rows 24–27 of `STORAGE-ARCHITECTURE.md` §13 were added by applying the section's own
declared rule — *an unfiltered walk reads the edge without naming it; the class is enumerated, not excluded*.
Every subsequent pass applied that rule and reached a different number, because a rule that admits every
unfiltered walk admits more than any hand pass will find, and nothing recorded where it stopped. Measured at
the head this entry was written for: the table carried 27 sites and the tree had 33, and the six it missed
included the two whose absence mattered most to the ruling.

**The six, and why the backup ones are load-bearing.**

- `hosted_backup.py::_DUMP_EDGE_PAGE_QUERY` — every edge of the graph keyset-paged out **as data**
  (`MATCH (a)-[r]->(b) … RETURN …, type(r), properties(r), …`, no rel-type filter, no label constraint), into
  the per-org encrypted backup, behind `POST /v1/backups`.
- `hosted_backup.py::_COUNT_ALL_EDGES_QUERY`, `::restore_graph`, `::_restore_copy_settled` — the unlabelled
  whole-graph edge counts (`MATCH ()-[r]->() RETURN count(r)`) that make a dump's completeness checkable, take
  a restore's baseline/delta and gate whether the copy has settled. Behind `POST /v1/backups` and
  `POST /v1/backups/restore`.
- `sdk.py::TortoiseSDK.backfill_v25` — the same unlabelled all-edge count, returned to the caller as
  `report["edge_count"]`, on a public `TortoiseSDK` method with a registered MCP tool
  (`tortoise_backfill_v25`) in front of it.
- `sdk.py::TortoiseSDK._promote_incident_operators` — every incident operator edge of a freshly-live Point,
  target swept across all four epistemic labels (`epistemic_disjunction`, which **includes `Object`**) with no
  rel-type filter, so an `aboutObject` edge is walked and its endpoint status gates the promotion.

⚠️ **The backup readers are not a marginal addition to the count — they are the site the §13 ruling's own
durability bullet is about.** That bullet asks whether this edge class is consistent about durability, and the
backup **is** the durability mechanism, so *would a `SET`-free derive change what the customer can restore?*
was answerable only from a reader the table did not carry. `sdk.py::TortoiseSDK.backfill_v25` is the direct
analogue of row 27 (`definite_description_backfill.CENSUS_QUERY`), which the table already carried — on a
public SDK method rather than an ops audit.

**The structural fix — the part that stops a sixth pass.** `tests/test_7948_about_object_read_sites.py` now
censuses every Cypher relationship bracket that carries **no type at all** (`[r]`, `[r*1..2]`, `[x]`) inside a
read clause, reconstructing f-strings and `+`-concatenations so `[r:{rel}]` still reads as typed and the
`_hop_query` shape is not lost. Ownership is by enclosing `module.py::symbol`, exactly as the literal census
is, so a new unfiltered walk added beside an existing row reds. A site that genuinely **cannot** observe the
edge must appear in `_EXCLUDED_UNTYPED` with the code fact that earns it **present in that walk's own
clause** (a fact in an earlier clause of the same query counts only when it binds a variable the walk uses) —
and, from the other side, still present in the symbol's span — today that is only two shapes: the walked type restricted downstream
(`type(r) IN ['IMPL','NAND']` in `hosted_api._load_commit_graph_state`) and endpoints sitting outside every
`aboutObject` triple — a `Point`/`Event`/`Session` source, an `Object` target (`session_link.ENTITY_LINKED_TRIPLES`;
`STRUCTURAL_REL_LABELS` carries the target half). Measured: the register's nine entries cover exactly
the remaining untyped walks, and deleting a constraint reds
`test_every_stated_exclusion_is_still_true` while leaving the site unowned in
`test_no_untyped_walk_lacks_a_row_or_a_stated_exclusion`. **Nothing in this class is out by silence any more.**

**Counts.** §13, §13's blast-radius sentence, §13.6 and §13.8 stated, AT THAT PASS, THIRTY-THREE read sites, SEVENTEEN
customer-callable, TWENTY doors — ten MCP tools, six SDK methods and four REST routes. ⚠️ **SUPERSEDED** — §13 is the authority and states the table's current rows and counts — deliberately not restated here, so
do not re-read these as current. (`POST /v1/backups` and
`POST /v1/backups/restore` are new; `backfill_v25` / `tortoise_backfill_v25` are new) — every figure derived
from the reach cells by the guard, never hand-counted. §13.8's enumeration names all seventeen, and
`test_the_extractor_document_agrees_on_the_count` holds it there in both directions. The ruling is unchanged:
**KEEP the stored edges, deferred to post-launch** — the corrected count moves in the direction the ruling
already bet on, since more read sites make keeping the edge more strongly indicated.

---

## G5's destructive half — the mechanism was re-measured and §4.3 now states it (2026-10-22, `#5333`)

**What changed in the design document.** §4.3 gained a subsection, *G5's destructive half — the target and the
journaling arm EXIST; the bar, the arbiter and the RULING do not*, and the G5 row plus the *"Two need an owner
ruling"* sentence now point at it. **The ruling itself is unchanged and still the owner's**: `#5333`'s task 1 is
open, `DESTRUCTIVE_DISPOSITIONS` stays empty, and the subsection states a proposal rather than a decision.

**Why it was written: a premise in the decision record was false.** `#5059`'s record told the ruling that *"the
`(Object)` layer has no writer that folds an archive-style relocation — every existing `status` write is a Point
supersession (`ObjectSuperseded`, `#1350`)"*, and made the missing mechanism the reason the destructive half
needed its own issue. Measured against the tree, that is not the state:

| the claim | measured 2026-10-22 |
|---|---|
| no fold carries an Object relocation | `update_entity(<object>, status=…)` journals `EntityMutated`, the classifier labels it `restatus`, and replay runs the **same** `SET n += $s` on the canonical label (`sdk.py:33029`; `projection/__init__.py:18664` over `_ENTITY_MUTATION_STATE_OPS`, `:7383`) |
| every `Object.status` write is a Point supersession | `ObjectSuperseded` folds Objects (`projection/entities.py:4059`, rebuild sweep `projection/__init__.py:14280`) — but it requires a **visible distinct successor** (`commit_ops.py:1408`), so it is the one arm that cannot express relocation-without-successor |
| the target needs new vocabulary | `archived` is already in `commit_ops.OBJECT_TERMINAL_STATUSES` (`:51`) and the read-exclusion alias (`:59`), routed by `search_engine._status_vocab_for`; **no purpose-built producer writes it and no `_ALLOWED_TRANSITIONS` transition does** — ⚠️ CORRECTED 2026-10-26 (`#10069`): this cell read *"no first-class producer or lifecycle transition writes it"*, which the same design subsection refutes — the writers that exist are the generic unvalidated `update_entity` path and a pack-declared status fold (a fold's status is validated against the declaring kind's vocabulary) — `_ALLOWED_TRANSITIONS` (`sdk.py:2942`) declares it terminal with *"reserved — no v1 write path"* (`:2955`) **and nothing consults that map**, so it proves nothing about the Object family — while the generic unvalidated `update_entity` path **can** already place `archived` on an `:Object`, so the existing `archived` population is **UNMEASURED** and must be enumerated before any relocation pass |
| `#3304`'s raw-`SET` tombstone gap is adjacent and open | `#3304` closed 2026-10-06; the remaining gap is narrower and is stated in the subsection — **no Object-status vocabulary guard on the write side**, and it cannot be a closed set because pack-declared lifecycles own their words |

**The correction moves the cost of the authorisation, so it belongs in front of the ruling.** What the ruling
would buy is now: an applier plus the **Jev arbitration seam that does not exist** (§4.2 — an adapter, a prompt
and a parser), not a new event type and not a new fold. #5059's *"one thing B needs that does not exist today"*
is therefore replaced by an accurate list, and nothing in the ledger's invariants (I1/I2, the empty
`DESTRUCTIVE_DISPOSITIONS`) changed.

**Not changed, deliberately.** No producer for `archived`, no applier, no disposition rung, no Object-status
vocabulary guard (a closed set would break pack lifecycles — stated in the subsection rather than implemented),
and no `OVERRIDES:` marker: this is a proposal awaiting a ruling, not a departure from a recorded one.

**Two record defects fixed in the same pass, neither a design change.** ⚠️ **The subsection now names its revision
pin (`653980445`).** Its citations are HEAD numbers: read against the document's default pin (`c79ba1cf2`) five of
the cited files are shorter than the cited line (`sdk.py`, `assembly.py`, `projection/entities.py`,
`projection/__init__.py`, `commit_ops.py`) and `tortoise/definite_description_backfill.py` does not exist, so
the header's default sent a reader to no citation on every line of the passage — the pin is stated in the passage
and named in the header's exception list, and a guard reds if the pin is dropped. ⚠️ **The closing sentence
counted the steps wrong** — it read *"§4.4 declares the policy for the six pipeline steps … not a seventh
declaration"*, while §4.0/§4.1 declare **EIGHT** (and §4.4 covers all eight), so the carried-over rule is
*"not a ninth declaration"*. Both are corrected in the design document; a guard now reds if that subsection states a
spelled or numeric step count other than §4.0's canonical one, or a `… declaration` ordinal other than the next one.

## 2026-10-23 — §4.2's `S0a/S0b` verdict was stale in both directions

The four-way dedup audit's closing sentence said *"`S0a NORMALIZE + HASH → S0b SOURCE DEDUP` is genuinely absent"*. That
was true when measured and stopped being true when `tortoise/source_identity.py` landed (#5012/#5053 + `bc8b420d1`):
the step is implemented (`normalize_source_url` / `resolve_source_key`), wired in `projection/entities.py`, and
acceptance-tested by `tests/test_source_dedup_registration.py` — while the D-a row of that same table (URL-keyed only)
is the part that is still absent. The passage now states the design in the present tense with that division, and the
measurement history lives here rather than in the design document (#8542's rule). `config/v4-mechanisms.yml` carries
`source_dedup` as `implemented` and its declaration marker sits on this passage, so a stale
verdict there would be a row saying "shipped" anchored to a document saying "absent".

## 2026-10-24 — D12/O4's FUSION landed: the flagged verdict finally has a consumer (#5006)

`config/v4-mechanisms.yml` recorded `near_dup_merge` as `state: not-implemented, tracking_issue: 5006` while
`EXTRACTOR-V4-ARCHITECTURE.md` §4.2 named **three** sites for the near-duplicate concern — DETECT the pair
(`_boundary`, S2.2a-β), record the VERDICT (`vet_gate.OUTCOMES`' `MERGE`, S2.2b), execute the FUSION (S3) — and the
third had no implementation. `vet_gate.py`'s own docstring was the honest record of it: *"nothing consumes it yet: no
code routes a `MERGE` verdict to S3"*. #9424 had already landed the REFUSAL half (a fold that would drop an
attachment is not taken); what was missing is the authorised alternative for exactly that pair.

**What landed.** `tortoise/near_dup_merge.py` is the fusion: ONE survivor (the prior, its claim **byte-identical** —
never fused), the **union** of both sides' attachments written onto it, and the losing document's provenance kept as
a `(Session)-[:CONTAINS]->(Point)` edge with its own quote on the merge record. `extract_session_v2` routes the VET
`MERGE` verdicts (`_merge_flagged_contents`, matched by normalised content, not by VET's index-shaped item ids) into
`execute_embed`'s consolidation pass; `classify_consolidation` gained the fifth `MERGE` outcome the design always had.
The fusion runs in `capture_session` through `apply_merge_union`: the `aboutObject` union edges go through the
journaled `link_entity` writer, so they replay (`#3664`). The losing document's provenance edge
(`(Session)-[:CONTAINS]->(Point)`) is the same raw, unjournaled write the `#2104` fold uses — durable through the
`:Session` snapshot rather than the journal (`#3947`, `#3722` scope) — and an id-less `Object` stub stays a
live-only `MERGE` (`#1727`). What is guaranteed is that no edge is *lost*; what is not is that every one of them
replays.

**Why the bar is 0.95 and not the NOOP band's 0.45.** §16.4's evidence (*"practical thresholds cluster at ~0.95"*;
*"a false merge costs MORE than keeping near-duplicates"*) plus `#9424`'s split: a mid-band paraphrase pair is still
REFUSED and still goes to `ADD` — both claims survive. The fusion only makes the near-IDENTICAL pair admissible.

**The verdict is necessary and never sufficient.** The mechanical ladder (§16.4's never-across guard, the bar, a
readable union) is re-checked inside the mechanism. With no arbiter injected there are no `MERGE` verdicts, so the
mechanism is **inert** — the same injected-seam posture VET ships with — and the registry row says `implemented` on
the strength of `tests/test_near_dup_merge_5006.py` driving the real classifier, the real write path and a real
graph, not on a flag flip. A false `contained` counter (counting the ATTEMPT rather than the edge) was found and
fixed by that battery before landing.

**Review round 2 — three residual gaps, all closed by measurement.** (1) The verdict key was content-only: `MERGE` is
a legal outcome for `entities`/`events`/`points` alike and `_item_text` is `name or content` for all three, so an
event's text — or an entity's name — that normalised to a point's could authorise a fusion of a Point the arbiter
never marked. `_merge_flagged_contents` now keeps only the `points` section (a row with no readable section
contributes nothing), and `test_a_verdict_on_another_SECTION_does_not_authorise_a_point_merge` pins it. (2) The
id-less `Object`-stub branch of `apply_merge_union` counted the ATTEMPT (`linked += 1` with no read-back) while the
`CONTAINS` branch beside it counted the EDGE — reachable whenever the survivor is absent, and the one place `linked`
would stop being a measurement; it is now `MERGE … RETURN count(*)` like its sibling. (3) The ONE production
invocation of the fusion (`capture_session`'s merge block) was reached by no test — a mutation deleting it stayed
green. `TestTheCapturePathExecutesTheFusion` now drives the real capture write path (the S1–S5 extraction stubbed,
since `MERGE` needs an injected arbiter `capture_session` has no seam for) and asserts on the graph: the survivor
carries the candidate-only attachment, `(Session)-[:CONTAINS]->(Point)` exists, and no second Point was minted.

## 2026-10-24 — the declared set became an ENUMERATION: 8 rows → 13 (`#5064`)

`config/v4-mechanisms.yml` carried eight mechanisms. §4.2's FOUR-DEDUP AUDIT table declares four dedups by name
(`D-a`..`D-d`) and **only `D-a` had a row**; §4.1's step list declares `S2.2a-α EXACT DEDUP` and `S2.2a-β NEAR-DUP
DETECT` and neither was registered (only the S3 FUSION was); §4.2's *"the against-store exact dedup has FOUR
preconditions"* paragraph declares the fourth — an ATOMIC write — and says it does not exist, and the registry's own
`near_dup_merge` note recorded that it had **no row and no tracking issue**; §4.3 G3 / §4.4 declare a per-step
FAILURE POLICY and §4.4 states its own status (*"a SPEC, not a description of what runs"*). Five declared mechanisms,
no row for any of them — the first deliverable `#5064`'s Scope section names.

**What landed in the registry.** `exact_dedup` (`D-b`+`D-c`, one mechanism at two keys — `dedup_classify.exact_hit_id`
for the in-capture leg; the design's `D-b` within-batch leg is an INLINE `_norm`-keyed dict inside `execute_embed` and
`D-c` is `sdk._find_point_by_content`, a METHOD, so both are named in the note rather than anchored — and the three
legs do not share one equality notion), `near_dup_detect` (`extractor_v2._boundary`), `against_priors_consolidation`
(`D-d`, `extractor_v2.classify_consolidation`), `atomic_point_write` (**`not-implemented`**, tracking `#9879` — filed
because a declared precondition with nobody tracking it is the defect), `failure_policy` (**`not-implemented`**,
tracking `#5087`, the code half the design itself names). The marker convention is per-DOCUMENT and requires
"at least one" — `span_link`, `fanout_cap` and `sufficiency_router` mark two sites each — while a row citing several
sections of one document may be marked at only one, so `membership_errors` holds the new rows exactly as it holds the
old ones.

**The most valuable thing the enumeration found: `near_dup_detect` is a guard whose declared PLACEMENT does not
exist.** `_boundary` is real and tested (its wrappers are `distinguishing_difference`, `_difference`, `fold_allowed`
and `supersede_allowed`), but the FOUR call-site families are the post-gate consolidation pass
(`classify_consolidation`, called from `execute_embed`), the capture seam (`dedup_classify.rephrase_hit` — which
returns only the `(canonical_id, overlap)` verdict and does no graph I/O; the SDK's seam then FOLDS and DETACH-DELETEs), the temporal counting lane (`temporal_aggregation._restatement`; that COUNTING LANE has no
in-product `tortoise/` caller today — its consumer is `tools/longmem_eval/`), and the mining/ingest apply-first path
(`SDK._dedup_content_candidates`, a shipped write path reached from `mining.py` and from the public `SDK.mine_corpus`
→ `mining.mine_corpus_with_sdk`, whose `content_dedup` defaults to True — the parameter is the miner's, not
`SDK.mine_corpus`'s — which WIRES a draft-to-draft IMPL on the guard's verdict).
In `extract_session_v2`, `apply_vet` runs BEFORE
`execute_embed` — so the design's pre-gate `S2.2a-β` site does not exist, which §4.2's own *"The order is inverted"*
already records and `#5087` owns the code half. The row says so in its note rather than letting `implemented` read as
"the pre-gate step ships".

**Drift is now checked in one structure, not only by convention.** §4.2's audit table is a mechanism enumeration a
prose document can be read for, so `test_the_four_audited_dedups_each_have_a_row` parses it and compares it to the
registry BOTH ways — a fifth audited dedup with no row reds, and a mapping entry for a withdrawn one reds. The
marker convention remains what covers every other declaration, and an UNMARKED prose declaration is still invisible
(the residual #9778 states, unchanged and restated in the registry header). Two absence claims are pinned to the
code rather than to their notes: the degraded S3 render still instructing *"assume everything is new"*
(`failure_policy`), and no UNIQUE constraint naming `Point` while one already exists for another label
(`atomic_point_write`).

**What this does NOT do.** No mechanism was implemented — recording the absences is the output. `sufficiency_router`
(`#5009`) and `state_value_carve_out` (`#9775`) stay `not-implemented`, so `#5064`'s Target 3 (the attached symptoms
stop reproducing) is still open; the substrate half is done and should not be redone.

## 2026-10-26 — §4.3's *G5's destructive half* now carries the DESIGN decision (`#5333`)

A pass may only add (plus an evidence-preserving D12 MERGE), a physical delete is foreclosed to everything but GDPR/retention, and removal-from-recall is a journaled lifecycle fold alone — the fold's event stream stays the reconstruction source. The AUTHORISATION to ever run a relocation (task 1) remains the owner's; nothing is authorised. The gaps table's G5 row no longer reports the design question open, and the two capstone figures are cited to their record (`#5333`; probe `#9889`'s to land) instead of standing uncited. Pinned by `test_definite_description_backfill_5059.py::test_the_design_decision_is_recorded_add_only_never_delete`.

## 2026-10-26 — the S2.2/S2.3 typed-decision seam EXISTS, and the "seam does not exist" passages are stamped (#5087)

**What changed in the design document.** Two statements that the seam does not exist are now
historical, and both say so where a reader meets them: §4.2's "`llm_tail` is not the seam"
subsection carries a **STATUS** stamp at its head, §S2.3's table row records that the adapter is
built, and the `#5333` G5 subsection's *"the bar is not buildable today"* paragraph now names what
authorising that pass would still buy (the evidence bar and the relocation — not the seam). §8's
*Reuse these* table carried a **third** such passage in a frame that reads as present tense ("Jev
does NOT slot into its LLM tail … the swap needs an adapter, a prompt, and a parser") — that row is
now stamped too, because §8 is the section a reader consults to answer "where does Jev go?" and a
stale "it cannot go there" is exactly how the next lane rebuilds what this landed (`#10072` review). **No
decision was reversed:** O4's admissible set, `SPLIT`'s absence, the `MERGE`/`MERGE-INTO-EXISTING`
split, S2.3's vocabulary (`documentKind` genres and pack kinds, never `sourceKind`) and S2.2's
fail-open direction are all served by the seam as recorded.

**What was built.** `tortoise/s2_decision_seam.py` — the adapter (one round trip: `state` + a
**map** of typed questions, shape copied from a working caller per `#7866`; §2 of the record owns the
per-primitive evidence ledger, and this entry states it the same way rather than for "the caller", so
that no artifact in this PR can understate or overstate what has been sent), the two versioned
prompts (`vet-v1`: `noul` + `score` per candidate; `classify-v1`: a `choice` over the kinds the kNN
offered), and the validating parser (`type` echo, `[0,1]`, the declared 0–3 scale, membership of
the offered set — a violation names its question and yields **no verdict**). Consumers are the
seams that already existed and had no production caller: `vet_gate.vet_candidates(arbiter=…)` and
`KindClassifier(tail_decider=…)`. Armed only by `TORTOISE_S2_DECISION_SEAM`; **OFF by default**, so
no rate effect is measurable until an operator arms it. Design record and measured cost:
`docs/architecture/S2-DECISION-SEAM.md`.

**Which of those shapes is corroborated live, per primitive** (the ledger §2 owns; the split is
stated rather than lumped, because a lumped claim is how review round 18's `noul`/`score` finding
arose). `noul` and `choice` each have a live caller of ours: `agent-infra/scripts/ask-owner.py`
posts five `noul` questions to the vendor endpoint on the alias, and
`agent-infra/scripts/turn-classify.py` sends one `choice` carrying a `criteria` **map** and reads the
envelope's `model` on every call — so both the `choice` criteria map and the `model` field are
**corroborated**, not taken from a reference page. `score` is **documented only**: the vendor
publishes it (`docs.typesafe.ai/primitives/score`) and no caller of ours has ever sent one, so its
2–10 level `criteria` array — the dominant term in `vet-v1`'s request — is unmeasured here rather
than corroborated. The `model` echo is a **receipt, never an assertion**: it records which version
answered and is never compared to `MODEL`, because asserting it would make an armed seam inert on
the vendor's next release.

**What it does NOT do (each a recorded decision, not an omission).** No `SPLIT` (absent from O4),
no `RENARRATE`/coverage verdict (batch-level, and §S2.2 bounds that loop), no `MERGE` (`#5006`'s
ladder, and §16.4's different evidence bar), nothing at S3 or S5 (S3 is the sole authority on
new-vs-existing), no retry ladder and no cache.

**Cost, measured rather than quoted.** The seam's own `vet-v1` batch of 40 items / 80 questions
serializes to 39,484 B ≈ 9,265 input tokens ⇒ **$0.00039 per round trip (~$0.0000097/item)**;
`classify-v1` at 40 tail items ⇒ 23,292 B ≈ 6,112 tokens ⇒ **$0.00026 (~$0.0000064/item)**. The price
per token is the **vendor's** documented rate — `$42 / $0.042` per Btok / Mtok for `jev-1.13.0`,
input-only (output tokens are free), i.e. `4.2e-8 $/token` — read from `docs.typesafe.ai/models` on
2026-10-27. ⚠️ An earlier version of this entry used `4.19e-7 $/token` (10× the vendor's rate)
derived from "the vendor's documented example: `$0.000026 / 62 tokens`", which does not exist in the
vendor's documentation; the 100-row usage ledger it was cross-checked against fits `4.20e-7` because
it was billed by the LOOKALIKE host, and it is evidence of that overcharge, not of the vendor's price. ⚠️ **No live call was made**: the box that wrote this
had only a `jv_live_…` key — the LOOKALIKE host's shape, refused by the vendor (`403`, measured
2026-10-27) — so the token counts are a `cl100k` proxy (read them ±15%). The seam posts to the
VENDOR endpoint (`https://api.typesafe.ai/v1/systemone`; `tortoise#7864` / `#10095`), whose `usage`
reports TOKENS rather than money, so the receipt an armed run produces is a token count in
`stats["seam"]["input_tokens"]` — `cost_usd` is added only when a response carries one, and it is
the VET gate's `stats` and the classify `stats` that both nest the seam's own record under that key. ⚠️ It lands in
`result["vet"][*]["stats"]`, **not** in `stats["llm"]`, so the A/B cost gate does not add it to
the LLM tail's spend — the two providers are counted separately on purpose, and an armed run's true
tail cost is their sum.

**Two things review found, both fixed here rather than documented.** (1) A degraded VET used to emit
a `NOOP` **verdict** per candidate; since `vet_gate` applies arbiter verdicts over the mechanical
pass, that **rescinded the `#4899` identifier-only `DISCARD`** on a key outage — an outage silently
keeping what a working predicate had dropped, and erasing the rule id that explains it. A degraded
item now emits **no verdict** and the failure is recorded by name in `stats["seam"]["degraded"]` /
`["schema_rejects"]`; the composition is pinned by an assertion. (2) The VET half accumulated
`cost_usd` and threw it away (`vet_arbiter` discarded its stats) — the receipt the flag exists for
was unreadable for the half that spends more. `vet_candidates` now folds the arbiter's own record
into its `stats["seam"]` (additive; its counters stay its own), so the spend, the bands and the
degraded items reach `result["vet"][*]["stats"]["seam"]`.

**Tests:** each is mutation-checked (`tests/test_s2_decision_seam_5087.py`), and no count is stated —
the number moves every review cycle and a stale count has itself been a finding: a valid typed
answer decides through the REAL `vet_gate` and the REAL `KindClassifier`; an out-of-schema answer
(wrong primitive / `9.0` on a 0–3 scale / a choice outside the offered set) produces no verdict and
names its question; the seam's own record (`prompt`/`model`/`bands`/`cost_usd`) reaches the gate's
stats; an injected decider's unrepresentable spend cannot abort the fail-open classify pass; and
further tests supply the inputs the guards documented in this module exist to refuse — `_noul`'s
bound on both sides (and on a JSON `true`), the atomicity veto on a fused candidate, `Bands`'
threshold ordering, a negative reported spend, and an item with nothing offered — plus the arming
flag reaching the two PRODUCTION call sites (not merely a callable at the helper), the batch clamp /
response-body cap / generator-`offered` guards, and the `NOOP` precedence against a mechanical
`DISCARD` (with a standalone `KEEP` still keeping its own attribution). Every mutant reddens
at least one of them and restores byte-identically — including
`_noul`'s coercion of a missing probability to `0.0` (a JSON `true` then reads as a probability of
`1.0`, because the coercion bypasses `_finite`'s `bool` rejection, reddening
`test_the_two_numeric_bounds_and_the_fused_veto_redden_when_removed` — ⚠️ **not** `1.5`, which the
intact `[0, 1]` band still refuses, and not the out-of-schema test either, because `_answer`'s
echoed-`type` gate refuses that fixture before the coercion can read a value), the rejected
`NOOP`-on-degradation shape, and `vet_arbiter` dropping its stats again.
