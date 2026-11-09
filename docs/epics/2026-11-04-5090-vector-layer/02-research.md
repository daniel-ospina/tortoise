---
title: "Epic research brief — the vector layer (#5090)"
type: research
domain: engineering
doc_status: draft
created: 2026-11-04
updated: 2026-11-07
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #5090, decision V1, filtered ANN, pgvector, FalkorDB HNSW, embed gate"
---

# Epic research brief — the vector layer (#5090)

**Stage:** 2 of 6 (`epic-workflow` → `epic-research`). **Head `641e5f1b37`; §*Extractor-v4 alignment* measured at `28d13ccc8`** — a LATER commit than that pin, and the section says so where it matters, because at the pin three of its claims are false and at `28d13ccc8` they are true. **Review gate:** verifier.

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
  why this is S4 in the scope: a closing-out item **plus one piece of new state** — the served set is never
  retained today (`_record_vector_index_inventory` computes it locally as
  `served = set(ENTITY_TYPE_LABELS.values())`; only `self._vector_indexed_labels = measured & served` is
  stored), so it is not one line (`03-scope.md` S4's corrected premise; `05-decompose.md` C4).

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
- **Operator workflow.** `_ensure_indexes` runs from `Projection.__init__` **unguarded**; ~28 sequential round trips (the `#3143: the probe has TWO phases` block in `monitoring.py` and the `#3243: the cold-start allowance lives on the REFRESHER, not the read` block in `selfhost.py`; the figure wraps across two lines in the first, so grep the block header, not the literal). Any new read there **must be fail-open** or it breaks
  store construction.

## Tech Stack Research

**Two engines, two index rules — and applying one to the other is the documented cause of the false
"7 TB" scare.**

| | FalkorDB (the cache) | Postgres + `pgvector` (the record) |
|---|---|---|
| index residency | **RAM-mandatory.** No spill, no pageable index, no eviction | **page-cached**; need not fit in RAM; only latency degrades |
| durability | index is **rebuilt on restore, on the serving thread** — the instance stops answering for the rebuild's duration, with no progress line and no error: 30 000 nodes, index-free `DUMP`/`DEL`/`RESTORE` **0.17 s** (worst bystander `PING` **53.3 ms**) vs index-bearing **2.62 s** (bystander **2 497.7 ms** — the outage IS the restore), and at 90 000 nodes it outlived a 10 s client read bound. ⚠️ **The original ">700 s, instance unresponsive" reading is falsified** — that harness's graph carried no populated index, so it measured nothing; the index **is** carried inside the graph key's serialized payload, and the block is avoided by dropping it **before** the `DUMP` (20 000 nodes: **0.069 s** / **69.4 ms** bystander, against **2.955 s** / **2 899.4 ms** raw — `docs/evidence/7846-indexed-graph-restore/README.md` §3, §3a, §4a). Index BUILD is separately CPU-bound: 20k × 384-d = **436.5 s** (`M=16/efC=200`), **191.8 s** (`M=4`), `efConstruction` **inert** | index is a **WAL-logged relation file** — every page goes to WAL at build and every later write is `GenericXLogStart/Finish`. Restart, crash, failover, `pg_upgrade` **replay**, never rebuild. No startup hook |
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
   project. ⚠️ **E1 is `#4051`'s, not this epic's** — it was item 2 of the scope's first
   revision and the second revision moved it there (`03-scope.md` §6 row 2; `01-align.md`
   K3): it is evidence *for a move*, and the location is decided (2026-11-07), so it is no longer
   consumer-less — `SEARCH-ARCHITECTURE.md` §9 **M1** consumes it as a private-beta gate. Its numbers
   stay here so they are not lost.
3. **`hnsw.iterative_scan` is a genuine capability we do not have at all today** — FalkorDB has no
   equivalent. So branch 2 is not strictly a loss: it trades in-graph index residency for **filtered ANN
   we currently cannot do**, paying tail latency for it. ⚠️ This is an argument *for* branch 2 that the
   record did not carry, and it was to be placed in front of the owner when V1 was decided — V1 was
   decided on 2026-11-07, so it now belongs to `#4051`'s implementation.

## Assumptions Register

| Assumption | Confidence | Source | Validation plan |
|---|---|---|---|
| Vectors are stored and unindexed on **two** labels in the 2026-09-24 count (`Object` 7,859 + `Event` 4,798 = 12,657; four served labels are unindexed). ⚠️ NOT a claim that `Source`/`Subject` can never hold one — their writers DO store `embedding` (`_upsert_document` → `:Source`, `_upsert_subject`), which is why the census measures `db.labels()` instead of the index inventory | high | `ENTITY_TYPE_LABELS` (5 served) + `Point`-only DDL (`_ensure_vector_index_api` in `tortoise/projection/__init__.py`: the `CALL db.idx.vector.createNodeIndex('Point', 'embedding'` procedure form, falling back to the `CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding)` Cypher form) | re-run `tools/dod_5090_probe.py --census` on a graph that holds rows |
| The EMBED rule exists and is **off** by default | high | `embed_gate.RULE_SET is value_gate.RULES` → `True`; flag unset in the 4 shipped surfaces that exist | the arming measurement (scope item 1) |
| **A cross-store move PRESERVES recall on OUR mix** (this is the `01-align.md` K3 statement; §3's evidence below is *against* it) | **low — the hinge** | 0.942 → 0.401 @ 10 % selectivity (post-filter); 0.63–0.72 (epic's comparable); both **not our corpus** | **E1** (`#4051` — measured, not parked) — exact ground truth, our selectivities, filtered recall reported separately |
| `hnsw.iterative_scan` compensates enough to be a net win | **low — unmeasured** | recovers 0.847 in one config; costs CPU/memory/tail latency | **E1**, same sweep |
| The record can serve reads without the graph | medium | 4/4 layers in schema; `record_*` modules exist; **armed** (`record.armed: true` at 2026-11-05T15:07Z, `#9548`) but hosted writes were rejected `22P02` (`#10593`, **CLOSED COMPLETED 2026-11-06T19:51:42Z**) and no hosted projection is evidenced | serve a query with the graph stopped |
| Arming the gate moves the write rate materially | medium | `#5008` ordered measurement first | scope item 1 |
| Object/Subject embeddings absent from the record are **re-embed cost, not data loss** | high | an embedding is derivable from its text | a stored vector whose source text is gone |
| Two `PARTIAL` DoD rows can close without a new mechanism | medium | `#10541` closed COMPLETED with both rows still PARTIAL | the arming measurement |

## Raw Notes

> ⚠️ **SUPERSEDED 2026-11-07 — read this before acting on any "V1" line below.** V1 is decided: the vector
> index and the embedding payload move to Postgres (`docs/architecture/SEARCH-ARCHITECTURE.md`, `#4051`). Every
> "parked with V1" / "when V1 is decided" below is the state this pass was written against, kept as the record
> and **not** as current text. What stays open is the DIFFERENT question of §14.3 **V3: whether
> `:Object`/`:Event` carry — and index — embeddings.**

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

## Extractor-v4 alignment — the check this brief originally omitted (2026-11-05; corrected 2026-11-06)

**Why this section exists.** The first version of this brief never read `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` (the design of record for the write path, home `#4894`). That was a Research-stage omission, and it matters because the epic's rule lives **inside** that write path. Reading it produced one genuine gap in what the epic asked for, and three corrections to this brief's own first draft — one where the draft was right at its `Head` pin and went stale, one where it repeated a design-document note the code has since outgrown, and one where it asserted an ordering no source records at all.

### ⛔ v4 ALREADY RECORDS THE GAP, as G2

`EXTRACTOR-V4-ARCHITECTURE.md` §4.3, requirement **G2** (owner `#5008`, CLOSED COMPLETED):

> *"**No EMBEDDING-DECISION step.** `STORAGE-ARCHITECTURE.md` §12.2a **decides** the `EMBED` gate exists … and this step list has no home for it. S2.3's kNN path **silently assumes every candidate is embedded.** ⛔ **The two documents would ship contradictory designs.** The `EMBED` gate and the `KEEP` gate **share one rule set and enforce at TWO points** (§12.2a) — so the second enforcement point needs a home in this step list."*

**⇒ So the epic's S1 was under-specified — but not in the way this section first said.** Arming a default does not resolve G2: the step list is the **eight steps of §4.1** — `S0`, `S1`, `S2.1`, `S2.2`, `S2.3`, `S3`, `S5`, `S6` (⛔ *"There is no S4 — do not restore one"*) — and **none of them is an EMBED decision**. `S2.2` is the VET/`DISCARD` point; the EMBED question ("is this row worth an embedding?") has no home **in that step list** — its only home is the embedding submission seam in code (see the write path above). That is a **step-list** gap, and no flag can close it.

### ⚠️ The VALUE gate's default half has SINCE been satisfied (`#10996`) — the draft was right when written

This section first claimed *"the VALUE gate is off too"*. **At this document's `Head` pin (`641e5f1b37`) that was true**: `tortoise/value_gate.py` there resolves `is_truthy(os.environ.get(ENV_FLAG))`, with no default. It is no longer true — `#10996` (`277a559b1`) landed before this PR and flipped the gate **ON**.

⛔ **So the draft was correct and went stale**, which is a different defect from being wrong: a requirement already met is a criterion that can never fail. Measured at the **branch head**, which is later than this document's `Head` pin — that distinction is the whole point:

- `tortoise/value_gate.py` now resolves `is_truthy(os.environ.get(ENV_FLAG, "1"))` — **unset means ON** — and its own comment says so: *"The predicate is ON unless this says otherwise (`#10996`)"*, citing §S2.2's requirement as the reason it was flipped.
- `tortoise/embed_gate.py` is the one still off by default. `277a559b1` also updated its docstring, which now says of the other flag: `TORTOISE_VALUE_GATE` *"was the third member of that precedent until `#10996` flipped it ON by default — copy this pattern, not that one."*
- `tests/test_value_gate_4899.py::test_unset_arms_the_predicate` pins it, and states the rule as *"unset must not mean 'the predicate never runs'"*.

⇒ **Requiring `TORTOISE_VALUE_GATE` to be "set" is a criterion that measures nothing**: unset already means ON, so an explicit set has a zero delta by construction, and no shipped config sets it. What S1 owes is (i) `TORTOISE_EMBED_GATE`, the second enforcement point, and (ii) G2's step-list home.

⚠️ Note what this does **not** claim: §S2.2 was never the stale source. It always required the default; at the pin, the code simply did not yet do what it said.

### ✅ The DISCARD-before-EMBED wiring is IMPLEMENTED

This section first repeated v4's wiring note as an open defect. At the branch head the prune happens **before** the classify pass:

- `_run_vet_pass` **returns** the list and the caller assigns it back (`tortoise/extractor_v2.py`), and `_collect_classify_items` is called **after** that assignment — so it collects from the already-pruned candidate set, not the whole one;
- `apply_vet`'s Rule 1 is *"Only an explicit `DISCARD` removes anything"* (`tortoise/vet_gate.py`);
- `vet_s2_pool` carries the S2 pass's removals forward: an **operator** the live `S4` gap-filler re-added against an `S2.2`-discarded item is **not** re-minted by `execute_embed`, and an entity it started referencing does not 422 the session (`S4` here is the code's second extraction pass, which is not the design's removed *"S4 — granularity review"*). Item removal itself is driven by the union pass's own `DISCARD` decisions, not by the pool.

⇒ `DISCARD` already removes from the candidate set **before the resolve/embed tail on every arm** — the unconditional guarantee is *"a discarded candidate is never resolved or embedded"*. ⚠️ **The never-classified half is narrower than it looks**: VET runs before the classify pass only under `TORTOISE_CLASSIFY_LATER=1`, and with that flag off (the default) kinds are emitted inline by `S2.1`/`S4`, so a candidate can reach VET already labelled — `tortoise/vet_gate.py`'s own ordering note states exactly this split. (`classify_consolidation` is a per-point dedup API, not the pruning site — citing it for this was wrong twice over.) Candidate items are removed by the union pass's own `DISCARD` decisions (`discarded_ids`); the pool additionally participates in the **operator** prune, which is why its two roles are stated separately here. v4's wiring note still **reads** as outstanding because its citations belong to the `c79ba1cf2` pin; that passage is stale at the branch head, and re-asserting it here is how the stale claim would have propagated.

### The ordering (v4 §4.2, §S2.2)

| constraint | source |
|---|---|
| the **value** predicate stays **subordinate to VET** | v4 §S2.2: the predicate is the mechanical half of the step whose adversarial question is VET's, and its flag switches that pass on (`vet_enabled` folds `_value_gate_enabled()`) |

⚠️ **Not** both gates: `embed_gate_enabled()` reads only `TORTOISE_EMBED_GATE` and is **not** folded into `vet_enabled`, so the EMBED point is not subordinate to VET — it sits at the embedding submission seam (`embeddings.encode_for_row`, storage §12.2a), a different step from `S2.2`. A lane that folded it in on the strength of "one rule set, two enforcement points" would be inventing a subordination no source records.

⛔ **And there is no EMBED row to add to that table — its absence IS G2.** v4 §4.2's "resulting order" block has no EMBED entry at all, which is exactly what G2 records as missing. The code applies the gate at `encode_for_row`, called from the graph writers **when the payload `execute_embed` returns is committed** — `execute_embed` performs no write (it returns a `payload` dict), and the SDK's `create_point` performs the commit and calls `encode_for_row` — so the gate is neither "before `execute_embed`" nor a step of its own. Stating it as an ordering constraint would invent the placement the design says is absent.

(`DISCARD` already precedes the resolve/embed tail on every arm — see above; the classify half is flag-dependent.)

### Two v4 passages that CONFIRM the epic's sequencing

- **The entity vector is an entry key, not a reasoning mechanism** (§5, the traversal measurement at `:1053`; and storage §12.2c): *"Traversal is cheap enough to be the default … measured 1.55–2.35 ms … the why/who/when value does **not depend on vectorising the entity at all**. What a vector is for is FINDING the entry node, not reasoning from it."*
- **The definite-description problem is upstream of the vector** (§1:108, §4.3 G5; measured 2026-09-25 under **`#5059`**): 62.3% of Objects are `the <X>`; and measured, **≈52% of the `the <X>` rows are load-bearing** (2,570 of 4,901), so a bare name-pattern delete carries **≈48% precision on an irreversible delete**. ⇒ **Any census that proposes deleting rows by name pattern is at coin-flip precision and must not be built.** `#11088`'s census is about *vectors on labels with no index*, which is a different population — and it removes **vectors, never rows**.

### What this changes for the epic

- **S1 / #11086** — arms `TORTOISE_EMBED_GATE` (the second enforcement point) and gives the EMBED decision a home in the step list (**G2**). It does **not** need `TORTOISE_VALUE_GATE` set: that is already ON by default since `#10996` (`03-scope.md`, S1).
- **S3 / #11088** — states that it removes **vectors**, never rows, and cites the 48%-precision measurement as the reason that distinction is not negotiable (`03-scope.md`, S3; the issue body carries the same). ⚠️ `#11088` is **CLOSED** and no successor carried the removal/surface-approval call, so that acceptance item had no tracking surface — **`#11395` was opened to carry it** (a tracking surface only: it authorises nothing, and its own DoD is Daniel's recorded ruling).
- **V1 stays out of scope** and unaffected: v4 is silent on where the index lives, and §14.3 remains the decision's home.

### ⚠️ The stale premises have already propagated into the child issues

This doc can only stop the NEXT copy; each issue body is what a lane building that slice reads as its brief.

- **`#11086`** carries **four** superseded premises, not one: the value gate is no longer off by default; a `DISCARD` no longer reaches `S2.3`/`execute_embed`; its ordering table *and* its checklist both require **both** gates to stay subordinate to VET — the invented subordination this section disclaims; and that same table still carries the **`EMBED` before `execute_embed`** row, the invented placement refuted above. Correcting all four is part of S1, not a follow-up.
- **`#11088`** — now **CLOSED**, and it carries the stale wiring note from v4's **`S2.3 — CLASSIFY`** passage — `EXTRACTOR-V4-ARCHITECTURE.md` `:755-756`, which is inside **§4.1**'s step list, **not** §4.2. §4.2 begins at `:781` and is not the ordering block alone: it carries the five correctness constraints (`:827`) **before** the resulting-order block (`:849`), and the against-store dedup preconditions (`:867`) and the Jev classification-seam note (`:883`) after it. A reader sent to §4.2 for the S2.3 passage would therefore not find it there, and would land instead on `:883`, which is about `llm_tail` and the classifier — **not** about `embed_list` (§4.2 mentions `EMBED` nowhere, which is the absence G2 records). The wiring note is `#11088`'s own paraphrase of `:755-756`, quoted here from its body and not from v4: *"`S2.3 — CLASSIFY` collects from `embed_list`, and `classify_consolidation` does not remove items from it — so anything this issue stops embedding must also be skipped there, or it is classified and embedded anyway"*. ⚠️ The §4.2 mis-attribution is `#11088`'s own — its body calls it *"the ordering note from v4 §4.2"* — and this inventory repeated it as fact; a lane correcting the issue that opens §4.2 will not find it there. The "embedded anyway" half is false: the gate is at `encode_for_row`, so a row that is not embedded gets no vector whatever the classifier does. No successor issue carried the removal/surface-approval call (**`#11395`** carries it now), so this one needs checking before it is read as settled. ⚠️ It carries a **second** stale premise this inventory must not omit: *"Removing those two labels' vectors makes those queries return **zero rows** — that is the surface consequence"*. Both halves of that are superseded — the population is **FOUR labels / FIVE `entity_type` values**, and a lexically-matching query keeps answering, so what is lost is **semantic recall** and only a meaning-only query goes to zero rows (`03-scope.md` S3; `S3-VECTOR-CENSUS.md` §3.1). Do **not** quote the closed body's consequence statement; quote §3.1.

## Review gate

**Cycle 1** — 2 P1 + 2 P2 + 2 P3, all incorporated below. The two P1s were cases of the same defect: **the
brief presented the favourable half of its own evidence without the unfavourable half.**

**Cycles 2–8 — the §Extractor-v4 alignment section, added after the gate log above was written.** Recorded
for the same reason `03-scope.md` §7.5 records its own rounds: a log that stops at round 1 while seven more
landed fixes is the *"each cycle's issues and fixes documented"* exit condition failing quietly. Each finding
was verified against its primary source before being applied, and one was falsified and **not** applied —
⚠️ a cycle claimed `#11088`'s body lacks the 48%/52% material, **⛔ DO NOT APPLY: it carries all of it**
(`:92`: *"≈52% … (2,570 of 4,901) … ≈48% precision"*).
The load-bearing corrections, in order: **2** the census population is the **union**, not the graph labels
alone; **3** the EMBED gate is **not** subordinate to VET; **4** this document was **right at its own `Head`
pin** and went stale, and the `EMBED before execute_embed` row was an invention; **5** `execute_embed`
performs no commit, and `#5059`'s dispositions are the owner's; **6** the never-classified half is
flag-dependent; **7** the pin clause's polarity was inverted; **8** `NO ISSUES FOUND` at `28d13ccc8` — at
which point the PR-level review (rounds 1–2, two fresh-context reviewers) added the G2 verifiable-target
clause to `03-scope.md` S1, the qualification that a flag-only S1 is correct on the **configuration half**
*only*, `#11395` as S3's tracking surface, this inventory's second `#11088` premise, and the measured-head
sha in the header above.

**Round 3 (PR level, fresh context, `24967e2d4b51`)** — 1 P1 + 1 P2, both applied or answered where the fix
belongs: **P1** the PR *body* is the pre-correction draft and asserts four of the premises this section
refutes (value gate "off too", the DISCARD reaching `S2.3`, both gates subordinate to VET, `EMBED` before
`execute_embed`) — answered in a PR comment rather than by editing the body, because the body carries the
review attestation and an edit wipes it; **P2** this inventory said §4.2 "holds only the ordering block",
which is false — the five correctness constraints sit before the resulting-order block and the dedup
preconditions and the Jev note after it — corrected at the `#11088` bullet above.

**Round 4 (PR level, fresh context, `0fe67cf486`)** — 4 P2: **two** were defects the round-3 fix itself
introduced (the `:883` attribution; the §4.2 ordering), one pre-dated round 3 (the S1 row's `path:line`
pointer into `encode_for_row`'s module, from `3f31bbcade`), and one was a claim in this log that nothing
backed (`468bbf8c10`). All four were verified
against their primary source first: **(1)** the round-3 sentence attached `#11088`'s paraphrase to `:883` — that section is
the Jev/`llm_tail` note and §4.2 contains no `EMBED` text at all, so the quote is now attributed to
`#11088`'s body where it actually lives; **(2)** "past the ordering block" misordered §4.2 — the five
correctness constraints (`:827`) precede the resulting-order block (`:849`), not follow it; **(3)** the only
`path:line` **code** citation in the diff (against `01-align.md:15-19`'s convention) — already fixed by
`416adf603191`, a peer commit on this branch, so this round records rather than repeats it; **(4)** this log
claimed a falsified finding *"was marked ⛔ DO NOT APPLY"*, and **no such mark existed anywhere** — the
correction is the mark itself, inline at the claim above, which is what that rule exists to leave behind.

**Round 5 (PR level, fresh context, `efac75e92a`)** — 1 P2: the Round 4 entry's summary sentence miscounted
its own findings, saying three of the four were introduced by the round-3 fix while the enumeration directly
beneath it conceded that item (3) was a peer commit's. Measured before correcting —
`git log -S` on the two tokens and `git show --stat 0fe67cf486` — the accurate partition is two introduced
by round 3, one pre-dated it (`3f31bbcade`), and one a log claim with no mark behind it (`468bbf8c10`), and
the sentence was corrected in place.

**Round 6 (PR level, fresh context, `dc524f02e7`)** — 2 P2, both defects the round-5 edit introduced, and
both the same class this log keeps recording: **the fix repeats the fault it fixes.** **(1)** the corrected
sentence spelled the pre-existing citation as a code `path:line` — in an added line, in a paragraph that then
claims the diff carries none, and against `01-align.md:15-19`; replaced with a symbol-level identification
that carries no line number. **(2)** the round-5 correction was made *in place* with no round of its own, so
the artifact's log stopped at round 4 while a fifth round's fixes landed — the precise divergence this log's
own opening paragraph says it exists to prevent. Both fixed here, and the two rounds recorded.

**Rounds 7–9 (PR level, fresh context)** — three more rounds landed after the entry above and are recorded
here for the same reason: the log had stopped while its own subject kept moving. (The `dc524f02e7` above is
a pre-rebase head and **not** an ancestor of the branch, so read these entries as an ordered disclosure, not
as a `git range`.) All three closed the same defect class — **a converted anchor that named the right topic
but did not resolve to a location**, which is this PR's own subject one level up.
**(7)** `d723dce1f70`: `if gap:` is not unique in `projection/__init__.py` — an earlier hit sits in
`_rebuild_all_replay`, not the inventory report — so every occurrence was qualified by its enclosing
function; and the `#3143` / `#3243` markers are prose tags that recur throughout their files rather than
locations, so they were replaced by the figure they name. **(8)** `32959faf7df`: round 7's replacement figure
was then refuted by measurement — in `monitoring.py` its copy is line-wrapped, so the exact literal resolves
to a different docstring many lines away; re-anchored to the block header, which is unwrapped and unique.
**(9)** `d451a66fb71`: the `Point`-only DDL cell had been rewritten to drop the file name in favour of two
literals that each resolve to many `.py` files, at least one of whose plain-grep hits is a
label-parameterised runbook call — the opposite shape from the "`Point`-only" claim the cell supports;
re-anchored to `_ensure_vector_index_api`, whose definition is unique among `.py` files.
**(10)** round 8's fix was applied to `monitoring.py` only: the same sentence still anchored `selfhost.py`
to the bare tag `#3243`, which greps to **10** hits there — the first two *before* the intended block, and
one of them names the cold-start allowance for the *opposite* subject (the on-demand MCP tool, not this
read). So the sentence's own instruction ("grep the block header, not the literal") was not followable for
that file. Re-anchored to the block header quoted above, which is unique in `selfhost.py` (`grep -cF` → 1).

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
