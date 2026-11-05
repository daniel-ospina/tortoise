---
title: "Scope — epic #5090, the vector layer"
type: brief
domain: planning
doc_status: draft
created: 2026-11-04
updated: 2026-11-05
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, embed gate, vector index, unindexed embeddings, test-design, capstone"
---

# Scope — epic #5090, the vector layer

**Stage:** 3 of 6 (`epic-workflow` → `epic-scope`), **second revision**. **Head `641e5f1b37`.**
**Inputs:** `01-align.md` (§8 carries the settled record) · `02-research.md`. **Gate log:** §7.

> **Why this was rewritten.** The first revision proposed *"decide whether the vectors move to Postgres"*
> as this epic's central item. **That is not this epic's question** — the storage architecture is
> **`#4333`**'s (owner re-scope, 2026-09-23), V1's home is **`STORAGE-ARCHITECTURE.md` §14.3**, and the
> changelog records *"#5090 is a **consumer** of the vector layer, not the enumeration."* The second
> revision also **drops two levers the record had already refused**: self-hosting (owner, 2026-11-05:
> *"not now"*) and embedding quantization (on §15's do-not-re-invent list — *"no engine to apply it to"*).

---

## 0. Plain language

**Two questions were in this epic. One of them is not ours any more.**

1. **Which rows deserve an embedding?** An embedding is a 1,536-byte list of numbers stored beside a row so
   it can be found by meaning. `issue #4432` gets one; it can never be *usefully* found by meaning, so its
   1,536 bytes are cost with no benefit. The rule that says so **is written** (`tortoise/embed_gate.py`) and
   it is **switched off** — nobody turned it on or measured the effect. **That is ours.**
2. **Where do the numbers physically live?** **Not ours.** That is the storage architecture (`#4333`), and
   the decision's home is §14.3. Its deciding input — the fraction of a tenant's stock the cache actually
   holds — **cannot be measured today** (hosted capture is off by your 2026-10-14 ruling). So this epic
   **carries** that decision as blocked and does not spend it.

**What is left for this epic is small, and every item is something nobody else owns:**

- **Turn the rule on**, and give the "this row is legitimately exempt" declarations a home in the product
  instead of only inside a measurement script.
- **Report the store's own vector contract** — which labels it searches versus which it indexes — for every
  label, not only when something is broken.
- **Census the vectors nothing can retrieve** and state the consequence of removing them, so that the one
  serving rule the epic promises ("no vector is stored without a path to query it") can be satisfied
  without waiting for a storage decision.
- **Produce the two verification documents the pipeline requires** — neither has ever existed here.

**Explicitly not this epic's, one line each:** how big the graph gets (`#4333`), what is evicted (`#10976`,
`#11004`), what raw conversation is admitted (`#10000`), whether Postgres is authoritative (`#7879`),
self-hosting (**owner: not now**), and compression (§15: no engine to apply it to).

---

## 1. Scope Boundaries

### In Scope

| # | capability | what "done" looks like — verifiable |
|---|---|---|
| **S1** | **The embedding rule is operative, not opt-in** | `TORTOISE_EMBED_GATE` is set in each shipped config surface that exists; the effect on the write rate is **measured** and the measurement is recorded with the command that produced it. |
| **S2** | **The exemption register has a production home** | The three writers carrying **no declared exemption** (`hosted_backup::restore_graph`, `projection.entities::_event_plain_merge`, `projection::_rebuild_all_replay`) have one, resolvable at runtime in `tortoise/` — not only inside `tools/dod_5090_probe.py::WRITER_CENSUS`, which is a read-only instrument. DoD rows 1–2 are then re-scored as a **recorded decision**, with the reading it applies stated. |
| **S3** | **The unretrievable vectors are censused and their removal is decided** | A dry-run census names every stored vector whose label has no vector index and no efficient query path, per label, with bytes; and the **surface consequence** is stated exactly — `entity_type='object'` / `'event'` vector search currently returns rows by full scan, and would return none afterwards. The removal itself is a **surface change** and is therefore carried by the child with the approval request attached, not spent here. |
| **S4** | **The store reports its vector contract positively** | `_record_vector_index_inventory` reports the **served set beside the measured indexed set** for every setup, not only when a gap exists. ⚠️ **Corrected premise:** `self._vector_indexed_labels = measured & served` is assigned **unconditionally** at `projection/__init__.py:18570`; it is the `log.warning` at **`:18582`** that `if gap:` (`:18577`) gates, and `tests/test_4997_vector_index_parity.py:415` already asserts the attribute for a no-gap store. **The served set is never retained**, so this needs **new state** — it is not one line. Not a second inventory either. |
| **S5** | **The epic's verification artifacts exist** | A **test-design / integration-surface map** and a **capstone**. `epic-workflow` mandates both; neither has ever existed for this epic. |

