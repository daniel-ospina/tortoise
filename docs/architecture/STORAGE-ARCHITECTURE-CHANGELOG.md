---
title: "Storage Architecture — Changelog"
type: engineering
domain: platform
doc_status: live
created: 2026-10-15
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: STORAGE-ARCHITECTURE.md, issue map, storage design revisions
---

# Storage Architecture — Changelog

> **What this is.** The record of *how the storage design got where it is* — the
> issue map that indexed the document's sections to the work they drove, the
> review outcomes, and the corrections applied.
>
> **What `STORAGE-ARCHITECTURE.md` is.** The design and the measurements behind
> it. It states the physical layout and the evidence for each ruling; it does
> not index its own sections to tracker entries, and it does not narrate its own
> revisions.
>
> **Moved out of the design document on 2026-10-15** (issue #8542), verbatim.
> Nothing is deleted. The design document keeps the measurement evidence and the
> rulings the map pointed at; only the index moved here.

---

## The issue map (moved from `STORAGE-ARCHITECTURE.md` §13)

**Purpose:** every section of the design document exists to inform a filed
issue. This table was the index, so a reader holding an issue could find the
reasoning, and a reader holding a section could find the work it drove. Issue
states are as they stood on the move date. The table itself is moved verbatim.

| section | issue(s) | what the issue takes from here |
|---|---|---|
| §1 the problem | `#4333` | the measured volume, and the true bytes per quota node. ⚠️ **Corrected 2026-09-23:** the earlier *"5.6 KB vs 1 KB, a 5.6× understatement"* divided **total instance RAM (140 MB)** by the **capped node count (25,000)** — but the numerator includes ~19,944 **quota-FREE** episodic turns, plus Events, Sessions, Documents, scaffolding and the full HNSW index. **`#4333` — the authority this row cites — estimates 2.5–4×.** The honest figure is **~3 KB per node (140 MB ÷ ~45k total nodes) ≈ ~3×**; 5.6 KB must be labelled *"total resident bytes per CAPPED node, including uncapped nodes"* |
| §2 mechanism | `#4333` | why the RAM/disk difference *is* the economics |
| §3 architecture | `#4333` | the physical split, **in the ontology's own vocabulary** — not a new layer |
| §3 what is already in code | **`#4240`** | **the hosted path does not journal unless `TORTOISE_EVENT_LOG_BASE_DIR` is set (`#4240` wired the per-graph journal) → a wiring gap, not a design gap** |
| §3 why this shape | `#3895` | the derived layer becomes **REBUILDABLE** — ⚠️ **NOT "disposable"**: §3 forbids that reading in bold, and `#3895` is a *restore*. "Disposable" is the label that would license dropping the only copy. It regenerates **from the truth layer**, and only because the journal carries the payload |
| §4 tenancy | `#3885` | **one project, tenant-scoped rows** — not one project per team |
| §5 cost | `#4333` · **`#7954`** | published rates; ~10× headroom at 1,000 users. ⚠️ **CORRECTED 2026-10-10: the earlier `#4614` citation was WRONG** — `#4614` is a *quota-cliff capture bug* (`402` at 9,998/10,000), not a rates issue. **This subsection's prices are now vendor-page-verified, and the two-store arithmetic is `#7954`'s (§17.4)** |
| §6 the split does NOT fix volume | **`#4899`** · `#1026` | **storage ~580× on the STORAGE line × selection ~10× (a target, not a measurement) — neither alone reaches 100×.** This is the section that stops a storage migration being sold as the fix |
| §7 state values | **`#2453`** (LANDED) · **`#4899`** (enforcement) · `#2820` | the carve-out, plus the DECIDED placement (owner, 2026-10-06): a state/numeric value is a **structural field on an entity**, with the embedding clause conditional on the §7 query-pattern measurement. ⚠️ **The former "`#1509` (E2, §9)" citation was WRONG** — `#1509` has no §9 and no E2; the placement recommendation there is a **comment** on `#1509` — now **SUPERSEDED by the PLACEMENT ACCEPTED decision (§7)** — and **E2 is `#1534`'s slot** (CLOSED). See §7's own corrected note |
| §7 caveat | **`#4889`** | ⚠️ **CORRECTED (owner, 2026-10-06).** The live graph is a **DOGFOOD instance** — an edge count there was never the right test, and the earlier *"0 edges"* framing is withdrawn. **The code and its tests exist** (`tortoise/subject_binding.py` and the `aboutSubject` write paths exist; `#4889` is CLOSED), and the **end-to-end check needs a capstone issue (not yet filed)**, not a caveat asserting a production count. |
| §8 granularity | `#1509` (E3) · `#4333` | **volume reduction may not come from fusing claims** — it bounds the whole document |
| §9.1 narrative placement | `#2281` | the narrative **is** the ingest-time-distilled layer that issue asks for |
| §9.2 PR / Object / Event | `#1844` | object-only sources; the anchor is a document, **never an event** |
| §10.1 relocation | `#4240` | the hot table stays the working set — which is the invariant this document rests on |
| §10.2 allowlist | — | a reusable correctness rule (no issue needed) |
| §12.1b unindexed embeddings | ⭐ **`#4997`** · **`#4999`** | **only `:Point` has a vector index; `Object`/`Event` full-scan (`leg_trace.reason` remains `ok`; since PR `#5159` the entry also carries the `mechanism` — `#4999` CLOSED `COMPLETED`); 19 MB stored unindexed — and the unindexed-embeddings defect itself is still LIVE, homed on the V1/§12.1c decision** |
| §12.2c vector vs traversal | ⭐ **`#4997`** · `#2730` | **the entity vector is an ENTRY/RESOLUTION key, never the reasoning mechanism — and our Objects have no description to embed** |
| §9.4 source summary vector | — | **needs a ruling (V2); `Source` already carries `summary`, `topics`, `url`, `contentHash`** |
| §9.6 source versioning | — | **the version must be recorded on the extraction link as `sourceVersion`**; a version costs **three timestamps + a hash** (D30 keeps content out of the graph) — **bound it: windows and hashes only, never content copies** |
| §12.1a index shape | `#5090` · `#5331` | **the decision is the size-earned conditional index (C)** — not per-tenant partitioning, not a shared filtered index. The partition comparison is a measured *alternative*: `PARTITION BY` flat to T=500 (96.6 ms at T=2,100, unexplained), the partial-index form degrading to 83 ms at T=500 |
| §17.2 the completeness marker | **`#7952`** ✅ **built 2026-10-12** | a read for a node outside the known-hot set returns explicit **`not-hot`** — **never empty** — and coherence is reconciled as a **number**, not a boolean. This is the input §17.3's router reads. Landed: `tortoise/residency.py` (the marker, its own `residency` field, off by default) + `tortoise/reconciliation.py` (divergence as integer counts, **refusing** when a side is unreadable) + the hosted `/v1/search` field. ⚠️ The declaration's authoritative home is §2.6's control-plane metadata (`#7971`); until then the reader takes the graph's `:Meta` marker and defaults to **complete**, under which the marker cannot fire |
| §17.3 the residency router | **`#7953`** ✅ **built 2026-10-13** | a residency miss is not an insufficiency: **fetch from the truth store**, never escalate a tier, never surface an absence. Decidable only against §17.2's marker (**hard edge**). Landed: `tortoise/residency_router.py` (a pure decision — `serve` / `fetch-record` / `escalate-tier`, with residency outranking sufficiency, a `cache-unreachable` branch, and `route_for_coordinate` for a partial declaration's unwritten key) + the read path's per-key coordinate (`tortoise/sdk.py::tortoise_fts_query(_residency_key=…)`) |
| §17.4 the two-store cost model | **`#7954`** | price **both** stores (Postgres compute + disk **and** FalkorDB RAM) plus the blended total §5–§6 omit |
| §17 the five gaps | **`#7951`** (17.1) · **`#7952`** (17.2) · **`#7953`** (17.3) · **`#7954`** (17.4) · **`#7955`** (17.5) · **`#7967`** (the surface map) | what the record/cache split still needs: a write-path chokepoint, cache coherence + a completeness marker, partial-cache semantics, a two-store cost model, and a field-level placement table. **None is a new decision** — each follows from §2.0. ⚠️ **Corrected 2026-10-10 — this row previously read `#5089` (17.1) · `#5090` (17.5), which named the wrong owners:** **the routing half of §17.1 is `#7951`'s** (`#5089` owns the journal half — §17.1 says so itself), and **§17.5's placement ENUMERATION is `#7955`'s** (`#5090` is a *consumer* of the vector layer, not the enumeration). The five gaps are children of **`#7879`**; **`#7950`** is the capstone that verifies them; **`#7968`**/**`#7969`**/**`#7971`** carry the schema, the record projection and the cache projection, and **`#7970`** the read path. The integration-surface map every one of them cites is **`#7967`** → `docs/epics/2026-10-10-7879-postgres-record/01-test-design.md` |

---

## Removed wording, by section (moved from the design document, 2026-10-15, #8542)

Every line this pass removed from the design document, keyed to the section it sat
in. Emphasis markers are dropped from the recorded wording so the record renders, and a
line longer than ~190 characters is shortened with an ellipsis — the moved section
above is verbatim; this list is an index to it, not a second copy.
The **substance** each carried — a measurement, a rule, an open question, a
decision — is stated in the design document's rewritten sentence; what is not
carried anywhere is the delivery state and the account of the passage's own
revision, which is what this file is for.

### (front matter)

