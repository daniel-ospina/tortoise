# The storage picture — where the layer is going, and where it hurts

**Issue:** `#13232` · **Written:** 2026-11-17 · **Status:** point-in-time synthesis, not a decision record.

**What this is.** One picture of the storage layer: the destination the repo has already written down, the
strands already walking toward it, and the one place where it currently hurts (~23 s on
`POST /v1/subjects`, `#13224`). It exists so a fresh lane reads this instead of re-deriving the map.

**What this is NOT.** ⛔ It is not a design change, not a proposal to adopt, and not a measurement of the
subject path. `#13224` owns the fix and its numbers; `#7879` owns the record; `#4051`'s search doc owns the
vector move. Everything below is **read** from the tree at `2060c92fd3`, and every cell is sourced.

**How to read a proposal in this document.** Every proposal carries a **CONTRADICTION TEST** line. The test is
run FIRST, before cost, fit or convergence, against the recorded decisions listed in §6. A proposal that
contradicts one is **not a candidate** — it appears in §6 as a *reopen candidate* for the owner, never as an
action. `#13232` is a lane that must not quietly pre-empt a decision another lane is already executing.

---

## 1. The destination, in six sentences (cited, not paraphrased)

1. **Postgres is the system of record; FalkorDB is a derived hot cache.** Owner ruling 2026-10-08
   (`STORAGE-ARCHITECTURE.md` §2.0). FalkorDB is *demoted, not dropped* (§14.2). The correct sentence is
   *"authoritative in Postgres, cached in FalkorDB"* — never *"they left FalkorDB"* (§3's ⛔ authority box).
2. **The vector index and the embedding payload move to Postgres (`pgvector`); traversal, writes and EP stay
   in the graph.** Owner ruling 2026-11-07, V1 resolved (`STORAGE-ARCHITECTURE.md` §12.1c/§14.3;
   `SEARCH-ARCHITECTURE.md` §3). The text index **does not** move — measured 8–23 nDCG@10 behind BM25
   (`SEARCH-ARCHITECTURE.md` §6.2).
3. **The cache stays COMPLETE through private-beta and becomes a bounded window only after the search doc's
   M2 measures retrieval quality** (`STORAGE-ARCHITECTURE.md` §2.0's NARROWED clause; `SEARCH-ARCHITECTURE.md`
   §1.5/§9). This is why §17.2/§17.3's residency router exists and does not yet fire.
4. **Every write goes to the truth first, then projects to the cache** — journal → record → cache
   (`STORAGE-ARCHITECTURE.md` §17.1). *"A derived cache that is also written to directly is not derived."*
5. **The narrative and the raw turns live in Postgres, never in the graph** (`§9.1`, D1). The narrative's one
   home is `public.session_narrative`; a turn row declares `public.point.cache_tier = 'record_only'`.
6. **What earns a vector is decided by the TEXT, not the class** (`§12.2`), the rule set is the pack's
   `memory_granularity` and the predicate is engine code (`§12.2a`), enforced at two points — `DISCARD`
   (does the row exist?) and `EMBED` (does it carry a vector?).

**One correction to how this is usually told, and it matters for every proposal below:** the removal half of
V3 is **refused** — `:Object` and `:Subject` embeddings are **KEPT** and no pass may remove them or stop
writing them (owner, 2026-11-10, `#11395` comment `6781921075`, cited at `§12.1c` and `§14.3`). What survives
in V3 is whether those labels are **indexed**, which is now a *work item*, not a decision.

---

## 2. The map

Columns: **layer** · **where it lives today** (with source) · **where the destination says it lives** ·
**what is in flight** · **what nobody owns**.

| layer | today | destination | in flight | unowned |
|---|---|---|---|---|
| **The record** | Postgres (Supabase). `public.point`, `public.object`, `public.subject`, `public.event`, `public.source`, `public.relation`, `public.session`; 85 migrations, `pgvector` in 1 of them (`20261109000001_record_embedding_vector.sql`, `#11642`) — `SEARCH-ARCHITECTURE.md` §3 | same — Postgres is the record, *for every layer including epistemic* (`STORAGE-ARCHITECTURE.md` §3 authority box) | `#7879` (epic), `#11642` (pgvector column + HNSW build), `#13133` (dense leg bounded to the read budget), `#9721` (record-sourced `:Event` loses ON CREATE props) | — |
| **The hot cache (graph)** | FalkorDB, written **directly** by 387 mutating Cypher sites across 211 functions in 32 files (`STORAGE-ARCHITECTURE.md` §17.1, measured 2026-10-08) | derived only; never authoritative (§2.0/§3) | `tortoise/cache_projection.py` (Interface B) projects record → cache; §17.1's eight declared unjournalled lanes | ⛔ **the chokepoint.** §17.1: *"'Point the write path at Postgres' is not one change"* — until a chokepoint exists the cache is written to directly. No issue owns it. |
| **Embedding payload** | a graph node property (`embedding`) on `Point`, `Subject`, `Object`, `Event`, `Source`, `Document` — written synchronously on every create (`tortoise/projection/entities.py:4844` Subject, `:5031` Object, `:2927`, `:5392`, `:5760`) | moves to Postgres (`public.*.embedding` stays `jsonb`; ANN half is a GENERATED `embedding_vec vector(384)`) — §12.1c, `SEARCH-ARCHITECTURE.md` §3 | V1 **decided** 2026-11-07; the build is `#11642` (measured: backfill 20k rows 6.9 s, HNSW build 4.0–4.2 s) | the **payload** column move is not scheduled — the doc is explicit that the `jsonb` payload deliberately does **not** convert (`SEARCH-ARCHITECTURE.md` §3). That is a decision, not a gap — but nothing tracks the graph-side payload's retirement, either. |
| **Vector index** | **exactly ONE** vector index in the whole graph — `Point.embedding` (HNSW). `Object`/`Event`/`Source`/`Subject`/`Document` have **none** (`§12.1b`, `CALL db.indexes()` 2026-09-24; `_ensure_indexes` hardcodes the literal `'Point'`) | pgvector HNSW in Postgres — **one filtered index** over a shared table, not five per-label indexes (§12.1c's per-label argument) | `#4997` (make index creation label-generic — the surviving V3 work), `#11642` (the pgvector index itself) | **the ordering between `#4997` and the pgvector cutover.** Building four more FalkorDB HNSW indexes now is RAM we are about to stop renting (§12.1c: *"four further resident HNSW index structures, five in total, vs one filtered index"*). Nobody owns that sequencing call. |
| **Text index** | FalkorDB `db.idx.fulltext` on 5 labels (Point/Event/Subject/Object/Source) — `§12.1b` | **stays** in FalkorDB; the move is deferred behind M3 (`SEARCH-ARCHITECTURE.md` §3/§6.2) | M3 (measure our own BM25 gap) | M3 has no issue number in either doc. **Unknown, not unowned** — see §7. |
| **What earns a vector (policy)** | `tortoise/embed_gate.py` — rule set **is** `value_gate.RULES`, one rule set two questions; flag-gated **off in code** (`TORTOISE_EMBED_GATE`) and **armed in the shipped template** (`.env.example`) — `§12.2a` | pack `memory_granularity` declares the policy; the engine owns the predicate — `§12.2a` | `#5008` (the EMBED gate), `#4899` (the shared rule set), `#11086` (the post-arming rate measurement) | ⛔ **the per-kind override block** — `§12.2a` names it *"still absent"*: no pack slot exists and no caller at the submission step holds the brief. So the owner's own meeting-vs-commit example cannot be declared today. |
| **The write order** | journal → record → cache, enforced at three runtime doors (`§17.1`); eight named unjournalled lanes declared | same | `#9206` (apply-first lanes), `#9079`/`#9391` (handle doors) | ⛔ **a residual stated in §17.1 and owned by nobody:** *"a RAW vendor client this package did not build … carries no hook, so the runtime doors are guard rails, not a proof"*. §17.1's durable fix is `#7951`. |
| **Residency / completeness marker** | `tortoise/residency.py` — two verdicts, `hot`/`not-hot`; exposed as a response field **off by default** (`TORTOISE_RESIDENCY`) — `§17.2` | fires once the cache is windowed (`§2.0` NARROWED; `§17.3`) | `#9189` (`record-reconcile` — the production trigger the comparison lacked) | — |
| **Narrative + raw turns** | `public.session_narrative`; turn rows are `public.point` with `cache_tier='record_only'`; the graph keeps `:Session` + counters — `§9.1` | Postgres only, never the RAM-resident layer (§9.1, D1) | `#9555` (narrative carrier), `#10000`/`#10017` (turns), `#10070` (`cache_tier`) | — |
| **Entity identity / linking** | `entity_identity.resolve_or_mint` (opaque id minted once; `name` is a mutable natural key); `entity_resolver` ladder (exact → alias → embedding similarity → adjudication → review) | unchanged by the storage move | `#12047` (entity resolver merged; `holdsRole`), `#12060` (kind guard), `#9964` (kind decides the layer) | — |

**The one-line reading of the map:** every *storage* row has a decision and an owner. The rows with nobody on
them are all **write-path discipline** (the chokepoint, the raw-client residual, the per-kind override), not
storage placement. That asymmetry is the whole reason a local fix to the subject write path is the wrong move
and a *write-path* framing is the right one.

---

## 3. The latency verdict

### 3.1 What is measured today

`#13224` measured, 2026-11-17 on production `tortoise-y4mjjq` (app `0a827908936c`, `TORTOISE_RECORD_SERVE=prefer`,
`TORTOISE_RECORD_DSN` present):

| call | result |
|---|---|
| `POST /v1/subjects` (`naturalPerson`), attempt 1 | **504 after 18.4 s** |
| `POST /v1/subjects`, attempt 2 (5 s later) | **200 after 22.9 s** |
| `POST /v1/points` (statement) | 200 after ~1.1 s |
| `POST /v1/objects` (goal) | 200 after ~1.4 s |

`#13224` has **zero comments** at the time of writing and no phase breakdown. **So there is no measured
cause yet, and this document does not invent one.**

### 3.2 What the code already settles — the paths are the SAME

I read both paths end to end. **`POST /v1/subjects` and `POST /v1/objects` are stage-for-stage the same code**,
and the only subject-only work on the create path is a branch that does not run for this call.

| stage | subject | object | same? |
|---|---|---|---|
| hosted route | `hosted_api.py:13889` | `hosted_api.py:13808` | ✅ same shape: `_require_scope` → `_data_sdk_offloaded` (`:10303`, a **fresh per-request SDK**) → `_check_org_limit_off_loop(org, "points")` → `named_kind` → `_require_approved_kind` (`:13758`, **a WHOLE-GRAPH read**, documented O(nodes) and *"run per call"*) |
| SDK alias | `create_subject` `sdk.py:54919` | `create_object` `sdk.py:54970` | ✅ both are thin relays to `create_entity` |
| kind guard | `_apply_kind_guard` `sdk.py:54074-54080`, with `installed_provider=_graph_installed_namespaces(self)` (`:7361` → `pack_state.graph_installed_namespaces`, **whole-graph, O(nodes)**, `pack_state.py:1170`/`:1198`) | identical call site | ✅ |
| layer note | `_kind_layer_note` | identical | ✅ |
| subject-only | `roleHolder`/`role_holder` pops (`:54152ff`), `resolve_role_holder`, `refuse_role_self_edge`, `wire_holds_role` | — | ⚠️ **only when a caller supplies `roleHolder`.** `#13224`'s probe did not. These do not run. |
| create lock | `entity_create_lock("Subject", name)` `sdk.py:54206` | `entity_create_lock("Object", name)` `:54229` | ✅ |
| identity | `_entity_identity_for` → `resolve_or_mint` (`entity_identity.py:547`) → `_live_holders_same_entity` (two indexed reads) | identical | ✅ |
| generic write | `_create_entity` `sdk.py:50428` → `_refuse_unapproved_kinds_in_props` (`:50368`, **another whole-graph read per kind prop**) → `_sanitize_props` → `_refuse_ending_dropped_by_a_re_mention` → `canonical_name` fold → event dict → `_emit_event` | identical funnel | ✅ |
| journal | `SubjectAdded` (`:50772`) | `ObjectRegistered` (`:50633`) | ✅ same mirror shape, post-apply |
| fold | `_upsert_subject` `projection/entities.py:4818` | `_upsert_object` `:5003` | ✅ |
| **embedding** | `encode_for_row(name, self.required_embedding_dim)` — **SYNCHRONOUS, inside the fold** `entities.py:4844-4846` | the **same call** `:5031-5033` | ✅ **both embed on create** |
| record schema | `public.subject`: `idx_subject_scope (org_id, graph_id)` (`20261012000002:106`), name index (`20261027000001:208`), RLS `subject_app_tenant` (`20261025000001:274`) | `public.object`: `idx_object_scope` (`:65`), name index (`:206`), RLS `object_app_tenant` (`:268`) | ✅ identical index and policy shape |

**⇒ Conclusion about the code, stated as narrowly as the evidence allows:** there is **no subject-only stage**
in the write path for this call. Any explanation of the 20× that rests on *"subjects do more work"* is already
**falsified by reading** — and that is why the difference must be found by measurement, not by inspection.

### 3.3 The verdict

> **INCIDENTAL — not structural.** A cost we chose (synchronous embedding, record-first write, entity
> linking, the kind guard) is paid by **both** paths, and the object path pays it in 1.4 s. A ~20× gap
> between two stage-identical paths cannot be a decision we made. It is either a **plan/index/pool/runtime
> property of one deployment** or a **data-shaped cost inside a stage both paths share** — and both of
> those are incidental.

⚠️ **The condition that would falsify this**, stated so it cannot be quietly dropped: if the breakdown in
§3.4 shows the seconds inside a stage **only** the subject path executes, the code-symmetry claim above is
wrong and the verdict flips to structural. §3.2 is a reading of one head (`2060c92fd3`); if a subject-only
stage landed after it, the reading expires.

### 3.4 The single probe that settles it — for `#13224` to run, not this lane

**One ablation, four calls.** Re-run exactly `#13224`'s four requests on the same box with
`TORTOISE_RECORD_SERVE=off` (the seam is `tortoise/record_serve.py:589` — the accepted modes are `off`,
`record`, `prefer`; the production run used `prefer`).

| arm | `subject` | `object` | verdict |
|---|---|---|---|
| `prefer` (already measured) | 22.9 s | 1.4 s | the gap |
| `off` | ? | ? | **if subject collapses toward object** → the seconds are in the **record leg** (Postgres plan / RLS / round-trips / pool) → incidental, and `#13224`'s fix is a DB-side or ordering fix, not a write-path redesign. **if subject stays ~20 s** → the cost is in the graph/journal/embedding path → the symmetry claim is falsified → escalate to a per-phase breakdown before any fix is chosen. |

**Why this probe and not a profiler first.** It is one env flip and four requests, it needs no instrumentation,
and it partitions the entire space in two — a `py-spy dump` mid-create is the right *second* step (it names the
frame) but the wrong first one, because it answers "where" without telling you whether the answer is inside a
decision or inside an accident.

**Two stages to watch, because they are the only whole-graph reads on the path and they are paid
*twice-to-thrice* per create:** `_require_approved_kind` (`hosted_api.py:13758-13790`) and
`_apply_kind_guard`'s `installed_provider` (`sdk.py:54079`), plus `_refuse_unapproved_kinds_in_props`
(`sdk.py:50368`) in the funnel. Each runs `pack_state.graph_kind_namespaces` — and
`pack_state.py:1198` says of its sibling: *"⚠️ This is a whole-graph read (it runs the per-property kind scans) …
the scan runs per call"*. **This is a stage both paths share, so it cannot be the 20× on its own — but it is a
plausible reason the BASELINE is 1.4 s rather than 200 ms, and it belongs in the same breakdown.**

⚠️ **Not a proposal.** Reading this as "remove the approved-kind gate" would be a *bypass* (it is the gate that
stops a write widening the graph's own pack approval set, `#5475`) — see §6.

### 3.5 What `#13224` can act on without re-deriving

- The write path is symmetric (§3.2, with citations). **Do not look for a subject-only stage in the SDK.**
- `:Subject` gets a **synchronous embedding on create** (`entities.py:4844`) for a vector that **no index
  serves** (§12.1b: `Subject` has no vector index) — the same is true of `:Object` (`:5031`). That is a real
  cost with no retrieval benefit **today**; it is also **decided** (2026-11-10 keeps the writing — §6.3), so
  it is not `#13224`'s fix. It is `#4997`'s.
- The read path is not the subject path's problem: `#13093` owns the doors, `#13133` bounds the dense leg to
  the read budget.

---

## 4. Proposals, ranked

Each: **what it changes** · **the decision/design it serves** · **what it forecloses** · **rough cost** ·
**class**. Class is `urgent` / `scheduled` / `do not do` (§6 carries the `do not do` list separately).

### P1 — Finish the `#13224` diagnosis before any fix, using §3.4's ablation — **urgent**

- **Changes:** nothing in the product. It produces the number `#13224`'s own O/I/T asks for and names which
  half of the space the fix lives in.
- **Serves:** `#13224` indicator 2 (*"a phase breakdown — where the ~20 s goes"*); this document's §3.3.
- **Forecloses:** a local optimisation of a stage that is not the cause — the exact short-termist move
  `#13232` was opened to prevent.
- **Cost:** one env flip, four HTTP calls, ~1 minute of wall clock.
- **CONTRADICTION TEST:** against `§2.0` ✅ (measures, changes no placement) · `§9.1` ✅ · `§12.1c` ✅ ·
  `#4010` (no capture caps) ✅ · off-by-default rules ✅ · MCP/SDK surface ⛔ n/a (no surface change).
  **No contradiction.**

### P2 — Land `#4997` as *index creation becomes label-generic*, ordered AFTER the pgvector cutover decision point — **scheduled**

- **Changes:** `_ensure_indexes()` stops hardcoding the literal `'Point'` so a served label can carry a vector
  index; the trace already reports `index`/`scan`, so the degradation stays observable.
- **Serves:** `§12.1b`/`§12.1c` — the surviving V3 work item, explicitly the *indexing* question the owner left
  open after refusing removal on 2026-11-10.
- **Forecloses:** nothing, if it is ordered right. ⚠️ **It forecloses RAM headroom if it is ordered wrong** —
  four more resident FalkorDB HNSW indexes (§12.1c: *"five in total, on the most expensive resource we
  have"*) on a cache the destination bounds after M2.
- **Cost:** the query side is already label-generic (`#172`); the index side is one function. The **cost that
  matters is the sequencing**, not the code.
- **CONTRADICTION TEST:** `§12.1c` ✅ **serves** the decision rather than pre-empting it (the decision moved
  *where* the index lives; it did not answer *whether these labels are indexed*) · `§2.0` ⚠️ **this is the one
  to watch** — building FalkorDB indexes for vectors that are scheduled to move to Postgres is a spend on a
  store that is being demoted. **So P2's real deliverable is a sequencing statement, not an index:** *do not
  build the four indices in FalkorDB; make the DDL label-generic so the same code serves a label that is
  genuinely staying, and let pgvector carry the served set.* **No recorded decision forbids that reading**;
  a lane that reads §12.1c as "build them now" contradicts §2.0's demotion and should stop.

### P3 — Close the per-kind embedding override slot (§12.2a's named absence) — **scheduled**

- **Changes:** adds the pack slot `embedding.overrides[{kind, embed}]` and passes the brief to the submission
  step, so the owner's own counterexample (a meeting with an authoritative calendar title earns a vector, a
  commit hash does not) becomes declarable instead of hard-coded.
- **Serves:** `§12.2`'s text rule and `§12.2a`'s *"the shape is BOTH"* (policy in the pack, predicate in the
  engine) — the design the EMBED gate already implements for the general case.
- **Forecloses:** nothing recorded. It is the declared remainder of a decided design.
- **Cost:** one pack slot + threading the brief to `embeddings.encode_for_row`'s caller. Small code, real
  design surface (a new pack clause needs the pack manifest's own review).
- **CONTRADICTION TEST:** `§12.2a` ✅ **it IS the decision's remainder** · `§2.0` ✅ · `#4010` ✅ (this narrows
  *embeddings*, never *capture* — a row still exists; only its vector is at stake) · off-by-default ✅
  (the gate is flag-gated in code) · surface ⚠️ **it touches the pack manifest format, not the MCP/SDK
  surface** — a pack-schema change is not the gated surface, but confirm that reading with the pack owner
  before writing it. **No contradiction.**

### P4 — Give the write-path chokepoint an owner — **scheduled** (the largest item here, and the least urgent)

- **Changes:** starts the work §17.1 says does not exist: *"⇒ 'Point the write path at Postgres' is not one
  change — until a chokepoint exists, the cache is written to directly, and is therefore not derived."*
  Measured: 387 mutating Cypher sites across 211 functions in 32 files.
- **Serves:** `§17.1`'s write-path rule, and `§17.2`'s second divergence surface (Postgres ↔ FalkorDB), which
  **has no designed mechanism at all** (*"No mechanism for the second surface is designed yet"*).
- **Forecloses:** nothing — but it is **not** a prerequisite for the subject latency or the vector move, and
  treating it as one would stall both.
- **Cost:** the 387-site census is the estimate; the §17.1 ledger shows the shape of the work (one declared
  lane per unjournalled writer), not its size.
- **CONTRADICTION TEST:** `§17.1`/`§17.2` ✅ **it is the decision's own stated gap** · `§2.0` ✅ · off-by-default
  ✅. **No contradiction.** ⚠️ **File it as a scope/owner question, not as a refactor.** A lane that starts
  rewriting 387 sites has misread a diagnosis as a mandate.

### P5 — Make the P2/`#4997` sequencing explicit in `STORAGE-ARCHITECTURE.md` §12.1b — **scheduled, cheap**

- **Changes:** one sentence in `§12.1b` stating that the surviving V3 index work is *served by the pgvector
  index for the labels that move*, so a lane reading the section cannot conclude "add four FalkorDB HNSW
  indexes now". §12.1c already argues the per-label cost; the two sections do not yet say what a lane should
  *do*.
- **Serves:** `§12.1c`'s decision; `docs/00_index.md`'s own claim that `#4997` is *"the one candidate that is
  a defect rather than a design question"*.
- **Forecloses:** a lane spending RAM on indexes that are about to be unnecessary.
- **Cost:** one documentation edit, reviewed.
- **CONTRADICTION TEST:** `§12.1c` ✅ · `§2.0` ✅ · nothing else is touched. **No contradiction.** ⚠️ This is a
  category-B (documentation) item under our own A/B rule: **its consequence is real but narrow** — the failure
  it prevents is *spend*, which is A-shaped (unrecoverable cost), so it clears admission.

---

## 5. What the destination says about the ~23 s — and what it does not

Worth stating plainly, because it is the question the issue actually asks: **does the destination keep this
cost or remove it?**

| cost the subject create pays | kept by the destination? | why |
|---|---|---|
| **synchronous embedding on create** (`entities.py:4844`) | **KEPT** — but its *placement* moves. The vector payload moves to Postgres (`§12.1c`), and Postgres's index is an ordinary transactional index (pgvector README: *"ACID compliance"*, the index is maintained with the row). So the **model call** stays on the write path; the **cross-store coherence problem disappears**. | This is the one place the destination genuinely helps: today a graph-node vector and the record can disagree (§17.2's *"no mechanism for the second surface is designed yet"*); after the move there is one store. |
| **record-first write order** (`§17.1`) | **KEPT** — it is the decision. | Not a cost to optimise away; it is *why* the cache is derived. |
| **entity resolution / identity** (`resolve_or_mint`) | **KEPT**, unchanged by the storage move. | The resolver is not a storage mechanism. |
| **the kind guard's whole-graph reads** (`pack_state.graph_installed_namespaces`) | **NOT ADDRESSED by any recorded decision.** | §12.2's embedding levers and §17's gaps say nothing about it. It is a per-call O(nodes) read on the write path, run 2–3× per hosted create. **This is the one genuinely unowned cost this document found** — and it belongs to `#13224`'s breakdown, not to a storage decision. |
| **per-request SDK + `_ensure_indexes()` cold open** (`hosted_api.py:10303`, `projection/__init__.py:20949`) | **NOT ADDRESSED**. `selfhost.py:392` states the shape: *"~28 sequential round trips — scales with graph size"*, and `projection/__init__.py:21020` says the function *"is reached from the hosted PER-REQUEST path"*. | Same: the destination does not speak to it. If the baseline of 1.4 s for an object is mostly this, then the *destination's* win is that a windowed cache makes the sweep cheaper — but nothing states that. **Unknown, not decided.** |

---

## 6. Do not do — the plausible wins that contradict the destination

Each names the decision it would reverse. These are **not** candidates; several are the first thing a lane
would reach for.

| # | the tempting win | the decision it contradicts | why it looks right and is not |
|---|---|---|---|
| **D1** | **"Add a vector index to `Subject`/`Object` now — it's one line."** | `§12.1c` (RESOLVED 2026-11-07: the vector index does **not** live in FalkorDB) + `§2.0`'s cache demotion | It is one line and it would work. It would also rent four more resident HNSW structures (§12.1c) in a store whose vector payload is scheduled to move. **Serve `#4997` instead** (P2/P5). |
| **D2** | **"Make the subject create async — queue the embedding, return fast."** | `§17.1`'s write order + `§3`'s *derived = replay(journal)* | A deferred embedding means the write returns before the row carries its vector, so `derived == replay(journal)` becomes false for a window — and the record/vector would disagree with no reconciliation (§17.2's undesigned second surface). **Not a tuning flag; a change to what "written" means.** If the owner wants this, it is a **reopen** — argue it, don't ship it. |
| **D3** | **"Drop the `Subject.embedding` write — nothing indexes it anyway."** | Owner ruling 2026-11-10 (`#11395` comment `6781921075`): `:Object`/`:Subject` embeddings are **KEPT**, no pass may remove them *or stop writing them*; `§12.1c`, `§14.3` V3 | The premise is true (no vector index serves `Subject`, `§12.1b`) and the conclusion is forbidden. This is the **exact** proposal the owner already refused. |
| **D4** | **"Remove the approved-kind gate / the kind guard — they're whole-graph reads on a hot path."** | `#5475`'s gate (a write must not widen the graph's own pack approval set), `§12.2a`'s "policy lives in the pack" | The reads are genuinely expensive (P-none — see §5). Removing the gate is a **bypass**: an unvalidated `ns:kind` makes the whole pack writable (`hosted_api.py:13758-13766`). **The fix is caching or a cheaper invariant, never the gate's absence.** |
| **D5** | **"Put the narrative / raw turns back on the node — it's just a property."** | `§9.1` (D1) — the narrative lives in Postgres, not the graph; `CACHE_FORBIDDEN_COLUMNS` | The graph can never unload it (`§2.1`) and no read path reads it back. §9.1's deny list exists because this was tried: seven routes are denied by name. |
| **D6** | **"Point the read path at Postgres now — one store is simpler."** | `§2.0`: `#7970`'s anchored-read cutover stays **DEFERRED**; §14.2 | Explicitly named as still-deferred *by the same ruling that adopted the dual-store search path*. Not open. |
| **D7** | **"Move the text index to Postgres too — one store."** | `SEARCH-ARCHITECTURE.md` §3/§6.2: the text index **stays**, measured 8–23 nDCG@10 behind BM25 | The vector move's momentum makes this look like the same change. It is not: it is a measured quality regression. |
| **D8** | **"Cap capture / sample the writes to make the numbers look better."** | `#4010` — no capture caps | Named in the brief's contradiction list; listed because "write less" is §12.2b's lever 2 and it is easy to slide from *write less noise* into *capture less*. |
| **D9** | **"Expose `residency` / a new tool so callers can see the cache is partial."** | ⛔ MCP/SDK surface change requires **Daniel's approval**; `§17.2` already exposes `residency` as a response field, **off by default** | The capability exists. Adding a *tool* is a surface change and is gated. |

### 6b. Reopen candidates — put in front of the owner, not adopted

These contradict a recorded decision, so they are **not** proposals. They are recorded here because refusing an
adoption is not a verdict on the finding.

**R1 — Defer the embedding off the write path (contradicts `§17.1` + `§3`'s replay invariant).** The external
half of this research (rung 3, §8) shows at least one comparable system pays an inline extraction+embedding on
ingest (Graphiti's `add_episode` builds node/edge embeddings inside the call — see
`graphiti_core/graphiti.py`'s `create_entity_node_embeddings`/`create_entity_edge_embeddings` imports and use),
so our synchronous embedding is **the common shape, not an outlier**. The argument *for* reopening is narrow and
worth stating honestly: **Postgres's own answer to this is cheaper than ours** — an embedding written in the
same transaction as the row means deferral is possible without a coherence problem, which it is not today
(§17.2's undesigned second surface). *If* the owner wants sub-second creates, that is the decision to reopen, and
the price is `derived == replay(journal)` for an interval. **Do not adopt; do not drop.**

**R2 — Index only what is served, in one store (contradicts `§12.1c`'s ordered sequence, not its decision).**
Not a reversal — a **sequencing** question the docs do not answer: pgvector's index is transactional, so a served
label with an embedding could be queryable from Postgres *before* the FalkorDB index is retired. If that holds,
`#4997`'s "make the DDL label-generic" may be work with no consumer. **This is a question for `#4997`'s owner,
not a lane's call** — flagged because P2 and P5 both lean on it.

---

## 7. Unknown — what this picture cannot say, and what would settle each

⛔ A confident synthesis of an incomplete picture is the failure this issue exists to prevent. These are the
holes, stated as holes.

| # | unknown | what would settle it |
|---|---|---|
| **U1** | **Where the ~20 s actually goes.** `#13224` has no breakdown (0 comments at this head). §3.3's verdict is derived from *code symmetry*, not from a measurement of the gap. | §3.4's ablation; then a `py-spy dump` (or per-stage timers) inside the half it implicates. |
| **U2** | **Whether the ~1.4 s object baseline is mostly the per-request `_ensure_indexes()` sweep.** `projection/__init__.py:21020` says the function is reached from the hosted per-request path; `selfhost.py:392` says it is ~28 sequential round trips that scale with graph size. **Nobody has measured its share of a hosted create.** | Time one `POST /v1/objects` with the projection cold-open hoisted out (or against a warm cached SDK). If the baseline is the sweep, the **window** (§2.0 NARROWED) is the lever, not any write-path code. |
| **U3** | **Whether `graph_installed_namespaces` is 2 or 3 whole-graph scans per create.** I found three call sites on the path (`hosted_api.py:13790`, `sdk.py:54079`, `sdk.py:50368`); whether all three fire for one write depends on the guard's abstain path, which I did not trace to ground. | One traced create with a counter on `graph_kind_namespaces`. Cheap, and it is the *only* unowned cost in §5. |
| **U4** | **The size and cost of that scan on the production graph.** It is O(nodes) over the per-property kind scans; no figure for the production graph exists in either doc. | Time `graph_kind_namespaces` against `tortoise-y4mjjq` (read-only). |
| **U5** | **Whether the four `#4997` labels' vectors are actually payload-worth-indexing** — i.e. whether a `:Subject` similarity query is a modality anyone uses. §13's *"What remains UNKNOWN"* asks the analogous question about `belief_timeline`/`topic_summarize` and answers it *"unanswerable before launch"*. | Post-launch call volume on the entity vector leg. **This is why P2 is scheduled, not urgent.** |
| **U6** | **M3 (our own BM25 gap) has no issue number** in either architecture doc. The text index's move is deferred behind it, and it is cited as a plan item with no ticket. | Either an issue, or a correction to `SEARCH-ARCHITECTURE.md` §3. **Unknown, possibly a documentation gap — I did not search the tracker exhaustively for it.** |
| **U7** | **What the per-kind embedding override (P3) costs in pack-schema review.** `§12.2a` names the slot as absent but does not size it. | The pack manifest owner's read; not in scope here. |
| **U8** | **Whether the raw-client residual in §17.1 is reachable in the hosted deployment** (a vendor client built outside `guarded_client`). The doc says the doors are *"guard rails, not a proof"*. | A grep census over the deployment's own entry points — `hosted_api`/`hosted_*` — for `FalkorDB(...)` constructions outside `guarded_client`. |

---

## 8. Research record — rungs taken and rungs that failed

The external half was mandated to answer: how comparable systems place embeddings and vector indexes relative to
the write path (synchronous vs deferred), what latency budget they hold for a single-entity create, and how they
keep a vector index coherent with a relational record.

- **Rung 1 — UNAVAILABLE.** `mcp__seo-intelligence__perplexity_search` → `Unknown MCP server 'seo-intelligence'`.
  That server is not declared on this box.
- **Rung 2 — ATTEMPTED, UNAVAILABLE.** `web_search` with `model="sonar"` passed explicitly →
  `PERPLEXITY_API_KEY not set. Set it via PERPLEXITY_API_KEY env var, AGENT_MCP_ENV_PATH (path to .env file), or
  $AGENT_INFRA_PATH/../.env`. ⚠️ **The key exists** (`~/.env` holds `PERPLEXITY_API_KEY`), but the tool resolves
  it through `AGENT_MCP_ENV_PATH`, which is **unset in this process** — so the rung failed on a *configuration
  gap in the harness*, not on a missing credential. Reported rather than worked around.
- **Rung 3 — USED (primary sources, `web_fetch`).**

  | source | what it settles |
  |---|---|
  | `pgvector/pgvector` README (`raw.githubusercontent.com/pgvector/pgvector/master/README.md`) | **The coherence answer.** pgvector advertises *"ACID compliance, point-in-time recovery, JOINs"* — the vector index is an ordinary Postgres index, maintained **in the same transaction** as the row. **This is the structural difference from our two-store layout:** after the V1 move there is no vector-vs-record divergence window, which is precisely the surface `§17.2` says has *"no mechanism designed yet"*. Also: an HNSW index *"can be created without any data in the table since there isn't a training step like IVFFlat"* — so the move's index build does not need a backfill ordering. |
  | `getzep/graphiti` — `graphiti_core/graphiti.py` (+ README) | **The write-path placement answer.** Graphiti's ingestion (`add_episode`) builds node and edge embeddings **inside the ingest call** (`create_entity_node_embeddings`, `create_entity_edge_embeddings` imported and used there), i.e. **synchronous embedding is the common shape among comparables**, not an outlier we invented. Its README's framing (*"Everything traces back to episodes … the ground truth stream"*) is the same record-first posture as §3/§17.1. |
  | `mem0ai/mem0` README | **The latency number, with a caveat.** Mem0 publishes **retrieval** p50 (0.88–1.09 s across LoCoMo/LongMemEval/BEAM) — **not** a write budget. So the widely-quoted "memory systems are ~1 s" figures are read-path figures and must not be used as a write budget. **Useful only as a negative result:** no comparable publishes a per-entity-create budget, so we have no external bar to hold ourselves to; `#13224`'s `p95 < 1 s` target is **ours**, and it is not contradicted by anything found. |

⚠️ **What the external half does NOT establish:** no comparable publishes a single-entity-create latency SLO, and
none of the three is a two-store record/cache split, so **none of them can tell us whether our 23 s is
acceptable** — only that synchronous embedding is normal and that the coherence half of our problem is a
consequence of *our* split, not of the design space. That is the honest limit of this rung.

---

## 9. What this document does not claim

- It does **not** measure the subject path (§3.4 is a probe for `#13224` to run).
- It does **not** propose a storage change. The map (§2) reports decisions already made.
- It does **not** re-open V1, the hybrid, or the text index's home. §6/§6b name them as closed or as
  owner-reopen-only.
- It does **not** claim the code-symmetry in §3.2 survives the next merge. It is a reading at `2060c92fd3`.

**Sources.** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0, §2.1, §3, §9.1, §12.1b, §12.1c, §12.1d, §12.2,
§12.2a, §12.2b, §13, §14.2, §14.3, §17.1, §17.2, §17.3 · `docs/architecture/SEARCH-ARCHITECTURE.md` §3, §6.2,
§9, §10 · `docs/ONTOLOGY.md` §2, §3, §4 · `tortoise/hosted_api.py`, `tortoise/sdk.py`,
`tortoise/projection/entities.py`, `tortoise/projection/__init__.py`, `tortoise/entity_identity.py`,
`tortoise/pack_state.py`, `tortoise/record_serve.py`, `supabase/migrations/20261012000002_record_semantic.sql`,
`20261025000001_record_tenancy_role_and_guc.sql`, `20261027000001_record_semantic_id_key.sql` ·
issues `#7879`, `#5087`, `#11002`, `#7949`, `#4997`, `#4999`, `#9721`, `#12901`, `#13133`, `#13093`, `#13224`,
`#13232`, `#11395`, `#4010`, `#5475`, `#7951`, `#8008`(see `#5008`), `#4899`, `#11086`, `#9189`, `#9206`,
`#9079`, `#9391`, `#9555`, `#10000`, `#10017`, `#10070`, `#12047`, `#12060`, `#9964`, `#11642`, `#7970`,
`#4051`.