### Out of Scope — with explicit deferral

| excluded | deferred to | why |
|---|---|---|
| **Where the vector index lives (V1)** | **§14.3** (decision home) · **`#4333`** (architecture) | Not this epic's — the changelog records `#5090` as a **consumer**. Its deciding input (`f`) is unmeasurable today (§17.4.6). |
| Self-hosting FalkorDB | **D11** (`#4333`), **deferred** — and **owner, 2026-11-05: not now** | *"we have a lot of work that needs doing first."* Not in this epic's option set. |
| Embedding compression / quantization | **§15's do-not-re-invent list** | *"no engine to apply it to"* — FalkorDB's stored form is `vecf32`. |
| Filtered-ANN recall measurement | **V1's evidence, when V1 is taken** | It is evidence *for a move*. With no move planned, measuring it now is work with no consumer. Recorded in `02-research.md` so it is not lost. |
| Creating indexes for the four unindexed labels | **V1** | It is V1's content, and it commits four further resident HNSW structures. The brief forbids it without a stated non-reversal; there is none. |
| Nothing evicts the cache | **`#10976`** (OPEN — `#11004` is its merged census PR) | **Egress.** |
| Raw turns + narrative in the graph | **`#10000`** (`P0-blocker`) | **Ingress**; larger than this epic. |
| The record's authority / arming | **`#7879`** · **`#9548`** | **Authority.** |
| Node volume + per-node cost | **`#4333`** | Economics. |
| `_upsert_document` embeds retired text | **`#8419`** (open) | Root is the **D10 fold** (`#5088`). Named, not re-parented. |
| Migration safety for existing customers | **nothing — no customers exist** | `#7879` carries the **`OVERRIDES:`** marker: the only customer is the owner's dogfood instance. |
| Re-embedding `Object`/`Subject` vectors on rebuild | **S3**, as a cost line | An embedding is **derivable from its text** ⇒ a **re-embed cost, not data loss**. Scoping it as index-loss repair mis-sizes a durability incident. |

### Boundary Rationale