- `STORAGE-ARCHITECTURE.md:14` — Status: design — AMENDED 2026-10-08 to the owner's hybrid ruling (§2.0): Postgres is the system of record for every layer; FalkorDB is a derived hot cache. Read §2.0 and §3's table first.…
- `STORAGE-ARCHITECTURE.md:20` — Related: `#7869` (this amendment — the doc's canonicalisation) · `#5086` (substrate-rebuild tracking) · `#5089` (durability — derived = replay(journal)) · `#5090` (the vector layer) · `#3…

### ⛔ THE STORAGE UNIT IS BYTES — decision, owner, 2026-09-26 (`#4495`)

- `STORAGE-ARCHITECTURE.md:79` — block reads 143 MB / 46 MB, and §13's issue-map row concludes "~3 KB per node (140 MB ÷ ~45k total nodes)" —
- `STORAGE-ARCHITECTURE.md:82` — §13's row carries the earlier denominator correction (the retracted 5.6 KB claim); this block, not §1, is the

### 1. The problem

- `STORAGE-ARCHITECTURE.md:119` — > ⚠️ FIGURE CORRECTED 2026-09-23. An earlier draft said "~6 GB ≈ ~$460/month". The $460 implied a 6.3 GB horizon that was never stated and mixed a growth rate with a total. The correct st…

### 2.0 ⭐ DECISION (owner, 2026-10-08): **hybrid now — Postgres is the system of record; FalkorDB is a derived hot cache**

- `STORAGE-ARCHITECTURE.md:141` — ⚠️ SCOPE — this decides a PLACEMENT AND AN AUTHORITY, and it settles what §2 previously deferred. The earlier note here read "this decides a PROVIDER, not a PLACEMENT (owner clarification…

### 2.1 What FalkorDB actually charges for — measured 2026-09-24

- `STORAGE-ARCHITECTURE.md:162` — ⚠️ CORRECTED TWICE, AND THE TWO CORRECTIONS POINT OPPOSITE WAYS — read this before the table. (i) An earlier draft described "disk-resident, RAM as a cache" as the mechanism; that was cor…

### The class that dominates our bill — measured

- `STORAGE-ARCHITECTURE.md:196` — In our graph the vector class — measured (§12.1b) — is ≈50 MB of stored vector properties plus a 45 MB index total (of which only part is the `Point` HNSW index) against a 141 MB graph, i…

### ✅ THE `#2881` COLLISION IS CLOSED — by SCOPING, not by overriding (see §14.1 **O1**)

- `STORAGE-ARCHITECTURE.md:272` — #### ✅ THE `#2881` COLLISION IS CLOSED — by SCOPING, not by overriding (see §14.1 O1)
- `STORAGE-ARCHITECTURE.md:274` — `docs/durability-posture.md` (`#2881`) is a recorded decision: the journal "is never the durability mechanism … authority stays in a store-managed artifact." An earlier draft of this sect…

### 4. Tenancy — DECIDED (owner, 2026-09-23): one database, tenant-scoped rows

- `STORAGE-ARCHITECTURE.md:379` — ⭐ AND ITS CONSEQUENCE FOR THE VECTOR INDEX — corrected 2026-09-23. "Rows scoped by tenant" and "the index is partitioned by tenant" are different physical designs, and only the second bou…
- `STORAGE-ARCHITECTURE.md:385` — ⇒ The TENANCY decision above does not change. What this bullet settles is the INDEX SHAPE, and it is THE SIZE-EARNED CONDITIONAL INDEX (C, §12.1a) — bounded relation count, earned by volu…

### 5.1 ⚠️ Corrections to earlier drafts of this section — and the two lines it omitted

- `STORAGE-ARCHITECTURE.md:401` — ### 5.1 ⚠️ Corrections to earlier drafts of this section — and the two lines it omitted
- `STORAGE-ARCHITECTURE.md:403` — ⚠️ These figures were quoted elsewhere in an earlier draft at amounts up to ~14× higher, and that draft was never reconciled. The numbers in the table above are the ones to use; the ~$460…

### 6. The split does NOT fix the volume problem

- `STORAGE-ARCHITECTURE.md:422` — ⚠️ CORRECTED 2026-09-23 — this section's multiplier was derived from a wrong vector share, and the correction moves it in the opposite direction from the other fixes. An earlier draft sai…

### 7. State values — RETAIN is decided and landed; PLACEMENT is DECIDED (2026-10-06, below)

- `STORAGE-ARCHITECTURE.md:455` — - ✅ RETAINING verbatim operational values is DECIDED and LANDED — commit `4a690d0be` / PR `#2456` (2026-09-07): `STATE_VALUE_CARVE_OUT` (`extractor_v2.py:145-171`) plus `VALUE_FIDELITY_RU…
- `STORAGE-ARCHITECTURE.md:456` — - ⛔ What is missing is enforcement BY DEFAULT — not the mechanism. `valueGate` exists in code (`tortoise/value_gate.py`) and ships OFF, subordinate to VET. `#4899` is CLOSED.
- `STORAGE-ARCHITECTURE.md:459` — ✅ PLACEMENT ACCEPTED (owner, 2026-10-06): a state/numeric value is a STRUCTURAL FIELD ON AN ENTITY, not a `Point`. The reasoning is the ontology's own: operators connect only epistemic ta…
- `STORAGE-ARCHITECTURE.md:465` — ⚠️ CITATION CORRECTED 2026-09-23. An earlier draft cited "`#1509` §9" for the decision that state values are Points, and "`#1509` E2" for verbatim preservation. Neither exists. `#1509` ha…
- `STORAGE-ARCHITECTURE.md:478` — | Operational / decision values | "p99 latency 480 ms at 3k rps" | ⚠️ RETAINED — the fix LANDED (commit `4a690d0be`, 2026-09-07; PR `#2456`) | retained |
- `STORAGE-ARCHITECTURE.md:480` — Operational values are DURABLE. `#2453` landed and extended `STATE_VALUE_CARVE_OUT` with an operational-value paragraph — "a concrete value that is the SUBJECT of a decision, observation,…
- `STORAGE-ARCHITECTURE.md:484` — > ⚠️ Caveat from `#4889` — CORRECTED 2026-10-06 (owner). An earlier version of this caveat asserted that the `aboutSubject` half does not exist in production today and cited 0 `aboutSubje…

### 8. Constraint carried from the extractor — granularity is NOT negotiable

- `STORAGE-ARCHITECTURE.md:492` — This bounds how far volume reduction may go. The Epistemic layer's operators (`IMPL`, `NAND`) act on individual claims — and the mitigation mechanism is the `mitigated_by` edge on an oper…

### 9.1 The narrative lives in **Postgres** — NOT in the graph (D1, destination corrected 2026-10-09 — the owner's own answer, one day after the §2.0 ruling)

- `STORAGE-ARCHITECTURE.md:512` — ### 9.1 The narrative lives in Postgres — NOT in the graph (D1, destination corrected 2026-10-09 — the owner's own answer, one day after the §2.0 ruling)
- `STORAGE-ARCHITECTURE.md:518` — ⚠️ The same rule applies to raw turns (owner, 2026-09-23): "the narrative is not something we're suggesting to store in the graph (same as raw) but store in supabase." Both of the two non…

### 9.3 ⭐ A document is a **SOURCE**, not a graph node — and this is what makes the entity layer an abstraction

- `STORAGE-ARCHITECTURE.md:557` — ⚠️ What this corrects in an earlier draft. The earlier version placed `:Document` in the truth layer and then discovered it also appeared in the derived layer:
- `STORAGE-ARCHITECTURE.md:563` — ⇒ `:Document` folds into `:Source` — LANDED. Its content leaves the graph — to raw text in Postgres (true binaries to the object bucket; §3) — referenced per D30, and the entities extract…

### ⭐ Scope of the anchor — a **Point-level** guarantee, extended to derivation links (decided 2026-09-25)

- `STORAGE-ARCHITECTURE.md:706` — - derivation — an `Event` (or `Document`) built from a source's content: the connector path materializes the link at the projection choke point (`projection/entities.py::_materialize_conn…

### ⭐ Why it is affordably cheap HERE and expensive elsewhere

- `STORAGE-ARCHITECTURE.md:751` — Bound it there: windows and hashes only, never content copies. If a version ever starts carrying bytes, this stops being cheap and §13's cost argument changes. The content lives in Postgr…

### ⭐ Where this stands (2026-09-25) — a status pointer, not a new rule

- `STORAGE-ARCHITECTURE.md:765` — - What a stale fact does — Policy B, REFINED by the owner on 2026-09-26 (`#5038` comment `5845802608`): "We should not return an out-of-date fact when we have a newer one" — so when a new…
- `STORAGE-ARCHITECTURE.md:767` — - The re-inference engine — `#5422` (its ownership gap) is CLOSED `COMPLETED` (2026-09-28); acceptance A2 of `#5038` is what remains outstanding for it. `#5024` landed (`SourceVersioned`,…

### 11.7 The exception, unchanged

- `STORAGE-ARCHITECTURE.md:851` — ⚠️ The claim that used to sit here — "Vectors are the one layer that does not follow the disk rule (§2) — `pgvector`'s HNSW index is RAM-resident" — was STALE and is removed (corrected 20…

### 12.1 The vector index — the class that dominates our bill

- `STORAGE-ARCHITECTURE.md:863` — ⚠️ THIS SECTION WAS REWRITTEN TWICE. Read the version below, not either earlier one.
- `STORAGE-ARCHITECTURE.md:865` — - Draft 1 claimed the index "is RAM-mandatory and does not follow the disk rule" and inferred an unprovisionable 7 TB of RAM.
- `STORAGE-ARCHITECTURE.md:866` — - Draft 2 corrected that to "disk-backed and page-cached" — correct for pgvector, but written for the then-deferred Postgres target.
- `STORAGE-ARCHITECTURE.md:867` — - ✅ Draft 3 (this one) is about the engine we actually run. ⚠️ And the 2026-10-08 ruling (§2.0) makes that Postgres target the RECORD — so Draft 2's mechanism is now live for the record s…
- `STORAGE-ARCHITECTURE.md:884` — ⚠️ Correcting an earlier draft that said "95.6 MB of 140 MB (~68%)". That figure added the whole index total — but the index total also contains every fulltext index we have (`Point.conte…

### ⭐ A vector is POINTER-SIZED — 1.5 KB, whatever the text says

- `STORAGE-ARCHITECTURE.md:899` — A 3 KB summary → a 1.50 KB vector. A 50 KB transcript → the same 1.50 KB vector. A 384-dim float32 embedding is a fixed-width row: it is a pointer, not a payload. So "vectorise the summar…

### What is NOT a problem: latency (measured 2026-09-24 — an earlier draft got this badly wrong)

- `STORAGE-ARCHITECTURE.md:905` — #### What is NOT a problem: latency (measured 2026-09-24 — an earlier draft got this badly wrong)
- `STORAGE-ARCHITECTURE.md:907` — An earlier draft claimed a cold index produced "p90 96ms vs 24ms" and that this was "20–50× over every published latency budget, so it cannot be user-facing." BOTH HALVES WERE WRONG.
- `STORAGE-ARCHITECTURE.md:909` — Wrong half 1 — the number was not ours. 96ms/24ms came from OpenSearch's designed disk mode — a different engine. And the "10,500ms cold" figure came from a billion-vector AWS deployment.…
- `STORAGE-ARCHITECTURE.md:911` — Wrong half 2 — the budget is not a requirement. The "50–500ms" figures are vendor self-report:
- `STORAGE-ARCHITECTURE.md:925` — ⇒ CONCLUSION: latency is not a constraint at our size and is not a reason to change anything. Recorded because the earlier draft would have justified an architecture on it. (Retrieval gen…

### 12.1a Why "just partition per tenant" is NOT one mechanism — and the two halves have OPPOSITE answers

- `STORAGE-ARCHITECTURE.md:933` — ⚠️ CORRECTED 2026-10-08 by this lane's own first-party measurement. An earlier version of this section withdrew per-tenant partitioning wholesale, on a study whose mechanism does not reac…
- `STORAGE-ARCHITECTURE.md:940` — §12.1a's cited mechanism — "Postgres takes a shared lock on every index of a table during planning" — can only reach (A). It is a count of indexes per relation, so it cannot reach (B), wh…

### How big our index is, and why "fewer vectors" is the whole game

- `STORAGE-ARCHITECTURE.md:970` — ⚠️ The single most valuable number to stop guessing: (caveat kept from the earlier draft) `2.5M` has ONE meaning in this document: the product total. An earlier draft used it a second tim…

### 12.1b ⚠️ CORRECTED ACCOUNTING — where the vectors actually sit (2026-09-24)

- `STORAGE-ARCHITECTURE.md:974` — ### 12.1b ⚠️ CORRECTED ACCOUNTING — where the vectors actually sit (2026-09-24)
- `STORAGE-ARCHITECTURE.md:976` — An earlier draft attributed the whole vector class to one index. Reading the code changed the picture; then the live graph changed it again. One finding is a filed defect.
- `STORAGE-ARCHITECTURE.md:1002` — `:Object` costs 3.4× `:Point` for the same query. Before PR `#5159` the trace could not distinguish an index hit from a table scan — both reported `ok` on the brute-force path, so degrada…
- `STORAGE-ARCHITECTURE.md:1006` — ⇒ Filed as `#4997` — CLOSED `COMPLETED`. ⚠️ REFRAMED 2026-09-24 (owner): the "stop storing OR index them" framing was a FALSE CHOICE. The third option is better than either: keep the vect…

### 12.2 ⇒ The lever is "what gets an embedding"

- `STORAGE-ARCHITECTURE.md:1069` — ⚠️ Correction from the owner's counterexample (2026-09-23). An earlier draft of this section proposed leaning on class ("don't embed Events / Objects / mechanical statements"). The owner …

### 12.2a Where the rule lives — ALREADY DECIDED (adoption gate, contradiction test run 2026-09-23)

- `STORAGE-ARCHITECTURE.md:1110` — ⚠️ Correction to an earlier note in this section. An earlier draft recommended "computed, because a class-level rule fails", citing the owner's meeting counterexample as the reason to mov…

### 12.2b The levers, ranked — and one earlier recommendation is WITHDRAWN

- `STORAGE-ARCHITECTURE.md:1163` — ### 12.2b The levers, ranked — and one earlier recommendation is WITHDRAWN
- `STORAGE-ARCHITECTURE.md:1176` — ⚠️ Lever 4 is PARTIALLY RESTORED, and the withdrawal stays visible on purpose. It was published in an earlier draft as "none — pure win", then withdrawn wholesale when a study showed a pe…

### 12.2c ⭐ What earns a VECTOR and what earns a TRAVERSAL — the research answer (2026-09-24)

- `STORAGE-ARCHITECTURE.md:1215` — ⭐ What we already have is the right shape: `Object.name` already has a FULLTEXT index, as do `Event.subject`/`Event.name` (measured, §12.1b). Lexical entity lookup is the resolution signa…
- `STORAGE-ARCHITECTURE.md:1217` — ⚠️ CORRECTION ACCEPTED (owner, 2026-09-24) — weak names are a SEQUENCING fact, not an argument against the vector. The definite-description problem is already an in-flight extractor works…

### 12.8 What this means for our design — the four things to do

- `STORAGE-ARCHITECTURE.md:1306` — ⚠️ And the discipline that keeps this honest: our own numbers for the cost TIERS still do not exist. Every tier figure above is published third-party and must be re-measured on our data b…

### 13. Issue map — what this document informs

- `STORAGE-ARCHITECTURE.md:1308` — ## 13. Issue map — what this document informs
- `STORAGE-ARCHITECTURE.md:1310` — Purpose: every section above exists to inform a filed issue. This table is the index, so a reader holding an issue can find the reasoning, and a reader holding a section can find the work…
- `STORAGE-ARCHITECTURE.md:1312` — | section | issue(s) | what the issue takes from here |
- `STORAGE-ARCHITECTURE.md:1313` — |---|---|---|
- `STORAGE-ARCHITECTURE.md:1314` — | §1 the problem | `#4333` | the measured volume, and the true bytes per quota node. ⚠️ Corrected 2026-09-23: the earlier "5.6 KB vs 1 KB, a 5.6× understatement" divided total instance RA…
- `STORAGE-ARCHITECTURE.md:1315` — | §2 mechanism | `#4333` | why the RAM/disk difference is the economics |
- `STORAGE-ARCHITECTURE.md:1316` — | §3 architecture | `#4333` | the physical split, in the ontology's own vocabulary — not a new layer |
- `STORAGE-ARCHITECTURE.md:1317` — | §3 what is already in code | `#4240` | the hosted path does not journal unless `TORTOISE_EVENT_LOG_BASE_DIR` is set (`#4240` wired the per-graph journal) → a wiring gap, not a design gap |
- `STORAGE-ARCHITECTURE.md:1318` — | §3 why this shape | `#3895` | the derived layer becomes REBUILDABLE — ⚠️ NOT "disposable": §3 forbids that reading in bold, and `#3895` is a restore. "Disposable" is the label that woul…
- `STORAGE-ARCHITECTURE.md:1319` — | §4 tenancy | `#3885` | one project, tenant-scoped rows — not one project per team |
- `STORAGE-ARCHITECTURE.md:1320` — | §5 cost | `#4333` · `#7954` | published rates; ~10× headroom at 1,000 users. ⚠️ CORRECTED 2026-10-10: the earlier `#4614` citation was WRONG — `#4614` is a quota-cliff capture bug (`402…
- `STORAGE-ARCHITECTURE.md:1321` — | §6 the split does NOT fix volume | `#4899` · `#1026` | storage ~580× on the STORAGE line × selection ~10× (a target, not a measurement) — neither alone reaches 100×. This is the section…
- `STORAGE-ARCHITECTURE.md:1322` — | §7 state values | `#2453` (LANDED) · `#4899` (enforcement) · `#2820` | the carve-out, plus the DECIDED placement (owner, 2026-10-06): a state/numeric value is a structural field on an e…
- `STORAGE-ARCHITECTURE.md:1323` — | §7 caveat | `#4889` | ⚠️ CORRECTED (owner, 2026-10-06). The live graph is a DOGFOOD instance — an edge count there was never the right test, and the earlier "0 edges" framing is withdra…
- `STORAGE-ARCHITECTURE.md:1324` — | §8 granularity | `#1509` (E3) · `#4333` | volume reduction may not come from fusing claims — it bounds the whole document |
- `STORAGE-ARCHITECTURE.md:1325` — | §9.1 narrative placement | `#2281` | the narrative is the ingest-time-distilled layer that issue asks for |
- `STORAGE-ARCHITECTURE.md:1326` — | §9.2 PR / Object / Event | `#1844` | object-only sources; the anchor is a document, never an event |
- `STORAGE-ARCHITECTURE.md:1327` — | §10.1 relocation | `#4240` | the hot table stays the working set — which is the invariant this document rests on |
- `STORAGE-ARCHITECTURE.md:1328` — | §10.2 allowlist | — | a reusable correctness rule (no issue needed) |
- `STORAGE-ARCHITECTURE.md:1329` — | §12.1b unindexed embeddings | ⭐ `#4997` · `#4999` | only `:Point` has a vector index; `Object`/`Event` full-scan (`leg_trace.reason` remains `ok`; since PR `#5159` the entry also carrie…
- `STORAGE-ARCHITECTURE.md:1330` — | §12.2c vector vs traversal | ⭐ `#4997` · `#2730` | the entity vector is an ENTRY/RESOLUTION key, never the reasoning mechanism — and our Objects have no description to embed |
- `STORAGE-ARCHITECTURE.md:1331` — | §9.4 source summary vector | — | needs a ruling (V2); `Source` already carries `summary`, `topics`, `url`, `contentHash` |
- `STORAGE-ARCHITECTURE.md:1332` — | §9.6 source versioning | — | the version must be recorded on the extraction link as `sourceVersion`; a version costs three timestamps + a hash (D30 keeps content out of the graph) — bou…
- `STORAGE-ARCHITECTURE.md:1333` — | §12.1a index shape | `#5090` · `#5331` | the decision is the size-earned conditional index (C) — not per-tenant partitioning, not a shared filtered index. The partition comparison is a …
- `STORAGE-ARCHITECTURE.md:1334` — | §17.2 the completeness marker | `#7952` ✅ built 2026-10-12 | a read for a node outside the known-hot set returns explicit `not-hot` — never empty — and coherence is reconciled as a numb…
- `STORAGE-ARCHITECTURE.md:1335` — | §17.3 the residency router | `#7953` ✅ built 2026-10-13 | a residency miss is not an insufficiency: fetch from the truth store, never escalate a tier, never surface an absence. Decidabl…
- `STORAGE-ARCHITECTURE.md:1336` — | §17.4 the two-store cost model | `#7954` | price both stores (Postgres compute + disk and FalkorDB RAM) plus the blended total §5–§6 omit |
- `STORAGE-ARCHITECTURE.md:1337` — | §17 the five gaps | `#7951` (17.1) · `#7952` (17.2) · `#7953` (17.3) · `#7954` (17.4) · `#7955` (17.5) · `#7967` (the surface map) | what the record/cache split still needs: a write-pat…
- `tests/test_7948_about_object_read_sites.py` · `tests/_about_call_time_census.py` — **ONE derivation, ONE register for the empty-pattern class (2026-10-22, `#9493`).** The `#9400` remerge left the class derived twice: the guard's own `_EXCLUDED_UNTYPED` (bracket-only, module-keyed, nine entries) beside `tests/_about_call_time_census.py`'s `EMPTY_PATTERN` (both spellings, symbol-keyed, nineteen entries), and the two disagreed on 24 of their 26 key spellings while §13 called the support register *"the guard's `EMPTY_PATTERN` register (nineteen members today)"* — wrong twice — and this changelog's fifth-pass row named the other. Measured before the fold: every site the guard's derivation found is found by the support derivation (24 symbol keys over the same 36 sites), and the support derivation sees what the guard's could not — the bracket-less `(n)--()` member that was live and undeclared while only the bracketed form derived. The guard's duplicate derivation, its register and its two arms are deleted; the comprehension rule (a pattern comprehension enumerates the edges it walks, so its bracket is a read whatever clause introduces it) moved into `_is_empty_pattern_read`, and the `_in_pattern_comprehension` predicate moved with it so the literal classifier and the empty-pattern walk share ONE definition. The whole-package derivation is unchanged by the move (24 entries / 36 sites, measured before and after, and again on this tree after `#9503`'s `AnnAssign` attribution landed), and a walk split across string parts still enters the census through the consumer the resolver follows. **Ten `EMPTY_PATTERN` entries went with the fold as dead:** the backup family, the graph export, the delete preview, the replay, the v2.5 backfill, the operator promotion and the hub-degree count are §13 ROWS since the third and fifth passes, and an entry beside a row makes opposite claims at once — `test_no_empty_pattern_entry_is_also_a_row` reds any regrowth, `test_no_empty_pattern_entry_outlives_its_site` still holds the stale direction, and `test_a_comprehension_only_walk_derives` pins the moved rule on a synthetic source. §13 now names the one register and where it lives.
- `tests/_about_call_time_census.py` · `tests/test_7948_about_object_read_sites.py` — **the folded register's REASONS are checked against their code, and the file gate admits a part-assembled walk (2026-10-22, `#9493`).** Three repairs to the fold's VERIFICATION; the derivation itself is measured unchanged (24 symbols / 36 sites, 20 named, 11 call-time, before and after). (1) **An entry is now `(file, symbol, why, facts)`** and `test_every_empty_pattern_entry_states_a_true_fact` asserts each fact inside the symbol's own span — plus the body of any module-level constant that span NAMES, because three entries live in a query constant their body executes (`_DEGREE`, `_OUT_TYPES`/`_IN_TYPES`). Before this arm the prose was the register's only record of *why* and nothing read it: retyping `session_source_migration._OUT_TYPES` from `(old:Source {url:$u})` to `(old)`, or dropping `:Subject` from `sdk.provenance`, made the walk start observing an `aboutObject` edge while all 35 arms stayed green. Both mutations now red naming that symbol. (2) **Two of the nine reasons were FALSE about their own code** and are re-derived with the rest: `hosted_api._load_commit_graph_state` does not *"rewire every edge type it finds"* — its one untyped bracket is followed by `WHERE type(r) IN ['IMPL','NAND']` (type-restricted) and its docstring states the state is computed in memory, nothing written; `projection/entities._materialize_connector_source` does not *"rewire the node's edges by type"* — it is a degree COUNT, `size([(old)-[x]-(y) | x])`, anchored on a `:Source`. §13's enumeration of the legal grounds gains the third one an entry actually rests on — a structural degree/existence test that returns no edge (`domain_validators.validate_chain_integrity`'s `AND NOT (n)--()`, whose source IS a `Point`) — which a two-ground sentence did not admit. (3) **`_may_traverse_every_type` admitted only the one-literal spelling**: measured, `"MATCH (n)-" + "[r]->(m)"` and `"MATCH (n)-[r]" + "->(m)"` both returned `False` while `"MATCH (n)-[x]->(m)"` returned `True`, so the module was dropped before the resolver — which folds `ast.BinOp/Add` and DOES derive the site — ran; latent on today's corpus, which is why `test_the_file_filter_un_sees_nothing` cannot see it. `_SPLIT_TRAVERSAL` admits both, anchored on a bracket that opens with a NAME and carries no comma or quote followed by the `-` every Cypher traversal puts after its `]` — the unanchored form admitted 61 extra modules for a byte-identical derivation, this one admits 17. Four properties that lost their fixtures with the retired guard's case table are pinned while here: `_SITE_ALIASES` is grounded on BOTH halves (the row key in §13, the target in `_empty_pattern_sites()`), a part-assembled walk is DERIVED (not merely admitted), `[r*1..2]` and `[r {idx:0}]` name no type while `MERGE`/`CREATE` are writes for this class, and one symbol may own two sites (`mining.verify_no_auto_wire`,
`session_source_migration._rel_types`).
- `tests/test_7948_about_object_read_sites.py` — **the count arm reads EVERY statement, and every literal bracket is classified (2026-10-21, `#7948`).** Two fail-opens, each now pinned by a fixture that asserts the RULE rather than the document's current wording. (1) §13 states the customer-callable figure **twice** — `TWELVE of them are CUSTOMER-CALLABLE` at the head of the section and `TWELVE customer-callable read sites` in the blast-radius sentence — and the guard `search`ed once, so the second copy was read by nothing: rewriting it to `FIFTEEN customer-callable read sites` left all thirteen arms green, the identical "hand copy with no reader" shape the DOOR figure in that same sentence was fixed for. Both figures are now collected with `finditer` and compared as SETS (`{27}` / `{12}`), the shape the sibling §13.8 arm already used, and a fixture states the figure twice in both shapes so a first-match reader reds. (2) `_reads_literally` classified only the FIRST `aboutObject` bracket in a string literal, so a query shaped write-then-read (`… MERGE (p)-[:aboutObject]->(o) WITH p MATCH (x)-[:aboutObject]->(y) …`) was skipped whole on the `MERGE` and its read entered neither the census nor any row check — while `test_no_read_site_lacks_a_row` promised "every read clause that names the edge". Every bracket is now classified; the fixture pins the four orderings, and under the pre-fix rule the write-then-read case is red.
- `STORAGE-ARCHITECTURE.md:1353/1348/1388` — **the wording §13 no longer carries (2026-10-21, `#7948`).** Three change-history clauses left the design section and are recorded here: row 2's reach cell no longer says *"the door this table was missing"* (the row states what the Point signal is for); the moving-ref paragraph no longer recounts that the head-relative figure *"was written down as 60 and measured 62 one merge later"* (it states the rule: a figure pinned to a branch tip has no pin, re-measure with the command and quote the sha); and the line-number sentence no longer narrates that *"the lines had all rotted"* (it states the rule: the symbol is authoritative, the line is a convenience). `_CHANGE_NARRATION` in `tests/test_extractor_v4_citation_rot.py` gained the matching arms — `this|that|the <section-word> was missing`, `was written down as`, `had all rotted` — with those three sentences as `must_flag` fixtures, because the guard had reported the design documents clean while all three sat in §13.
- `STORAGE-ARCHITECTURE.md:1344-1382` — **FIFTH PASS 2026-10-21 (`#7948`): 33 sites, SEVENTEEN customer-callable, TWENTY DERIVED doors — and the untyped-walk class stops being a hand pass.** Rows 24–27 were added by applying §13's declared rule (*an unfiltered walk reads the edge without naming it*), and every later reader applied the same rule and got a different count, because nothing recorded where the rule STOPPED. The fifth pass is what made that visible: **six all-edge reads were missing, and the sharpest two were a customer door the ruling's own argument needs** — `hosted_backup._DUMP_EDGE_PAGE_QUERY` (every edge paged out as data into the per-org encrypted backup, behind `POST /v1/backups`) and the three whole-graph counts that verify a dump and gate its restore (`_COUNT_ALL_EDGES_QUERY` behind `POST /v1/backups`, where its only consumer `dump_graph` runs; `restore_graph` and `_restore_copy_settled` behind `POST /v1/backups/restore`), plus `sdk.TortoiseSDK.backfill_v25` (an all-edge count handed back as `report["edge_count"]`, behind SDK `backfill_v25` and MCP `tortoise_backfill_v25`) and the write-side `sdk.TortoiseSDK._promote_incident_operators` (every incident operator edge, target swept over all four epistemic labels — `Object` included). ⚠️ **The backup rows are not a marginal addition:** §13's `#4240` durability bullet is about THIS edge class, and the backup IS the durability mechanism, so *would a `SET`-free derive change what the customer can restore?* was answerable only from a site the table did not carry. **The class is now derived from the tree instead of restated by hand**: `tests/test_7948_about_object_read_sites.py` censuses every relationship bracket carrying no type at all, in a read clause — keyed on `module.py::symbol`, so a new walk beside a row reds — and requires each site to own a row or appear in `_EXCLUDED_UNTYPED` **with the code fact that earns it present in that walk's own clause** (a fact in an earlier clause of the same query counts only when it binds a variable the walk uses), and, from the other side, still present in the symbol's span — `test_every_stated_exclusion_is_still_true` (a downstream `type(r) IN […]` restriction, or endpoints sitting outside every `aboutObject` triple — `Point`/`Event`/`Session` → `Object`, per `session_link.ENTITY_LINKED_TRIPLES`; `STRUCTURAL_REL_LABELS` carries the target half). An exclusion cannot be a claim and cannot silently stop being true: remove the constraint and the site reds as an unowned read. `_NON_LITERAL` carries the six new rows' pins. Counts moved in §13, §13's blast-radius sentence and §13.8 (both documents), and §13.8's enumeration now names all seventeen.
- `STORAGE-ARCHITECTURE.md:1344-1380` — **FOURTH PASS 2026-10-21 (`#7948`): 27 sites, TWELVE customer-callable, and the blast-radius figure is now SIXTEEN DERIVED doors — the declared CLASS, applied to the code, found two more readers that are not customer doors at all.** The first three passes each asked *which customer surface reaches this edge?*; this one asked the question §13 actually declares — *which code reads every edge?* — and the answer is two readers no customer path touches and no grep can find: `subgraph._CYPHER_HUB_DEGREE` (`MATCH (o)-[r]-()`, an unfiltered incident-edge COUNT feeding the `#3011` engine's `hub_degree`, reach **eval harness**) and `definite_description_backfill.CENSUS_QUERY` (an unlabelled inbound-edge count whose own docstring says a referent is an Object if *anything* points at it, `aboutObject` included, reach **ops audit**). Both count what they walk, so a `SET`-free derive silently changes the number they report — the identical argument that put rows 3, 22, 24 and 25 in the table — and both are carried by `_NON_LITERAL` pins, because neither names the edge. Row 1's MCP door was also wrong in the reach cell: `tortoise_search` defaults `entity_type="point"` and forwards it unchanged, so `order_by="graph"` alone lands on `_fetch_point_signals`, not on row 1's `_fetch_event_signals` — the same defect the second pass corrected on the SDK side (`suggest_entry_points`), left uncorrected on the MCP side. ⚠️ **And the blast-radius DOOR figure was a hand-count that nothing read**: it said *twelve* while the 12 customer-callable reach cells name **sixteen** distinct door identifiers, the folding rule was applied inconsistently (graph-ordered `search`/`tortoise_fts_query` folded into one door, the three `dry_run` delete tools left as three), and *"TWELVE sites behind those twelve doors, because `recall_state` alone reads the edge on FOUR code paths"* requires doors to be fewer than sites while asserting they are equal. At that pass both documents stated SIXTEEN and stated the rule (*a door is one independently callable entry point, counted once per site that reaches it* — ⚠️ a rule since RETRACTED: it yields cell occurrences, not distinct doors), and `tests/test_7948_about_object_read_sites.py` derived the total and the per-kind breakdown (then nine MCP tools, five SDK methods, two REST routes) from the reach cells. ⚠️ These figures are SUPERSEDED — the census now carries the row set and the counts §13's table states, and the present figures live in §13 and §13.8 of the two design documents; a restated count is a copy that rots on the next pass. Three fail-opens in the guard were closed with it: the census pattern was bounded by a 60-character window, so the six-member `about*` alternation `ranking.ABOUT_EDGE_TYPES` uses was invisible to BOTH the census and the row check (it is now bounded by the bracketed string and anchored on Cypher's dash-delimited relationship form, which also refuses a JSON payload that merely contains the word); the door test parsed only the strict ``REST `VERB path` `` form, so row 5's `hosted REST (`/v1/…/topic`)` never reached the route check (row 5 now writes the real route, and an unparseable `/v1/…` door reds); and `_rows()` asserted no uniqueness, so a duplicated row raised the stated count without adding a reader (row numbers and `(module, symbol)` pairs are now unique).
- `STORAGE-ARCHITECTURE.md:1344-1380` — **THIRD PASS 2026-10-21 (`#7948`): 25 sites, TWELVE customer-callable, twelve doors — two more all-edge walks behind customer doors, found by reviewing the guard's own rule against the code.** Row 22 asserts the inclusion rule is universal (*an unfiltered `MATCH (n)-[r]->(m)` reads the edge without naming it*), and applied to the code that rule found two readers the table did not carry: `mcp_server._preview_delete_edges` — the delete BLAST RADIUS, `MATCH (a)-[r]-(b) WHERE ID(a) IN $nids OR ID(b) IN $nids`, matching every incident edge and reporting `type(r)` per edge, behind three registered `dry_run` tools (`tortoise_delete`, `tortoise_delete_point`, `tortoise_delete_entity`) — and `hosted_api._export_graph_snapshot`, the org graph export, `MATCH (a)-[r]->(b) … type(r) …`, behind `GET /v1/organizations/{org_id}/export`. Both are customer-callable and both would silently lose `aboutObject` rows from what they report or ship if the edge were derived, which is exactly the argument row 21 (`_preview_supersede`) already rests on — so neither could be classed as transport and left out. The guard carries both as `_NON_LITERAL` rows pinning the consuming query INSIDE the reader's span, and its door test now resolves a REST door (the row writes `REST` beside the route, e.g. `GET /v1/organizations/{org_id}/export`) against the actual `@app.<verb>(path)` routes in `hosted_api.py`, so a customer-callable row cannot name a door that does not exist while REST-only reach stays expressible. ⚠️ Still a lower bound in one direction, and it is recorded on `#9418`: a NEW carrier-constant reader or a new unfiltered walk is invisible to the census until a row is added by hand, because the census sees only literal `aboutObject` brackets. The count itself is asserted, and so is every row's read.
- `STORAGE-ARCHITECTURE.md:1339` — Filed from this document — the tracking issue and its current state are named on each bullet:
- `STORAGE-ARCHITECTURE.md:1341` — - `aboutObject` as a stored edge vs a derived join — `#7948` (OPEN) — the measurement is DONE (research brief, 2026-09-23) and the answer is SPLIT, which decides it.
- `STORAGE-ARCHITECTURE.md:1363` — ⇒ The comparable's conclusion does NOT transfer. Their justification was "recall never touched them" — false here. Deleting the edge would silently break four customer-callable surfaces. …
- `STORAGE-ARCHITECTURE.md:1369` — ⚠️ The derived design is what CREATES a fan-out problem. Hindsight's join needed a `LATERAL LIMIT per_entity_limit` (default 200) and a timeout that drops the entire entity arm. A join on…
- `STORAGE-ARCHITECTURE.md:1344-1380` — **CORRECTED AGAIN 2026-10-20, second pass (`#7948`): 23 sites, TEN customer-callable, EIGHT doors — and the census is now keyed on `module.py::symbol`, not on the module.** Three readers were still missing, and every one of them sat INSIDE a module the table already named, which is exactly the drift a module-level census cannot see: `navigation._hop_query` (the second all-edge walk, behind MCP `tortoise_entity_profile` and `tortoise_traverse` — the same inclusion rule that makes `SubgraphExpander._neighbors` a row), `mcp_server._preview_supersede` (the supersede dry run, matching every `SUPERSEDE_STRUCTURAL_RELS` member), and `projection.FalkorProjection._rebuild_all_replay` (the replay delete-leg, matching a run-time rel name gated on `DERIVABLE_STRUCTURAL_RELS`). Row 1's SDK door was also wrong: `suggest_entry_points` hardcodes `entity_type="point"` and cannot reach `_fetch_event_signals`, so the door is `tortoise_fts_query(entity_type="event", order_by="graph")`. The guard was tightened in four places to make those failures red rather than latent: the census resolves the enclosing SYMBOL of every literal read; every carrier/computed row pins its CONSUMING line inside its own span (row 20 was the last row decided by a bare constant mention); carrier constants are resolved through the reader's IMPORT, so a constant that leaves `aboutObject` cannot pass; and the spelled counts go past `TWENTY`.
- `STORAGE-ARCHITECTURE.md:1344-1380` — **REPLACED 2026-10-20 (`#7948`): the `aboutObject` read-site enumeration was prose and had rotted, so it is now EXECUTABLE.** Removed: the `| # | site | … |` table whose rows cited `file.py:NNNN` (the lines had all moved; two named symbols — `topic_summarize`, `collect_slices` — did not read the edge at all, and row 4 named no symbol), the prefixed count *"ELEVEN read sites, and FOUR of them are CUSTOMER-CALLABLE"*, and *"And 36 test files assert the edge at the pin"*. What was wrong underneath, measured at `26aff163c`: **the customer-callable count was five doors / eight sites, not four**, because the whole state-recall path was missing — `recall_state` alone reads the edge on four paths (`StateRanker._fetch_point_signals` literal; `StateRanker._fetch_object_signals`, `TortoiseSDK._state_related_objects` and `TortoiseSDK._state_related_points` through `about*` alternation CONSTANTS, which no grep can see) — and `session_schema.audit_session_retrofit`, `projection/edges._count_about_out` were unnamed. Replaced by rows keyed on `module.py::symbol` (20 sites at that pass) + `tests/test_7948_about_object_read_sites.py`, which resolves every row against the code, re-runs the census, and asserts both counts equal the table. **The ruling is unchanged: KEEP the stored edges, deferred to post-launch.**
- `STORAGE-ARCHITECTURE.md:1374` — - Fan-out caps — `#5010` (CLOSED `COMPLETED`) — nothing in our write path bounds how many edges one entity accumulates (`config/ci-surfaces.yml` at 123, `durations map` at 111, `the admin…
- `STORAGE-ARCHITECTURE.md:1375` — - ⭐ NEW (2026-09-24) — the unindexed embeddings (V3, §12.1b). MEASURED ON THE LIVE GRAPH AND FILED: `#4997` — the measurement half is CLOSED `COMPLETED`; the unindexed-embeddings defect i…

### 14. What this document does not decide

- `STORAGE-ARCHITECTURE.md:1381` — - The vendor — Supabase Postgres is the owner's recorded choice for the system of record (local default now, Supabase post-beta). ⚠️ `pgvectorscale` availability on Supabase Postgres is n…

### 14.1 ✅ Decisions — all six closed (owner, 2026-09-24)

- `STORAGE-ARCHITECTURE.md:1394` — | O2 | Per-item or per-batch target; what recall floor? | ⛔ THE QUESTION IS WITHDRAWN — the owner rejected its framing. "we're not optimising stupidly for a number… this is not a corporat…

### 14.2 ⭐ The engine decision — AMENDED 2026-10-08: the engine stays, its ROLE changes

- `STORAGE-ARCHITECTURE.md:1400` — ### 14.2 ⭐ The engine decision — AMENDED 2026-10-08: the engine stays, its ROLE changes

### 14.3 ⚠️ Open — raised 2026-09-24, NOT decided

- `STORAGE-ARCHITECTURE.md:1417` — ⚠️ AMENDED 2026-10-08 — the hybrid makes V1 more urgent, not less. Under the 2026-09-24 decision the graph was the store, so moving the vector index was an optimisation. Under the hybrid,…
- `STORAGE-ARCHITECTURE.md:1423` — | V3 | ⚠️ The unindexed-embeddings defect (§12.1b) | MEASURED AND FILED 2026-09-24 → `#4997`. REFRAMED: this and V1 are the SAME decision (owner, 2026-09-24) — "isn't the research pointin…
- `STORAGE-ARCHITECTURE.md:1424` — | V4 | ✅ The silent `ok` — FIXED. | `#4999` — CLOSED `COMPLETED` (PR `#5159`). `run_vector_query` now records the mechanism (`index` / `scan` / `scan_fallback`), so a full scan is disting…

### 15. The measurement plan — what is actually worth measuring

- `STORAGE-ARCHITECTURE.md:1430` — ⚠️ CUT HARD 2026-09-24. An earlier version listed seven measurements, several invented to defend numbers this document has since withdrawn. A measurement is only worth listing if a decisi…
- `STORAGE-ARCHITECTURE.md:1441` — WITHDRAWN (recorded so they are not re-invented): the cold-index p95/p99 study (premise was vendor marketing), the quantization-recall study (no engine to apply it to), the 1,000-partitio…

### 16.2 The latency question — the claim was withdrawn, not softened

- `STORAGE-ARCHITECTURE.md:1462` — ### 16.2 The latency question — the claim was withdrawn, not softened

### 17.2 Cache coherence — and a COMPLETENESS MARKER

- `STORAGE-ARCHITECTURE.md:1526` — ⚠️ What is NOT built, and what is deferred rather than absent: the declaration's authoritative home is the control-plane residency metadata named in §2.6 (`#7971`'s cache projection write…

### The marker is NOT a fifth status term

- `STORAGE-ARCHITECTURE.md:1566` — Owner and verification. `#7953` — interface D, part 2; test-design `#7967` surfaces S4 (e2e: a non-resident read returns `not-hot`, never empty) and S5 (unit + integration: a miss is neve…

### 17.4.1 The two rate cards — one per store

- `STORAGE-ARCHITECTURE.md:1619` — ⚠️ `#4614` is cited in this issue — and in §13's issue map, corrected there — as the "published rates" issue, and it is NOT one. It is a quota-cliff capture bug (`402` at 9,998/10,000). T…

### 17.4.2 The measured inputs — every per-node figure with its base AND its denominator

- `STORAGE-ARCHITECTURE.md:1636` — | ⛔ the withdrawn 5.6 KB | 5,600 B ≈ 5.6 KB | 140 decimal MB ÷ 25,000 (CAPPED) | decimal MB | WITHDRAWN — total RAM ÷ the capped count |
- `STORAGE-ARCHITECTURE.md:1643` — 1. `~3 KB` is not the withdrawn `5.6 KB` — different denominators (≈45k vs the capped 25,000), so the `~3×` headline stands. ⚠️ But even `~3 KB` is now superseded for pricing, because the…

### 17.5 A field-level placement table — 9 derived/volatile property names on the sampled surface

- `STORAGE-ARCHITECTURE.md:1745` — §3's per-write test IS the mechanism — "if the journal does not carry it, it is not derived — it is PRIMARY, and belongs in a truth-layer table" — and §7 moves state values onto entity fi…
- `STORAGE-ARCHITECTURE.md:1751` — ✅ The canonical id contract has LANDED (`#7955`, 2026-10-11). The stable key is fixed per label — `:Point` → `id` (minted by `tortoise/ids.py::ulid()`), `:Event` → `eventId`, `:Subject`/`…
- `STORAGE-ARCHITECTURE.md:1753` — ✅ THE SCHEMA HAS LANDED (`#7968`, 2026-10-12). The four ontology layers are relational structure under `supabase/migrations/20261012000001`–`0004` (event stream · semantic · epistemic · p…

## Removed wording — the design-language pass (2026-10-15, `#8542`)

The copy pass that removed the implementation-state and change-narration
wrappers from the design document, leaving the design statements. Every
removed line is recorded here, keyed to the section heading in effect in
the pre-change file. Emphasis markers are dropped so the record renders,
and a line longer than ~190 characters is shortened with an ellipsis — the
moved material above is verbatim; this list is an index to it, not a
second copy. Section labels are bullets, not headings, so a removed line
that was itself a heading cannot collide with this file's own outline.

- **Storage Architecture — the physical layout of the record**
  - Status: design. The ruling this document is built on (§2.0, owner, 2026-10-08): Postgres is the system of record for every layer; FalkorDB is a derived hot cache. Read §2.0 and §3's table…
  - Related: `#7869` (this amendment — the doc's canonicalisation) · `#5086` (substrate-rebuild tracking) · `#5089` (durability — derived = replay(journal)) · `#5090` (the vector layer) · `#3…

- **⛔ THE STORAGE UNIT IS BYTES — decision, owner, 2026-09-26 (`#4495`)**
  - \*"does not shrink back on its own"* (**the wording is this issue's QUESTION comment, not the ruling's** — the
  - ruling's own synthesis renders the same fact as *"stock never resets while flow does"*. The qualifier carries the
  - argument, so it is kept: a node count does fall when nodes are deleted
  - per-node figure states its base and its denominator**, because that pair is where this document has already erred
  - true at once. The *decision* is untouched by this (bytes remain the unit); the *derivation* is corrected here so the
  - ⚠️ **This block SUPERSEDES the body's older figures.** §2 and §12 read **141 MB** and **45 MB** of indices where this
  - block reads **143 MB / 46 MB**, and the changelog's issue map concludes *"~3 KB per node (140 MB ÷ ~45k total nodes)"* —
  - a **third** denominator (~45k, and a different residency set). The **`~3 KB` estimate is superseded by this block's
  - `1,672 B` per resident node**; the figures are not interchangeable and the `~45k` node count is not re-measured here.
  - The changelog's issue map carries the denominator correction (the retracted 5.6 KB claim); **this block, not §1, is the
  - precedent for the denominator error class** — §1 corrects a *growth-horizon* error, a different mistake.
  - \**⛔ Status of the implementation — read this as FORWARD-LOOKING, not shipped.** Nothing in the byte path is on
  - \- `tortoise/graph_storage.py` is **not on `main`** (nor on this document's own revision pin `c79ba1cf2`). It exists
  - only on the **unmerged** branch `feat/5331-graph-byte-meter` (PR #5696). *"Built"* is true of an open PR only.
  - \- **There is no byte gate on `main`.** The gate — and with it the `max_storage_bytes` allowance key — is the
  - \**separate**, also-unmerged branch `feat/5331-node-to-byte-cap`. The key is therefore **absent on `main`** (and
  - absent from the meter branch), but it is **not absent from the repository**: it is written in `tortoise/` on that
  - \- The meter is measurement-only and FAIL-SOFT — its own docstring is explicit: "NOT A DIAL. Nothing here
  - ⇒ **The gate itself IS written — on the cap branch, not on `main`.** `_enforce_storage_allowance`
  - (`tortoise/quota.py` on `feat/5331-node-to-byte-cap`) raises `QuotaCheckError` when no reading is supplied, and that
  - branch's `pricing.py` describes the design as *"FAIL-CLOSED when no reading is supplied"*. The gate is therefore not
  - missing. **What is missing is the WIRING**: no production caller supplies a `storage_reading`, so the gate is inert,
  - and configuring an allowance today would 500 every points-gated write.
  - ⇒ **A requirement on the not-yet-done wiring, not a shipped default:** the meter must be wired into the gate **before**
  - any allowance is configured. That ordering is a constraint on the work, not a statement of what exists.

- **2.0 ⭐ DECISION (owner, 2026-10-08): hybrid now — Postgres is the system of record; FalkorDB is a derived hot cache**
  - ⚠️ IT AMENDS — and in one respect SUPERSEDES — the 2026-09-24 decision ("for now we can keep FalkorDB hosted and then we figure out further optimisation"). What changed is the TRIGGER, no…
  - \**✅ WHAT SURVIVES FROM 2026-09-24, UNCHANGED — and it is the whole reason this amendment is small:**
  - \- The PROVIDER question is untouched — the ruling moves who is authoritative, not which Postgres vendor (§14). Its PLACEMENT half is superseded, because placement and authority are now de…
  - \- §3's architecture was already the hybrid. Its table already read "the derived layer … Postgres; RAM-cached". §3's split does not change; this amendment adds the authority/cache columns …
  - ⚠️ SCOPE — this decides a PLACEMENT AND AN AUTHORITY, and it settles what §2 previously deferred. An earlier clarification said this decided a provider, not a placement, and did not rule …

- **The class that dominates our bill — measured**
  - ⚠️ A note on the Postgres side, kept separate so it does not confuse the current one. Earlier drafts asserted "pgvector's index is RAM-mandatory" and then over-corrected it. The settled f…

- **Why these are truth — the test is PER-WRITE, not per-class**
  - \- `Document.updatedAt` was set, and `doc_status` transitioned `captured`→`extracted` — both retired under D10/v3.15 (Q3): `doc_status` is RETIRED (`tortoise/api.py:458`) and liveness is a…
  - \- `Document ⊂ Object` (`ONTOLOGY.md` §1/§4.4/§6 — `objectKind: document`), so the class appeared in both layers. ⚠️ SUPERSEDED (v3.15/D10): a document is a `:Source`, not an Object subcla…

- **✅ A derived cache on a truth row is NOT a violation — it is a named pattern**
  - \**This dissolves the `Document` problem rather than deferring it.** A read-model cache written through to an aggregate row is the **standard CQRS pattern**, and it is safe under five rules:
  - `Source.reliability` already satisfies all five — `ONTOLOGY.md` calls it a "documented cache, never authoritative" (`#398`). `Document` was once placed explicitly — its existence was trut…

- **⚠️ The truth set is larger than these three — and the invariant is false today**
  - \#### ⚠️ The truth set is larger than these three — and the invariant is false today
  - ⚠️ And `derived = replay(journal)` is FALSE in the code as it stands (`#4641`, `#4653`). Journal completeness is a PRECONDITION of this design, not an observation about it — raw-Cypher wr…

- **The `#2881` collision: resolved by SCOPING, not by overriding (see §14.1 O1)**
  - \#### The `#2881` collision: resolved by SCOPING, not by overriding (see §14.1 **O1**)
  - `docs/durability-posture.md` (`#2881`) is a recorded decision: the journal "is never the durability mechanism … authority stays in a store-managed artifact." That decision collides with t…
  - ⚠️ IT IS NO LONGER OPEN. §14.1 O1 (owner, 2026-09-24) settled it, and the resolution is that the two questions are different and both hold: DURABILITY = the store's backup (`#2881`, uncha…
  - ⚠️ What O1 DID leave open, and it is a real gap this document must name: O1 identifies the defect in its own words — "`rebuild_all` performs an unconditional wipe + journal-only replay — …
  - \**⚠️ A second finding worth its own issue: the durability gate `tests/test_durability_posture.py` does NOT scan `docs/architecture/`** — so the colliding wording passes CI today.
  - \**✅ One epistemic line, kept because it explains WHY they are mutable — the only non-storage sentence left here:**

- **The invariant — and it is a REQUIREMENT ON THE JOURNAL'S SCHEMA**
  - The general rule (research, 2026-09-23):
  - | `content_hash` | **RECOMPUTE** | pure, self-contained. *The code already does this correctly.* |
  - ⚠️ `projection/__init__.py:3342` STRIPS the embedding from the journal-derived snapshot with the comment "the replay re-derives it". That is the defect. A re-embed is a re-run, not a repl…
  - ⚠️ And bound the growth, which the cost model currently omits. Naively, a Point revised N times produces N full-payload events and the journal grows without bound. The standard fix: creat…

- **Divergence detection and repair — and the answer is simpler than the doc implies**
  - \#### Divergence detection and repair — and the answer is simpler than the doc implies
  - \**The doc has no failure path. The research found the standard answer, in priority order:**
  - \1. ⭐ ONE ATOMIC WRITE — journal + projection in the same transaction (or an outbox with an idempotent projection). This is the whole answer to "the append succeeds and the projection fail…
  - Repo status measured: (1) absent · (2) absent · (3) count-only (`consistency.py`) · (4) present and guarded · (5) absent. ⚠️ The current JSONL cannot do (1) — `durability-posture.md` reco…

- **Why this shape**
  - \- The derived layer becomes REBUILDABLE — which removes the current blocker: `#3895` (the restore re-drill that failed, 9,687/10,000 edges) exists because the graph is currently the only …
  - \- One transaction covers the journal and its projection — no dual write between them. ⚠️ That is the truth↔derived write, and it is the only sync the pre-hybrid shape needed. The hybrid a…

- **What is already in the code**
  - \### What is already in the code
  - The store sits behind a two-method Protocol — `apply(event)` and `rebuild(log)` — and an `InMemoryProjection` already exists as a second implementation. The architecture is event-sourced …

- **4. Tenancy — DECIDED (owner, 2026-09-23): one database, tenant-scoped rows**
  - \## 4. Tenancy — DECIDED (owner, 2026-09-23): one database, tenant-scoped rows
  - \**Why — the shape is already ours.** This is the same shape as the current FalkorDB arrangement — *one account, many graphs* — so it is a migration of mechanism, not of model.
  - \> ⚠️ The trap that makes this a real decision, not a default. On Supabase the platform, "one database per team" does not mean one database — it means one Supabase project per team, and ea…
  - \- The mechanism that makes pruning possible is declarative partitioning (`PARTITION BY` tenant) + partition pruning, or binding the tenant key as a literal on the connection. Verify with …

- **5.1 The two cost lines the table omits**
  - \**The numbers in the table above are the ones to use; the ~$460 and ~$4,100-adjacent figures earlier in the doc came from a different, superseded arithmetic.**
  - ✅ One of the two omitted lines — egress + IOPS — is now PRICED and carried as a row of the two-store table (§17.4). Egress and IOPS are $0.09/GB (250 GB included) and $0.024/IOPS/month (3…

- **6. The split does NOT fix the volume problem**
  - ⚠️ This section's multiplier is derived from a vector share that §2 corrects — and the correction moves it in the opposite direction from the other fixes. The naive figure is ~15× blended…
  - \1. **The vector class is roughly half the graph, not ~1/3** (§2/§12.1b correction) — so weighting the cheap leg less would *lower* the blend.
  - \2. But the vector leg is no longer a ~5× RAM win. §12.1's correction establishes the index is disk-backed like everything else — so the ~584× disk-vs-RAM multiplier applies to ALL bytes, …
  - ⇒ The storage-cost multiplier is larger than published, not smaller — on the order of the full ~580× rather than a 15× blend. ⚠️ But this is a STORAGE-cost multiplier only, and it is not …

- **6.1 ⚠️ The "~10×" is a target, not a measurement — and volume alone is a gameable metric**
  - \- ⚠️ Volume with no retention measurement is gameable — you can always hit a node target by writing nothing. A volume objective must be paired with the measurement of how many durable cla…

- **7. State values — RETAIN is decided and landed; PLACEMENT is DECIDED (2026-10-06, below)**
  - \## 7. State values — RETAIN is decided and landed; PLACEMENT is DECIDED (2026-10-06, below)
  - ⚠️ STATUS — and the two documents disagreed until 2026-09-24 (extractor §9 records D8 as `DECIDED (retain half landed)`; this section is headed PLACEMENT is DECIDED as of 2026-10-06, and …
  - \- ⛔ **What is missing is enforcement BY DEFAULT — not the mechanism.** `valueGate` exists in code (`tortoise/value_gate.py`) and ships **OFF**, subordinate to VET.
  - \- ✅ PLACEMENT is NO LONGER OPEN — it is DECIDED (owner, 2026-10-06; the decision paragraph follows immediately below). It was: whether a state value becomes a `Point`, or a structural fie…
  - ✅ PLACEMENT ACCEPTED (owner, 2026-10-06): a state/numeric value is a STRUCTURAL FIELD ON AN ENTITY, not a `Point`. The reasoning is the ontology's own: operators connect only epistemic ta…
  - Raised by the owner 2026-09-23: "State values we might need to think through how to store them for low-cost (so ideally out of RAM or only if must) yet good search and good association."
  - The superseded placement recommendation is a comment on `#1509` ("Decision requested (2.2) … A (recommended): state-value facts as Points"), superseded by the PLACEMENT ACCEPTED decision …
  - Superseded proposal (this WAS the open PLACEMENT half; it is now DECIDED above — kept as reasoning, not as an open option): state values are structured — subject + attribute + value + dat…
  - ⚠️ STILL UNVERIFIED — and it now bears directly on the decision above: whether the query patterns users actually need for state values are structural (find "5K time for this user") or sem…
  - ⇒ This is the storage half of a two-part design. The extractor half is `EXTRACTOR-V4-ARCHITECTURE.md` §7 — and it carries a finding that changes this section: the existing carve-out keeps…
  - \> Caveat (`#4889`). The live graph is a DOGFOOD instance, so an edge count there is not the test: the `aboutSubject` half could not be disproved that way. The code exists and is tested (`…

- **8. Constraint carried from the extractor — granularity is NOT negotiable**
  - \- Operators connect only epistemic targets (any of `:Point`/`:Subject`/`:Object`/`:Event`) — you cannot `NAND` a paragraph. The `:Subject`/`:Object` operator endpoints are live (`#7902`);…

- **9.3 ⭐ A document is a SOURCE, not a graph node — and this is what makes the entity layer an abstraction**
  - \- **My "Document sits in two graph layers" finding was WRONG** — it has **one** label; the subclass is conceptual, expressed as a property.
  - ⇒ `:Document` folds into `:Source`. Its content leaves the graph — to raw text in Postgres (true binaries to the object bucket; §3) — referenced per D30, and the entities extracted from i…

- **9.4 ⭐ The Source: link first, summary vector second — and identity is never a vector**
  - | *"which of my SOURCES is about pricing?"* | ⛔ **nothing can answer this today** — needs a source-level vector |

- **9.5 ⭐ The five consequences of "a document is a source" — ruled (owner, 2026-09-24)**
  - D10 is one sentence. Landing it touches five things, and each was run through the `AGENTS.md` decision protocol (research first, contradiction test before anything else). The outcome is i…

- **Q1 — the `aboutDocument` link: KEEP IT; move only its label. ⛔ This is the one hard block.**
  - ⇒ Collapsing `aboutDocument` into `aboutSource` moves the edge class OUT of the replayable set. That is a durability regression, and it would be invisible until a rebuild was actually nee…
  - What does change: its target label (`:Document` → `:Source`), and therefore its replay key — `coalesce(title, name)` → `url`, because a `:Source` resolves by `url`. ⚠️ These two must chan…

- **Q2 — the classification axes: KEEP BOTH. They answer different questions**
  - These are not the same question, and collapsing them is a category error — the library world has kept document type and genre in separate MARC fields for decades (genre/form vs content ty…
  - ⭐ And the finding that reframes the whole decision: `document` was already a declared pack `sourceTypes` value (`packs/dev/manifest.yaml`) while `ONTOLOGY.md` §4.4 called it an Object sub…

- **Q3 — the document fields: `doc_status` goes; `format` moves; the rest stay.**
  - | `format` | **MOVES to `:Source`** | it is genuinely a source property; `_SOURCE_HANDLED` does not carry it yet |
  - | `content` | ⛔ LEAVES THE GRAPH | raw text is the raw-text store's job — Postgres under §3/§9.1/D30; the object bucket holds only true binaries. This was the 29th site and it had gone un…

- **9.6 ⭐ A source is VERSIONED — and the version lives on the extraction link as `sourceVersion` (owner ruling, 2026-09-24)**
  - The owner raised the gap the D10 pass left open: "shouldn't we have some form of version tracking for sources? at least to know if they changed and we might need to re-infer the Entities …
  - The requirement was already half-stated. The ontology's `§4.6` already defined `contentHash` as "idempotency anchor — skip re-extraction if unchanged" and already listed `document` under …

- **⭐ Scope of the anchor — a Point-level guarantee, extended to derivation links (decided 2026-09-25)**
  - `sourceVersion` rides on `extractedFrom`, which is declared `Point → Source` (`ONTOLOGY.md` §3.3) — so a class whose provenance does not pass through an `extractedFrom` link carries no re…
  - This is not a storage omission, and for the derivation half it is now decided (below). §12.1's cost question is where bytes live; this is whether the anchor exists. Two facts follow for s…
  - \- derivation — an `Event` (or `Document`) built from a source's content: the connector path materializes the link at the projection choke point (`projection/entities.py::_materialize_conn…
  - \- So the cost of closing it falls on the derivation links only — one hash each, since a version is three timestamps + a hash and never a content copy (D30). ⚠️ No total is derivable witho…
  - DECIDED — option A (owner-approved 2026-09-25, `#5199`). The anchor extends to the derivation `references` link only — targets `Event` or `Document` (`Document` retires with D10): an opti…
  - ⚠️ The model statement lives in `ONTOLOGY.md` §3.4 / §4.6 — this section records only what the anchor costs. The ontology wording is in owner review (`#5199`); until it lands, the code an…

- **⭐ Why document-level versioning ALONE would not have answered the owner's question**
  - Research: `docs/research/2026-09-24-source-versioning/research-brief.md`. Adoption gate: ADOPT — no recorded decision contradicted; D7 governs it; the ontology's `§4.7` already declares t…

- **⭐ Where this stands (2026-09-25) — a status pointer, not a new rule**
  - \#### ⭐ Where this stands (2026-09-25) — a status pointer, not a new rule
  - \- The anchor shipped. The read version rides on the `extractedFrom` link as `sourceVersion` (`#5256`, PR `#5288`), with the honest-absent rule (`''`/blank/non-string hash ⇒ no property at…
  - \- What a stale fact does — Policy B (`#5038`, owner, 2026-09-26): "We should not return an out-of-date fact when we have a newer one" — so when a newer fact exists, the out-of-date fact i…
  - \- **A non-re-read successor records no version** (owner, O1) and reads `unknown` until it is itself read from a source; `ONTOLOGY.md` §4.6 is unmodified and the §4.6 reopen was not taken.
  - \- The re-inference engine — its ownership gap is `#5422`; acceptance A2 of `#5038` is what remains outstanding for it. `SourceVersioned` (`log.py:155`): a re-fetched `:Source` that change…
  - \*This pointer records status only. `ONTOLOGY.md` §4.6 is owner-gated and is NOT edited by this work.*

- **10.1 Invalidate by RELOCATION, not by a flag**
  - Why it matters here: it means the hot table stays the working set — which is exactly the invariant this whole document rests on (cost scales with the working set, not with total stored da…

- **11.1 Why the connections are not the cost problem — measured**
  - Our `aboutObject` links are property-free: the write path emits `MERGE (s)-[:aboutObject]->(t)` and never a `SET` (`projection/edges.py`, `create_about_edge` — `:344` at the pinned revisi…

- **11.4 The four rules that keep it cheap**
  - \2. **Cap the fan-out** *(adopted 2026-09-23, §11.5)* — bounds how many edge pages one hub query pulls, which is the working-set bound this whole document rests on.

- **11.5 Fan-out cap — ADOPTED (owner, 2026-09-23; decision protocol)**
  - \### 11.5 Fan-out cap — ADOPTED (owner, 2026-09-23; decision protocol)
  - Adopted in principle; initial value 200 — the comparable's proven value, and above our current worst hub (123), so it binds nothing today and cannot lose data now. It is a guard rail agai…

- **11.7 The exception, unchanged**
  - \### 11.7 The exception, unchanged
  - ⚠️ No layer "follows a disk rule", and `pgvector`'s HNSW index is not RAM-resident. The premise that produced that claim was wrong, and §2.1 and §12.1 state the RAM model: `pgvector`'s HN…

- **12.1 The vector index — the class that dominates our bill**
  - And it is the biggest single class we pay for — ⚠️ and the exact attribution is now MEASURED, replacing an earlier estimate. `GRAPH.MEMORY USAGE` on the live graph (2026-09-24, `SAMPLES 1…
  - ⇒ Therefore "what earns an embedding" is the largest lever on our bill that requires no engine change and no migration. That is §12.2's subject. And see §12.1d — the latency half of this …

- **What is NOT a problem: latency (measured 2026-09-24)**
  - ✅ AND THE GAP IS NOW CLOSED — M1 was taken 2026-09-24 (§12.1d): our own retrieval layer is 1.5–5 ms. Every number above has been superseded by ours; latency is a non-issue and the only op…

- **12.1a Why "just partition per tenant" is NOT one mechanism — and the two halves have OPPOSITE answers**
  - \**⚠️ The study that argued against per-tenant partitioning conflates TWO mechanisms that behave oppositely — its mechanism does not reach declarative partitioning.**
  - §12.1a's cited mechanism — "Postgres takes a shared lock on every index of a table during planning" — can only reach (A). It is a count of indexes per relation, so it cannot reach (B), wh…
  - \- The earlier table's column was MISLABELLED — it was headed "recall p50", but the values it carried (154 ms, 184 ms, 2.3 s, ~11 s — the superseded study's own figures, at its own index c…
  - ⚠️ And the correction to the reasoning matters as much as the numbers: the withdrawal took a PER-RELATION PLANNING COST and applied it to a mechanism it cannot reach. ⚠️ Read it as a cost…
  - ⚠️ **`pgvectorscale`/DiskANN is NOT available on Supabase** (confirmed on the live extensions list; two open requests `#27474`, `#29095`). `pg_prewarm` **is** available.

- **How big our index is, and why "fewer vectors" is the whole game**
  - ✅ MEASURED 2026-09-24 — M1 is done. See §12.1d: 1.49 ms network floor, 2.74 ms indexed vector search, 1.55–1.75 ms traversals. Our retrieval layer is single-digit milliseconds and latency…

- **12.1b Where the vectors actually sit**
  - \**The vector class is not a single index. One finding is a filed defect.**
  - `:Object` costs 3.4× `:Point` for the same query. The trace records the mechanism (`index` / `scan` / `scan_fallback`), so degradation is observable to any caller (PR `#5159`) — before it…
  - ⚠️ The mechanism, and it is a drift, not an oversight: `#172` (`[P1][bug] run_vector_query lacks entity_type`, closed 2026-08-06) made the query label dynamic — `label = entity_type.capit…
  - ⇒ Filed as `#4997`. ⚠️ REFRAMED 2026-09-24 (owner): the "stop storing OR index them" framing was a FALSE CHOICE. The third option is better than either: keep the vectors, make them search…

- **12.1c ⭐ OPEN DECISION — should the vector index live in FalkorDB at all?**
  - ⭐ Recommendation. The measurement now exists (§12.1d) and it supports option 2. Our whole vector leg is 2.74 ms indexed; a full linear scan of 7,859 Object vectors is 9.26 ms; our network…
  - \**⚠️ NOT DECIDED — owner's call.** Recorded as an open decision (§14.3 V1) rather than adopted, because it changes what the graph holds.

- **12.1d ⭐ M1 — OUR OWN LATENCY, MEASURED (2026-09-24)**
  - \**This section existed for a day as *"we have never measured our own query latency"*. We have now. It ends the latency half of the debate.**

- **12.2 ⇒ The lever is "what gets an embedding"**
  - \- ⚠️ `classify_consolidation` (`extractor_v2.py:3021`) is NOT this gate — it is the 4-way consolidation classifier (ADD/UPDATE/NOOP/DELETE). And the 34.4% mechanical figure was an audit r…

- **12.2a Where the rule lives — ALREADY DECIDED (adoption gate, contradiction test run 2026-09-23)**
  - \### 12.2a Where the rule lives — ALREADY DECIDED (adoption gate, contradiction test run 2026-09-23)
  - The question posed was: does this rule belong in the pack (declared) or in the write path (computed)? The question is falsely binary, and it was already answered in this repository. It sh…
  - \**⚠️ A decision is recorded — `#1026` §3, verbatim:**
  - \**The field converges on the same split** (so this is adoption, not a judgement call):
  - \**⇒ ADOPTED — DECIDED 2026-09-23. The shape is BOTH, and each half is settled:**

- **⭐ And the decisive finding: the policy is ALREADY WRITTEN DOWN — as prose, unenforced**
  - | **DISCARD** | does the row **exist**? | `classify_consolidation` | **`#4899`** (Phase 1, flag-gated) |
  - ⇒ The work order is: enforce the declaration that already exists, at both points. `#4899` Phase 1 builds the rule set from `memory_granularity`; the embedding gate reuses that same rule s…
  - \**✅ DECIDED 2026-09-23 — the two gates SHARE ONE RULE SET and enforce at TWO POINTS. Adopted, not escalated (contradiction test run, field converges).**
  - The tension was posed as *"share one rule set, or let them diverge?"* — **and the research shows those are not alternatives; they operate at different levels:**
  - ✅ Why the contradiction test passes, and this is the reason it was adopted rather than escalated: `#4899`'s own criterion already IS the shared-rule-set requirement — "the rule set is der…

- **12.2b The levers, ranked**
  - | 2 | Do not store what we do not need (§11.5 fan-out cap; the extractor work) | fewer nodes ⇒ fewer vectors ⇒ less RAM rent, with no engine change | the extractor work we are already doi…
  - | 4 | ⚠️ Partition the vector index per tenant | PARTIALLY RESTORED — see §12.1a. The withdrawal conflated two mechanisms. Declarative `PARTITION BY tenant` is FLAT (measured 6.4 → 1.8 → …
  - | **5** | **Self-host the engine** (SSPLv1, free) | removes the `$0.10/GB-hour` rent entirely; same engine, no rewrite | ops work — **DEFERRED by owner decision until there are users** |
  - ⇒ Ranked honestly: levers 1 and 2 are the ones we can pull now, and they are the same work — write less, and write things worth keeping. Lever 3 is the ops dial. Lever 5 is the largest re…
  - ⚠️ Lever 4 is partially restored — the mechanism is narrower than the study suggested. The per-relation planning cost reaches only the partial-index-per-tenant form, and declarative `PART…
  - ⭐ And the lesson worth keeping is the one §12.1a states: the withdrawal mis-attributed ONE mechanism to BOTH branches. It is a query-planner cost on the number of indexes per relation — n…

- **12.2c ⭐ What earns a VECTOR and what earns a TRAVERSAL — the research answer (2026-09-24)**
  - ⚠️ Weak names are a SEQUENCING fact, not an argument against the vector (owner, 2026-09-24). The definite-description problem is already an in-flight extractor workstream (VET/CLASSIFY, t…
  - \**⚠️ Also settled, because the distinction is easy to get wrong: *"embedding an entity"* means two unrelated things, and only one is deployed.**
  - ⚠️ Adversarial honesty: the counter-case was NOT found — no system reports adding entity embeddings and removing them, and no measured ablation isolates an entity vector's contribution to…

- **12.3 The tiers — and there are TWO, not three**
  - \**Owner proposed three tiers; the published architecture uses two, and the two-tier version is the one with measured results.**
  - Why folding the owner's middle tier in is correct: the owner's tiers 2 and 3 ("TLDR epistemic/events related to an entity" vs "fuller data around an entity") are the same tier at two dept…

- **12.6 The published architecture — `TierMem` (arXiv 2602.17913, 20 Feb 2026)**
  - \**The owner's design, published, with measured results — which means we adopt its specifics rather than invent them.**

- **12.7 Failure modes to design against (adversarial pass — all published, none ours)**
  - \### 12.7 Failure modes to design against (adversarial pass — all published, none ours)

- **12.8 What this means for our design — the four things to do**
  - \1. ✅ DONE — M1 measured our own latency (§12.1d, 2026-09-24). The whole retrieval layer is single-digit ms; the vector leg is ~3–5 ms; a full scan of all 7,859 `:Object` vectors is 9.26 m…
  - \2. Decide what gets an EMBEDDING, not what goes in the graph. §12.1–12.2: the vector class is the one that dominates the measured RAM attribute bytes (§12.1's `Point` 50 MB / `Object` 13 …
  - ⚠️ And the discipline that keeps this honest: our own numbers for the cost TIERS still do not exist. Every tier figure above is published third-party and must be re-measured on our data b…

- **13. Filed from this document — the open rulings**
  - The issue map that used to open this section — a section→issue index carrying
  - \**Filed from this document:**
  - \- **`aboutObject` as a stored edge vs a derived join** — **`#7948`** — the measurement is complete (research brief, 2026-09-23) and the answer is SPLIT, which decides it.
  - And **36 test files assert the edge at the pin** (41 at HEAD, 2026-10-10).
  - ⇒ The comparable's conclusion does NOT transfer. Their justification was "recall never touched them" — false here. Deleting the edge would silently break four customer-callable surfaces. …
  - But the structural door stays open: no `aboutObject` edge carries any property — `create_about_edge` (`projection/edges.py:344`) emits `MERGE (s)-[:aboutObject]->(t)` with no `SET`, and t…
  - ⚠️ The derived design is what CREATES a fan-out problem. Hindsight's join needed a `LATERAL LIMIT per_entity_limit` (default 200) and a timeout that drops the entire entity arm. A join on…
  - \- Fan-out caps — `#5010` — nothing in our write path bounds how many edges one entity accumulates (`config/ci-surfaces.yml` at 123, `durations map` at 111, `the admin-merge rail` at 70, `…
  - \- ⭐ The unindexed embeddings (V3, §12.1b). MEASURED ON THE LIVE GRAPH: the unindexed-embeddings defect is LIVE, and its home is the V1/§12.1c decision (`#4997`). `CALL db.indexes()` confi…

- **14. What this document does not decide**
  - \- The vendor — Supabase Postgres is the owner's recorded choice for the system of record (local default now, Supabase post-beta). ⚠️ `pgvectorscale` availability on Supabase Postgres is n…
  - ✅ AND ONE ITEM LEFT THIS LIST on 2026-10-08: "whether to drop a separate graph engine at all." It is decided by §2.0 — the graph engine is retained as a derived hot cache and DEMOTED, not…

- **14.1 ✅ Decisions — all six closed (owner, 2026-09-24)**
  - \### 14.1 ✅ Decisions — all six closed (owner, 2026-09-24)
  - \**These were escalated per the AGENTS.md protocol rather than settled by the lane. They are now answered. Recorded here so no future lane re-opens them.**
  - | # | the question | ✅ ANSWER (owner, 2026-09-24) | what changed |
  - | O1 | Is the journal allowed to be the authority? | ⭐ Neither side was ever decided — and the conflict was mine. `#2826` A2 (the actual decision row, owner-required) recommended the jour…
  - | O2 | Per-item or per-batch target; what recall floor? | ⛔ Not a question — the owner rejected its framing. "we're not optimising stupidly for a number… this is not a corporate OKR setti…
  - | O3 | Which outcome word does the gate emit? | ✅ ADOPTED — use the declared word (`DISCARD`), matching the engine's existing classifier. | `#4899`'s text is corrected to the declared voc…
  - | O4 | Can near-duplicates be merged? | ✅ YES — the owner authorised it, with Jev as arbiter over the claim plus narrative/raw data. "yes we can merge near duplicate claims. maybe we can …
  - | O6 | Which layer owns `Document`? | ✅ Resolved: a document is a SOURCE (§9.3). "I am suggesting making them sources so our entity layer can be extracted from them…" | `:Document` folds …

- **14.2 ⭐ The engine decision — the engine stays, its ROLE changes**
  - \**Owner, 2026-09-24:** *"for now we can keep FalkorDB hosted and then we figure out further optimisation."*
  - \**Owner, 2026-10-08 (§2.0):** *"we do hybrid now, data goes to postgres and it's loaded to hot (falkor); for now we keep the limit in falkor high…"*
  - What is UNCHANGED: FalkorDB stays hosted, and there is still no engine rewrite. The 2026-09-24 decision was about the engine and its host, and the 2026-10-08 ruling disturbs neither.
  - \**What changed, and it is the ROLE:**
  - \- **Then:** FalkorDB was **the store** — the only durable home of the reasoning layer — with the Postgres target deferred until *"there are real users and a measured footprint."*
  - \- **Now:** **Postgres is the system of record and FalkorDB is a derived hot cache** (§2.0, §3). **The engine is not replaced; it is DEMOTED.**
  - \- ⚠️ And the deferred TRIGGER was inverted. "Revisit when there are real users" is the most expensive moment to move a store, because migration cost scales with customer count. The 2026-1…
  - \- ⚠️ The line "Do NOT re-open this as 'shall we migrate to Postgres?'" is RETIRED. It was right under the 2026-09-24 decision; the owner has since reopened it and answered it. §3's table …

- **14.3 ⚠️ Open — raised 2026-09-24, NOT decided**
  - \### 14.3 ⚠️ Open — raised 2026-09-24, NOT decided
  - ⚠️ The hybrid makes V1 more urgent, not less. Where the graph is the store, moving the vector index is an optimisation. Under the hybrid, FalkorDB is a CACHE, so every byte it holds is re…
  - | V1 | Should the vector index live in FalkorDB at all? (§12.1c) | It changes what the graph holds — a real change to the retrieval path, not a tune. Owner raised it; owner decides. The l…
  - | V4 | ✅ The silent `ok` — FIXED. | `#4999` (PR `#5159`). `run_vector_query` now records the mechanism (`index` / `scan` / `scan_fallback`), so a full scan is distinguishable from an inde…

- **15. The measurement plan — what is actually worth measuring**
  - A measurement is only worth listing if a decision turns on it. ⭐ M6 was added 2026-09-30 under that rule: the owner re-affirmed that the retention measurement is wanted (a decision turns …
  - | id | question | why it matters | status |
  - | M1 | What is OUR query latency, warm? (`EXPLAIN (ANALYZE)` + timing on the live graph) | Every latency figure here is someone else's. A one-liner that retires the entire §12.1 debate. |…
  - | M2 | What is our real bill? — the actual FalkorDB invoice against the $9 → $19 budget, at month-6 footprint | the only cost question asked, and §5/§6 are arithmetic on published rates, …
  - | M3 | How much of the 140 MB is junk? — re-measure after the extractor work lands | tells us how much of the CACHE's footprint is junk — i.e. cache-tier sizing and whether self-hosting (…
  - | M4 | Does self-hosting beat Cloud at the CACHE tier's footprint? — price a VM holding N tenants at ≤75% RAM | a cache-tier ops lever — no longer "the named revisit lever" (§14.2 amended…
  - | M5 | ⭐ Where should the vector index live? — our query latency with the index resident, against a real `GRAPH.MEMORY USAGE` read | decides V1 (§12.1c) — the first cache-tier question (§…
  - | M6 | ⭐ How many durable claims did we keep? — the share of the candidate facts the pipeline saw that survived as claims | The guard §6.1 requires: without it, a volume objective can be …

- **16. Research outcomes (2026-09-24) — what was checked, and what it overturned**
  - \## 16. Research outcomes (2026-09-24) — what was checked, and what it overturned
  - Three research passes were run for this decision (FalkorDB memory accounting; the idle-vs-active cost model; the latency question). They are recorded here because two of them overturned c…

- **16.1 FalkorDB memory accounting — the cost model, settled**
  - \**⇒ Overturned:** every claim in earlier drafts built on *"an idle tenant is cheaper"*. **On our stack it is not, and cannot be.**

- **16.2 The latency question**
  - \**Owner's challenge, 2026-09-24:** *"is the 300ms a hard ceiling? where does it come from? and why would supabase queries be so slow >500ms?"*
  - | what the document said | what is true |
  - ⚠️ The generalisable lesson, and it is the reason this section exists: a vendor's performance claim was promoted to a constraint, and an architecture was argued on it. "Cheapest first", "…

- **17. The five gaps the hybrid ruling requires — and that this document did not cover**
  - \## 17. The five gaps the hybrid ruling requires — and that this document did not cover
  - ⚠️ Added 2026-10-08 (`#7869`). The 2026-10-08 ruling (§2.0) makes Postgres the system of record and FalkorDB a derived cache. §3 states the shape; these five are consequences the document…

- **17.1 The write-path rule — unstated, and unenforced**
  - \### 17.1 The write-path rule — unstated, and unenforced
  - §3 already states the rule for the derived TABLES — "Writes go to the truth first, then project" (`:308`) — but it does not extend it to the CACHE, and nothing enforces it anywhere. The R…
  - Measured 2026-10-08 (method: a regex scan for mutating Cypher verbs over `tortoise//.py`): the write surface has NO chokepoint — 387 mutating `.query(` sites across 211 functions in 32 fi…
  - ⛔ CORRECTION TO §17.1 AS WRITTEN — the sibling journal engine's `:GraphEventMeta` watermark is NOT "truthful", and the `#9213` deferral is the honest statement. An earlier version of §17.1's pre-wipe paragraph called that write "truthful … that engine's value comes from the replayed log"; the code refutes it — `rebuild()` captures the counter from the graph with `capture_watermark` BEFORE its own wipe — and a blanket restore reason is not the honest fix either, because `reestablish_watermark` stores `max(carried, max(:GraphEvent.seq))`, so the value can come from the replayed log when it is higher. The two sources have to be split before one lane can describe that write. §17.1 now states the design and carries no change narration; the correction history is this entry (`#9210`, PR #9218, found by `tests/test_extractor_v4_citation_rot.py`'s class pin).

- **17.2 Cache coherence — and a COMPLETENESS MARKER**
  - §3 covers ONE divergence surface — journal ↔ projection, with five mechanisms and the measured status "(1) absent · (2) absent · (3) count-only · (4) present and guarded · (5) absent." Th…
  - \**Two requirements follow, and neither is in the document:**
  - \**✅ 2026-10-12 (`#7952`) — the mechanism exists for both; the readers and the trigger do not.** Neither half is a new decision; both follow from §2.0. What is now BUILT and pinned by tests:
  - ⚠️ What is deferred rather than absent: the declaration's authoritative home is the control-plane residency metadata named in §2.6 (`#7971`'s cache projection writes it; until then the re…
  - ✅ 2026-10-13 (`#7971`) — the cache projection exists; the carry of §2.0's second half is no longer missing. `tortoise/cache_projection.py` (Interface B) projects the record's STATE — the …

- **17.3 Partial-cache semantics — a residency miss is not an insufficiency**
  - §12.3's tier table assumes each tier is COMPLETE. Under the hybrid, FalkorDB is partial by design — the limit is held high precisely so this is deferred, not absent. §12.4's sufficiency r…

- **⛔ THE HARD EDGE: marker → router**
  - Without §17.2's marker the router cannot exist. `not-hot` is the only signal that separates the two events; an empty result set carries no signal at all — which is exactly why an empty re…

- **The marker is NOT a fifth status term**
  - ⚠️ `not-hot` must not be minted as a fifth term of the read-status vocabulary. That vocabulary is RECORDED — `available | empty | degraded | unconfigured`, ONE home, `tortoise/status_voca…
  - Owner and verification. `#7953` — interface D, part 2; test-design `#7967` surfaces S4 (e2e: a non-resident read returns `not-hot`, never empty) and S5 (unit + integration: a miss is neve…

- **✅ 2026-10-13 (`#7953`) — the router exists, and the two events are separable where the decision is made**
  - \#### ✅ 2026-10-13 (`#7953`) — the router exists, and the two events are separable where the decision is made
  - A cache that is DOWN is a fourth branch, and it is a residency miss (test-design `#7967` S5 names it; §17.3 did not). `cache_reachable=False` routes to the record with its own `reason` (`…
  - The reader passes the coordinate. `tortoise_fts_query` takes a private `_residency_key` (the `_elevated_timeout_ms` precedent) and hands it to the marker, so the per-key branch is observa…
  - Verified: `tests/test_residency_router.py` — 30 tests, with the mutation proofs recorded (collapse the two events · route a down cache as no-evidence · report a partial miss as an absence…

- **17.4 A two-store cost model — the existing tables price ONE store**
  - §5's cost table and §6's multiplier both model a single store. The hybrid pays both: Postgres compute + disk and FalkorDB RAM. §6 conceded that gap in its own words — "the blended total-c…
  - This matters because the split's whole justification is arithmetic. The claim is not "Postgres is cheaper" but "bytes that are never read again stop renting RAM." That claim has no table …
  - ✅ 2026-10-10 (`#7954`) — the table exists, the arithmetic is closed, and it NAMES ITS CONDITION rather than confirming the conclusion. The rate cards are published and verified against th…
  - It answers the four things that were missing: a rate card for both stores (§17.4.1), the IOPS line §5.1 omits (also §17.4.1), the blended total §6 called uncomputed (§17.4.4), and the RAM…

- **17.4.1 The two rate cards — one per store**
  - ⚠️ `#4614` is cited in this issue — and in the issue map, corrected there — as the "published rates" issue, and it is NOT one. It is a quota-cliff capture bug (`402` at 9,998/10,000). The…

- **17.4.2 The measured inputs — every per-node figure with its base AND its denominator**
  - This is the table §1's correction demands, and it is where this document has erred three times. The counts below are four different sets, and the two "resident" readings are two different…
  - | ⚠️ the superseded ~3 KB | **3,111 B ≈ 3 KB** | 140 **decimal** MB ÷ **~45,000** | decimal MB | **superseded** by the 2026-09-25 row above; ≈**3×** over the declared 1,024 B |
  - \1. `~3 KB` is not `5.6 KB` — different denominators (≈45k vs the capped 25,000), so the `~3×` headline stands. ⚠️ But even `~3 KB` is now superseded for pricing, because the 2026-09-25 bl…

- **17.4.3 The model — per tenant, per month, both stores**
  - ⛔ THE FINDING THAT MATTERS — AND IT IS NOT A FOREGONE CONCLUSION: at `f = 1` the hybrid is a STRICT NET COST. The split's saving is `(1 − f) × cache`; while the cache holds everything — w…

- **17.4.4 The blended total — closed form, with the two unmeasured inputs visible**
  - \**§6's "uncomputed blended total" now has a closed form.** Let `ratio` = the storage-line multiplier = `(rate ÷ 0.75) ÷ $0.125`:
  - \**⇒ The blended total becomes a NUMBER as soon as two magnitudes are measured, and neither exists today:**
  - ⇒ Both magnitudes are filed as `#7983` — the model is complete; two of its inputs are not. Until they are measured, §17.4 is the table the issue asked for and NOT yet a point estimate, an…

- **17.5 A field-level placement table — 9 derived/volatile property names on the sampled surface**
  - §3's per-write test IS the mechanism — "if the journal does not carry it, it is not derived — it is PRIMARY, and belongs in a truth-layer table" — and §7 moves state values onto entity fi…
  - Measured 2026-10-08 (10,000 `Point`s; 87 distinct property names on that sample — itself truncated by FalkorDB's silent 10,000-row cap): 9 of those 87 property names are DERIVED or VOLATI…
  - ✅ The canonical id contract (`#7955`). The stable key is fixed per label — `:Point` → `id` (minted by `tortoise/ids.py::ulid()`), `:Event` → `eventId`, `:Subject`/`:Object` → `name`, `:So…
  - ⛔ CORRECTION TO §17.5 AS WRITTEN — `baseline_source` is NOT unconditionally DERIVED; `baseline_set` is DERIVED, but not as a record column. When an author sets the prior (`baseline_source…

---

## §13 — the `aboutObject` headline claim is SCOPED to the files it measured (`#9399`)

The removed sentence and why it went. §13's evidence for the KEEP ruling opened
with an unqualified claim about *"the primary search path"*, but the command it
quotes as proof measures exactly three modules — and `tortoise/ranking.py`, which
holds both product ranking read paths and therefore the reads behind the default
recall door (`tortoise_recall` → `sdk_method="recall_state"`), is not one of them.

**Removed (verbatim):**

> **The primary search path does NOT read it.** `rg -n 'aboutObject'
> tortoise/retrieval.py tortoise/search_engine.py tortoise/session_reinjection.py`
> → **exit 1, zero matches**, case-insensitive. **Hindsight's premise holds for
> this path.**

The measurement was never wrong; the **scope** was unstated, so the sentence read
as a whole-product claim and a reader checking only the quoted command concluded
the opposite of what the section rules on. The design document now states the
lane the three files bound and names the module and the door outside it;
`tests/test_9399_search_path_claim_scope.py` holds it there by parsing the file
list OUT of the sentence and re-running the scan over it, so widening the list or
reverting the qualification reds.

---

### 2026-10-20 — `#9397`: §11.1 and §13 said the `aboutObject` edge carries **no** property — it carries `confidence`

**What was recorded.** §11.1 (*"Our `aboutObject` links are **property-free** … a full-repo search for a property-bearing edge finds **none**"*) and §13 (*"**no** `aboutObject` edge carries **any property** … we lose nothing semantically by deriving later"*), plus the same fact in `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 question 2 and in this ledger's `aboutObject` decision row. ⚠️ **The retraction had to reach §11.1's estimate footnote and §11.3 too**, which stated it in a third wording a reader reaches twenty lines below the correction: the cost footnote rested on *"from the measured property-free finding"*, §11.3's table called the rows *"**property-free rows**"*, and §11.3's prose said *"the entity links stay bare"*. A correction that fixes one wording and leaves three is how the same absolute gets re-derived.

**Why it was wrong, and why the method hid it.** The evidence was a search for a Cypher **property MAP** (`rg -n 'aboutObject \{'`) → 0 matches. A property written by a **`SET` clause on a relationship variable** never matches that pattern, so the search could not see the writer, and a zero from a search that cannot match the writer was recorded as a measured absence. Two shipped sites originate one; three further paths re-materialize it — and one of those three is the replay fold, `projection/entities._fold_entity_linked_reason`, so "originating" here means *writes it first*, not *writes it in the named form*: the named form is shared by both classes (two originate, one re-materializes), while a fourth suspect, `sdk.supersede_point`, does NOT write it at all: see the review-cycle correction at the end of this entry):

| site | what it writes |
|---|---|
| `tortoise/session_link.py` — `link_entity` | `MERGE (s)-[r:{edge_type}]->(t) SET r.confidence=$conf`; `edge_type` **defaults to `aboutObject`** |
| `tortoise/subject_binding.py` — `bind_point_subjects` | `SET r.confidence=$conf` on an already-present about edge; the `object` slot role binds via `aboutObject` (reached from `_extract_session_v2` on the default ingestion path) |
| `tortoise/projection/entities.py` — `_fold_entity_linked_reason` | the **replay fold** re-SETs it, so the property is journaled — `live == rebuild` |

The property is asserted by a shipped test (`tests/test_subject_binding_1370.py::test_binder_reports_already_present_and_sets_confidence`, reading `r.confidence` off a `(Point)-[:aboutObject]->(Object)` edge), so this was never an obscure branch.

**What changed.** §11.1 states the measured property set (exactly one name — `confidence` — on bound entity links only; the extractor/indexer links that are the bulk of the rows are genuinely bare, so the **cost** conclusion stands) and §13 narrows *"we lose nothing semantically by deriving later"* to *"we lose one recorded attribute"*. **The KEEP ruling and its deferral to post-launch are unchanged** — if anything the finding strengthens keeping the edge. §11.4 rule 1 is now recorded as **in tension** with `#1370` rather than as satisfied: whether a binding confidence belongs on the link or on the claim is an unrecorded design decision, flagged, not resolved here.

**What now prevents a repeat.** `tests/test_9397_about_edge_properties.py` — the retracted absolute sentences must not reappear; the corrected text must name the writers and the property; each recorded writer must still write it; and a **census over relationship-variable property writes** — the named `SET r.<prop>` form, the property-MAP `SET r += $props` form and an inline pattern map — reds if a NEW property-bearing `about*` writer appears without being recorded. ⚠️ **That promise is bounded and the bound is stated, not assumed:** an about edge is recognised by an `about*` literal or by a predicate interpolated from a vocabulary named in the census's `_ABOUT_VOCAB` (the predicate allowlist included), so a NEW predicate vocabulary has to be added there in the same change. The lesson is stated for the next reader: *re-derive a property claim from the WRITER CENSUS, not from a grep whose pattern cannot match the writer.*

**Review-cycle correction (same day) — the first census reproduced the original error one level down, and the retraction was incomplete.** Two blocking findings, both real:

- The census matched only the **named** form `SET r.<prop>`. The **property-MAP** form `SET r += $props` / `SET r = $p` was invisible to it — the same class of blindness as the `aboutObject {` grep — and three shipped sites already use it on an allowlisted `aboutObject` predicate: `cache_projection.relation_statement` (the record-layer projection; the `public.relation` row carries `confidence`), `projection._rebuild_all_replay` (rebuilding it from the journal), and `hosted_backup.restore_graph` (`SET r = $p` over the dump's stored edge properties). A fourth, `sdk.supersede_point`, was recorded here too until the next review cycle showed that its `SET r += $attrs` is on the `IMPL|NAND` leg and its about transfer is **bare** — a census FALSE POSITIVE, not a re-materialization. So "**three** shipped sites" was an incomplete enumeration at the very revision that calls it measured. The census now reads **both** forms, and its about-vocabulary includes the predicate allowlist (`validate_rel_type` / `KNOWN_REL_TYPES` / `_sanitize_label`) — because taking the predicate from the allowlist that contains `aboutObject` is exactly how a map-form writer names its edge without writing `aboutObject` anywhere. Measured against the tree: the population goes from 3 to **6** (3 originated + 3 re-materialized; a seventh candidate is a declared false positive — see below), and the property set stays **one semantic attribute** — the map-form writers re-materialize `confidence` from the record row, the journal event or the dump, and add no second attribute (the third review-cycle correction below finds the one further NAME the record projection writes).
- The retracted "property-free" wording survived in **§11.3's table row** (*"property-free rows"*) and in **§11's estimate footnote** (*"the measured property-free finding"*) — twenty lines below the corrected §11.1, where a reader would take it as current. Both now state the measured property set, and both literals are pinned as retracted.

Also corrected in the guard itself: the `§13.8 q2` retracted literal was pinned **unbolded** while the document bolds it, so it matched nothing and would have passed a verbatim revert (dead entry). And the corrected-passage pin was whole-FILE, so it could not red on one section's silent revert — it is now section-scoped (§11.1, §13, §13.8 q2), the `test_entity_attachment_brief_claims.py` precedent (`#8639` review).

**Second review-cycle correction (same day) — a THIRD write form, and a second property name.** The correction above replaced *"property-free"* with *"The property set is exactly one name — `confidence`"*, and that is still an absolute one form short. A property can be set in three forms, not two: `SET r.<prop>`, the property-MAP `SET r += $props`, and an **inline pattern map** `MERGE (s)-[r:TYPE {name:$v}]->(t)` — which appears in NO `SET` clause, so both rules in the census were blind to it. `cache_projection.relation_statement` ships it: `MERGE (src)-[r:{rel.rel_type} {idx:$idx}]->(tgt) SET r += $props` (`cache_projection.py:717` — at HEAD), over a predicate taken from `validate_rel_type`'s allowlist, which contains every `about*` predicate (`security.py:85` — at HEAD; `:77-78` at the pinned revision, where the about entries sit), on a relation row the projection builds for `EntityLinked` (`record_projection.py:732` — at HEAD; the module does not exist at the pin). So a **record-projected** about edge carries `idx` — the positional ordinal of `public.relation.idx` (`supabase/migrations/20261012000003_record_epistemic.sql:175` — at HEAD; the migration does not exist at the pin), a position and not a claim attribute — and §11.1/§13 now state the measured set as `confidence` (originated, three sites) **plus `idx`** (re-materialized, one site) with the placement-column and `props` tail named. `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 q2 no longer says *"never a second property name"*. Both retracted phrasings — *"The property set is exactly one name — `confidence`"* and *"never a second property name"* — are pinned in `tests/test_9397_about_edge_properties.py`, which now reads **all three** forms and asserts that `relation_statement` is seen writing `idx`. Its module prefilter was also fail-open: it skipped any file without the literal word "about", which is the very word the allowlist-driven map writer does not contain, so the widened vocabulary could never reach the modules it was widened for. ⚠️ **The row it added for `sdk.supersede_point` was a FALSE POSITIVE, removed the same day:** a whole-function census cannot tell which edge a `SET` lands on, and this function's `SET r += $attrs` is on the `IMPL|NAND` leg (`MATCH (old:Point {id:$old_id})-[r:IMPL|NAND]->(t)`) while its about edge is transferred by a bare `MERGE (new)-[:{rel}]->(t)` with no `SET`. On an about edge it writes nothing — it **drops** the binder's `confidence` (`#9417`, filed from this correction). The population is 6, not 7; the guard keeps the row as a declared false positive (`NOT_AN_ABOUT_WRITER`) so it stays visible without being counted, and it now compares every recorded writer's **name set** so a second property at an existing site cannot slip past. A **third** stale absolute survived in the same section: §11.1's method paragraph still said *"Two forms write to an edge, and both are in scope"* while the paragraph above it documented the inline form — it now says three forms / all three, and the discarded phrasing is pinned as retracted.

**Third review-cycle correction (same day) — two surviving wordings, one stale citation, and a census that skipped what it could not read.** Three P3s, none touching the ruling, all in the evidence layer:

- **A stale `file:line` cited as current.** §13 and §13.8 q2 said `create_about_edge` (`projection/edges.py:344`) unqualified. `:344` was the definition **at the `c79ba1cf2` pin**; at HEAD the definition is `projection/edges.py:1447`, and `:344` is a `#:` comment block that *mentions* the symbol — which is why the number read as plausible to anyone who checked it. Both copies now cite the **symbol** only, per this document set's own rule that line numbers are not asserted; §11.1's copy keeps `:344` because it carries the `at the pinned revision` qualifier, which is what makes it true. Pinned by `test_the_corrected_passages_carry_no_bare_edges_line_citation`, whose matcher is itself proved live against the pre-fix spelling.
- **The same absolute survived in §9.6, above the §11.1 correction, for a different link class.** §9.6 said identity/mention (`Object`) and referential-containment (`Source → Source`) `references` links *"stay **property-free**"* — the wording this entry retracts for `aboutObject`. It is retracted here as an **absolute**, not because those links carry a property today: no writer that MINTS an identity/mention or containment `references` link stamps anything on it (`projection/edges.py`'s non-derivation branch is a bare `MERGE`; so are `projection/entities.py`'s identity/mention connector link and `hosted_api.py`'s session→external containment; and so is `graph-scripts/backfill_references.py`, which mints BOTH classes — its docstring declares the `aboutEvent`/`aboutDocument` derivation rows beside the identity ones — with a bare `MERGE`, being an ops script outside the provenance writers, so its derivation rows are not auto-anchored either; `session_source_migration._move_out` only re-points an edge with `ON CREATE SET r2 = properties(r)`, so it copies attrs on create and never mints one). `cache_projection.relation_statement` does stamp `{idx:$idx}` + `SET r += $props`, but only for record-projected `public.relation` rows, and no record kind emits a `references` relation (`EntityLinked` is about*-only; `DirectEdgeCreated` is IMPL/NAND; `DirectEdgeRepoint` is IMPL/NAND plus the `SUPERSEDE_STRUCTURAL_RELS` transfer, which excludes `references`) — so the reached-for reason in the first version of this entry was wrong even though its conclusion was right. The FACT §9.6 establishes is narrower: those links carry **no `sourceVersion`**, and the wording now says exactly that. The retraction reaches the prose that CITES it too — the public SDK's `link_source_to_entity` docstring (`tortoise/sdk.py`, both the `"Source"`/containment paragraph and the `Object` anchor paragraph), the two `Object`-reads-`unknown` passages in `tortoise/search_engine.py::read_reference_currency_links`, and the two in `tools/source_currency.py` all said "property-free" and now scope the claim to the anchor — as does the ruling's own record in `tests/test_source_version_references_5199.py`'s module docstring (its assertions were already true: they read `r.sourceVersion`, which is the narrow fact). Pinned section-scoped by `test_the_references_paragraph_scopes_property_freedom_to_source_version` (the `RETRACTED` list is about `aboutObject` wording, so this claim needs its own leg — and the changelog thesis applies verbatim: a correction that fixes one wording and leaves three is how the same absolute gets re-derived), with the pin-index entry pinned by `test_the_pin_index_entry_for_the_9_6_retraction_states_no_measured_distance` and the six cited production sites by `test_the_retracted_references_absolute_is_gone_from_production_docstrings`.
- **The census failed OPEN on a module it could not parse.** `_writers_in_module` returned its (empty or partial) finding on `SyntaxError`, so a module `ast.parse` rejects was censused as *"writes no property on any about edge"* — mechanically the `rg 'aboutObject {'` defect, one level down — and the promise's stated bound (a list of vocabulary NAMES) did not cover it, so a reader was told the only gap was a token nobody had written yet. It now raises `UnparsableModule` naming the file, and the strict per-module read (`test_the_code_still_agrees_with_the_corrected_evidence`, leg 7; `test_the_declared_false_positive_premises_hold_at_the_statement`, leg 9) is UTF-8 like the census's (it read `errors="replace"`, so a byte-compromised module would have been censused as different text than the leg that parses it). Pinned by `test_the_census_fails_closed_on_a_module_it_cannot_parse`, red on the pre-fix `return found`.

⚠️ **The census also read one FALSE POSITIVE as a writer of the map form.** `projection._rebuild_all_replay` was recorded as a re-materializer alongside `cache_projection.relation_statement` and `hosted_backup.restore_graph`; it is not. Its `SET r += $attrs` belongs to the `DirectEdgeCreated` fold, and that event has exactly one producer — `create_direct_edge`, which refuses any `op_type` outside `{IMPL, NAND}` — while its about/structural transfer is a separate BARE `MERGE` (the code says so in place: *"Structural 2b transfers are bare: NO attr SET, no direction/confidence/weight"*), and `create_about_edge` journals no `DirectEdgeCreated` at all, so no about edge can reach the attr fold. **The measured population is therefore 5 writers — 3 in the named form (`SET r.<prop>`) and 2 by property MAP, which is the axis the census reads; on the causal axis they split 2 originating + 3 re-materializing** — with TWO declared false positives (`sdk.supersede_point` and `projection._rebuild_all_replay`). Each exemption now pins the STATEMENT that makes it one — the about transfer carries no `SET`, and the `SET` the census does see names no `about*` predicate — so an exemption cannot rot into a silent lie, and §13 states the count as 5 (`three of the five`, not `three of the six`).

**Fourth review-cycle correction (same day) — the guard's own provenance claim, and its false-positive exemption.** Three fixes in `tests/test_9397_about_edge_properties.py`, all in the guard rather than the corrected text: (1) *"Two forms write to an edge, and both are in scope"* was **written by an earlier revision of this correction and then withdrawn**, not carried over from the pre-fix document — measured absent at the pre-fix revision — so it is now declared in `RETRACTED_INTRODUCED_HERE`, and a new leg MEASURES the split against the pre-fix revision instead of restating it (a misclassified entry made the guard's own failure message claim a pre-fix presence that never existed). (2) The `sdk.supersede_point` row in `NOT_AN_ABOUT_WRITER` was a **fail-open exemption**: both name-comparison legs skipped it, so a property added on that about edge redded nothing while §13 and §13.8 q2 assert it carries none. The row now declares its expected name set (`<map:$props>`) and is compared like a recorded writer, and the premise is asserted **at the statement** — the about transfer is a bare `MERGE` and the `SET r += $attrs` sits in a query whose patterns name no `about*` predicate. (3) The failure diagnostic now prints the live document a retracted sentence actually survived in, not only its recorded pre-fix home. §13's `create_about_edge` citation was reduced to the SYMBOL only — §11.1's copy is the one that keeps `:344` under its `at the pinned revision` qualifier, so there the number reads as a pin rather than as drift. ⚠️ This clause first claimed §13's copy was ALSO *qualified*: it is not (measured at HEAD — `grep -n 344` finds `:344` only in §11.1), which is the third correction's state, not this one's.

**Fifth review-cycle correction (same day) — one count, three stated versions of it; and the census is blind to a shipped sixth site.** Two P1s and a count defect, all in the evidence layer. The KEEP ruling, its deferral and the *one semantic attribute + one identity coordinate* finding are unchanged.

- **The entry stated the population three times and no test compared any of them.** *"the population goes from 3 to **6**"* (second correction), *"The population is 6, not 7"* (second correction's tail), and *"3 originating + 2 re-materializing = 5 writers"* (§13 and this ledger). Each was right when written — the 6 became 5 when `projection._rebuild_all_replay` was reclassified as a false positive — but a reader arriving at any single sentence cannot tell which is current, and deleting the current one changes no test. **The count is now stated in exactly one form in every document: 5 census-VISIBLE writers — 3 in the named write form and 2 by property MAP, which is the axis the census reads; on the causal axis those same 5 split 2 originating + 3 re-materializing, because the replay fold re-produces the property rather than originating it (round 10) — with two declared false positives, plus one known-INVISIBLE shipped site named below.** The earlier numbers in this entry stay as the revision history that produced that sentence; they are not current claims.
- **The census cannot see a shipped sixth site, and the documents now say so instead of implying there is none.** `session_source_migration._move_in` moves an edge with `MERGE ({src})-[r2:`{rel}`]->(new) ON CREATE SET r2 = properties(r)` — it copies the WHOLE property map — over a predicate handed in from `MOVABLE_SOURCE_RELS`, which contains `aboutSource` and `aboutDocument`. ⚠️ **The first version of this correction stated the bound as "no writer that SPELLS an about vocabulary … can add a property unseen", and that was itself false — measured three escape shapes against `_writers_in_module`, not one:** (1) the predicate as a PARAMETER (`_move_in`; adding `MOVABLE_SOURCE_RELS` to `_ABOUT_VOCAB` still yields `{}` for that module); (2) an UNTYPED binding tested with `WHERE type(r) = 'about…'` — `_PATTERN` needs a typed relationship pattern, so the function is continued before any rule runs (measured `{}`; already live style in `sdk.py`/`analyze.py` as `WHERE type(r) = 'NAND'`); (3) an fn-call RHS `SET r = properties(x)` **even with the about predicate spelled in a typed pattern** (measured `{}`, while the same writer with `SET r = $p` is recorded) — so the parameter is NOT what makes `_move_in` invisible. **§11.1, §13 and the guard's module docstring now state the bound the CODE enforces** (typed pattern or interpolated from `_ABOUT_VOCAB`, AND one of `SET var.<prop>` / `SET var (+=|=) $map/{…}` / inline pattern map), §11.1's method paragraph **still opened with "Three forms write a property to an edge, and all three are in scope" — an absolute this same entry contradicts — so that sentence is now rewritten** (it says the census *reads* three write forms and then states the gap list), and the old wording is pinned as retracted, and `test_the_census_bound_is_pinned_as_a_gap_not_an_absence` pins both remaining synthetic shapes plus a control so the bound cannot drift back into an absence claim **in the code** — the PROSE is pinned separately by `test_the_documents_state_the_bound_as_a_detector_not_a_proof`, because measured without it, deleting the bound paragraphs or inserting a fresh absolute left all 16 legs green.
- **The provenance leg could never run in CI, and its comment claimed the opposite.** `PRE_FIX_REV` was justified with *"CI checks out with `fetch-depth: 0`, so the rev is reachable there"*. Measured: the gating `test` job (`python-ci.yml:631`) is a bare `actions/checkout@v4` (default `fetch-depth: 1`); the only `fetch-depth: 0` is the `changes` job at `:224`. So `git show` returned nothing on every CI run and leg 8 SKIPPED — silent-green, since `skip-guard.py` guards only FalkorDB/embedder/module-collection reasons. ⛔ **The obvious fix does not work, and this is measured, not assumed:** in a `--depth 1` clone, `git fetch --depth=1 origin 01b5486ec` → *"couldn't find remote ref"* (the server does not expose arbitrary reachable shas), so fetching the raw sha would have been a second false claim. Fetching the `main` REF at a bounded depth does work — `git fetch --depth=60 origin main` makes `01b5486ec` reachable in the same clone. `_ensure_commit` now does exactly that, raising the depth only until the rev resolves (`_SHALLOW_DEPTHS = (120, 400, 1500)`), so the leg actually runs in CI; the remaining skip states that this checkout could not supply the history and that it never passes on a history it could not read.
- **Three `cache_projection.py:717` — at HEAD citations were added unqualified**, while both documents declare (`STORAGE-ARCHITECTURE.md:18`, `EXTRACTOR-V4-ARCHITECTURE.md:16`) that every `file.py:NNNN` reference is pinned to `c79ba1cf2` — and `tortoise/cache_projection.py` does not exist at that commit (measured: `git show c79ba1cf2:tortoise/cache_projection.py` → *"exists on disk, but not in 'c79ba1cf2'"*). All three now carry an explicit `at HEAD` qualifier. The companion guard's matcher (`_EDGES_LINE_CITE`) covers only `edges.py:<n>`, so it does not enforce this for other modules; stated here rather than left implied.
- **The selection rationale in `config/ci-surfaces.yml` named the wrong mechanism.** It claimed a `docs/`-only diff *"takes `select()`'s early return and runs `tier1` only"*. Measured with the committed manifest: `select(['docs/architecture/STORAGE-ARCHITECTURE.md'], 'pull_request', manifest)` → `surfaces=['onboarding'], full=False, 121 files` (the `onboarding` SOURCE_PATTERNS entry claims that path, so the early return never fires; the count is re-measured **at this head**, and it is one higher than the reading this bullet first recorded because the change added a `tier1` row and `tier1` is unconditional in the tier-2 pool — re-run `--changed-files` after any `tier1` addition rather than carrying either number forward); `select(['docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md'], …)` → `surfaces=[]` and DOES take it. The guard is selected in both cases (`guard_selected=True`, measured at this head with `tools/ci_selection.py`), which is the property the entry exists to record; the comment now states the measured path instead of the assumed one.
- **The corrected-passage pin covers 2 of the 5 recorded writers.** `test_the_corrected_passages_name_the_property_bearing_class` requires only `link_entity` and `bind_point_subjects` per section, so the other three can vanish from §11.1 / §13 / §13.8 q2 with every leg green (measured: deleting `hosted_backup.restore_graph` and the *"3 originating + 2 re-materializing = 5 writers"* sentence from §13 → 14 passed, 1 skipped). ⚠️ Left as a stated gap rather than fixed here: the pin iterates the retracted literals properly but not the corrected writer set, and widening it to all five symbols is a guard change that deserves its own leg and its own review rather than a sixth pass inside this correction.

**Sixth review-cycle correction (same day) — the completeness claim was falsified a third time, so it is gone.** Round 5 replaced one absolute with a "bound the code enforces"; round 6 measured **four more escapes inside the conjunction that bound required**, and the sentence it was pinned by did not pin it at all. The resolution is structural rather than a fifth wording:

- **The census is now described as a DETECTOR, not a PROOF of absence** — in §11.1, §13 and the guard's module docstring — and the gap list is explicitly NON-EXHAUSTIVE. The escapes measured in round 6, each `{}` against `_writers_in_module` with a recorded control: whitespace before the relationship-type colon (`[r :aboutObject]`), whitespace after `[` (`[ r:aboutObject]`), a `]` inside an inline map (`{tags:["a"],confidence:$c}` truncates `_PATTERN`'s `[^\]]{0,120}` span), and an `aboutClaim` edge — because `_ABOUT_REL` enumerates seven names, `about(Subject|Object|Event|Point|Document|Action|Source)`, while the guard's own comment claimed the `about*` prefix. That comment now names the enumerated set and says an `aboutClaim` edge is unseen.
- **The census's FILE SCOPE was undeclared while the documents presented it as the replacement for a full-repo grep.** The walk is `PKG.rglob("*.py")` over `tortoise/`; the retracted evidence was a *full-repo* search, and `tools/longmem_eval/` and `graph-scripts/` already hold `aboutObject`/`aboutSubject` writes (none currently property-bearing). The scope is now stated next to the other gaps.
- **The pin did not pin the bound it was named for.** Measured: deleting both bound paragraphs from §11.1/§13, inserting a fresh absolute into §13, replacing the module docstring with an absolute, or emptying `__doc__` all left **16 passed**. `test_the_documents_state_the_bound_as_a_detector_not_a_proof` now asserts the detector wording is present in `__doc__` and, SECTION-scoped, in each of §11.1 and §13, that the retracted absolutes are absent, and it was mutation-checked: replacing the marker and restoring the old sentence reds it, and restoring the document greens it. A third `{}` probe covers the parameterised-predicate shape (`_move_in`), which had been described in prose but never measured by a test.
- **Two provenance statements in this entry were false and are corrected**: it said §11.1's method paragraph *no longer* opened with "all three forms … are in scope" while that sentence was still there verbatim (it is now genuinely rewritten and the old wording is pinned as retracted), and it said §13 / **§13.8 q2** state the count as 5 when §13.8 q2 states no count at all (`grep` → 0 for `five`), so only §13 does.

The lesson this entry keeps re-learning, now recorded in the guard itself: **a guard that cannot be shown to red is not evidence.** Each of the three completeness claims here was plausible, internally consistent, and false; the only thing that caught them was measuring the claim against the code and mutating the pin to watch it fail.

**Seventh review-cycle correction (same day, a different claim from the sixth) — the one rule this correction rewrote in place was pinned by nothing.** One P2 from the `#9403` review, plus the weaker half of the same gap. No factual claim moved: the ruling, the measured property set, the five-writer population and its bound are unchanged.

- **§11.4 rule 1's own imperative was unpinned, so a verbatim revert of a sentence this PR changed stayed green.** The rule was rewritten from *"**Keep entity links property-free.**"* to *"Keep entity links near-bare"* plus the recorded `#1370` tension, and it was in **neither** place a guard reaches: not in the retracted list (so a revert to the absolute could not red), and not in the corrected-passage pins (§11.1, §13, §13.8 q2), so a **silent deletion of the tension record** — the only place the ruling's exception is written down as unresolved — was green too. **Measured, not inferred:** restoring the pre-fix sentence verbatim at the PR head left every leg green. This is the sharpest instance of the class the entry above describes, because it is not a carried-over wording but a sentence this PR itself changed. The pre-fix imperative is now a `RETRACTED` entry (quoted here, where the retracted text is allowed to live), and `test_the_cheapness_rule_states_the_measured_set_and_records_the_tension` pins §11.4 rule 1 **section-scoped to the rule**, positively: it must say `near-bare`, it must carry the `#1370` tension journaled through `EntityLinked`, and it must keep the `RECORDED, not resolved` qualifier — a guard must not let prose settle a design decision by dropping the words that mark it open.
- **The extractor ledger's KEEP row named one of the two measured properties.** *"Cheap because edges are bare apart from the binder's `confidence`"* is true of the ORIGINATED set and false as the reason the layer is cheap: a record-projected `aboutObject` edge also carries `idx`, in the third write form. The row now names both, and `test_the_ledger_keep_row_names_both_recorded_properties` pins it — the row's retracted wording was already pinned, so a revert to THAT red, but any other re-derivation of the one-name absolute in the live ledger was not.

**Eighth review-cycle correction (same day, a second pass on the seventh) — the pin's own claim, and two probes that were asserted but never written.** Round 7 mutation-tested the pin instead of reading it, and found the same defect one level up again, plus a stale count and two unbacked measurements.

- **The documents claimed a red the guard cannot produce.** §11.1 said the guard *"pins both the shapes it reads and the gap list above"*, §13 said *"the guard pins it"*, and the module docstring said the probes stop the bound *"drifting back to an absolute"*. Measured: deleting the entire enumerated gap list from both paragraphs left **16 passed, 1 skipped**, and inserting a freshly-worded completeness absolute into §11.1 — *"This census enumerates every way an about edge can acquire a property, so no property can be added unseen"* — also stayed green. **The CLAIMS are deleted, not re-pinned.** The text now says only what was measured: the gap list is what was measured and is not guaranteed complete, and no test is claimed to block a future absolute, because none can. This is the entry's own lesson applied to itself — a process claim the instrument cannot back is the thing being corrected, so it does not get re-narrated.
- **The marker assertion was a file-wide `count() >= 2` presented as section-scoped**, so deleting §11.1's copy while §13 kept two occurrences stayed green — the same scoping defect `_section`'s own docstring names (`#8639`). It now slices §11.1 and §13 via `CORRECTED_SECTIONS`; mutation-checked individually, deleting either section's copy alone reds it.
- **Two gap categories claimed "measured `{}` with a control recorded" when neither probe existed.** Gaps 4 (spellings `_PATTERN` does not recognise: whitespace before the relationship-type colon, whitespace after `[`, a `]` inside an inline map) and 5 (an `aboutClaim` edge, outside `_ABOUT_REL`'s seven enumerated names) were prose only — `grep` found no probe, no control, no fixture. Both now have probes in `test_the_census_bound_is_pinned_as_a_gap_not_an_absence`, so the sentence describes the file rather than the reviewer's terminal.
- **A fourth stale count, and a numbering contradiction.** Three places still said the leg pins "two synthetic shapes" while it asserted more, and the probe comments were numbered `1+n / 2 / 3` against the docstring's gap list `1 / 2 / 3`, so a maintainer mapping a probe to a documented gap got the wrong pairing. The comments now use the docstring's numbering, and **no probe COUNT is stated anywhere** — a count in prose is a claim the code outgrows silently, which is exactly how this defect kept returning. Gap 6 (writers outside `tortoise/`) is stated as unprobeable from inside the walk rather than counted among the pinned ones.
**Ninth review-cycle correction (same day, a different claim from the eighth) — the remaining unmeasured claims and three fail-open cells in the new legs.** Fresh-context review of the seventh correction found the pinning pattern repeating its own defect one level up. No ruling, no measured property name and no census population changed; what changed is that the text and the legs now state only what was measured.

- **§11.4 rule 1 still carried the retracted absolute at POPULATION scale, in a spelling no pin knew.** The rule was rewritten to `near-bare` but kept *"turns 27,310 **bare rows** into 27,310 rows you must maintain and index"* — which asserts the whole population is bare, forty lines after §11.1 says a record-projected edge is not. Measured: the leg was green with that clause live, and its only absence check was the retracted imperative. The clause now says the rows are *bare apart from the projection's `idx` ordinal*, and the leg carries a population-absolute list (`bare rows`, `carry no properties`, `property free`) — mutation-checked: restoring the clause, or inserting a freshly worded in-rule absolute, now reds.
- **§11.1's *"the extractor/indexer links that are the bulk of the rows are genuinely bare"* was an unmeasured population claim, and the document contradicted it.** The binder ORIGINATES `confidence` on every object link it decides BOUND, and it runs on the default ingestion path (§13's own sentence) — while `docs_miner`'s `link_entity` calls pass none, and `sdk._connect_issue_objects` does not call `link_entity` at all (it calls `create_about_edge`, a bare `MERGE`). Neither the share nor the absolute was measured. The clause now states the shape (bare = unbound; the binder's bound links carry one) and says the SHARE is not measured here, so the cost estimate rests on the row shape, not on a bare-row count.
- **A closure the same sentence calls open.** §13 said *"The measured set is therefore `confidence` … plus `idx`"* after acknowledging the record row's placement columns and open-set `props` tail; the §11.1 estimate footnote and `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 q2 (`the one name beyond confidence`) closed it the same way, and the ledger's KEEP row presented the two names as the whole exception. All four now say the NAMED set, with the map writers' names stated as not statically bounded — matching what the guard can actually see (`EXPECTED_NAMES[relation_statement]` is a `<map:$props>` sentinel for exactly that reason). The KEEP-row leg also asserts the open-set qualifier, so a closed reading reds.
- **Two counts in the guard's own prose were stale.** The module docstring said *"four shipped sites already use that form on an allowlisted `aboutObject` predicate"* — two do, and two more are the declared false positives (the population is 5 writers, not 7); `_ABOUT_VOCAB`'s comment said the same thing as *"3 writers → exactly 7"*, which conflates ROWS with WRITERS. Both corrected; §11.4 rule 1 also called the `#1370` attribute a *"weight"* where the code sets `confidence` (and no about-edge writer writes `weight`).
- **Three fail-open cells closed, and one diagnostic fixed.** `_flat` — the normaliser every retracted literal is matched through — treated a hyphen as significant, so *"property free"* (the form a person types) matched nothing: measured green when §11.1's lead-in was rewritten that way. It now normalises a hyphen to a space. The §11.4 leg pinned only the qualifier vocabulary while its name claimed the measured set — it now asserts both property names. The ledger-row leg's docstring claimed it closed *"any other route back to the one-name absolute"*, which was false; it now states exactly what it closes (a deleted name, the retracted phrase, four closed-count spellings) and says the rest is a detector's residual. `_section` also raised a bare `ValueError` when an anchor moved, which reads as a broken test rather than a rotted guarantee; it now fails with the anchor named.
- **A false record of a past fix.** The fourth correction said §13's `create_about_edge` citation *"is also qualified (`:344` at the pinned revision) the way §11.1's copy already was"*. Measured at HEAD, `:344` appears only in §11.1 (§13 asserts the symbol only, which is the THIRD correction's state). The clause is corrected in place rather than left to read as the latest record of what shipped.

**Tenth review-cycle correction (a fresh-context review of `#9403`'s head, P1 × 2 and P2 × 5) — two unmeasured population claims written INTO the correction, a misclassified writer, and four claims that could not go red.** No ruling, no writer and no measured property name changed; what changed is that the corrected text states only what was measured, and every sentence this round touched now has a leg that reds without it.

- **§13 and §13.8 q2 stated an unmeasured population SHARE, in the same commit whose §11.1 withdraws it.** §13 said *"so most entity links are two ids and nothing else"* and §13.8 q2 said *"The structural door stays open **for most links**"*, while §11.1 says in as many words that *"Which SHARE of the rows is bare is not measured here"* — so two live documents disagreed about whether a majority-share fact had been measured. Measured while all three sentences were live: every leg green. Both now state the SHAPE (an **unbound** link is two ids), and `test_the_corrected_passages_name_the_property_bearing_class` rejects the share spellings (`most entity links`, `most links`, `mostly stays open`, `bulk of the rows` …) in §11.1, §13 and §13.8 q2. Mutation-checked: restoring either sentence reds.
- **§11.4 rule 1 still asserted the population absolute in the round-9 spelling.** Round 9 replaced *"bare rows"* with *"rows that are bare apart from the projection's `idx` ordinal"* — which asserts the whole population is bare except one ordinal, ten lines before the same rule records that a **bound** link carries `confidence`. Green while live, because the pin's list could not see the new spelling. The clause now names the row's shape (two ids; plus the binder's `confidence` on a bound link and the projection's `idx` ordinal on a record-projected edge), and `bare apart from` is in the leg's population-absolute list. Mutation-checked: restoring the round-9 clause reds.
- **The writer classification conflated the causal axis with the write-form axis, so the count's composition was false.** `projection/entities._fold_entity_linked_reason` was filed as an **originating** writer, while §13.8 q2 in the same PR calls it *"reproduced by … the replay fold"* — the definition this document uses to classify `cache_projection.relation_statement` and `hosted_backup.restore_graph` as RE-MATERIALIZING. §13's *"The ORIGINATED property set is exactly one name — `confidence` — and it is written in two forms"* compounded it: every originating writer uses the NAMED form, and the map form belongs to the re-materializing class in the next sentence. The split is now stated on BOTH axes — 5 census-visible writers = 3 named-form + 2 property-MAP (what the census reads), and on the causal axis 2 originating + 3 re-materializing — and `test_the_writer_classification_separates_the_causal_axis_from_the_write_form` pins it plus the retracted form count. Mutation-checked: refiling the fold as an originator, restoring the two-form count, or reverting the composition reds.
- **The guard's own provenance comment carried a stale leg count, one paragraph below the rule that forbids it.** It said *"9 failed / 10 passed of the 19 legs at this revision"*; the file collected 21 legs and the pre-fix split was 10 failed / 11 passed. This is the round-8 failure mode (*"a count in prose is a claim the code outgrows silently"*) reproduced in the comment that declares it eliminated. The comment now says how to RE-MEASURE the split instead of quoting one, and `test_the_guard_states_its_own_execution_and_probe_coverage_honestly` reds on a numeric leg count anywhere in the header block. Mutation-checked: restoring the sentence reds.
- **The extractor ledger's KEEP row did not RENDER.** It carried 2 opening and 3 closing parentheses and an odd count of `**` markers, so its emphasis span broke and a stray `)` appeared mid-sentence — in the row a reader uses to decide the layer is cheap, and past every substring assertion the KEEP-row leg made (markdownlint's emphasis checks do not run inside a table cell). Balanced now, and the leg asserts paren and `**` balance. Mutation-checked: unbalanced again reds.
- **Three citations in this entry were unqualified, and one is false at the declared pin.** The entry's own round-5 rule is that a number not true at the pin carries `at HEAD`; `security.py:85`, `record_projection.py:732` and `20261012000003_record_epistemic.sql:175` were bare, and at `c79ba1cf2` the first lands on `"INFORMED_BY", "PRODUCES"` (the about allowlist is at `:77-78`) while the other two do not exist. All three are qualified, and `test_the_correction_changelog_qualifies_its_line_citations` applies the rule to the paragraph that makes the citations. Mutation-checked: dropping one qualifier reds — the first version of that leg was VACUOUS, because a flat character window let a neighbour's `— at HEAD` qualify a bare citation beside it; the window now stops at the next citation.
- **A CI fix, not a review finding.** The required `docs` check failed on a NEW markdownlint finding this PR introduced: the `§13 — the aboutObject headline claim is SCOPED…` heading was pressed against the preceding `---` with no blank line (MD022). One blank line added; the finding was authored here, so the baseline snapshot was not regenerated (`#7435` / `#7534`).
- **Merged from a concurrent lane on this branch, and reconciled rather than stacked.** That lane re-measured the provenance reading (`10 failed / 11 passed`, obtained by swapping ALL FOUR live documents for their `01b5486ec` versions — a PARTIAL swap measures something else), and corrected §11.1's claim that `sdk._connect_issue_objects` calls `link_entity`: it does not, it calls `create_about_edge` (`sdk.py:20193` at the pinned revision), which emits the bare `MERGE`. Both are kept. The COUNT is not: two legs landed in the same merge, so the number was stale on arrival — which is exactly the argument for the instruction (the swap) over the number, and why the reading now lives here, in the ledger, where it is history rather than a claim about the current file.

**Eleventh review-cycle correction (fresh-context review of the tenth round's own fixes, P1 × 2 and P2 × 6) — the closed pair survived in the cost argument, an exemption checked one of its two statements, and this entry's own citation rule was broken by three of its citations.** No ruling and no measured property name changed.

- **The two-name enumeration survived in the three passages a reader uses to DECIDE about the migration.** §11.3's table row, §11.3's prose and §11.4 rule 1 all said "two ids, plus the binder's `confidence` on a *bound* link and the projection's `idx` ordinal" — a CLOSED pair — while §11.1's own footnote says the record-map writers' further attributes are an open set and §13 says the same. A record-projected about edge carries the row's placement columns (`RelationUpsert.attrs`: direction, confidence, weight, label, batch_id, source_version — `tortoise/record_projection.py`) and the open `props` tail beside `idx`. All three now state the open set, and §11.4's leg requires `placement columns`, `props` and `open set` in the rule.
- **`_rebuild_all_replay`'s exemption was checked against ONE of its two `SET r += $attrs` statements.** `_query_call_containing` returned the FIRST match, so the structural-repoint fold (`projection/__init__.py:15782`) — the leg's actual subject, since `#9417` would touch exactly that shape — was never inspected, and a property reaching an about edge there stayed green. The helper is now `_query_calls_containing` (plural, EVERY match, each asserted), plus the exemption's second premise is pinned: the statement is gated on a fixed attribute vocabulary (`if k in (…)` / `if attrs:`), so a structural descriptor carrying none of those keys writes nothing on the edge its interpolated `etype` names. Mutation-checked: an `aboutObject` predicate in the SECOND statement reds.
- **The ledger's KEEP row counted "two recorded properties" and conceded the open set in the same sentence.** Two names plus an open set is not two properties. The count is gone, and `two recorded properties` / `exactly two properties` join the leg's rejected spellings.
- **Three citations were presented as current while false at the declared pin, and the pin could not see one of them.** The `record_epistemic.sql:175` citation appears twice in §11.3/§13 and `cache_projection.py:717` once in this entry, all bare; at `c79ba1cf2` that migration and `cache_projection`'s current line are not what the numbers say, and in one case the file does not exist. The qualifier window also ran PAST the paragraph break, so a later bullet's "— at HEAD" qualified a bare citation in an earlier line. All three carry `at HEAD`, and `_unqualified_line_citations` now bounds its window at the newline as well as at the next citation.
- **§11.4 rule 1 claimed an ABSENCE its instrument cannot answer.** "no about-edge writer in the census writes `weight`" is unanswerable for the two property-MAP writers, which the census records only as the `<map:$props>` sentinel — the exact defect this PR exists to stop (a zero from a pattern that cannot match the writer). Scoped: no NAMED-form writer writes `weight`; the map-form writers copy the record row's whole attribute set, and `public.relation` HAS a `weight` column, so a `weight` on a record-projected about edge is not excluded.
- **§13's one-line summary re-conflated the axes.** "`confidence` (originated, three sites)" — only TWO sites originate it; the third writes it in the named form and re-materializes it. The summary now says so, and the classification leg rejects the phrase.
- **Change narration in the live design documents.** "the earlier *exactly one name* reading…", "an earlier revision of this correction counted it", "every completeness claim written here has been falsified by a shape the next reviewer measured" — process history, which the 2026-10-15 design-language pass removed from these documents and which this entry is the home for. Removed from §11.1/§13; the statements they carried survive here and in the round records above.
- **A pre-existing defect filed, not silently carried** (this PR's scope guard says the product code is untouched): four comments in `tortoise/projection/edges.py` still assert the retracted "`property-free`" absolute, one of them cross-referencing §9.6 — which now says the opposite. Filed as `#9450`.

**Twelfth review-cycle correction (fresh-context review of the eleventh round's fixes, P3 × 2) — the exemption was documented as one fold when the code carries two, and the guard's own pin list was one entry short.** No ruling, no writer and no measured property name changed; what changed is that the design documents now name the protection that actually carries the second `SET`, and that the file's prose inventory is enforced against the file.

- **`_rebuild_all_replay`'s exemption was documented by the WRONG fold, in both design documents.** §13 said the replay's `SET r += $attrs` "belongs to the `DirectEdgeCreated` fold, whose only producer (`create_direct_edge`) refuses any `op_type` outside `{IMPL, NAND}`", and §13.8 q2 said "**each** of whose `SET r += $attrs` is on an `IMPL|NAND` leg". The function carries **two** such statements (`projection/__init__.py:15437` and `:15785` at HEAD): the `DirectEdgeCreated` fold, and the structural-`DirectEdgeRepoint` fold — whose `etype` is validated by `validate_rel_type` and therefore **can be `aboutObject`**, since `supersede_point` emits `DirectEdgeRepoint` over `SUPERSEDE_STRUCTURAL_RELS`. The operative protection in the repoint fold is the empty-`attrs` gate over a fixed attribute vocabulary (structural descriptors carry none of `direction`/`confidence`/`weight`/`label`/`batch_id`), **not** the producer restriction, which does not reach it. `DirectEdgeRepoint` appeared in **neither** document (`grep -n DirectEdgeRepoint docs/architecture/*.md` → 0 at the eleventh head), so the fold `#9417` would change was the one the documents did not describe. Both now name both folds and both protections, and the exemption leg pins the two-fold mechanism section-scoped (§13, §13.8 q2). Mutation-checked: restoring the retracted single-fold sentence at §13 reds.
- **The exemption's SECOND premise was asserted function-wide, so it was satisfied by the other fold's gate.** `re.search(r"if k in \(|if attrs:", body)` over the whole function passed on the creation fold's `if attrs:` and said nothing about the repoint fold — the same "function-wide text union" leg 9's own docstring rejects for the about-literal half, one level down. It is now `_setter_gate_premises`: each `SET` statement is walked to its OWN enclosing `if` and to the assignment that builds the map it writes, and the map must be built from a fixed literal attribute vocabulary (a ceiling, `FALSE_POSITIVE_ATTR_VOCAB`). A constant gate (`if True:`) is rejected explicitly — a gate that can never be false is not a gate — and the leg carries a synthetic two-fold probe in which only the first fold is gated, asserting the rule reds there and passes when both are. Mutation-checked: `if True:` on the repoint fold reds (the function-wide search this replaced stayed green).
- **The guard's `WHAT IS PINNED` list claimed "one entry per test, in file order" and was one entry short and out of order.** It listed 24 entries for 25 tests, and the omitted leg (`test_the_unmeasured_share_retraction_is_pinned`) is the **2nd** in the file, so every later entry described the wrong test — and the last entry described the 7th. The merge is where it went stale: two parents carried 24 tests each but *different* sets, and the resolution unioned them without re-deriving either count. The list is now one entry per test **in file order**, and its dedicated leg (`test_the_pin_index_names_every_leg`) re-derives the coverage, the uniqueness AND the order from `def test_` instead of trusting the prose — the same class round 8 declared eliminated, in the artifact that declares it. Mutation-checked: deleting an entry, duplicating one, or reordering one reds.
- **A CI-configuration count, corrected by measurement, and a duplicate row removed.** The `durations` row's tail still said "over ALL **24** tests"; the merge union made it 25, and this round's added leg makes 26 — and the merge left the row TWICE (both lanes had edited it), so the surviving row is single, states 26, and retires the restated seconds in favour of how to re-derive them. The same cycle removed a 19.5 s O(n²) in the exemption leg (it called `ast.get_source_segment` for every call in a 16k-line module; the line index is now built once), which is the reading's real cause and not a property of the assertions.

---

## The capture-path write order — the invariant §9.6 states, now pinned (`#8501`)

**What §9.6 says, and what the code did.** §9.6 records that the capture owns a
session `:Source`'s version. The code did not: `capture_session` (and hosted
`_capture_session_impl`) ran extraction — which creates the Points and MERGEs
their `extractedFrom` links — **before** `_materialize_session_source`, the one
writer that SETs `contentHash`. So `resolve_source_versions` read an empty hash
**at link time**, `$v` bound nothing, and `_anchor_on_create` wrote
`sourceVersion = NULL` on every create-path link. Measured on the pre-fix tree:
`references` 1/1 anchored, `extractedFrom` 0/1. The consequence is not cosmetic —
a revised transcript could never mark the claims it had superseded, because no
claim recorded which version it was read from.

**The invariant, stated so the next lane does not have to re-derive it.** *A
capture must write its session Source's version BEFORE anything reads it at link
time.* That is what §9.6 already assumed; it was true in prose and false in code,
and nothing compared the two.

**What changed.** Both lanes move the MINT + MATERIALIZE half above extraction,
inside the **same gate** each lane already used (`not session_existed or
(retry_failed_capture and not no_provider)` / `… and not store_only`), and the
provenance stamp stays after extraction. Three consequences were handled
deliberately rather than discovered later:

- **The materializer can now CREATE the stub** (nothing created it yet), so it
  mints `_link_source`'s create-time shape through the replay-shared
  `_mint_source_stub` and upgrades it — and the `SourceCreated` record is
  emitted before the link, so the raw `SET` still has a journal carrier (§17.1).
- **`_mint_source_stub(now=…)` carries the CAPTURE's instant**, not a fresh clock
  read: for a keyless capture this mint is the only writer of `ingestedAt`, and
  its `SourceCreated` record carries `now`. A fresh read would put a later clock
  on the live node than the replay folds in — a live≠replay divergence on a path
  that previously had none.
- **A KEYLESS capture's `:Source` gains four properties** (`title`,
  `canonicalUrl`, `urlAliases`, `is_episodic`). Pre-fix that lane created the node
  with only its `SET` properties, because it never reached `_link_source`. The
  new shape is the stub shape the extracting lane has always had (and
  `is_episodic` is the `#1486` discriminator), but it IS a behaviour change on
  that lane, and it is pinned rather than disclosed-away.

**What now prevents a repeat.** `tests/test_8501_capture_path_anchor.py` — the
anchor must be non-NULL, equal to the Source's stored `contentHash`, and present
in the Point's own journaled `sourceVersionTransit`; it must survive a
destructive `rebuild_all()`; a re-capture must neither advance nor drop it; the
keyless lane's full key set is pinned; and the pre-fix Source shape is pinned
against the literal set. Registered on the `core` surface with a `durations:`
weight, because a guard no CI leg selects is a guard that does not run.

## §13's `aboutObject` read-site table — the fourth correction, and the end of the hand audit (2026-10-20, `#7948`)

The design document's §13 table is the **whole evidence base** for the KEEP ruling on
`aboutObject` (owner, 2026-09-23), and the document says so itself: *"⚠️ The count is the whole
argument — the table must name every customer-callable surface"*. It has now been corrected
**four** times, each by hand, each finding a different part of it wrong:

1. the issue body (`#7948`) — the first eleven-site envelope;
2. review finding `B2.7` (`REVIEW-CONSOLIDATED-2026-09-23.md`) — three further readers, two of
   them under-classed as non-customer-callable;
3. the `#7956` erratum on `B2.7` — that finding was itself incomplete (the customer-callable
   count was four, not two);
4. **this one** — two rows cited symbols that exist but **do not read the edge**, one row named
   no symbol at all, and two readers were absent entirely.

What moved, and why it is the last hand pass: the table is now **executable**
(`tests/test_7948_about_object_read_sites.py`). It derives the census from the source's AST and reds
when a reader under `tortoise/` is undeclared or a declared symbol does not perform the read, so
the fifth correction is a red test rather than a fifth audit.

- **Row 8 cited `collect_slices`** — which orchestrates the connected-assembly read and never
  traverses the edge. The reads are in **`spine_rows`** (Object→Points and Object→Events, both inside
  `docker_walker_port`) — the symbol is what the arm resolves; no line number is offered here, per the
  rule stated below. A re-verifier following the row found no
  traversal and could only conclude the row was wrong.
- **Row 9 cited `build_subgraph`** — the Cypher is the module constants `_CYPHER_ABOUT` /
  `_CYPHER_SIBLINGS`, executed by **`_collect_raw`** at `subgraph.py:439`/`:440`.
- **Row 4 named no symbol at all** (`search(order_by="graph")` is a door, not a site), so its two
  degree-centrality readers could not be re-verified by anyone. They are
  **`GraphRanker._fetch_event_signals`** (`ranking.py:703`) and
  **`StateRanker._fetch_point_signals`** (`ranking.py:1018`).
- **New row 12 — `_EdgeHandlers._count_about_out`** (`projection/edges.py:1871`). It is the
  link-verification predicate: `resolve_about_link` asks "did this record leave the Event joined to
  anything". ⚠️ Reachable only on the **DEFENSIVE** `how: "name"` replay path — no released version
  and no `origin/main` writes that form — so it cannot fire on data written today.
- **New row 13 — `session_schema.audit_session_retrofit`** (`session_schema.py:663`), the retrofit
  audit's recorded-vs-live edge counter, split across two `WITH` stages so the session-origin and
  turn-Point-origin counts do not cross-multiply.
- **Row 2's symbol was the SDK door, not the reader.** `topic_summarize` is the entry point; the
  read is in **`_retrieve_topic_neighborhood`** (`topic_summarization.py:141`, `edge_type` at
  `:185`).
- **The count moved eleven → fifteen, and the customer-callable count did not move: four.** The
  correction is entirely in the non-callable tail, which is where three hand audits kept losing
  sites.
- **Line numbers are gone from the table, deliberately.** They were pinned to `c79ba1cf2` and every
  one of them had rotted; the documents' own rule is *"the symbol name is authoritative, the line
  number is a convenience"*. Dropping the convenience removes a whole rot class instead of
  re-pinning it. The test asserts symbols and census membership, never line numbers — a line
  assertion would red on every unrelated edit, which is a different way to be dead.
- **The test-file count was restated with its measurement.** `rg -l aboutObject tests/` → **59**
  at `26aff163c` (36 at the pin; 41 recorded by the `#7956` pass at `1e5fc00ca`). All three count
  files that **mention** the edge, which is the only thing a grep can measure — the sentence said
  *"assert"*, which overstated what was behind the number.
- **The sibling passage in `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 was corrected in the same pass** (its
  `assembly.py:573`/`:789`/`:809` pointer, its `:NNNN` citations and its test-file count), because
  a repair that fixes one copy and leaves the other is how this drift survived the previous three.

### Follow-up, same day — the FIFTH correction, and the first one the census could not have made (`PR #9398` review)

The section above called itself "the last hand pass" and was **wrong within hours**: the review of
that PR found the newly-written census blind in a whole class, and the claim it guards therefore
still false. The review is the reason the count below moved in the direction that matters.

- **The census could only see a string LITERAL.** It parsed `ast.Constant` and nothing else, so a
  reader that builds the relationship name at runtime was invisible — and five of them exist:
  `StateRanker._fetch_object_signals` (`ranking.py:1066`, `"|".join(ABOUT_EDGE_TYPES)`),
  `TortoiseSDK._state_related_objects` / `_state_related_points` (`sdk.py`, no line hint — it
  builds `"MATCH (n)-[a:" + self._ABOUT_TYPES + "]…"`, and the numbers this entry originally
  shipped with were already wrong by a few lines when written), `supersede_point`
  (`sdk.py:13559`) and `_preview_supersede` (`mcp_server.py:6804`), both looping over
  `SUPERSEDE_STRUCTURAL_RELS`.
  Worse, the table and the census **agreed perfectly** on the 13 sites the literal scan could see,
  and that agreement was circular: both were blind in the same place. Adding rows alone would have
  left a sixth hand pass guaranteed, so the fix is the derivation, not the list.
- **The derivation is now one level through the expression**: `+`-concatenation, `JoinedStr`
  (f-strings, including ones with no placeholders — the old scan skipped those outright),
  `"|".join(CONSTANT)`, `for rel in CONSTANT` loop variables, class attributes (`self._ABOUT_TYPES`),
  and constants **imported** from another module (resolved only when the name is unique package-wide,
  so an ambiguous name is a reported gap rather than a guess).
- **That un-blinded a site the earlier table had written off as un-derivable.**
  `_retrieve_topic_neighborhood` (`topic_summarization.py:211`) builds `edge_type` from a conditional
  and interpolates it into an f-string; the literal scan could not follow it and the row was listed
  as one of "the two no census can find". It is now derived, so `UN_DERIVABLE` is down to **one**
  entry: `SubgraphExpander._neighbors`, whose `MATCH (n)-[r]->(m)` names no type anywhere.
- **The count moved fifteen → twenty, and the customer-callable count moved four → SIX.** New row 14
  (`_state_related_objects`, `_state_related_points` — MCP `tortoise_recall` through `recall_state`)
  and new row 16 (`_preview_supersede` — the `tortoise_supersede` dry-run preview) are both
  **customer-callable**; row 15 (`supersede_point`) is the read on the write path that row 12's
  precedent already admits. Row 4 gains its third reader, `_fetch_object_signals`. ⚠️ **The
  customer-callable number is the one the KEEP ruling rests on, so a correction that raises it (and
  was invisible to four hand passes) is the most important line in this entry.**
- **The completeness arm now pairs files with symbols POSITIONALLY.** It used to build a
  cross-product of a row's backticked files × its symbols, so a row citing files A,B and symbols
  X,Y "declared" all four pairs — harmless while every row is single-file, and exactly wrong the
  moment a row cites two files (the shape the table invites). Pairing symbols to the file token that
  precedes them is mechanical and exact.
- **The anti-vacuity floors were lowered, deliberately.** They sat at today's exact census (13 sites
  / 13 rows), so deleting a reader *and* its row — a legitimate contraction — failed with a message
  claiming the parser had broken. They are now floors (~10) that only catch "found nothing".
- **The guard is registered in `config/ci-surfaces.yml`** (`core`, with a `durations` row). Without
  that it was invisible to `tools/ci_selection.py --integrity` (a required, merge-gating job) AND
  selected into no CI leg at all — the instrument that exists because nothing executable checked the
  table would itself have run nowhere.
- **A mechanism test pins the derivation** on synthetic sources (literal, `+`-concat, f-string,
  `"|".join(TUPLE)`, `for rel in TUPLE`, and a `MERGE` writer that must NOT count), so the resolver
  cannot regress to literal-only without a red.

### Follow-up, same day — the SIXTH correction: the counts are now asserted, and the coverage claim stopped overreaching (`PR #9398`, second review)

Two findings, both in what §13 **asserts about** the guard rather than in the guard's derivation. The
fifth-correction entry above calls the customer-callable number *"the most important line in this
entry"* — and that line was the one thing in §13 nothing checked.

- **§13 claimed the guard's coverage was complete except for ONE shape; the guard documents two.**
  The document said *"The one shape it cannot see is an empty relationship pattern"*, while the guard's
  own `WHAT THE CENSUS CANNOT SEE` lists a second blind class: a relationship name **assembled entirely
  at call time** (a function argument, a dict lookup, a value stored on an instance at construction),
  which the resolver follows one level deep and then leaves as a gap. Such a reader derives nothing and
  the table can stay short with five tests green — the same silent-omission class this whole entry
  exists to end, sitting one paragraph away from a claim that the guard would have caught it. §13 now
  states both shapes and states the guarantee as what it is: *a reader that NAMES the edge — literally,
  or through a constant the resolver can follow*.
- **⚠️ The count was guarded by a `>= 4` floor while the table declares SIX.** §13 calls the number
  *"the whole argument"* and the ruling rests on it, yet a demotion of two `reach` cells — the `#7956`
  erratum's exact direction, and the one that matters — left every arm green while the headline still
  said SIX. Worse, §13 contained the contradiction already: a later sentence listed four customer-callable
  surfaces and said *"are four of them"*, three paragraphs under a headline saying SIX. The new
  `test_the_prose_counts_match_the_table` reads the numerals **out of the sentences that state them**
  (`TWENTY read sites`, `SIX of them are CUSTOMER-CALLABLE`, and any later *"are N of them"* restatement)
  and compares each to the table — the `reach`-column count for the customer-callable numerals, and
  `census ∪ UN_DERIVABLE` for the site count (as the mechanism stood then — the seventh entry below
  splits the units, because those two numerals counted different things). Only the numeral is read, never the prose around it, so the
  sentences stay free to be rewritten as long as they stay true. Non-vacuity is measured, not assumed:
  the arm reds on the previous revision of §13 (it reads `four` against six declared rows) and reds again
  when a `reach` cell is demoted with the prose left alone.
- **The two sentences were corrected to match**: the customer-callable enumeration now names all six
  (`belief_timeline`, `topic_summarize`, `recall_subgraph`, `search(order_by="graph")`, `recall_state`,
  the `tortoise_supersede` dry-run preview) and says *six*, and the customer-callable count is no longer
  described in the guard's docstring as "not asserted beyond a floor" — that paragraph is what permitted
  the stale headline in the first place.

### Follow-up, same day — the SEVENTH correction: the class was ONE example, and the headline mixed two units (`PR #9398`, third review)

The sixth entry closed the blind classes it knew about and asserted the counts they produced. The
review of *that* state found both halves still wrong in the same direction — a reader the table did
not name, and a number that read as a count of something it did not count.

- **⚠️ The empty-pattern shape was declared as ONE example, and the class had three further
  members.** §13 said *"an empty relationship pattern (`MATCH (n)-[r]->(m)`, row 3)"*, and
  `UN_DERIVABLE` held a single entry (`SubgraphExpander._neighbors`). But the shape is not a
  symbol — it is a **class**: any query whose relationship pattern names no type walks every type,
  `aboutObject` included. Three live members were absent, one of them **customer-callable**:
  `navigation._hop_query` — the per-hop query behind `entityProfile` and `tortoise_traverse` (MCP
  `tortoise_entity_profile`, and `tortoise_traverse`), which emits `-[r]->` and `<-[r]-` and returns
  `type(r)` verbatim — and the backfill census `CENSUS_QUERY`
  (`definite_description_backfill.census_rows`, diagnostic), while `_preview_supersede` was a row
  for a different reason entirely. **The guard could not see any of them**: `navigation.py` carries
  no `aboutObject` literal and no rel constant, so no derivation parsed it, and the single
  hand-declared entry covered only `_neighbors`. Six arms stayed green over a customer-callable
  reader — a PASS about a false artifact, which is the failure mode this whole instrument exists to
  end.
- **The class is now derived by its own shape walk** (`_is_empty_pattern_read`: a bracketed
  traversal with no `:` under a reading clause, docstrings excluded because they only quote Cypher),
  and every derived member must be either a §13 row or a named `EMPTY_PATTERN` entry saying why it
  is not an `aboutObject` read for a referent (eighteen such members today, from graph export and
  edge counts to delete/repoint sweeps). A **new** member of the class now reds instead of shipping:
  declaring one example by name was how this survived four passes. New rows **17** (`_hop_query`,
  customer-callable) and **18** (`census_rows`, diagnostic).
- **⚠️ The headline's numerals were compared against the wrong units.** *"TWENTY read sites, and SIX
  of them are CUSTOMER-CALLABLE"*: TWENTY counted backticked **symbols**, SIX counted **rows**, and
  the six customer-callable rows carried **nine** symbols — so "of them" was false by three, and the
  arm baked the mismatch in by comparing each numeral to a different set. §13 now states
  **TWENTY-TWO read sites across EIGHTEEN rows, and SEVEN of those rows (ten sites) are
  CUSTOMER-CALLABLE**, and `test_the_prose_counts_match_the_table` checks four numerals, each
  against the set its own noun names.
- **The sibling copy in `EXTRACTOR-V4-ARCHITECTURE.md` §13.8 stopped restating the counts.** It
  asserted an unguarded *"SIX sites are customer-callable"* and *"four customer-callable sites DO"*,
  plus a "TWENTY-site table" pointer, and nothing executed any of them (the citation guard resolves
  symbols, not counts) — so the two documents agreed on numbers neither checked, which is the
  circular agreement this changelog diagnoses one level down. Both numerals and the table-size
  pointer are now a reference to §13, and the name-invisible enumeration gained
  `navigation._hop_query`.
- **A rotted `file.py:NNNN` citation was fixed in the passage this PR already rewrote.**
  `_connect_issue_objects` was cited as `sdk.py:20154` — a line inside `_cross_lens_pool`'s Cypher.
  It sat three paragraphs below the sentence explaining that line numbers were dropped *because*
  they had rotted. ⚠️ **The first fix of that citation shipped a new number, and the number was
  wrong too** (`34896`, which is a different function at every head it was checked against) — the
  fix for a rotten line number was a rotten line number, which is the argument for the rule rather
  than an exception to it. The citation is now **symbol-only** (`sdk.py`, no `:NNNN`), the same form
  §13's headline states, and the third review's remaining line hints in `EXTRACTOR-V4-ARCHITECTURE.md`
  were dropped the same way — see the ninth entry below.
- **`_MIN_CUSTOMER_CALLABLE`'s comment stated a wrong delta** (*"the table now declares three
  more"*: four → six is two). A comment making a numeric claim inside a file whose whole subject is
  numeric claims is not cosmetic.

### Follow-up, same day — the EIGHTH correction: the shape that can only be DECLARED now has its members (`PR #9398`, third review, remaining half)

The seventh entry below turned the empty-pattern shape into a derived class and closed it. The
review's other half was still open, and it is the half no derivation can reach:

- **§13 said the call-time-name class was covered — and then said it had *"no member today"*, which
  was false.** A relationship name assembled entirely from a value computed at call time (a dict
  lookup, an instance attribute) resolves to nothing, so no census can find it; the document
  therefore has to name it. Two about\*-specific readers of exactly that shape are live:
  `session_link.link_entity` — the **existence pre-probe** before minting an `about*` edge, whose
  `edge_type` **defaults to `"aboutObject"` and whose every capture-path caller takes the default** —
  and `subject_binding.bind_point_subjects`, the **re-probe** after a refused link, whose
  `edge_type` comes from `_ROLE_TARGET["object"]`. Both read the edge on the write path, which row
  12's precedent (`_count_about_out`) already admits as a row.
- **One of the two was resolvable all along.** The resolver now follows **function-parameter
  defaults**, so `link_entity`'s pre-probe is DERIVED (row 19) instead of hand-declared and the
  class statement is no longer a matter of trust for that member. The other is a genuine dict lookup
  and stays un-derivable: `bind_point_subjects` is **row 20** and is registered in the guard's
  `UNDERIVABLE_CALL_TIME`.
- **The declaration is now checked in both directions**, which is what makes it a class rather than
  an example: every registered member must be a §13 row (the previous revision declared the class
  and named nobody) and must still be un-derivable (if the resolver learns to follow it, the entry is
  stale and reds). The mechanism arm gained the parameter-default case, so the new derivation shape
  is pinned on synthetic sources like the other five.
- **The count moved twenty-two → TWENTY-FOUR sites on eighteen → TWENTY rows.** The
  customer-callable count is unchanged (**seven rows, ten sites**) — both new readers are on the
  default-ON write path, not customer doors. The headline's four numerals and their units are
  asserted, so the count and the table cannot drift apart.

### Also corrected: "no `aboutObject` edge carries ANY property" was false (`#1370`)

The §13 sentence that closes the *structural door* argument — *"no `aboutObject` edge carries any
property, so we lose nothing semantically by deriving later"* — has been false since `#1370`:
`session_link.link_entity` sets `r.confidence` when a binding confidence is supplied, and
`projection/entities.py`'s replay fold restores it (a test asserts it on an `aboutObject` edge). The
supporting measurement was a literal grep (`rg 'aboutObject \{'` → 0), and it was blind in exactly
the class the census exists to fix: the relationship name there is a **variable**. The claim is now
scoped to what the code actually does — the binding confidence rides the edge — in §13 and in
`EXTRACTOR-V4-ARCHITECTURE.md` §13.8; `#9403` later refined it again (the record projection also
stamps `idx`, and the census sees properties without being a proof of absence), and §11's ruling
wording is flagged as predating `#1370` rather than silently rewritten — reopening a ruling is a
decision, not a copy-edit. (Surfaced by the earlier reviews of this PR; the third review's scope did
not include it.)

### Follow-up, same day — the NINTH correction: the line-number hints are gone, and three prose claims are now checked (`PR #9398`, fourth review)

The fourth review found nothing wrong with the census or the KEEP ruling. All three of its findings
were **prose that a machine could have caught and no machine was catching**, which is this PR's own
subject one level up:

- **The line hints were still there, in the one paragraph that says they are not the authority.** The
  §13.8 passage in `EXTRACTOR-V4-ARCHITECTURE.md` opened with *"These line numbers are the convenience,
  not the authority"* while carrying fifteen `file.py:NNNN` hints — and six of them pointed at
  unrelated code **at the PR head and at `origin/main`** (the reviewer measured each as a symbol
  grep vs a `sed -n 'Np'`, the two-command comparison the paragraph itself invites). §13 had already
  dropped its line numbers for exactly this reason, and the passage is inside the same argument, so
  the numbers are now **gone, not re-measured**: the paragraph resolves **symbols** only, and the
  sentence says why. The re-measure route was rejected because the last time this set was "fixed"
  by re-measuring, the new number was wrong too (the `_connect_issue_objects` entry above).
- **§13's empty-pattern class named two rows and has three.** *"an empty relationship pattern
  (`MATCH (n)-[r]->(m)`, rows 3 and 17)"* — row 18 (`definite_description_backfill.census_rows`,
  `OPTIONAL MATCH (o)<-[r]-()`) is the same class, and the guard's own docstring says so. Now
  *rows 3, 17 and 18*, and **the sentence is asserted against the derived class**: a new member, or
  a row dropped from the sentence, reds.
- **Row 7's `reach` cell cited a symbol that does not exist.** `QueryOptions` appears nowhere in the
  package (`grep -rn --include=*.py QueryOptions tortoise/` → 0 hits); the flag is a keyword
  parameter of `tortoise_fts_query`. Corrected, and the `reach` column gained an arm: every
  CamelCase symbol it backtick-quotes must exist in the package, so a reach cell can no longer cite
  a name that was never there.

**The three new arms, and why each is non-vacuous.** `tests/test_7948_about_object_read_sites.py`
gained `test_the_census_prose_carries_no_line_citations` (reds on the pre-fix document, which
carried twenty hints across the two passages — four in §13's bullet and sixteen in the sibling
paragraph, measured with the arm's own `_LINE_HINT`), `test_the_prose_names_every_row_in_the_empty_pattern_class`
(reds on *rows 3 and 17*, naming the missing row 18 by deriving it), and
`test_the_reach_column_names_only_symbols_that_exist` (reds on `QueryOptions`). The lesson the three
share: the guard resolved the `site` column and nothing else, so the prose around the table could
carry a stale number, an under-counted class and a non-existent symbol while every arm stayed green
— a **PASS about a false artifact**, which is the failure mode this whole instrument exists to end.

### Follow-up, same day — the TENTH correction: the chronology now arrives at the headline, and a digit rewrite stays checkable (`PR #9398`, fifth review)

The fifth review again found no defect in the census, the derivation or the KEEP ruling: the rows,
the four numerals and the declared classes all reconciled at that head. Its three findings were the
narrower kind — **a claim that disagreed with a claim next to it, a citation that had already
rotted, and a documented capability that could not be reached** — and the first two share a shape
worth naming: both were **prose asserting a fact about prose**, which no arm was reading.

- **§13's chronology stopped two sites short of its own headline.** The headline stated
  **TWENTY-FOUR read sites across TWENTY rows**; the three paragraphs explaining *why* the count
  moved ended at **twenty → twenty-two sites**, the state before rows 19 (`session_link.link_entity`)
  and 20 (`subject_binding.bind_point_subjects`) were added. The increment existed in this changelog
  and **nowhere in the document being narrated**, so the passage a re-verifier reads to understand
  the number answered with a smaller one. The closing increment is now stated (twenty-two →
  twenty-four sites, eighteen → twenty rows, and **neither new row is customer-callable**, so the
  numeral the ruling rests on — seven rows, ten sites — is unchanged), and the chain is **asserted**:
  `test_the_change_chain_ends_at_the_headline_counts` compares the LAST `<from> → <to> <unit>`
  increment of each unit to the table, so a chain that stops short reds instead of reading as
  history. Non-vacuity is measured: on the pre-fix document it reds on
  *"stops at `twenty-two` sites while its table has 24"* **and** on the missing rows increment.
- **Two `file.py:NNNN` citations in the fifth-correction entry were already rotten when written.**
  `_state_related_objects` / `_state_related_points` were cited at lines 28773 and 28790 of
  `sdk.py`; at the head those lines are a validity-window helper and a sort-key comparison, while
  the readers themselves sit **over 600 lines** further down (measured for this entry: 619 at the
  head, and 5 lines off already at the commit that wrote the citation). The entry now uses
  **`sdk.py`, symbol-only** — the same form the same block adopts for `_connect_issue_objects` —
  because re-measuring is what produced the wrong number in the first place, and the rotten numbers
  are named here as numbers in prose rather than as a `file.py:NNNN` hint, so the passage that says
  they rot does not hand a re-verifier a fresh one to copy. ⚠️ **This block is deliberately not
  guarded by a no-hints arm**, and the reason is structural rather than an omission: two of its
  hints exist **as examples of rot** (`assembly.py:573`/`:789`/`:809`, the `EXTRACTOR-V4` §13.8
  pointer the fourth correction replaced, and `sdk.py:20154`, the `_connect_issue_objects` citation
  that was wrong when written), so a blanket arm would red on the passage that *documents* the
  defect. What the arm enforces — *the symbols are authoritative, the line number is a
  convenience* — therefore applies here in the smaller form this block can actually keep: **no hint
  is written as a re-verification aid**, and the four that remain across these two entries
  (`ranking.py:1066`, `sdk.py:13559`, `mcp_server.py:6804` in the fifth-correction bullet, plus
  `topic_summarization.py:211` a paragraph below it) are dated record rather than pointers into the
  live table.
- **`_numeral` advertised digits it could not receive.** Its docstring promised *"digits are
  accepted as well, so a rewrite that writes `6` instead of `SIX` stays checkable"*, while all five
  `_PROSE_*` captures were `([A-Za-z][A-Za-z-]*)` — so `token.isdigit()` was unreachable and a
  digit rewrite of the headline matched **nothing**, redding the count arm with *"§13 no longer
  states its site count … restore the sentence"*: a false diagnosis telling a fixer to restore a
  sentence that is present. The captures now accept `[A-Za-z][A-Za-z-]*|\d+` through one shared
  `_NUMERAL` body, the parsing was split into `_parse_prose_counts()` so the sentence forms have a
  seam, and `test_the_numeral_parsing_accepts_digits` feeds **both spellings of the same claim**
  through all four units plus the chain form. It reds if the captures go back to words-only.

### Follow-up, same day — the ELEVENTH correction: the call-time class is DERIVED, and it found two customer doors the census was missing (`PR #9398`, sixth review)

The sixth review found no defect in the derivation, the table or the KEEP ruling either. Both of its
findings were about the **third class** — the one whose relationship type is an expression rather
than a name — and both are the same defect seen from two sides: the class was **declared, not
derived**, so its completeness was a matter of trust.

- **A hand list can only validate the entries it is handed.** `UNDERIVABLE_CALL_TIME` was a
  one-entry list (`bind_point_subjects`) and the arm checked it in both directions — but nothing
  enumerated a *new* member, so a reader of the shape shipped green **by construction**. The
  reviewer measured it on six synthetic probes: a parameter, a dict lookup, a `for rel in` loop over
  a rel-family `frozenset`, and so on, all invisible. That is exactly the defect the third review
  found for the empty-pattern shape (declared as ONE example instead of derived as a class), one
  class over.
- **§13 therefore overstated both the class and the resolver.** It said the resolver follows
  `for rel in CONSTANT` "in the module that defines the constant" — true for a tuple, **false for a
  frozenset**, which is the idiomatic shape here (`DERIVABLE_STRUCTURAL_RELS`,
  `_ABOUT_REPLAY_RELS`, `ENTITY_LINKED_RELS`). `_resolve` handled `ast.Tuple`/`ast.List` only and
  fell through to the gap for `ast.Set` and `frozenset({...})`. It also said the class had
  "one dict-lookup member", which was false at that head: the six probes above were live readers,
  and two of them are customer doors.

**The fix, in three moves.** (1) `_resolve` follows `ast.Set` and the collection constructors, so a
rel-family frozenset is a named site like a tuple. (2) An empty **type slot** on a resolved candidate
is a SHAPE (`-[:{rel}]->` resolves to `-[:]->`), so the class is walked like the empty-pattern one —
`_call_time_sites()` — and every member must be a §13 row or a `CALL_TIME_REL` register entry saying
why its gap cannot carry `aboutObject` for a referent. The register is not the class: the derivable
members are rows, and the floor is on the derived set, so an empty register cannot make the arm
vacuous. (3) The class walk's file filter must be a strict SUPERSET of the class or a filtered-out
reader is an invisible one; it is verified exhaustively (a full 242-module walk derives the same
eleven members, so nothing is missed by the 68-file filter).

**What the derivation found — and what it moved.** Two undeclared **customer-callable** readers:
`TortoiseSDK.traverse` (type is caller-supplied; `validate_rel_type` allowlists it against
`KNOWN_REL_TYPES`, which carries the about\* family) and `TortoiseSDK.ingest`'s connection duplicate
probe (`relation` allowlisted through `_VALID_EDGE_PREDICATES`). They are **rows 21 and 22**, and
unlike every previous increment **this one moves the numeral the KEEP ruling rests on: seven → nine
customer-callable rows, ten → twelve sites** — the direction an under-counted census fails in, since
it understates the surface that ruling defends. The remaining members are register entries with
their reasons: the five operator-rel legs (`_bfs_select_operators`, `apply_payload_operators`,
`_fold_edge_message_changed`, `_find_operator`, `create_direct_edge`), the fixed `COMPLETED_STEP`
onboarding edge (`_capture_onboarding_snapshot`), and `_rebuild_all_replay` — the reviewer's named
miss, which deletes and rewrites replayed edges **by type** from a journal field and so reads the
edge as data, not for a referent. Counts: **twenty-four → twenty-six sites, twenty → twenty-two
rows**; the chain's closing increment is stated and asserted, as the tenth correction requires.

**Measured, not asserted.** Non-vacuity for each new arm: against the pre-fix document **11 of 23
arms red** (the derivation arms stay green by design — they pin the mechanism, not the prose); with
the call-time walk removed, three arms red including the floor and the synthetic-new-member arm;
with the frozenset branch removed, `test_the_resolver_follows_a_frozenset_rel_family` reds. The
mechanism cases also pin the two near-misses that must NOT count (a named type, a `MERGE` writer)
and the neighbouring class's shapes (an empty pattern belongs to the empty-pattern walk), so the two
shape walks cannot double-count one site and then each re-declare it. `_type_slot` is shared by both
walks, which also closes a hole the fourth review named: `-[r {idx:$i}]->` names **no** type, because
that colon is a property-map key, not a type.

⚠️ **The two figures in this entry were re-read at the head that closed the round after it.** They
were written at `9b5d14b10`, when the file held 17 arms; five arms arrived with this very round
(17 → 22) and one more in the twelfth, so "10 of 17" described an artifact that no longer existed.
A record that stops at the head it was written against is the same rot this section exists to end,
one level up — hence the re-read, not a footnote.

### Follow-up, same day — the TWELFTH correction: the empty-pattern class was walked in ONE of its two spellings (`PR #9398`, seventh review)

The seventh review found no defect in the derivation the eleventh correction added, the table, the
CI registration or the numerals the KEEP ruling rests on. It found one **live reader the whole
census could not see**, and one promise that was in prose only.

- **A traversal that names no type has TWO legal spellings, and only one was walked.**
  `_EMPTY_TRAVERSAL` required brackets, so `-[r]->` derived and `(n)--()` did not — but the
  bracket-less form is the same traversal: no type is written, so it walks every relationship type,
  `aboutObject` included. There was a live member: `domain_validators.validate_chain_integrity`,
  inside the `orphaned_draft` rule (`#131`), runs `MATCH (n:Point {status:'draft'}) … AND NOT
  (n)--()` — a degree count over every edge type, the same read `session_source_migration._degree`
  is already registered for. The module carries no `aboutObject` literal and no rel-family constant,
  so the file filter skipped it and no walk ever reached it. §13's *"every such reader is enumerated
  by its own shape walk"* was therefore **false at that head**, and the shape a new orphan/degree
  rule is idiomaticly written in was the one that shipped green.
- **The "numbering of record" omitted two arms.** The docstring's `WHAT THIS PINS` list held 19
  entries for 22 arms; the two with no entry were the call-time class's mechanism arm and its
  synthetic-new-member arm — the latter being the arm the PR body advertised as the sixth review's
  headline fix. The list says of itself that it is the numbering of record, and three places in the
  file cite arms *by number*, so an omission there is not cosmetic.

**The fix, in three moves.** (1) `_BARE_TRAVERSAL` derives the bracket-less form — `<-->`, `<--`,
`-->`, `--`, anchored between two node patterns (`)` … `(`) so a bare `--` in prose, a CLI flag or a
`->` in a log message is not a traversal — and both shape predicates (`_may_traverse_every_type`,
`_is_empty_pattern_read`) use it, so the file FILTER and the walk cannot disagree. (2)
`validate_chain_integrity` is a named `EMPTY_PATTERN` entry with its reason (a degree count, not a
read for a referent), taking the register from eighteen to nineteen members, and `_EMPTY_PATTERN_CASES`
gains five bracket-less cases plus the two near-misses that must NOT derive (`--` inside a string
literal, a `MERGE (n)-->(m)` writer) — the mechanism arm has the new direction on sources that are
not today's tree. (3) `test_the_docstring_names_every_arm` makes the numbering of record executable:
every `test_` function is a numbered entry or a declared collapse (`_COLLAPSED_ARMS`), the numbers are
contiguous with no duplicates (an *"arm N"* reference must not silently resolve to the wrong arm),
and every collapsed name is a real arm the prose still names.

**Measured.** With the bracket-less spelling un-walked, three arms red — `test_the_empty_pattern_class_is_declared`
(the new register entry stops deriving), `test_the_empty_pattern_class_derivation_holds` (the five
new cases) and `test_a_new_call_time_reader_cannot_ship_green` (an `EMPTY_PATTERN` entry that no
longer resolves into the walk keyspace). Removing entries 20/21 from `WHAT THIS PINS` reds
`test_the_docstring_names_every_arm` naming both, and a duplicated entry number reds it on the
duplicate. Counts are unchanged: **twenty-six sites, twenty-two rows, nine customer-callable rows
(twelve sites)** — this round added no reader to the table, which is the point: the member it found
is not one.

### The eighth review — the walk's own gate, and a promise the prose made

The eighth review found no defect in the derivation, the table, the CI registration or the four
asserted numerals. Every finding was in the surface *around* the instrument: a text gate inside the
named census, and prose claims nothing read.

- **The named census re-imposed on itself the blindness `_files()` exists to refute.** `_sites_in()`
  kept only two of the four predicates its own file filter admits on, so a module admitted *because
  of its shape* was parsed and then deliberately not walked. A constant FRAGMENT interpolated into
  the type slot — `_REL = "about"` then `f"…[:{_REL}Object]…"`, which the resolver follows and which
  §13 promises *"reds the guard until it gets a row here"* — derived nothing. It also made the
  superset arm unfalsifiable: that arm re-uses this walk as its own proof, so a narrowing here was
  invisible to the arm written to catch narrowings.
- **§13.8 lost a question.** The rewrite of `EXTRACTOR-V4-ARCHITECTURE.md` §13.8's question 2 removed the
  newline before question 3, which became the tail of the paragraph above it (`…cheap to keep.**3.
  **Should every expansion…`). The list rendered one item short while the sentence that introduces
  it still said *"The three questions below"*, and §15 still cross-references *"question 3"*.
  Nothing saw it: the line-citation arm scans that line for `file.py:NNNN` and the sibling arm reads
  backticked symbols, and both survive a missing newline.
- **Three present-tense copies of the register's size, one numeral stale.** The arm that polices that
  numeral reads it out of this document, so the register could grow past the number three comments
  quoted and red nothing — the file that exists to make counts executable carried unread copies of
  one. `_MIN_CALL_TIME`'s comment carried the same kind of copy for the call-time walk's member
  count, a numeral behind the derivation.

**The fix.** The gate goes: `_files()` admits a module, so the named census walks it — and a
docstring is skipped there as it already is in the other two walks (prose that QUOTES a pattern is
not an executed read; a docstring naming the edge would otherwise have demanded a §13 row for a
comment). Two cases are added: a `{_REL}Object` fragment to `_MECHANISM_CASES` and to
`_NEW_READER_CASES`, and a docstring case to `_MECHANISM_CASES`, all asserted *through the walk*
rather than through the resolver alone — `_resolve` was already correct, which is why the gate hid
the member. §13.8's newline is restored, and the copy in each stale comment is replaced by the property
plus a pointer at the arm that measures it. Two arms make both prose defects executable:
`test_the_extractor_section_keeps_its_three_questions` compares the count the sentence states to the
items that start their own line (its predicate is run on a glued sample and a clean one, so the
detector is measured, not trusted), and `test_the_file_states_no_count_it_does_not_measure` bans a
restated census numeral in this test file altogether.

**Measured.** Restoring the gate reds two arms (`test_the_census_derives_a_relationship_name_built_at_runtime`
on the fragment case and `test_a_new_call_time_reader_cannot_ship_green` on the new reader); dropping
the docstring exclusion reds the mechanism arm; re-gluing §13.8's item 3 reds the new list arm; putting
the stale count back in a comment reds the count-copy arm. Counts are unchanged — **twenty-six sites,
twenty-two rows, nine customer-callable rows (twelve sites)** — the gate was hiding no member at
this head, which is the point: it would have hidden the next one.

### The ninth review — the clause detector was case-sensitive, and a numeral nothing claimed

The ninth review found no defect in the table, the three derivations, the CI registration or the four
asserted numerals. Both of its findings were in the instrument's own handling.

- **The governing-clause detector was case-SENSITIVE.** `_CLAUSE` carried no `re.IGNORECASE` while
  both of its consumers `.upper()`ed the capture — the normalization a regex that can only yield
  upper case makes dead code. A reader written `match (n)-[:aboutObject]->(m)` therefore derived into
  NO class: not the named census, not the empty-pattern walk, not the call-time walk. The
  completeness arm stayed green while §13 shipped a row short. No reader in the tree is written that
  way today, so nothing was missed at that head — the failure was latent, not shipped.
- **The flag alone was not the fix, and that was measured rather than reasoned.** With
  `re.IGNORECASE` and no anchor, the completeness arm derived a FALSE site in
  `value_extractor.GROUNDING_SYSTEM`: an LLM prompt whose prose says *"match summary items"* and
  whose JSON schema then carries an example naming the edge, so the English verb governed a bracket.
  The clause is now anchored on the `(` a real Cypher clause always carries, and both directions are
  pinned in `_MECHANISM_CASES` / `_EMPTY_PATTERN_CASES`: a lowercase clause that must derive, a
  lowercase `merge` and a prose sentence that must not.
- **The docstring stated an envelope site count that nothing claimed.** The issue body names no
  count; the issue's owner-decision comment and this changelog state the real one, and the docstring
  disagreed with both. The numeral is removed rather than corrected — a count restated in a file that
  does not measure it is the rot this guard polices — and the restated-count ban was widened to the
  two spellings it could not see (the hyphenated `-site envelope` form and the `sites claimed` form).

**Measured.** Restoring the case-sensitive detector reds two arms (both lowercase cases); taking the
flag but dropping the anchor reds two (the prose near-miss and the false `value_extractor` site).
Counts are unchanged.

### The tenth review — two more restated counts, and the ban stops listing spellings

The tenth review found the four asserted numerals, the three derived sets and the CI registration
sound, and one defect left: two present-tense copies of census counts inside the guard itself, in
spellings the restated-count ban could not see.

- **The anti-vacuity floors' comment stated a pair the table never carried.** It read *"deliberately
  BELOW today's census (20 named sites / 22 rows at the time of writing)"*, and those two numerals
  never coexisted — the row count and the site count arrived in different corrections — so the
  comment paired a symbol count with a row count, the units error the guard exists to stop. It also
  contradicted this changelog's own record of the floors.
- **The customer-callable floor's comment stated the row count one increment behind the table**, in a
  present-tense `the table declares <N> today`.

Neither was seen because the ban was a fixed list of four spellings, and this was the third round of
widening it after the fact. It now states the PROPERTY: a COUNT with a census UNIT, scoped as current
(`today`, `currently`) or as a dated reading (`at the time of writing`), in either order — plus a
numeral-last alternative for the spelling that carries no unit word. It also scans the UNWRAPPED text
(start-of-line comment markers are blanked to spaces, so every reported line number still points at
the line the author sees), because the wrapped pair is precisely how one of the copies survived a
per-line scan.

Both comments are now property-only and point at `test_the_prose_counts_match_the_table`, the arm
that measures the table. **Measured.** Restoring the two pre-fix comments reds
`test_the_file_states_no_count_it_does_not_measure` on both, and each pre-fix line is a control
sample inside that arm, so the shapes cannot silently fall out of coverage. Counts are unchanged —
**twenty-six sites, twenty-two rows, nine customer-callable rows (twelve sites)**.

## §13's derived class now covers the FRAGMENT half, and three claims in the guard became executable (2026-10-21, `#7948`)

Review cycle 5 found four defects, all inside the guard that makes §13 executable rather than anything
in the table it pins. Each is closed by making the claim run.

**The fragment census existed and was never CALLED.** `_untyped_fragment_sites()` carried a 40-line
docstring stating that *any string expression holding a dash-delimited relationship bracket with no rel
type and no clause at all is itself a site, and its enclosing symbol must account for it (a row, an
exclusion, or a `_NON_LITERAL` pin)* — and no test invoked it. So the bypass it documents was open:
`_WALK = "-[r]->"` beside `query("MATCH (n)" + _WALK + "(m)")` enters neither census (the bracket and
its clause live in different constants), and the whole suite stayed green. It is now wired into
`test_no_untyped_walk_lacks_a_row_or_a_stated_exclusion` with exactly the ownership rule its docstring
states — row, `_EXCLUDED_UNTYPED`, or `_NON_LITERAL` pin — a floor so it cannot go vacuous, and a
synthetic part-assembled fixture so the detection itself is falsifiable. **Non-vacuity measured**: the
probe above, appended to `tortoise/fanout.py`, reds naming `fanout.py (inside _WALK_FRAGMENT)`; it was
green on the pre-fix tree. §13's sentence now states that half of the class (*in a read clause, or
assembled from a dash-delimited fragment part that carries no clause of its own*), so the document and
the instrument say one thing.

**A docstring is prose, not a query — and the two halves of the census disagreed about it.** The untyped
census filtered prose through `_docstring_ids()`; `_literal_read_sites()` and `_reads_naming_the_edge()`
accepted every string constant. Measured on the pre-fix predicate: replace row 9's link query with
`CONTAINS` and paste the old query into `audit_session_retrofit`'s **docstring** → every arm green, so a
row kept its *"the named symbol really reads the edge"* verdict after the code stopped reading it. One
rule now, asserted both ways by `test_a_docstring_is_prose_and_not_a_read_site`: prose is dropped, and
the same query in the function BODY is still counted.

**The carrier arm had no floor, so it could report green having asserted nothing.** It is the only arm
asserting *"the constant IS the read"* (rows 17–21, 23), and it built its pairs in nested loops ending
`assert not broken`. A carrier refactored to `ABOUT_EDGE_TYPES = _SHARED_ABOUT + ("aboutObject",)` is no
longer splice-shaped, so `_carrier_declarations()` yields `{}`, the loops run zero times, and the arm
passes while the `_NON_LITERAL` pins those rows carry keep the other arms green. It now counts the pairs
it examined (`assert checked`; 7 at this head) and pins which refactor empties the candidate set, the way
the sibling arms already guard (`assert census`, `assert pins`, `assert _EXCLUDED_UNTYPED`).

**A third copy of the count was left stale — in the prior art this PR cites.** B2.7 of
`REVIEW-CONSOLIDATED-2026-09-23.md` closed with *"⇒ the customer-callable count is four, not two"* while
§13 and §13.8 stated SEVENTEEN at that pass, and nothing read that file. The durable form is a POINTER, not a fourth
number, so the row now marks its figure superseded and sends the reader to §13's table — and
`test_the_prior_art_points_at_the_table_instead_of_restating_the_count` keeps the pointer there, because
a restated count is a copy that rots on the next pass.

## Two censuses of the same table merge — and the first one's DERIVATION survives it (`#9400`, 2026-10-21)

Two branches made §13 executable inside the same hour. `#9398` landed
`tests/test_7948_about_edge_read_sites.py`, whose table covered **26 sites across 22 rows, 9
customer-callable, 12 door-reaching**. `#9400` is the wider pass: the merged table is §13's table, and its rows and
counts live there, never restated here — it derives the all-edge class a
name-based census cannot see (`SubgraphExpander._neighbors`, `navigation._hop_query`, the backup
dump and the counts that verify and settle it, the org export, the delete blast-radius preview,
the v2.5 backfill), and the carrier-constant readers (`ABOUT_EDGE_TYPES`, `TortoiseSDK._ABOUT_TYPES`).

⚠️ **The first resolution called it a straight supersession, and that was WRONG — it was a
measurement artefact, not a finding.** The claim "every symbol the landed table names is named
here, plus 36 more" came from comparing two different row spellings (`module.py **symbol**` vs
`module.py::Symbol`): the regex matched only one of them, so the other table looked almost empty.
Measured properly, the wider pass had **dropped four rows** the narrower one carried —
`sdk.py::TortoiseSDK.traverse` and `sdk.py::TortoiseSDK.ingest` (both customer-callable
CALL-TIME doors) and `session_link.py::link_entity` / `subject_binding.py::bind_point_subjects`
(both write-path readers whose relationship type is an expression). A name-and-literal census
cannot see any of the four; that is exactly why the narrower guard derived them from the resolved
SHAPE. They are **rows 34–37** now, so the table is a superset **by measurement** rather than by
assertion, and the "36" was gone: 37 rows against 26 was ELEVEN more at that pass.

**The narrower guard retires as a FILE, not as a capability.** The two cannot coexist — each
parses §13 in its own row shape, so the landed guard reads a table it can no longer find and
reddens 10 of its 25 legs. What it *derived*, though, is the part that mattered: the call-time
class (a type slot holding an expression, which no literal scan sees) and both legal spellings of
an empty relationship pattern (the bracketed `-[r]->` and the bracket-less `(n)--()`, the second of
which once left a live member undeclared). Those derivations moved, verbatim, into
`tests/_about_call_time_census.py`; `tests/test_7948_about_object_read_sites.py` consumes them and
pins both directions — every derived site must own a row or a stated exclusion, and every
exclusion must still derive. The prose pins that came with the old file (its own row count, its
`<from> → <to> sites` change chain) could not survive the restructure; they are the narration this
document no longer keeps, and the changelog is where it lives.

The old guard's name now appears nowhere in this file: a guard name in prose is a citation, and a
citation to a deleted file is rot of the same kind the guards exist to prevent — so the mention
above is the retirement record itself, not a citation of a live guard.

What did NOT move with the retirement is the measured property finding (`#9397`/`#9403`): the
`aboutObject` edge is **not** property-free — `confidence` is ORIGINATED by two writers
(`session_link.link_entity`, `subject_binding.bind_point_subjects`) and `idx` is re-materialized
in an inline pattern map by `cache_projection.relation_statement`. The wider census changed the
count, not that measurement, and `tests/test_9397_about_edge_properties.py` still pins it.

## §17.1's replay permit is HANDLE-ONLY, and the raw-query lane sits behind its own answer (`#9391`, 2026-10-22)

`FalkorProjection.query`'s refusal was scope-CONDITIONED on the same
`_CACHE_WRITE_AUTHORIZED` ContextVar every sanctioned lane sets, and
`_replay_write_scope()` sets it around a rebuild engine's body — so the public
raw-query lane, the one door that exists to write the cache WITHOUT the
projection, was a permitted writer for a whole rebuild. Measured, on a
journal-armed projection: outside the window `DirectCacheWriteRefused`, inside it
the same `CREATE` landed. Latent rather than live (no engine called `self.query`),
and pinned nowhere: the handoff on the closed `#9210` cited
`tests/test_9079_record_lane_declared.py::test_the_replay_window_also_authorizes_the_raw_query_lane`,
a file and test that never existed in this tree (the merged `#9213` file is
`tests/test_9079_record_replay_declared.py`, and `git log --all -S` finds no such
commit). The closed issue's scoping comment then excluded the leg as "already
pinned" on that premise; the correction note is on `#9210`, and the shape of the
defect — a withdrawn claim standing in for a measurement — is the one this
changelog exists to keep.

The design decision that replaced it, with its measurements. (1) The permit is
now LANE-DEPENDENT, not scope-dependent: `_raw_query_lane_permit()` answers "did a
lane deliberately open the raw lane too", and `_HANDLE_ONLY_WRITE_LANES` names the
lanes that do not. (2) The closure travels with the SCOPE STACK, because the first
shape of the fix closed only the un-nested window and every real rebuild nests —
`rebuild_all` opens the restore and synthetic lanes per leg inside its own replay
window, and `rebuild_from_record` opens the `schema-ddl` lane for its indexes — so
each nest re-opened the door (measured: the `CREATE` landed under all three).
`_handle_only_write_scope()` therefore carries `_RAW_QUERY_CLOSED_DEPTH` beside the
lane reason, inherited by nested lanes and cleared by the same `finally`. (3) The
match is on the LANE (`_lane_of`), because four of the ledger's seven rows declare
`f"{PREFIX}: {detail}"` templates — an exact-string set would have made adding one
a silent no-op — and the `graph-metadata` row is stated as the matcher's limit,
since its writers share no prefix. (4) Both `replay` declaration sites use the
helper: `_replay_write_scope()` and `event_store.reestablish_watermark`, which
declares its own lane because an inner scope shadows any `with` at its call site.

Two residuals are stated rather than closed. Every lane outside the set still opens
the raw lane when it is NOT nested inside a handle-only window — that width is
`#9079`'s sanctioning decision, and a wholesale refusal there is a measured P0 (it
silently degraded a hosted writer family). And `tests/test_9121_onboarding_residual_pins.py`
derives its opener population from the AST, so routing `_replay_write_scope()`
through the new helper DROPPED it from the population and would have blinded the
escape-(3) detector to any `with _replay_write_scope():` — the fix declared the
delegation hop (`_DELEGATED_OPENERS`) instead of recording only the new name.

Pin: `tests/test_9391_replay_window_raw_query_width.py` (nine legs: the refused raw
lane inside and outside the window, three real nests, the still-permitted handle at
an ARMED door, reads unblocked, the lane-name branch with no window, the
declaration constants, the second declaration site, the rebuild engines still
projecting INSIDE the window through an armed handle, and no permit past its
`with`).

## The narration stamp left §17.5, and the classification states its own ground (2026-10-22, `#8542`)

`#9523` (W1b) corrected the id classification in §17.5 with a `corrected 2026-10-21` stamp and a
"Provenance of that correction" preamble (the rule itself lives in the placement doc's §1.4). The
stamp, the preamble, and the passage's reference to its own wording (*"the sentence above records
three groups"*) are removed: a design document states the rule it settled on, and the record of what
changed belongs here. The ground the classification rests on is stated in the same place instead — a
re-derivation against `tortoise/ids.py` / `sdk._ULID_RE` and `tests/test_identity_contract.py`,
**not** a re-census of the live graph, with the populations still the 2026-10-08 measurement. The
two-store finding and the three id groups are unchanged.

`#9562` (main `22deecf83`) had already taken out the two words the narration guard matched — the
`corrected` before the date — so that arm is green there. What remained, and what this change
removes, is the preamble and the self-reference — shapes the guard's arms did not reach either. That
second gap is closed here too: `_CHANGE_NARRATION` now carries `Provenance of (that|this)
(correction|change)` and `the|this sentence|passage|paragraph|line above records|states|…`, each
measured at 0 hits across the three design documents at this head (so the widening reds nothing) and
pinned in both directions — the verbatim pre-fix lines and a near-miss — in
`test_the_class_check_is_neither_dead_nor_noisy`. Removing the word an arm happens to match is not
the same as pinning the class, which is why the class is now pinned. The entry's own pointer is
pinned by
`tests/test_extractor_v4_citation_rot.py::test_the_changelog_entry_names_the_section_the_stamp_actually_left`.

## §9.1's turn half — the rule stayed, the delivery state left (`#10000`, 2026-10-26)

The rule in §9.1 is unchanged and was already decided: the transcript lives outside the graph
(owner, 2026-09-23 — *"the narrative is not something we're suggesting to store in the graph (same as
raw) but store in supabase"*). What moved here is the paragraph that said **where that rule stands**:
the `✅ … IMPLEMENTED AND MEASURED (#10000, 2026-10-26)` stamp, the before/after narration, and the
note that the record is not armed on any deployment today. Issue state and the narrative of what
changed belong in this file (#8542); §9.1 states the rule they settled on.

What the design document now says in that place — the rule, plus the measured per-turn graph cost and the
pointer to the evidence bundle: the turn write has **one home**,
and the **capability** decides which — turns go to the record whenever the record can hold them (and
then no turn node is written at all; the graph keeps the `:Session` and its counters, and
`get_session_detail` serves the transcript from the record), and the graph write runs unchanged where
it cannot. The store follows the capability, because removing the graph write unconditionally would
put the transcript in no store at all.

Delivery state as of this entry, for the record: the turn half is in `main` and **measured** —
`docs/evidence/10000-turns-bytes/README.md`, three arms on one fixed corpus, 322,063 → 32,032 bytes
per captured session, −90.0%, 0 turn nodes. The record is **not armed in hosting yet** (`#9548`, the
open arming act; its state is readable from `GET /health`'s `record.armed` block, which is `#9989`,
merged), so live deployments still take the graph path by design — the documented capability fallback,
measured **within 0.28%** of the pre-change writer (888 B/session, `measurement-after-unarmed.json` vs
`measurement-before.json`; **not** byte-identical — run-to-run variance, not a re-implementation). The
nodes those deployments already hold are `#10017`'s separate, gated, destructive removal.

Why it left: the class guard
(`tests/test_extractor_v4_citation_rot.py::test_the_design_documents_carry_no_issue_state_or_change_narration`)
reds on an issue-state claim adjacent to a `#NNN` — `IMPLEMENTED … (#10000` is exactly that shape —
which is the same GUARD (the same test) whose other arm moved §13's issue map out with the
`CORRECTED 20xx-xx-xx` stamps (`_CHANGE_NARRATION`, a different regex) on 2026-10-15.

**2026-10-27 — `#9417`.** `sdk.supersede_point` stopped being a declared false positive: its structural transfer now carries the old edge's properties (`ON CREATE SET nr += $attrs`), so an `about*` edge keeps the binder's `confidence`. **Review round 2 corrected the form:** a bare `SET nr += $attrs` overwrites per key, so on the COMMON restatement path — where the successor already holds its own binding to the same subject — the RETIRED point's value replaced the successor's live one (measured 0.91 -> 0.42, unrecoverable: the repoint descriptor carries no `attrs`). `ON CREATE` states the precedence the pre-`#9417` bare `MERGE` already had: the successor's own value wins. The dead `source_version` whitelist entry went with it — the graph writes the anchor as `sourceVersion` (`projection/edges.py:504`), so the entry could never match and only implied a carry that never happened. The census composition is therefore **2 originating + 4 re-materializing** (6 writers: 3 NAMED + 3 MAP) with **one** declared false positive (`projection._rebuild_all_replay`). The REBUILD half is still bare — `#10139`.

**2026-10-27 — `#10149`: the census pins CLAIMS, and §13.8 q2's pre-`#9417` sentence went with it.** The
`aboutObject` census compared the documents' NUMERALS to its tables (`"6 writers"`, `"2 ORIGINATING"`,
`"one declared false positive"`) and never the sentences around them, so a section could state the
opposite of the table it sat beside and stay green — measured four times on the five-line `#9417` fix.
Four legs now pin the claims: every table key must be named in a sentence that states its CLASS
(`MAP-form` / `NAMED form` / exemption), no passage may call a counted writer an exemption or say it
drops / carries no property (history-marked statements — *"was a declared false positive before
`#9417`"* — remain writable, because that record is true), the two numerals that were TYPED rather
than derived — the declared-false-positive count in every spelling, and the invisible site's ordinal
(`total + 1`) — are now derived from the tables, and the VOCABULARY all three are written in is itself
checked: no marker may be a spelling `_flat` cannot produce
(`test_no_marker_is_written_in_a_spelling_the_flattener_cannot_produce`, review round 2). The count is
over the four tests that ship (`tests/test_9397_about_edge_properties.py`, legs 33–36 in its pin
index), not over the three the first form of this entry described.

**That third class of sentence was still live in a design document.** `EXTRACTOR-V4-ARCHITECTURE.md`
§13.8 q2 carried the pre-`#9417` world: *"**not** `supersede_point` … so neither carries a property on an
about edge, and the guard declares both census FALSE POSITIVES (`supersede_point` even DROPS the
property, `#9417`)"*. Every numeral in it is arithmetically consistent with the tables, which is why
nothing saw it. It now states the measured population — six writers, the supersede transfer counted as
property-MAP since `#9417` (`ON CREATE SET nr += $attrs`, successor's live value winning), and ONE
declared false positive, the rebuild replay `projection._rebuild_all_replay`, whose about/structural
transfer is a separate bare `MERGE`. The REBUILD half of the supersede transfer stays bare (`#10139`).

**2026-10-27 — `#10149` review round 1 corrected TWO things this PR's first form got wrong, and the `#10200` review measured both.** (1) *The `DirectEdgeCreated` protection story was false in BOTH documents.* Both said the fold's protection is that its producer is `{IMPL, NAND}`-only — `STORAGE-ARCHITECTURE.md` §13 stated it flatly (*"The `DirectEdgeCreated` fold's only producer (`create_direct_edge`) refuses any `op_type` outside `{IMPL, NAND}`"*) and §13.8 q2 said the same in one clause. **The fold has had TWO producers since `#9553`:** `sdk._record_structural_edge` journals whatever predicate the structural-edge mints wrote — the whole `about*` family included — and what keeps a property off that edge is that it emits NO vocabulary key, i.e. the attribute map is EMPTY, not that the relation is restricted. The old sentence also mis-framed the `#10139` fix, which has to audit the about-capable producer — the one the documents did not name. Both passages now name both producers. (2) *The census's own escape hatches were wider than the record says.* `HISTORY_MARKERS` carried a bare `#10139` — an OPEN defect reference, not a statement that the state changed — and excused every denial in the sentence, so the retracted cycle-3 sentence plus a trailing `#10139` reference was green (its own probe now pins that red). The escape is now read in the CLAUSE that carries the denial; the class matcher is polarity-aware (a NEGATED class marker DENIES the class it names, so *"is not a re-materializing writer"* — `#10149`'s claim in the class's own vocabulary — reds on both legs); and the `MAP` vocabulary's `re-materializ` marker, whose hyphenated form `_flat` renders as `re materializ`, matched **zero** occurrences in either document and is now written in the flattened spelling (0 hyphenated vs 8 flattened, measured).

**2026-10-27 — `#10149` review round 2 closed four more, and three of them were the SAME defect class in the instruments themselves.** (1) *A marker written in a spelling `_flat` cannot produce is a DEAD marker.* `_flat` turns `-` between word characters into a space, so `"MAP-form writer"` in `EXEMPT_COUNTED_MARKERS` matched **0** flattened occurrences — an exemption asserted to *"write the attribute set by property MAP"* was green, and only the literal word `counted` red it. `"MAP-form"`, `"NAMED-form"` and `"re-materializ"` were dead the same way, and a **probe per family is not enough to keep this closed**: reverting three of the four flattened spellings left the suite green, because a live neighbour covered the same sentence. The invariant is therefore static and covers every member of all four tuples — no marker may contain a hyphen between word characters, and every marker must be present in the flattened documents or listed as absent-by-design with a reason (`test_no_marker_is_written_in_a_spelling_the_flattener_cannot_produce`). (2) *The history escape was comma-blind and symbol-blind.* A comma-joined run was ONE clause, so *"was a declared false positive before `#9417`, and it still drops the property"* — history kept, the denial NOT corrected, i.e. exactly the `#10149` class — was green, and so was the variant whose history marker belonged to a DIFFERENT writer. `,` is now a clause boundary. (3) *The polarity rule red TRUE prose.* A quantifier negation (*"is not the ONLY writer that carries the set by property MAP"*, the idiom §13 already uses) read as a class negation, so an affirmative sentence redded on both legs — and the cheapest way for an editor to clear that red is to weaken true prose, the failure mode the legs exist to prevent, pointed the other way; `not the only` / `not solely` are now excluded from the negator scan. (4) *The census's own record still said `create_about_edge` "journals no `DirectEdgeCreated` at all"* — false since `#9553` (`projection/edges.py::create_about_edge` → `_record_structural_edges` → `sdk._record_structural_edge` → the fold), and the opposite of what §13 and §13.8 q2 now say. It names the empty attribute map instead.

**2026-10-27 — `#10149` review round 3: two more ways to state the class, and one overclaim in this record.** (1) *The comma that closed round 2's history escape silently re-opened round 1's polarity finding.* `,` belongs in the history escape's break set but NOT in the negation scan's: a parenthetical between the auxiliaries and the class hid the negator, so *"`sdk.supersede_point` is not, however, a re-materializing writer"* was green on BOTH legs. The negation scan reads the MARKER'S OWN predicate (`_marker_itself_negated`, with the comma-set parenthetical as an explicit alternation), and ⛔ the `_NEGATOR_BREAKS` break set this entry originally named was NEVER REACHABLE — its only caller was unreferenced — so cycle 5 deleted it and moved the one rule inside it that mattered (`_QUANTIFIER_NEGATION_RE`) into the live scan (see below). (2) *Polarity was read only BACKWARD.* English negates a predicate from behind it and these documents write `no longer` 14 times, so *"is a re-materializing writer no longer"* stated the class, and the exemption form *"… a declared false positive no longer: it re-materializes the set"* called the exemption counted — the direction the recorded decision forbids. A trailing-idiom read (`no longer`/`no more`/`no further`) closes it; a whole-clause negator scan does NOT (measured: it reds §13's true *"A third NAMED form writer does not originate it"*). (3) *This record overstated what was pinned.* "A probe per marker family reds when a marker stops matching" was false — three of the four flattened-spelling repairs were unpinned, and a live neighbour marker covered the same sentence. The instrument is now static rather than per-family, and it covers all four tuples (see round 2, reworded above).

**2026-10-27 — `#10149` review round 4 (cycle 5): three ways the instrument was fail-OPEN, and two of them were in rules this record already claimed were fixed.** (1) *Both denial escapes were quantified per MARKER instead of per OCCURRENCE, so one excused occurrence cancelled a second, un-excused denial of the same marker in the same sentence.* Measured: *"`sdk.supersede_point` was a declared false positive before `#9417`, and the guard still declares it a false positive at HEAD"* was GREEN — history kept, the denial not corrected, which is precisely the `#10149` class — and so was *"… is not a false positive, and it is a false positive"*. The counted branch now decides per occurrence (`_denies_the_class` / `_history_in_clause`), the same fail-closed direction the EXEMPT branch always had; one probe per escape reds when the per-marker quantifier is restored. (2) *`_QUANTIFIER_NEGATION_RE` — round 2's fix — lived in `_has_class_negator`, which nothing called.* The punctuation-window rewrite left it, `_clause_before`, `_NEGATOR_BREAKS`, `_COORDINATING_COMMA_RE` and `_NEGATOR_RE` unreachable (an AST call-graph found no caller; gutting all of them left the suite green). **So round 2's claim that `not the only` / `not solely` are excluded was FALSE from round 2 until now**, and its false red was live on both legs: *"`cache_projection.relation_statement` is not only a MAP form writer but the canonical one"* red as `not MAP form`. The exclusion now runs in `_marker_itself_negated`, and the dead window machinery is DELETED rather than left reading as coverage. ⚠️ A rule that only exists in dead code is not a rule — this is the same defect this PR corrected twice for `sdk.create_edge`, in the instrument instead of the census. (3) *The exemption denial was the bare stem `exempt`, whose "measured safe" reading was an artifact.* Its only `exempt*` matches in either document are `exempts` / `exemptions` / `exempting` — the one spelling that is NOT a denial — so it caught nothing and its only live effect was a FALSE RED: the documents' own §13 sentence *"the guard pins the shapes that make both exemptions true instead of merely exempting them"* red as `'exempt'` the moment a census key was named in it. The denials are now the predicate and articled phrases (`is exempt`, `is exempted`, `an exemption`, `the exemption`), each listed in `MARKERS_ABSENT_BY_DESIGN` because the corpus does not write them yet, and a probe pins that the corpus's own sentence stays green with a counted key named in it.

**2026-10-27 — `#10149` review round 5 (cycle 6): two fail-OPENS closed, two FALSE REDS removed, and one claim in this record corrected.** A fresh-context review of `#10200` executed each mutation rather than reading the rules, and measured both directions.

1. *The HISTORY escape's clause ended at punctuation only, so a comma-less coordination put the history marker and a present-tense denial in ONE clause.* *"`sdk.supersede_point` was a declared false positive before `#9417` **and** it still drops the property on an about edge"* was GREEN — one omitted comma away from the cycle-3 sentence, i.e. exactly the class this leg exists for. The history escape now breaks at the coordinators that join predicates (`and` / `but` / `yet` / `while` / `whereas`) as well as at punctuation (`_HISTORY_BREAKS`). The plain comma set is deliberately NOT widened: the count rule reads its window from it, and *"6 writers, 3 by property MAP, and 1 declared false positive"* must still be read as one.
2. *The denial vocabulary listed only the AFFIRMATIVE phrasings, so the owner's decision was escaped by changing one auxiliary.* *"`sdk.supersede_point` **does not carry** the binder's `confidence` on an about edge"* and *"`hosted_backup.restore_graph` itself **never writes** the attribute set at all"* were both GREEN, and so was the exemption direction's verb voice — *"`projection._rebuild_all_replay` **counts as a writer** of the attribute set"* — because only the participle `counted` was listed. The negated-auxiliary forms and the verb form are markers now, each keeping its determiner so a neighbour's object (`ingest_corpus` *"never writes a Source"*) does not red.
3. *Two rules red TRUE prose, which is the failure mode that makes an editor weaken correct sentences.* `not merely` / `not just` are QUANTIFIER negations read as class denials (*"`sdk.supersede_point` is not merely a MAP form writer but the canonical one"* red as `not MAP form`), and the post-positioned negation scanned the whole CLAUSE, so a NEIGHBOUR's `no longer` suppressed a marker it does not govern (*"An unconditional `SET nr += $attrs` no longer applies to `sdk.supersede_point`; it uses `ON CREATE`"*). The post half is predicate-scoped now (≤ three word-tokens behind the marker), and `_POST_NEGATION_RE` — the clause-wide scan — is DELETED rather than left reading as coverage.
4. *This record overstated two things, and the instrument's own docstrings were honest where the record was not.* (a) *"the declared-false-positive count in every spelling"* was false BACKWARD-only: a count written AFTER the phrase (*"The declared false positives are two in total"*) and the `only …` form produced NOTHING, so silence and an uncovered spelling were indistinguishable. Both are read now. (b) The AFFIRMATIVE leg's window is the SENTENCE, and in these passages a sentence reaches ~3.9k chars (§13) and ~780 (§11.1), so class vocabulary printed for one writer satisfies a DIFFERENT writer named later in the same sentence — measured: stripping the NAMED verdict from both sentences naming `_fold_entity_linked_reason` left the leg green. ⚠️ The alternative was measured and is NOT available: a CLAUSE-scoped affirmative rule would red 13 of the 17 live statements, whose verdict rides in the appositive or the following clause. The bound is recorded in `_claim_sentences` so it is a stated limit rather than an implied promise.
5. *One dead constant deleted.* `INLINE_FORM` was a sentinel nothing reached (the inline form's property NAMES are statically readable), and in a module whose doctrine is *"a rule that only lives in dead code is not a rule"* it read as coverage.

Every closure is pinned by a probe, each verified by REVERTING the fix and watching that probe red (measured: 6/6), and the two false reds by probes that must stay green.

**2026-10-28 — `#10149` review round 6 (cycle 7): the denial vocabulary pinned one VERB and one TENSE, and two of the instrument's own records claimed coverage they did not have.** A fresh-context review of `#10200` executed the rule rather than reading it, and measured both directions.

1. *The post-negated escape survived by swapping the verb.* `no longer carries the` was a member of `DENIAL_MARKERS`; `no longer writes the` was not, so *"`sdk.supersede_point` no longer writes the attribute set on an about edge"* was GREEN. ⛔ Worse, `MARKERS_ABSENT_BY_DESIGN["no longer writes"]` recorded the absent bare form as *"its determiner form is recorded above"* — and the only determiner form recorded was the CARRY verb. That entry was therefore a FALSE CLAIM of coverage in a record whose whole doctrine is *"a marker that matches nothing pins nothing"*. The determiner form is live now, and the entry names which verb it does and does not cover.
2. *The transfer denial was pinned in the PAST tense only.* `transferred bare` is the literal tripwire for the retracted sentence; the PRESENT tense — the tense a live denial is written in — matched nothing, so *"`sdk.supersede_point` transfers the about edge bare"* was GREEN. It is read by `DENIAL_PATTERNS`, a FRAME (a transfer predicate, then `bare` inside the clause) rather than a list of determiner spellings, because the object between the verb and `bare` varies (`it` / `the edge` / `the about edge`). ⚠️ STATED LIMIT: the VERB forms only — the NOUN form (*"the transfer is still bare"*) is deliberately not read, because the REBUILD note `#10139` requires is TRUE prose a counted writer's sentence may carry, and a noun-matching pattern would make deleting that note the only way to clear the red.
3. *Two bounds of the denial leg were recorded only where no lane reads them.* (a) The denial is attributed to EVERY census key named in the SENTENCE, so a sentence naming two keys and denying the class of one reds for both — measured, and **fail-closed on purpose**: the retracted sentence is itself multi-key (*"… are not one of them, so **neither** carries a property"*), and no mechanical rule was found that keeps it red without resolving a plural antecedent by English heuristic. The cost is a FALSE RED whose resolution does not weaken the prose — state each writer's verdict in its OWN sentence — and that resolution is pinned by a probe that must stay green. (b) A denial that names NO symbol is invisible (cycle 4's *"the guard declares that row a FALSE POSITIVE"*), and that bound lived only in the `#10149` scoping comment; it is in the artifact now.
4. *Round 6's own mutation count.* The round-5 entry's *"6/6"* covers the closures of rounds 1–5; round 6's closures are **2/2**, measured the same way — reverting `no longer writes the`, and removing the `DENIAL_PATTERNS` application, each reds exactly the leg and probe it was written for — and its two GREEN-direction probes (`does not transfer the about edge bare`; the contrast split into two sentences) are pinned as prose that must stay writable, not as proofs of absence.

**2026-10-28 — `#10149` review round 7 (cycle 8): the denial vocabulary was a LIST OF SPELLINGS, so it knew only the spellings in it.** Every sentence below was GREEN on a counted MAP row when spliced into §13 and run through the leg's own functions:

1. *The verb VOICE was unpinned.* Every literal spelled its denial in the third person, so the BASE/INFINITIVE form — *"`sdk.supersede_point` is said to drop the property"*, which is the wording of this issue's own decision (*"said to drop / carry no property"*) — matched nothing, and neither did the OBJECT voice (*"the guard EXEMPTS `supersede_point`"*, *"Rule 2 KEEPS `supersede_point` OUT OF the counted rows"*, *"the census DOES NOT COUNT `supersede_point`"*), the PASSIVE (*"No property IS WRITTEN BY `sdk.supersede_point`"*) or the ADJECTIVE (*"leaves its about edge PROPERTY FREE"*, which is the retracted §11.1 claim itself). `DENIAL_PATTERNS` is now one FRAME PER VOICE, and every entry declares its own probe pair (`pattern` + `must_see` + `must_not_see`) so a new frame cannot land unmeasured — the width used to belong to the leg rather than to the entry. The rule is asserted both at the frame and THROUGH the escapes, because *"does not drop the property"* matches the drop frame and is still correct prose.
2. *The property was named by the wrong NOUN.* The vocabulary pinned the generic word `property` and missed *"carries no `confidence`"* — the spelling the guarded passages actually use (§13: "keeps the binder's `confidence`").
3. *A frame that stopped at `.` could not see a census KEY.* A key contains a period (`sdk.supersede_point`), so the period-bounded first draft left the `exempts` and `keeps … out of` frames DEAD against their own probes; the voice frames read `[^;,]` instead.
4. *The count reader stopped at the width of a TYPED token.* `_COUNT_NUMBER_TOKEN` spelled its words out by hand and stopped at `six` while `NUMBER_WORDS` reaches `twelve`, so a count written above six was silently unread — invisible because the width guard one leg up says the tables already cover 7–12. It is DERIVED from `NUMBER_WORDS` now.
5. *`_denied_verdicts`' STATED LIMITS enumerated two bounds while a third — the open vocabulary — was unlisted.* The enumeration asserts it is complete, so the missing bound was itself a false coverage claim. Three now.

Measured at that head: all 18 measured-green sentences red; the three guarded passages stay green (`_denied_verdicts(_census_sections()) == []`, so no false red on the documents); eleven true-prose controls stay green; all twelve `NUMBER_WORDS` read as counts.

**2026-10-28 — `#10149` review round 8 (cycle 9): the object-negation frame was bound to ONE verb, "the exemption may not be counted" to the PRONOUN, and `MARKERS_ABSENT_BY_DESIGN` was only ever read in one direction.**

1. *The `carry` frame was bound to `carry|carries`.* The same construction with the file's OWN sibling verbs was green on a counted MAP row — `writes no property` (how §13 states the EXEMPTION's verdict, a member of `CLASS_VERDICT_MARKERS["EXEMPT"]`), `writes no confidence` and `sets no property` — while the `carry` control red. The frame reads `carry|carries|write|writes|set|sets` over the same object, with a `must_see` per verb, plus the control that keeps it out of the bare `writes no` prefix.
2. *The population SPELLED OUT was green.* "The exemption may not be called counted" was pinned on the pronoun (`is one of them`) and the participle (`counted`), so *"is one of the six writers"* — in words or in digits — matched nothing. `EXEMPT_COUNTED_PATTERNS` carries that frame, built from `NUMBER_WORDS` plus digits the way `_COUNT_NUMBER_TOKEN` is, because a STALE count is still a counted claim and so the width must not be typed. It declares its own `must_see`/`must_not_see` pair.
3. *`MARKERS_ABSENT_BY_DESIGN` was checked marker → declaration and for declaration staleness, never declaration → marker,* so `does not write` and `no longer writes` exempted nothing (their determiner forms are the live markers) and read as coverage. Both entries are deleted and the missing direction is asserted, so the next orphan reds.

Non-vacuity was measured against the pre-fix module at the same doc text: the five new probe sentences were all green before and all red after; the orphan subset was violated before and holds after; the three true-prose controls stay green.

**2026-10-28 — `#10149` review round 9 (cycle 10): three findings, all in this PR's record about itself rather than in the legs — and each is now pinned by the class it belongs to.** A fresh-context review of `#10200` settled all three by reading the tree at the head.

1. *The producer-evidence block cited `sdk.py` LINE NUMBERS, three of which no longer landed on the emitters.* The paragraph justifying `DIRECT_EDGE_CREATED_ATTR_GUARDS` pointed at `sdk.py:8493`, `sdk.py:23117` and `sdk.py:41317`; at the reviewed head those are `poll_events`, `list_batch` and `assess_source`, while the emitters sit in `_record_structural_edge` and `create_direct_edge`. A lane auditing "exactly two emitters" would land in prose — the same unpinned-number failure this PR removed from the documents, in the file that enforces the rule on them. The emitters are named by SYMBOL now, and `test_the_producer_evidence_block_cites_the_emitters_by_symbol` derives the enclosing functions from the AST and reds on a bare `sdk.py:<N>`.
2. *This ledger stopped at round 6 (cycle 7) while the artifact it indexes cited a cycle 9 review in four places,* so the two rounds that introduced the widest denial frames and the newest vocabulary table (`EXEMPT_COUNTED_PATTERNS`) had no entry at all — in a record whose own convention is that a bound left out is a bound a lane will rely on. Rounds 7 and 8 are above, and `test_the_ledger_records_every_review_cycle_the_record_cites` derives the newest cycle from both files so the ledger cannot fall behind again.
3. *A pin-index entry stated a numeral its own leg contradicted* (entry 33 said five review cycles where the leg's block says four, and `#10149`'s table lists exactly four). The value is `four` now, and `test_the_pin_index_states_the_9417_cycle_count_its_own_leg_does` reads the two statements against each other, because a count typed twice is exactly the defect class this census exists to close.

No leg, no document correction and no vocabulary entry changed in this round: the three findings are all in the record, and none of them asked the design to move.

**2026-10-29 — `#10149` review round 10 (cycle 11): one vocabulary table was still a hand-typed copy, and the PR body did not describe a third of the diff.** A fresh-context review of `#10200` at `bf96007bb` measured both by reading the tree, and reported nothing against the legs' logic: the four census legs and the new docs-lint producer were re-measured green, and the corrected prose was checked against `sdk.py`, `hosted_backup.py` and `cache_projection.py` line by line.

1. *`test_the_document_population_counts_match_the_census` carried a THIRD ordinal table — `{2: "second", … 6: "sixth"}` — beside the shared `ORDINAL_WORDS`, reaching only six while the cardinal family reaches twelve.* `ordinals[named]` is INDEXED from it, so a `RECORDED_WRITERS` grown to seven would have raised `KeyError: 7` here instead of reding with the actionable message the sibling leg gives ("extend `NUMBER_WORDS`/`ORDINAL_WORDS` rather than dropping the derivation"). It was the exact shape this file documents as a defect twice, in the same function that had just replaced its `words` literal with `NUMBER_WORDS`. The copy is gone (`ordinals = ORDINAL_WORDS`), and a span assertion pins the family nothing pinned before — `ORDINAL_WORDS` must run `2..12` against `NUMBER_WORDS`' `1..12` — so a re-narrowed copy reds with a message naming what to extend. Proved non-vacuous: restoring the literal reds it with ``assert (2, 6) == (2, 12)``.
2. *The PR body described four of the diff's nine files.* The three `fix(10265)` commits replace the invocation of **both** `Markdownlint` steps of the REQUIRED `docs` job with `.github/scripts/docs-markdownlint.sh`, and add its 365-line test module — and neither `#10265` nor the producer appeared in the body, so merging would have closed `#10149` while shipping an unannounced change to a required check and leaving `#10265` open. The body now carries that section and `Closes #10265`; the two recorded reviews in it are both ancestors of the head, so the producer's own commits were the ones no attestation covered.

No census vocabulary entry, no document correction and no producer behaviour changed in this round.

**2026-10-29 — `#10149` review round 11 (cycle 12): the record's own citation rule was enforced over one block, and the block the census leans on still carried a wrong number.** A fresh-context review of `#10200` at `450c8d885` reported one P2 and nothing against the legs' logic; it settled the finding by reading the tree at the head (`tortoise/cache_projection.py`:717 is `if section is None:` in the row-matching loop, at HEAD — `relation_statement` at `:1497` and the `{idx:$idx}` template at `:1520`, unchanged on `origin/main` — this PR touches no `tortoise/` file).

1. *`#10200` cycle 10 closed the bare-line-number class in ONE `#:` block, and the same class survived immediately outside it.* `test_the_producer_evidence_block_cites_the_emitters_by_symbol` applies the citation rule to the `DIRECT_EDGE_CREATED_ATTR_GUARDS` evidence pointer only, so the `MAP_WRITERS` justification — the census's evidence for its THIRD write form, the row the documents rely on for `confidence` + `idx` — still pointed at a line ~800 from the code it quotes, and two assertion messages repeated that number. A lane auditing that row followed it to a `section = None` branch and could only conclude the record was stale, or re-derive the inline-form rule from scratch, in the artifact that closes exactly that defect. The number is gone from all four places and the pointer is the symbol the sentence already named. The scan is now file-wide over the `#:` COMMENT layer (test DATA stays exempt: legs carry pre-fix spellings on purpose, and a blanket ban would red on the probes that keep the rules honest), plus a file-wide ban on a line number for `cache_projection` — the assertion messages are where two of the three stale pointers lived and a comment-only scan never reaches them. Proved non-vacuous: the pre-fix block, restored from this entry's own record, reds the scan.
2. *Nothing else moved.* No census vocabulary entry, no document correction and no producer behaviour changed in this round; the four census legs, the `#10265` producer and the corrected prose were re-measured green at the head.

**2026-10-29 — `#10149` review round 12 (cycle 13): the polarity read knew one ADVERB and not the OBJECT voice of the same denial, so it red TRUE prose about the exemption — and left `#10149`'s own claim green when it was written that way.** A fresh-context review of `#10200` at `3a20e63e9` executed the leg's own functions rather than reading them, and reported one P2: latent, no check red at the head, but a measured false-red trap on TRUE prose in the direction this record treats as P2 (an editor's cheapest way out of a false red is to weaken true prose).

1. *`_MARKER_NEGATION_AFTER_RE` held only `no longer` / `no more` / `no further`, so an OBJECT negation after a class marker read as an ASSERTION of that class.* `nothing` / `no <property noun>` is the spelling this file's own `DENIAL_PATTERNS` recognises for a counted writer, and it is the spelling the EXEMPT row's verdict is written in (*"This writer carries NO property on an about edge"*) — so the exemption's own wording red through `EXEMPT_COUNTED_MARKERS`. Measured at `3a20e63e9` by splicing into the leg's own functions: *"`projection._rebuild_all_replay` re-materializes nothing on an about edge"* RED, and so was the two-clause form. The mirror was the worse half: *"`sdk.supersede_point` re-materializes nothing on an about edge"* was GREEN — `#10149`'s claim restated in the class's own vocabulary, unread on both legs. The post half now reads the object voice too (`_PROPERTY_OBJECT_NEGATION_AFTER_RE`, the same `_PROPERTY_NOUN` `DENIAL_PATTERNS` uses, the same ≤ THREE word-token window the adverbial half has always used), and it additionally requires that no `_PREDICATE_TERMINATORS` word sits between the marker and the negator: without that bound *"drops the property **and** carries nothing"* read the SECOND predicate's object as a denial of the FIRST marker and cleared a real denial. Bounding a negation's scope with termination terms is the field's own rule (the ConText / NegEx family), which is what the external rung of this round's research returned.
2. *The bound was unstated.* `_denied_verdicts`' `STATED LIMITS` list enumerated three bounds and asserts it is COMPLETE, and this was a fourth in the harmful direction. It is limit 4 now, with what it cannot read (a property noun outside `_PROPERTY_NOUN`, a predicate longer than three tokens, a negator past a coordinator) and the direction each misses.

Proved non-vacuous in both directions: probes 25's two TRUE-prose sentences red at `3a20e63e9` and stay green now, its counted-writer sentence was green at `3a20e63e9` and reds now, and its two coordinated sentences go green if the terminator bound is removed.

No census vocabulary entry, no document correction and no producer behaviour changed in this round: the "re materializ" family, `RECORDED_WRITERS` / `MAP_WRITERS` / `NOT_AN_ABOUT_WRITER` and the corrected passages are untouched, and `_denied_verdicts` / `_unstated_verdicts` over the guarded sections stayed `[]` at the new head.

**2026-10-29 — `#10149` review round 13 (cycle 14): probe 25's third TRUE-prose sentence was labelled a WIDTH control and could not fire in either direction — and the head's two records contradicted each other about it.** A fresh-context review of `#10200` at `fac4728cb` reported one P2, in the class this record exists to stop: a claim that reads as coverage while pinning less than it claims. Nothing was red at that head; this is a record and probe defect, not a behaviour change.

1. *The width of `_PROPERTY_OBJECT_NEGATION_AFTER_RE` was said to be guarded by probe 25's third sentence, which cannot guard it.* That sentence names the exemption and carries NO counted marker, so the frame is never consulted on it — on the EXEMPT branch both hit sources guard with `not _marker_itself_negated(...)`, so the frame is a SUPPRESSION only there and a widening of it can only clear a hit, never raise one — and the sentence is green at EVERY width of it. Measured with the frame as written and with it widened to a bare `no \w+`: `[]` in both. The label is gone, the entry is recorded for what it is (a TRUE-prose pin), and the width is now asserted on the frame ITSELF, against a marker's own tail, in both directions — with the negative half proved able to fail by running that same tail through the bare-object widening a later lane would otherwise adopt unmeasured.
2. *The head contradicted itself, and the head commit was the witness.* `fac4728cb` rewrote the probe's comment to say the third sentence is GREEN at `3a20e63e9`, while round 12's sentence — added one commit earlier by `4b7423f7c` — put that count at `three`. `two` is the measured count: `3a20e63e9` carries no `re-materializes nothing` probe at all (those sentences are `a6a241eb0`'s), and the third carries no marker, so a splice of it into that revision's functions could not red either. The count is corrected in round 12 above and PINNED against the probes themselves, so a later entry has to update the ledger instead of falsifying it.

No census vocabulary entry, no document correction and no producer behaviour changed in this round: the frame's width, `EXEMPT_COUNTED_MARKERS` and the census tables are untouched, and `_denied_verdicts` / `_unstated_verdicts` over the guarded sections stayed `[]` at the new head.

**2026-10-29 — `#10149` review round 14 (cycle 15): the count window DOCUMENTED a sentence bound it did not implement, so a numeral from a neighbouring true sentence was read as this one's count — in both directions.** A fresh-context review of `#10200` at `71d7f457b` found the gap between a bound and its own record; nothing was red at that head, which is why it is recorded here rather than merely fixed.

1. **The documented bound the code did not have.** `_COUNT_WINDOW`'s comment said *"and never across a sentence end"*, and the `other_clause` probe's comment said the guarded paragraph separates counts with `;`, `:` and `—` **as well as `.`** — but the window was bounded by `_CLAUSE_BREAKS` ALONE, and that tuple holds no full stop. Measured before the fix, in both directions: `_declared_false_positive_counts` returned `6` for *"The census counts 6 writers. The declared false positive is …"* and `6` for *"The declared false positive is …. 6 writers are counted."*, where `[]` is the answer for both — a numeral in ANOTHER sentence says nothing about this phrase. Both are FALSE REDS on two true sentences, and the cheapest way out of a false red is to reword a true sentence or move a correct numeral: the failure mode rounds 2, 3, 5, 6 and 13 filed against the denial half, pointed the other way. In the forward direction it also let a numeral from the NEXT sentence stand in for a count the phrase does not state, so a stale count could read as the expected one.
2. *The fix is ONE shared rule, not a second hand-rolled window.* `_SENTENCE_BOUNDARY` (`[.!?]` followed by whitespace) is now the single definition of a sentence end: `_claim_sentences` splits on it and `_crosses_a_sentence_end` applies it to the before and after windows alike, so the two cannot drift apart. ⛔ The period is deliberately NOT added to `_CLAUSE_BREAKS`: `_clause_at` and the history escape read that tuple, and a sentence is a WIDER bound than a clause, so collapsing them would weaken both.
3. *The width is pinned in BOTH directions, including the direction that must stay readable.* The two sentences above must read as silence, and a dotted symbol inside the SAME sentence (*"one declared false positive at `projection._rebuild_all_replay`"*) must still read `1` — the narrower spelling (treating any `.` as a boundary) would red the documents' own house wording. Neutralising `_crosses_a_sentence_end` reds the first probe with *"a numeral from the PREVIOUS sentence is attached to `declared false positive`"*, so the pins are not vacuous.
4. *The looseness is STATED, not implied away.* The rule does NOT suppress abbreviations, so the period in `e.g.` IS read as a sentence end. That looseness is pre-existing in `_claim_sentences` and is retained rather than half-fixed, because one definition of a sentence end is the property this module needs and every past defect in this area came from two rules drifting apart. The candidate set itself is the one comparable segmenters start from (NLTK Punkt's `sent_end_chars`), and requiring the WHITESPACE is what keeps a dotted symbol in one sentence — Punkt's own motivating example is that a period alone is not a boundary.

This round is also the reason the ledger leg now reads in BOTH directions: the module cites this round as `cycle 15`, so `test_the_ledger_records_every_review_cycle_the_record_cites` reds if this entry is ever removed. The leg had been one-sided since round 9 — it could only catch a ledger that fell behind a CITED cycle — and a round whose comments carried no cycle number was invisible to it, which is exactly how this entry came to be missing while the leg stayed green.

No census vocabulary entry, no document correction and no producer behaviour changed in this round: `MAP_WRITERS` / `RECORDED_WRITERS` / `NOT_AN_ABOUT_WRITER`, the corrected passages and the `#10265` producer are untouched, and `_denied_verdicts` / `_unstated_verdicts` over the guarded sections stayed `[]` at the new head.

**2026-10-30 — `#10139`.** The REBUILD half of `#9417`, and the end of the LAST declared false
positive. The pass-2b structural `DirectEdgeRepoint` fold re-created a transferred `about*` edge with
a BARE `MERGE`, so the `confidence` the live transfer carried across a supersede was DROPPED on every
`rebuild_all` / `recover_from_log` — a live != rebuild divergence on a data-bearing property (the
`#9553` class; measured: 0.83 live, edge present with `{}` after the rebuild). The descriptor that
feeds that fold now carries the retired edge's attributes (flat, the same keys `DIRECT_EDGE_ATTR_KEYS`
holds for the 2a-DIRECT descriptor and its emission), and the fold re-applies them with `ON CREATE SET
nr += $attrs` — the SAME precedence the live transfer uses, so a successor that already holds its own
`confidence` keeps it (measured: 0.91 stays 0.91; an unconditional `SET` put the retired point's 0.42
there instead). `projection._rebuild_all_replay` therefore carries `confidence` on an about edge and is
a COUNTED map-form writer: the composition is **2 originating + 5 re-materializing**, **7 writers the
census sees (3 NAMED + 4 MAP)**, and zero declared false positives. The five transfer keys are named in
ONE place (`projection/edges.py::DIRECT_EDGE_ATTR_KEYS`) instead of a literal tuple at each emission
and replay site — the drift this row was one change away from, since the live and rebuilt halves each
had their own copy. Pinned by `tests/test_8398_aboutobject_replay.py` (the replay, behaviourally) and
`tests/test_9397_about_edge_properties.py` (the census).

**2026-10-30 — `#10139` / `#10303` review P1 — the RECORD route carries them too.** The pass-2b
fold above was only ONE of the two rebuild routes, and the other one
(`record_projection._structural_repoint_statements`, the 2b MOVE's insert leg) still wrote the moved
`relation` row with an EMPTY attribute set — so the descriptor carried the retired edge's
`confidence` and the journal-rebuild restored it while the record rebuild dropped it. That is the
`#9553` divergence class created by THIS change rather than existing before it: until `#10139` both
routes dropped the value, so they agreed. The move now reads the descriptor through the SAME
vocabulary the fold replays (`DIRECT_EDGE_ATTR_KEYS`, restated in `record_projection` for the
STANDALONE reason its other constants are and pinned to the fold's own tuple), and routes each name
through `_split_properties`: `direction`/`confidence`/`weight`/`label`/`batch_id` are COLUMNS of
`public.relation`, so they land there and the row's `props` tail stays `{}` — the tail is not where
this carry is observable. `ON CONFLICT … DO NOTHING` is kept, and it IS the fold's precedence rather
than an approximation of it: a row the successor does not have is inserted carrying the values, a row
it already has is left byte-identical, exactly as `ON CREATE SET` leaves it (`DO UPDATE` would
reproduce the measured 0.91 → 0.42 destruction). `sourceVersion` stays uncarried on BOTH sides — the
fold drops it too — so the two routes agree by both losing that anchor rather than the record keeping
an attribute the graph does not have. Pinned by
`tests/test_record_projection.py::test_a_structural_repoint_carries_the_descriptors_attributes`.

## §9.1's carrier paragraph — the design stayed, the delivery state left (`#10067`, 2026-10-30)

The rule in §9.1 is unchanged: the transcript lives outside the graph, the CAPABILITY decides which
store holds a capture's turns, and where the record holds them the graph keeps no turn node. What
left §9.1 with step 7 (`#10017`'s carrier, landed as `#10076`; the type declared by `#10129`) is the
paragraph that said **where that rule stood**: `"nothing sets 'record_only' yet"`, closed by `"do not
read the paragraph above as a description of current behaviour"`. That was a statement about the work's
state, and the landing made it false; `#8542` keeps delivery state and the narrative of what changed in
this file, so §9.1 states the rule they settled on and this entry records the move. The design that
replaced that paragraph is below.

What is stated now, in the design document and in the seams it points at — the statements this entry
is the record for:

- **The journal.** On the record-held lane a capture's turns are journalled as ONE `TurnsRecorded`
  carrier holding the writer's per-turn snapshots verbatim, **instead of** one `PointAdded` per turn,
  and its replay materialises no node — a `PointAdded` there re-created the turn nodes on every
  rebuild, i.e. a rebuild that undid this placement (`#10067`).
- **The row.** The carrier states its turn rows itself — `_turn_row_statements`
  (`tortoise/record_projection.py:3901`), one `public.point` row per snapshot at
  `cache_tier = 'record_only'`, the tier from the record TYPE's declaration and never from a payload.
  Doing it in the PROJECTION rather than at the writer is what keeps `record == replay(journal)`:
  the live write and a record rebuilt from the same journal run the same statement.
- **The fold.** The carrier's graph fold is nothing (`_NO_PROJECTION_FOLD`,
  `tortoise/projection/__init__.py:7492`), so neither replay engine can re-create a turn the graph
  deliberately does not hold — the `#3316` RESURRECTION class, refused at the declaration.
- **The change feed.** The carrier is deliberately NOT in `sdk._GRAPH_EVENT_TYPES`, so an armed
  capture's turns leave `poll_events`: a `:GraphEvent` for a turn the writer deliberately skipped
  would advertise an entity the graph does not have. The capture sweep's `PointRetracted` row is
  suppressed on the same lane for the same reason, and whether that asymmetry — a pre-change turn's
  creation row with no matching retraction — is the repair or should be accepted permanently is the
  open decision on `#10325`, not a settled one. The `EntityMutated` op=delete record the replay folds
  is kept, so a shorter re-capture is durable even though the feed is one-sided.
- **The torn tail.** A torn carrier line is TOLERATED (`tortoise/log.py:204`, step 7's settled answer
  to `#10129`'s placeholder): `_emit_event` returns on an append failure BEFORE the record write, so
  that capture's turn rows **never reached the record either** — its transcript is already lost, and
  refusing the tail would cost the whole journal while recovering nothing. The R1 removal record
  (`EntityMutated` op=delete) stays REFUSED, because a tolerated torn delete is a resurrection.
- **The tenant.** The store decision asks the tenant the SEAM will file under, not the ambient one:
  `record_tenant_effective` (`tortoise/record_writer.py:842`) is **carried > scope > env pair**, and
  `is_usable_tenant` (`tortoise/record_writer.py:875`) is the seam's own admissibility test, so the
  decision that picks the store and the write that fills it cannot disagree about who the row
  belongs to. A write the record cannot attribute keeps the turns in the graph, and a scratch buffer
  is not a journal.

Why it left: it spoke about where the work stood rather than about the design, and `#8542` keeps that
in this file. Nothing mechanical forced the move — the class guard
(`tests/test_extractor_v4_citation_rot.py::test_the_design_documents_carry_no_issue_state_or_change_narration`)
refuses an issue-state claim adjacent to a `#NNN`, the narration of a change, and delivery spoken as
delivery, and **none of its arms fires on that line** (measured on the removed text). The routing is
the ground, and a reader should not expect a red to catch a similar sentence written again.

The pins for the statements above are the carrier's own:
`tests/test_10017_turn_carrier_replay.py::test_the_record_held_lane_journals_the_carrier_not_point_added`,
`test_the_record_writes_one_ROW_per_turn_and_the_tier_goes_to_the_COLUMN`,
`test_a_rebuild_from_the_carrier_journal_creates_no_turn_nodes`, the torn-tail arm
`test_a_torn_tail_carrying_the_carrier_does_not_block_recovery`, and — for the tenant resolution that
the store decision depends on — the capability arms in `tests/test_10000_turns_record_only.py`
(a tenant CARRIED on the SDK arms the record-only lane; a tenant that cannot be named, and a scratch
log, keep the turns in the graph).

**2026-10-31 — `#10000` (with `#10403`, `#10394`, `#10017`).** The S1 narrative left the node, and
§9.1(b) was rewritten to state the design instead of the delivery. This entry keeps what moved out,
because the design document is not where the record of the change belongs (#8542).

**What changed in the graph.** `hosted_api._execute_commit_writes` used to raw-Cypher `SET
s.story_arc=$arc` on the transcript `:Source` **and** `e.story_arc=$arc` on the AgentSession
`:Event` — the whole S1 narrative, twice per commit, in the store §2.1 says can never unload it —
while **no production read path read it back**. Both SETs are deleted; the narrative's one writer is
the SHAPE-KEYED projection `record_projection._narrative_statements` (any record naming its session and
carrying `story_arc` lands in `public.session_narrative`), whose production producer for a commit is the
`SessionNarrated` carrier (`#9555`), unchanged. MEASURED live on
a FalkorDB, one commit with a 167-char narrative: `sum(size(n.story_arc))` = **334 bytes** before the
removal (`:Source` 167 + `:Event` 167), **0 bytes** after it, with `public.session_narrative` holding
the same 167 chars. `summary`/`title` deliberately stay on the node (§4.4 metadata, not the prose).

**The re-entry routes, and where each one stood.** The key stays DECLARED in `_SOURCE_NODE_PROPS`
so legacy nodes read back, and the declaration is what makes a route possible at all. The list is per
ROUTE over the three labels the commit path could reach, and the first cut of this entry counted four
because it was Source-shaped — the `:Event` anchor the same SET wrote was under the same declaration
and under no deny set, which #10389's fifth review round measured (`#10428`):

- The journal `SourceCreated` fold was **recorded** as the open fold door and it never was one —
  `_upsert_source` passes `allow_keys=_SOURCE_EXTRA_PROPS`, a closed set excluding `story_arc`, so
  the key is dropped and warned about (`#3998: denied …`).
- The LIVE TENANT route was CLOSED in review cycle 1: `story_arc` in
  `_SOURCE_SERVER_MANAGED_PROPS` refuses `update_entity(url, story_arc=…)` rather than journalling
  an `EntityMutated` record that a rebuild would restore (`#5196` round 3).
- Door 1 — the DOCUMENT fold's open passthrough — was CLOSED HERE by `b771ccd85` (`#10403`):
  `_upsert_document` passes NO `allow_keys` deliberately (`#228` pins the document route as a caller
  passthrough), so the deny had to be `story_arc` in `_DOC_PASSTHROUGH_HANDLED`. MEASURED through the
  SDK door behind `tortoise_create_document`, one call, before: `MATCH (s:Source) RETURN s.url,
  s.story_arc` = `[[…, 'NARRATIVE-DOOR1-PROSE']]` — the PROSE on the node; after:
  `[[…, None, 'kept-228']]` — denied, and `#228`'s arbitrary prop still landed, so the entry is ONE
  key and not a blanket refusal. The departure is marked ON `#228` with an `OVERRIDES:` line
  (`#issuecomment-6540959107`).
- Door 2 — the RECORD → CACHE restore — is still OPEN, filed as `#10394`. `source.story_arc` is
  marked **PRIMARY** on `#7879`'s placement table, so `projected_columns("source")` keeps it and
  `restore_from_record_view` / `rebuild_from_record` writes it back onto `:Source`; the SAME route
  carries the `:Event` half — `public.event.story_arc` is PRIMARY on the same table (`:309`, open-set
  passthrough), `projected_columns("event")` keeps it, and `NODE_TABLES` maps `Event → event`.
  `tests/test_9555_narrative_cache_rebuild.py:187` measures that lane and asserts the prose IS on the
  rebuilt node — the `:Source` half only, which is why the understatement was measured rather than
  assumed (`#10389` review P2: the residual had described `#10394` as the `:Source` half alone, and no
  test could red on it; both anchors are now named in §9.1(b) and their premise pinned by
  `tests/test_9555_narrative_carrier.py::test_door_two_restores_the_prose_on_BOTH_anchors_and_the_residual_says_so`).
  **So `#10017`'s destructive purge is not durable while door 2 is open** — the next
  restore re-rents the RAM. Closing it is a PLACEMENT decision (the column's class on `#7879`'s
  table, plus that test's premise), which is why it is filed rather than absorbed.
- **The `:Event` fold — door 1's counterpart one label over, denied here (`#10428`).** `_upsert_event`
  persists extras with `skip = _META_KEYS | _EVENT_HANDLED`, and `story_arc` was in neither while
  `docs/ONTOLOGY.md` declares it on `:Source` only — an UNDECLARED key on the open passthrough, the
  `#3998`/D30 shape, and DURABLE because the fold that writes it live is the fold a `rebuild_all`
  replays. `story_arc` joins `_EVENT_HANDLED`. MEASURED through the SDK door behind
  `tortoise_create_event`, one call, before: `e.story_arc` = `NARRATIVE-EVENT-DOOR-PROSE`; after:
  `None`, with `e.project` = `kept-228` still landing — one key, not a blanket refusal (`#228`, marked
  there with an `OVERRIDES:` line, `#issuecomment-6544613785` — the same marker governs the tenant
  route below, which is `:Event`'s second mount, and `projection/entities.py` cites it at both).
- **The `:Event` tenant route — a SECOND door the fold entry cannot reach (`#10428`).**
  `_update_entity` writes caller props with a live `SET n += $props` and consults no label skip-set, so
  `update_entity(<agentSession eventId>, story_arc=…)` was ACCEPTED, journalled into an
  `EntityMutated` record and restored by a rebuild. `_EVENT_SERVER_MANAGED_PROPS` (new, the write-filter
  counterpart of `_SOURCE_SERVER_MANAGED_PROPS`) refuses it, resolved with the same predicate the write
  uses.
- **The `:Point` passthrough — LIVE closed at the SDK door, REPLAY open (`#10429`).** ⚠️ **CORRECTED
  (`#10389` review cycle 4/5):** this entry first stated the whole route as live-open and prescribed a
  point deny set as the lever. At `#10389`'s head the two LIVE surfaces — `create_point` and
  `update_point` (which `update()`/`create_or_update_point()` reach) — REFUSE the key through
  `sdk._refuse_unpaired_narrative(..., label="Point")`; MEASURED, both raise and no node carries the
  prose afterwards. `_upsert_point_props`, the replay fold, still materialises it from a journal that
  carries it, and THAT is what `#10429` owns. ⛔ The lever named here is now replay-only and destructive:
  a point deny set is consulted only by the fold while `update_point` writes raw `SET n += $props`, so
  adding the key there would make a `rebuild_all` DELETE the property from every legacy `:Point` (live ≠
  replay) and would justify itself with an `OVERRIDES:` line asserting a live hole that no longer exists.
  §9.1(b) counts the route denied at its two write doors and names the replay half.

**And the replay half of that route reclaims what a legacy journal carried.** Residual (a) said only that
nodes already carrying the prose keep it, which is true of the copies this change stops writing and NOT
of one that arrived through a journalled `EntityMutated`: `story_arc` is in
`_SOURCE_SERVER_MANAGED_PROPS`, and the SAME set is read by the `EntityMutated` → `:Source` replay arm,
which pops every member and warns (`tortoise/projection/__init__.py`). So a `rebuild_all` over such a
journal reclaims the prose — deliberate, since it is the same deny the live route gets, and it must be
stated because deleting it silently from the set re-enables the replay (that fail-open is what
`tests/test_9555_narrative_carrier.py::test_the_journal_replay_drops_the_prose_and_the_residual_says_so`
pins). This is the consequence `#10389`'s review P1 asked to be named rather than left implicit.

**Why this entry exists at all.** The first draft of §9.1(b) wrote that history INTO the design
document — `CLOSED HERE (#10403)`, `OPEN, filed as #10394`, *"three are CLOSED, one is filed and
still OPEN"*, and the before/after measurement — and
`tests/test_extractor_v4_citation_rot.py::test_the_design_documents_carry_no_issue_state_or_change_narration`
reddened on it in CI (run 41183278512). That guard is the executable half of #8542's rule: the
design document states the design, the changelog keeps the record of how it got there. §9.1(b) now
states the four routes and their mechanisms in the present tense, with the measurement as evidence
that the deny holds rather than as a before/after of the change; the delivery state and the
before/after figure live here.

**§9.1(b)'s citations are symbol-keyed, and both directions are guarded.** The section cited each
route as a `file.py:NNNN` line. `#10389`'s review found those lines already ~40 stale within this PR
(`_SOURCE_SERVER_MANAGED_PROPS` cited at `entities.py:6766-6773`), and merging `origin/main`'s
`#10067` tenant work moved the same declaration to a set beginning at 6757 with `story_arc` at 6797
— no code wrong, no symbol changed, just the line. That is the rot `#7948` retired from §13.8: a
line in a design document reds every unrelated code move in a large file while being unable to tell
a correct line from a wrong one. §9.1(b) now names a symbol per route
(`projection/entities.py::_SOURCE_EXTRA_PROPS`, `projection/entities.py::_SOURCE_SERVER_MANAGED_PROPS`,
`projection/entities.py::_upsert_document`, plus the door-2 test by name), and
`tests/test_extractor_v4_citation_rot.py::test_section_9_1_cites_symbols_not_lines_and_its_symbols_resolve`
pins both directions, each measured on the pre-fix text: a `file.py:NNNN` in the section reds (all
four converted citations named), and a symbol that no longer resolves reds (measured by renaming
`_upsert_document`).
