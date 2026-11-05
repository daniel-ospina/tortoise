---
title: "Epic research brief — the vector layer (#5090)"
type: research
domain: engineering
doc_status: draft
created: 2026-11-04
updated: 2026-11-04
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, decision V1, filtered ANN, pgvector, FalkorDB HNSW, embed gate"
---

# Epic research brief — the vector layer (#5090)

**Stage:** 2 of 6 (`epic-workflow` → `epic-research`). **Head `641e5f1b37`.** **Review gate:** verifier.

> **What this brief is, and is not.** It does **not** re-derive what the repo already holds — the 35 KB
> `docs/research/2026-09-23-vector-index-ram-model/research-brief.md` (issue `#4333`) and the paid
> 2026-10-08 three-lens round recorded on #5090 are the primary artifacts. It **adds** the one axis the
> record does not answer — filtered-ANN recall across a store boundary — because that axis is the hinge
> of V1 and it was unmeasured. Research mandate honoured: `#5090` comment 8 (2026-10-26) requires the
> `research` skill and a memory-competitor survey before any design. That survey exists (§12.2c); this
> brief says what it did **not** cover.

---

## Strategy Context

- **Position in the substrate rebuild.** `#5086` maps five epics; this is E4. The governing ruling
  (`STORAGE-ARCHITECTURE.md` §2.0, owner 2026-10-08, canonical via PR #7872/`929228d09`) makes **Postgres
  the system of record for all four ontology layers** and FalkorDB a **derived hot cache**. §2.0 is the
  *convergent* design, not a local invention — Zep/Graphiti treats episodes as the ground-truth stream
  with entities/edges derived on top.
- **Where the bill is.** 195 MB graph, **93 MB (48 %) indices**, 68,526 nodes. FalkorDB bills
  **provisioned RAM**, no eviction — so **idle == active** and every stored vector is permanent rent.
  Recorded basis: `$73/GiB-month`.
- **The competing lever.** The 2026-10-08 research round on #5090 reframed the question: the cost lever is
  the **granularity at which the graph can be tiered**, not placement. Zep built Konig for this and kept
  vectors *inside* each graph snapshot by making **one graph per subject → "a deployment with a million
  graphs and one percent hot pays for one percent of the memory."** Our blocker on that is structural:
  IMPL/NAND propagation spans subjects, so the per-subject partition is unavailable — a reopen of §4
  (single global graph), upstream of V1 and **not** part of it.
- **Business model consequence.** Storage is billed in **bytes** (owner ruling 2026-09-26, `#4495`), and
  exceeding the allowance is **purchased overage, not refusal**. So the epic's value is not "avoid a wall"
  — it is "stop charging the customer for bytes that do no work".

## UX Pattern Research

- **No GUI surface.** The "users" are two: the **agent** (retrieval precision and whether a degraded leg is
  visible) and the **operator** (cost, cold-reload time, and whether the store states its own contract).
  UX rating therefore **low**; no journey, no prototype beyond a state diagram.
- **Precedent that matters: silence is the anti-pattern.** `#4999`/PR #5159 landed `mechanism: index |
  scan | scan_fallback` because an index hit and a table scan produced a *byte-identical* trace reporting
  `reason="ok"`. The published analogue is a **silently-switched backend** — the system keeps working,
  more expensively, and nothing says so. Every scope item inherits that principle: a degradation must be
  reported, not tolerated.
- **Second precedent: `#4997` had to reconstruct the gap by hand.** `CALL db.indexes()` plus reading two
  functions. The pattern conclusion — *"if the system holds the fact, the system should state it"* — is
  why scope item 4 is a closing-out item rather than new work.

## Workflow Pattern Research

- **Write path.** S0→S6 extractor (`EXTRACTOR-V4-ARCHITECTURE.md`). The EMBED gate sits at
  `embeddings.encode_for_row`, reached by the embedding write paths; the predicate is
  `embed_gate.embedding_verdict` over `value_gate.RULES` (**one rule set, shared by object identity** —
  `RULE_SET is value_gate.RULES` → `True`). Design pattern in §12.2a: **the POLICY lives in the pack
  (`memory_granularity` + per-kind overrides), the PREDICATE lives in the engine** — "what" declared,
  "how" coded. Still absent: the per-kind override block, and any reaping of vectors already written.
- **Two gates, one rule set, two questions.** `DISCARD` (`#4899`) asks *does the row exist?*; `EMBED`
  (`#5008`) asks *does the row get a vector?* The divergence is deliberate: *"a row can be worth KEEPING
  but not worth EMBEDDING"* (a merge Event). The shared rule set is what stops the two drifting.
- **Query path.** `run_vector_query` derives a label per call with two special cases; `#4997` relocated
  that to one declaration (`security.ENTITY_TYPE_LABELS` / `entity_label()`), and setup reports the
  served-vs-indexed gap. The **served** set is declared; the **indexed** set is **measured** from
  `CALL db.indexes()` (discriminator is the `types` column, index 2 — copying the older `_row[0]/_row[1]`
  read would read the wrong column).
- **Operator workflow.** `_ensure_indexes` runs from `Projection.__init__` **unguarded**; ~28 sequential round trips (`monitoring.py:65`, `selfhost.py:392`). Any new read there **must be fail-open** or it breaks
  store construction.

## Tech Stack Research

**Two engines, two index rules — and applying one to the other is the documented cause of the false
"7 TB" scare.**

| | FalkorDB (the cache) | Postgres + `pgvector` (the record) |
|---|---|---|
| index residency | **RAM-mandatory.** No spill, no pageable index, no eviction | **page-cached**; need not fit in RAM; only latency degrades |
| durability | index is **rebuilt on every load**: 116 MB graph, no index → `RESTORE` **7.77 s**; **with** it → **did not complete in >700 s**, instance unresponsive, ~269 % CPU, a `PING` on a second connection timed out. Index build 20k × 384-d: **436.5 s** (`M=16/efC=200`), **191.8 s** (`M=4`), `efConstruction` **inert** | index is a **WAL-logged relation file** — every page goes to WAL at build and every later write is `GenericXLogStart/Finish`. Restart, crash, failover, `pg_upgrade` **replay**, never rebuild. No startup hook |
| granularity | **per `(label, property)`** — `createNodeIndex('Point','embedding',…)`. 5 served labels ⇒ **4 further resident HNSW structures** if all were indexed | **one** index over a shared table, filtered by `kind`; partial/per-tenant available |
| measurement honesty | `GRAPH.MEMORY USAGE` reported **2.00 MB for 43.28 MiB** (21.6× blind) | `postgres_storage_footprint()` exists, never run against the deployment |

**Traps that forfeit pgvector's durability property** (primary-sourced, from the 2026-10-09 round):
`UNLOGGED` tables (truncated after a crash) **prohibited**; `pg_dump`/`pg_restore` **rebuilds** the index
(dumps only the definition) ⇒ **physical backup** is the correct tool; `VACUUM FULL`/`CLUSTER` rebuild;
plain `VACUUM` does not.

**⭐ NEW in this pass — filtered ANN, the one axis the record did not answer, and it is V1's hinge.**

The record said *"the honest question is not 'is recall preserved' but 'does filtered ANN beat
post-filtered graph-state filtering on OUR query mix?' — unmeasured, and cheap to measure."* The
comparables now say what that measurement will probably show, and how to run it:

| finding | figure | source |
|---|---|---|
| Post-filtering **collapses** recall as filter selectivity rises — non-matching candidates consume the `ef_search` budget | recall@10 **0.942** unfiltered → **0.401 at 10 % selectivity** (1M vectors, 45 configs, exact in-database ground truth) | `pgvector-filtered-recall` benchmark (2026) |
| `hnsw.iterative_scan` **recovers** recall by continuing the scan | `relaxed_order` recovered only **0.847** in one tested configuration at 1M | same |
| the recovery is **not free** | higher CPU, higher memory, worse **tail latency** | ParadeDB, *pgvector Limitations* (2026) |
| post-filtering survivor arithmetic | ~`k × selectivity` survivors — at 10 % selectivity and `ef_search = 40` roughly **4** candidates survive | Crunchy Data, *Hybrid vector search* (2026) |
| the academic framing | filtered ANN is a distinct problem from unfiltered ANN; post-filtering degrades *specifically* under selectivity | *Filtered Approximate Nearest Neighbor Search in Vector Databases* (arXiv 2602.11443) |
| **global recall hides the failure** | filtered recall must be monitored **separately** — sparse-result failures vanish inside a global average | AI Engineering Playbook, *Filtering & Metadata* |

**⇒ Three consequences for our design, each load-bearing:**

1. **K3 is confirmed as a real risk, not a hypothetical.** `pgvector` post-filters graph-state predicates
   (liveness, EP confidence, an operator) that **cannot be pushed into a vector index**. The magnitude in
   the literature is a **2.3× collapse** at 10 % selectivity. The epic's own cited 0.63–0.72 is the same
   phenomenon at milder selectivity.
2. **The measurement has a known-good method and a known-good control**: exact ground truth computed
   in-database, swept over **our** filter selectivities, reporting **filtered** recall (never global).
   That makes **E1**, the filtered-ANN recall measurement, cheap and decisive rather than a research
   project. ⚠️ **E1 is PARKED WITH V1, not in this epic's scope** — it was item 2 of the scope's first
   revision and the second revision removed it (`03-scope.md` §1's exclusion table and §6; `01-align.md`
   K3): it is evidence *for a move*, and no move is planned, so a lane must not read this brief as
   approving it. Its numbers stay here so they are not lost when V1 is taken.
3. **`hnsw.iterative_scan` is a genuine capability we do not have at all today** — FalkorDB has no
   equivalent. So branch 2 is not strictly a loss: it trades in-graph index residency for **filtered ANN
   we currently cannot do**, paying tail latency for it. ⚠️ This is an argument *for* branch 2 that the
   record did not carry, and it must be in front of the owner when V1 is decided.

## Assumptions Register

| Assumption | Confidence | Source | Validation plan |
|---|---|---|---|
| Vectors are stored and unindexed on **two** labels in the 2026-09-24 count (`Object` 7,859 + `Event` 4,798 = 12,657; four served labels are unindexed). ⚠️ NOT a claim that `Source`/`Subject` can never hold one — their writers DO store `embedding` (`_upsert_document` → `:Source`, `_upsert_subject`), which is why the census measures `db.labels()` instead of the index inventory | high | `ENTITY_TYPE_LABELS` (5 served) + `Point`-only DDL (`projection/__init__.py:19765`, `:19783`) | re-run `tools/dod_5090_probe.py --census` on a graph that holds rows |
| The EMBED rule exists and is **off** by default | high | `embed_gate.RULE_SET is value_gate.RULES` → `True`; flag unset in the 4 shipped surfaces that exist | the arming measurement (scope item 1) |
| **A cross-store move PRESERVES recall on OUR mix** (this is the `01-align.md` K3 statement; §3's evidence below is *against* it) | **low — the hinge** | 0.942 → 0.401 @ 10 % selectivity (post-filter); 0.63–0.72 (epic's comparable); both **not our corpus** | **E1** (parked with V1) — exact ground truth, our selectivities, filtered recall reported separately |
| `hnsw.iterative_scan` compensates enough to be a net win | **low — unmeasured** | recovers 0.847 in one config; costs CPU/memory/tail latency | **E1**, same sweep |
| The record can serve reads without the graph | medium | 4/4 layers in schema; `record_*` modules exist; **armed** (`record.armed: true` at 2026-11-05T15:07Z, `#9548`) but hosted writes reject `22P02` (`#10593`, OPEN) and no hosted projection is evidenced | serve a query with the graph stopped |
| Arming the gate moves the write rate materially | medium | `#5008` ordered measurement first | scope item 1 |
| Object/Subject embeddings absent from the record are **re-embed cost, not data loss** | high | an embedding is derivable from its text | a stored vector whose source text is gone |
| Two `PARTIAL` DoD rows can close without a new mechanism | medium | `#10541` closed COMPLETED with both rows still PARTIAL | the arming measurement |

## Raw Notes

| date | source-tag | finding |
|---|---|---|
| 2026-09-24 | `internal-measurement` | 141 MB / 45 MB indices / 12,657 embeddings / exactly one vector index (`Point.embedding`); `Object` 16.96 ms vs `Point` 4.97 ms, both `reason: ok` — §12.1's 2026-09-24 table (141/45 MB), §12.1b (12,657, the index inventory and the full-scan p50s) |
| 2026-09-23 | `research-brief` | `docs/research/2026-09-23-vector-index-ram-model/research-brief.md` (issue `#4333`): pgvector HNSW is **not** RAM-mandatory; adopt Position B; **four measurements M1–M4 proposed, none taken** |
| 2026-10-08 | `paid-research-3-lens` | the cost lever is tiering **granularity**, not placement; arithmetic 2.6× high (19.4 MB, not 50); migration surface is 2 files, not 1,396; **recall trap** 0.63–0.72; derived layer not rebuildable ⇒ persist extraction output into the journal first |
| 2026-10-09 | `paid-research-vendor` | index rebuild blocks the whole instance (7.77 s → >700 s); pgvector's index is WAL-logged; per-tenant isolation has **no clean mechanism** at our scale; planner cost, not lock exhaustion, is the real ceiling |
| 2026-10-13 | `vendor` | FalkorDB replied: indexes **not persisted** across offload |
| 2026-11-04 | `internal-measurement` | 195 MB / **93 MB indices (48 %)** / 68,526 nodes / 71,744 edges — `#10976` body, graph `live-v4`; **the index line doubled** since the epic was written (45 → 93 MB) while the resident node count **fell** (89,701 on 2026-09-25 → 68,526 here) — the bytes grew in the INDEX, not the node count |
| 2026-11-04 | `external-filtered-ann` | post-filter recall **0.942 → 0.401 @ 10 % selectivity**; `iterative_scan` recovers ~0.847 at 1M at CPU/tail-latency cost; filtered recall must be reported **separately** from global |
| 2026-11-04 | `internal-code` | **no `pgvector` anywhere**; embeddings are `jsonb` on `point`/`source`/`event`; **`public.object`/`public.subject` have no embedding column and a CHECK forbidding one** ⇒ branch 2 is build-from-nothing |
| 2026-11-04 | `gate-cycle-1` | the *"warning fires on every search"* premise is **false** — setup-time, once per (process, graph); query fallback logs at info/debug |

## What the survey did NOT find

- **No memory system reports adding entity embeddings and then removing them**, and **no measured ablation**
  isolates an entity vector's contribution to multi-hop answer quality (§12.2c, re-confirmed). So Half 1 of
  §12.2c is strong framework consensus rather than measured causation.
- **No comparable meters capacity in per-graph bytes** (`#7879`, comment 28). Our byte unit has no
  precedent to copy; it can only be measured here.
- **No published figure for filtered-ANN recall on a graph-state filter set like ours.** Every number found
  is on synthetic or unrelated selectivity. ⇒ the measurement in **E1** (parked with V1) has no substitute.

---

## Review gate

**Cycle 1** — 2 P1 + 2 P2 + 2 P3, all incorporated below. The two P1s were cases of the same defect: **the
brief presented the favourable half of its own evidence without the unfavourable half.**

### P1-a — `iterative_scan` was reported without its documented failure at correlated filters

§Tech Stack consequence 3 called filtered ANN *"a genuine capability we do not have at all today"* and
*"an argument **for** branch 2"*. The same benchmark's headline result runs the other way, and it is closer
to our shape than the 0.942→0.401 row:

> **At 0.1 % selectivity with CORRELATED filters, `iterative_scan` fully tuned still returned 0 rows in
> 683 ms; a btree returned them in 3.6 ms.**

Our filters are **correlated** — `kind`, EP-confidence bucket and operator presence are not independent
of the embedding region. ⇒ **Consequence 3 is restated:** filtered ANN is a capability we lack,
**and it is not a rescue at low selectivity with correlated filters** — which is precisely where a
graph-state filter lives. Branch 2 therefore carries a **failure mode at the narrow end of the sweep**,
not merely a tail-latency cost. E1 sweeps 0.1 % for this reason.

### P1-b — the case AGAINST the cross-store move was missing; the case against staying was untagged

The brief asserted the anti-staying evidence (7.77 s → **>700 s** `RESTORE`, 48 % index share) with no
confidence tag, while tagging every anti-moving figure *"low — unmeasured"*. **Asymmetric burden of
proof.** The anti-moving case, now stated with equal force and at equal weight:

| cost | figure / source |
|---|---|
| **37 call sites across 8 modules** (`sdk`, `search_engine`, `embeddings`, `consistency`, `session_indexer`, `rerank`, `log`, `hosted_backup`) | 2026-10-09 measured ledger |
| **two-hop retrieval**, **+10–15 ms** against today's 1.5–5 ms | same |
| **erasure spans two boundaries** — and in pgvector's HNSW an erased element is zeroed during `VACUUM`, which was corrupting indexes as recently as 0.8.3/0.8.4; the highest-probability false-erasure path is backup/PITR restore resurrecting erased rows | same, EDPB Opinion 28/2024 §37 |
| **silent partial divergence** between the two stores | same |
| **a mandatory offline reconcile**, and orphan vectors | same |
| **`pgvector` does not exist in this tree**; `object`/`subject` embeddings are **forbidden by a CHECK** | verified 2026-11-04 |

⇒ Neither branch is free. **The measurement (E1) is what makes the comparison decidable; it does not
favour either branch.**

### P2-a — K3's polarity was inverted in the Assumptions Register

Fixed above: the register now states the align-doc assumption (*"preserves recall"*) and marks the
evidence as against it. A low-confidence assumption cannot be *"confirmed"* by evidence that the risk is
real.

### P2-b — the benchmark's dataset was unnamed

It is **SIFT-128-euclidean** (ann-benchmarks), **128-dim**. **Our embeddings are 384-dim** (`tortoise/embeddings.py` `EMBEDDING_DIM = 384`); **1,536 is the stored BYTE width** (384 × float32), not the dimension — so the dimensional gap is **3×**, not 12×, and the distinction matters because HNSW's recall and build cost are functions of dimension, not of stored bytes. The
benchmark's own caveat is dimensional (*"do not substitute random vectors … in 128 dimensions"*), so its
distances must not be read as ours — and at 384 dims HNSW behaves differently in both recall and build
cost. Also noted: the *AI Engineering Playbook* row is the only citation that is neither fetchable nor a
repo artefact.

### P3-a / P3-b — heading contract and units

`### Raw Notes` → `## Raw Notes` (the contract names H2), and the extra section demoted to an H2 so a
reader walking headings gets the promised tree. **Units corrected: `$1.32/month`, not `$1.38`** — the
corrected figure is **$1.32/month** (19,441,152 B = 0.0181 GiB × $73). ⚠️ **And the chain itself is withdrawn — `01-align.md` §8.2:** the bill is provisioned RAM and retained bytes are revenue, so this figure's only surviving use is as the refutation.