**This epic owns the vector layer's RULE and its INSTRUMENTATION. It does not own the layer's PLACEMENT.**
That cut is not tidiness — it is what stops a consumer epic spending an architecture decision by accretion,
which is already happening: **`#10976`'s body says the vector index *"belongs in Postgres per §12.1c
(#5090)"* and `#7879`'s body says *"vectors out"*, while §12.1c says `NOT DECIDED — owner's call`.** Two
issues answering a question a third calls open, and nothing errors. Reconciling that is **S3's** stated
work; deciding it is not.

**Contradiction test on S3, run explicitly.** Does removing the unretrievable vectors contradict a recorded
decision? The **2026-09-24** direction said *"keep the vectors, make them searchable"* — and for the
unindexed labels that means ADDING indexes, which is more RAM and a surface change. The **later 2026-10-08
billing ruling** (tiers are capped and priced per feature; **retained bytes are revenue**) removes the COST
argument in both directions: removing bytes changes the bill by **$0.00**. So **no recorded decision
authorises** this removal — the earlier direction points the other way, and the later ruling is silent on
it. **That is why S3 stops at a census plus an approval request**: the removal is the owner's decision,
taken on the census — and no lane may skip the surface gate it triggers, which is why S3 stops at the census
plus a named approval request.

---

## 2. Customer Value Map

| Scoped Capability | User-Visible Value |
|---|---|
| **S1** — rule operative | An agent's search stops returning identifier-only rows dressed as semantic matches, so the memory it gets back is the memory it asked for. |
| **S2** — exemption register homed | *(Internal — makes S1's claim checkable. Stated rather than omitted, so no capability is justified by silence.)* |
| **S3** — unretrievable vectors censused | The customer stops paying storage for vectors that can never be retrieved **without reading every row**, and the product stops advertising a search mode that silently reads every row. |
| **S4** — contract reported positively | A customer running their own graph can see what the store indexes *and* what it searches, instead of learning it from a slow query months later. |
| **S5** — verification artifacts | *(Workflow mandate — `epic-workflow` requires both. No customer value claimed; recorded so it is not mistaken for a feature.)* |

---

## 3. Complexity Ratings

| Axis | Rating | Rationale |
|---|---|---|
| **UX** | **low** | No GUI. The users are an agent (precision, degradation visibility) and an operator (contract visibility). Artefact is a state diagram. |
| **Architecture** | **low** | **Nothing in this scope moves a store or changes a schema.** S1 flips a config default; S2 adds a declaration; S3 censuses; S4 un-gates an existing report; S5 writes documents. *Placement* is `#4333`'s, and it is out. |
| **Ontology** | **low** | The four layers, D10 and §4.7 temporal are **read**, not changed. |
| **Accessibility** | **low** | n/a — no user interface in scope. |
| **Library-deps** | **none** | No new dependency. No `pgvector`, no extension, no `create extension`. |

### Axis Research Notes

> **Findings date:** 2026-11-05. **Provenance:** `02-research.md`; §15's do-not-re-invent list; §17.4; the graph's cost-premise refutation; FalkorDB's own Enterprise-offloading and Cloud-pricing docs.

**No axis rates `medium+`, so no fresh external query was fired for boundary definition.** The one axis that
*would* have been high — architecture — is `low` **because the architecture question was removed from
scope**, not because it was judged easy. That is recorded so a later reader does not read `low` as an
underestimate: **if V1 ever returns to this epic, Architecture becomes high immediately** (37 call sites
across 8 modules, a cross-store join, two-boundary erasure, a mandatory offline reconcile).

**Justified skip, stated as a skip:** for V1's evidence, `02-research.md` carries the external pass (graph
filters post-filter ⇒ recall@10 **0.942 → 0.401 at 10 % selectivity**; `iterative_scan` recovering **0.847**
at CPU/tail-latency cost; at **0.1 %** selectivity with *correlated* filters — ours are correlated — **0 rows
in 683 ms** against a btree's 3.6 ms; **SIFT-128, 128-dim**, ours are **384-dim** — 1,536 is the stored *byte* width). It is parked with V1,
where it will be used.

---

## 4. High-Level E2E Test Cases

Behavioural, executable, each tracing to exactly one scope item.

### E2E-1: The rule is operative with no environment variable — *S1*

**Given** a deployment built from the shipped configuration
**When** a row whose text is only an identifier (a UUID, a content hash, a file path) is written **with no `TORTOISE_EMBED_GATE` set by the operator**
**Then** it is stored **without** an embedding
**And** the write records *why* it has none

### E2E-2: A prose row is still embedded — *S1*

**Given** the gate operative per E2E-1
**When** a row carrying prose is written
**Then** it **is** embedded
**And** the two rows are distinguishable in the store (`embedding IS NULL` vs not)

### E2E-3: Every embedding writer is gated or has a production exemption — *S2*

**Given** the exemption register resolvable at runtime in `tortoise/`
**When** the writer population is enumerated at the closing head
**Then** each writer is `gated` or names a **production** exemption (runtime-resolvable, not a code comment)
**And** no writer is `bypass`

### E2E-4: The store states its served and indexed label sets — *S4*

**Given** any store at setup
**When** `_record_vector_index_inventory` runs
**Then** it reports the serves/indexes pair for **every** served label, **including when there is no gap**
**And** a store with a gap still reports the gap

### E2E-5: An unretrievable vector is named before it is removed — *S3*

**Given** vectors stored on a label with no vector index
**When** the dry-run census runs
**Then** it names every such label with its vector count and bytes
**And** it states the query paths whose results change if those vectors are removed
**And** it writes nothing

### E2E-6: A degraded search says so — *S4*

**Given** a vector query for a label with no index
**When** the query runs
**Then** the leg trace names `scan_fallback`
**And** the caller can distinguish it from an index hit without reading the source

### E2E-7: The epic's claim is checkable at its closing head — *S5*

**Given** the four DoD rows and the integration-surface map
**When** `tools/dod_5090_probe.py` runs at the closing head **and** the capstone walks the surface map
**Then** every DoD row is `MET`, or names an issue that is **OPEN at that head** owning its remainder
**And** every capability S1–S4 appears in the surface map with a named test layer
**And** the capstone checklist passes end-to-end

---

## 5. Human Approval Gate — ✅ PASSED

`epic-scope` requires human approval of the boundaries before planning. **Granted by the owner,
2026-11-05:** *"i need you to have done proper research to not reinvent the wheel and then move forward."*
Recorded here as the gate's own record, together with the two constraints that came with it:

1. **Self-hosting FalkorDB is not now.** Removed from the option set (S-out-of-scope row 2; align §8.1).
2. **No questions for their own sake.** Every item above is either unfiled work nobody owns, or an approval
   request that a hard rule (`AGENTS.md` → MCP/SDK surface) makes unavoidable — carried **in the child's
   body**, not held in front of the lane.

**Approved scope:** 5 items (S1 rule operative · S2 exemption register homed · S3 unretrievable vectors
censused · S4 contract reported positively · S5 verification artifacts).
**Customer value map:** 3 of 5 rows carry customer value; 2 are labelled internal on purpose.
**E2E test cases:** 7, each tracing to one scope item.
**Complexity:** UX low · **Architecture low — because the architecture question is out of scope** ·
Ontology low · Accessibility low · Library-deps none.

---

## 6. What changed from the previous revision

| # | previous | now | why |
|---|---|---|---|
| 1 | S3 = "decide V1: keep vs move, and implement" | **S3 = census the unretrievable vectors; removal carried with its surface-approval request** | V1's home is §14.3 and its architecture owner is `#4333`; the changelog records `#5090` as a **consumer**, and corrects a wrong owner. |
| 2 | **E1** = filtered-ANN recall measurement | **removed** — parked with V1 | It is evidence *for a move*. No move is planned, so it has no consumer; measuring it now is work for a decision that is not being taken. |
| 3 | Architecture **high on branch 2** | **low** | The branch is gone from scope. Flagged explicitly so `low` is not misread. |
| 4 | Profit chain: "removable bytes ⇒ $1.32/tenant/month" | **deleted** | **Wrong model twice**: retained bytes are revenue (billing ruling), and the bill is provisioned RAM (§16.1). |
| 5 | 6 items + 9 exclusions | **5 items + 12 exclusions** | Self-hosting, quantization, the recall measurement and V1 itself are now explicitly out, each with its home. |

---

## 7. Review gates

### 7.1 Stage 1 — align (`01-align.md`)

**Cycle 1** — fresh context, `epic-align` prompt + 8 independent fact-checks with commands. **2 P0, 4 P1, 3 P2, 1 P3.** Incorporated: the `#9548` authorisation wording; the setup-time (not per-search) warning; `1 of 4` MET not 3-of-4; DoD row 2's omitted remainder; the `P0-blocker`s' matrix placement; A1's reasons; the profit recomputation; the four-vs-three label count; the `§14.3` register row; the 4-exist/6-walked surfaces.
**Cycle 2** — corrections-only pass. **2 P1, 1 P3.** Incorporated: A1's sole surviving reason **falsified** (the doc row at `:1520` and the runtime warning both state the deferral) → **A1 re-argued on actionability and the decision changed from PROCEED to REPOINT**; the `§14.3` register correction **delivered for real** rather than pointed at a non-existent section; the `§0-preamble` mis-citation.

### 7.2 Stage 2 — research (`02-research.md`)

**Cycle 1** — **2 P1, 2 P2, 2 P3.** Incorporated: `iterative_scan`'s **documented failure at correlated, low-selectivity filters** added (0 rows in 683 ms at 0.1 % against a btree's 3.6 ms — our filters are correlated); the case **against** a cross-store move added at equal force (37 call sites / 8 modules, two-hop latency, two-boundary erasure, silent divergence) so the burden of proof is symmetric; K3's polarity fixed; the benchmark's dataset named and **the dimension corrected to 384** (1,536 is the byte width); heading contract fixed; GiB units corrected.

### 7.3 Stage 3 — scope (`03-scope.md`, **first revision**)

**Scope reviewer** — fresh context. **2 P0, 7 P1, 3 P2, 1 P3.** Incorporated: S1's closure predicate no longer accepts a **closed** issue; the approval block no longer asks the owner to decide on undefined terms; a named E2E now tests S1's actual headline (the flag on by default) instead of presupposing it; E2E-4/6 cite a **declared** budget; E2E-6's orphan and E2E-9's circularity fixed; S2 re-framed as evidence; S5 labelled a workflow mandate; Architecture and Library-deps branch-qualified symmetrically; the *"changes 9 write paths"* error corrected.
**Duplication-architecture reviewer** (advisory, outside the fix-loop) — **5 findings (3 P0, 2 P1)**, verdicts `unify` ×3 / `unify-contract-keep-drivers` ×2. Incorporated: S4 was duplicating `_record_vector_index_inventory`; S1 was re-filing `#10541`'s resolved census (the real gap is the **exemption authority** having no production home); **V1's state is recorded oppositely in three artifacts**; `M1–M4` denotes two disjoint sets across two live docs; §15 already carries **M5**.
**⚠️ Degraded source, declared:** Tortoise MCP was reachable-but-unauthorized (`-32001`) during the duplication check, so the registry-axis cross-check was **not** performed — recorded as `DEGRADED`, not as clean.

### 7.4 Stage 3 — scope (**second revision**, this artifact)

Rewritten after the prior-answer audit (`01-align.md` §8). Changes: V1 removed from scope (its home is §14.3, its architecture owner `#4333`); self-hosting removed (owner, 2026-11-05); quantization removed (§15's do-not-re-invent list); the filtered-ANN measurement **parked with V1** rather than scoped; Architecture re-rated **low** with the reason stated. Reviewed in the PR cycle (§7.5).

### 7.5 Stages 4–5 and the PR review

`04-plan.md` carries the **coherence review and the risk table** in §8 (it records no separate gate log);
`05-decompose.md` carries its verification plan in §6. The plan's review outcome is recorded here (§7.5) and
in the PR cycle. The PR review at head `9f5a419` returned **2 P1 + 8 P2** — all incorporated in this revision: acceptance item 3 restated (it still read "V1 decided and implemented", the reversal the rewrite exists to prevent); **this gate log, which the rewrite had dropped**; the **384-dim** correction; **S4's premise corrected** (the attribute is assigned unconditionally; only the warning is gated; the served set is never retained); the withdrawn profit chain removed from §3/§5; the dangling `§3.2` pointer; the plan's false J2↔D6 claim and the dropped `scan_fallback` case; **C2's target replaced** ("zero unclassified" is already true at HEAD, so it could not distinguish completion from a no-op); and the **reconciliation against the 2026-10-31 child set**.
