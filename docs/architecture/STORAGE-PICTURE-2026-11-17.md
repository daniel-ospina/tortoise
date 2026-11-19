---
<<<<<<< HEAD
title: "The storage picture — where the layer is going, and where it hurts (#13232)"
=======
title: "The storage picture — where the layer is going, and where it hurts"
>>>>>>> origin/main
type: engineering
domain: platform
doc_status: draft
created: 2026-11-17
<<<<<<< HEAD
issue: "#13232"
subjects.team: epistemic-team
aboutSubjects: storage layer, Postgres record, FalkorDB cache, vector placement, write-path latency, search
aboutObjects: "STORAGE-ARCHITECTURE.md, #13232, #13224, #7879, #13220, #4051"
=======
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#13232, #13247, #13224, STORAGE-ARCHITECTURE.md"
>>>>>>> origin/main
---

# The storage picture — where the layer is going, and where it hurts

**Issue:** `#13232` · **Written:** 2026-11-17 · **Read at:** `d1bfef778f` (branch `research/13232-storage-picture`)
· **Status:** point-in-time synthesis, **not** a decision record.

**What this is.** One picture of the storage layer: the destination the repo has already written down, the strands
already walking toward it, and the one place it currently hurts (`~23 s` on `POST /v1/subjects`, `#13224`). It
exists so a fresh lane reads this instead of re-deriving the map.

**What this is NOT.** ⛔ Not a design change, not a proposal to adopt, and not this lane's measurement. `#13224` owns
the subject path; `#7879` owns the record; `#4051`'s search doc owns the vector move; `#13220` owns the index
question. Every cell below is **read** from the tree or an issue, and every claim about the latency is quoted
from the lane that measured it.

> ⚠️ **THIS PICTURE IS DATED, AND ONE CELL IT READS HAS SINCE MOVED — stated here so no reader takes a superseded
> status as current.** Its reading is at `d1bfef778f` (2026-11-17). **On 2026-11-18 the owner rescinded the
> anchored-read cutover's deferral** (`#7970` comment
> [`6965853268`](https://github.com/daniel-ospina/tortoise/issues/7970#issuecomment-6965853268)): the read path IS
> served from the record, `TORTOISE_RECORD_SERVE` selects `off` / `prefer` / `record`, and `prefer` — armed in
> production — is the mode that measures the migration. **The authority is `STORAGE-ARCHITECTURE.md` §2.0, not this
> file**, and the two rows below that read the old status now say so.

**How to read a proposal here.** Every proposal carries a **CONTRADICTION TEST** line, run FIRST — before cost, fit
or convergence — against the recorded decisions in §6. A proposal that contradicts one is **not a candidate**: it
goes to §6b as a *reopen candidate* for the owner, never to the action list.

---

## 1. The destination, in seven sentences (cited, not paraphrased)

1. **Postgres is the system of record; FalkorDB is a derived hot cache.** Owner ruling 2026-10-08
   (`STORAGE-ARCHITECTURE.md` §2.0). FalkorDB is *demoted, not dropped* (§14.2). The correct sentence is
   *"authoritative in Postgres, cached in FalkorDB"* — never *"they left FalkorDB"* (§3's ⛔ authority box).
2. **The vector index and the embedding payload move to Postgres (`pgvector`); traversal, writes and EP stay in the
   graph.** Owner ruling 2026-11-07, V1 resolved (§12.1c, §14.3; `SEARCH-ARCHITECTURE.md` §3). The **text index does
   not move** — measured 8–23 nDCG@10 behind BM25 (`SEARCH-ARCHITECTURE.md` §6.2).
3. **The cache stays COMPLETE through private-beta and becomes a bounded window only after M2 measures retrieval
   quality** (§2.0's NARROWED clause; `SEARCH-ARCHITECTURE.md` §1.5/§9). This is why §17.2/§17.3's residency router
   exists and does not yet fire.
4. **Every write goes to the truth first, then projects to the cache** — journal → record → cache (§17.1).
   *"A derived cache that is also written to directly is not derived."*
5. **The narrative and the raw turns live in Postgres, never in the graph** (§9.1, D1); a turn row declares
   `public.point.cache_tier = 'record_only'`.
6. **⭐ NEWEST — capability-first placement (owner, 2026-11-16, `#13082`):** *"anything no graph query reads is
   Postgres-only by default, and staying in the graph is something a shape must justify."* RAM is **~584× the price of
   disk** ($73/GB-month vs $0.125/GB), so solo's storage line (30 % of $19) gives a **~78 MB of RAM per tenant**
   ceiling. *"The window is derived from the budget — never from a calendar."*
7. **⭐ NEWEST — index them, but in Postgres (owner, 2026-11-17, `#13220`):** *"if indexing improves search for
   Object/Subject/Event of course we want that, and we just need it in Postgres and the search aligned with that and
   everything armed and working"* … *"we should likely also index source also in postgres."* This answers §14.3's
   **V3** (are `:Object`/`:Subject` indexed? **yes, in Postgres**). ⚠️ **It does NOT answer V2:** *"V2 stays open"*
   (§14.3, line 1577) — whether a `Source` gets a summary vector **at all**, and what a `Source` *with no session
   narrative* embeds, is *"itself part of this decision, unsettled"* (§14.3's V2 row). The owner's word on source was
   *"likely"*, and it is an inclination about **indexing**, not a ruling that a Source gets a vector. ⚠️ `#13220` also
   explicitly states that `#11901`'s *"not earned"* verdict was about **FalkorDB** indexes and does **not** apply to a
   pgvector index.

**Two corrections to how this is usually told, and they matter for every proposal below:**

- The **removal** half of V3 is **refused** — `:Object`/`:Subject` embeddings are **KEPT** and no pass may remove them
  or stop writing them (owner, 2026-11-10, `#11395` comment `6781921075`, cited at §12.1c/§14.3). What survives is
  *whether they are indexed* — and #13220 has now answered: yes, in Postgres.
- **`#4997` is CLOSED** (`stateReason: COMPLETED`), superseded by **`#13220`**. A lane that reads the brief's `#4997`
  reference as live work will rebuild a decision that has moved.

---

## 2. The map

Columns: **layer** · **where it lives today** · **where the destination says** · **what is in flight** ·
**what nobody owns**.

| layer | today | destination | in flight | unowned |
|---|---|---|---|---|
| **The record** | Postgres (Supabase): `public.point`, `object`, `subject`, `event`, `source`, `relation`, `session`; `pgvector` in 2 of 88 migrations (`20261109000001_record_embedding_vector.sql`, `20261117000001_record_object_subject_embedding.sql`) — `SEARCH-ARCHITECTURE.md` §3 | same — Postgres is the record for **every** layer, epistemic included (§3's authority box) | `#7879` (epic); `#11642` (pgvector column + HNSW build); `#13133` (**merged** — dense leg bounded to the read's budget); `#9721` (record-sourced `:Event` loses fold-written ON CREATE props — up to 8, PER COLUMN, never a fixed count); PR `#13210` (the record's runner + `apply` = `#7879`'s holes 1 and 2) | ⚠️ **Not what `#7879` says it is.** The epic's 2026-11-17 comment lists *"no live wiring visible: no write path in `sdk.py` calls the fold"* as hole 3 — **that text is wrong at this head**: `sdk.py:15719` `_write_record_projection` is labelled *"THE production call site"* and calls `record_writer.write_live_record` at `:15777`, wired from `_emit_event` (`:15341`) and reached by every entity create (`:50767`/`:50785`). **What is actually unowned** is one chokepoint + the journal↔record atomicity (§17.1; `#7951` is **CLOSED**, and closed the rule's *statement*, not the chokepoint), **per-graph arming** (`TORTOISE_RECORD_DSN` + allocator), and the **historic backfill** (hole 2 → PR `#13210`). |
| **The hot cache (graph)** | FalkorDB, written **directly** by 387 mutating Cypher sites across 211 functions in 32 files (§17.1, measured 2026-10-08) | derived only, never authoritative (§2.0/§3); *"staying in the graph is something a shape must justify"* (`#13082`) | `cache_projection.py` (Interface B) projects record → cache; §17.1's eight declared unjournalled lanes; `#13083` (capability audit, census landed), `#13094` (`:GraphEvent` has no graph reader) | ⛔ **the chokepoint.** §17.1: *"'Point the write path at Postgres' is not one change."* No issue owns it. |
| **Embedding payload** | a graph node property on `Point`/`Subject`/`Object`/`Event`/`Source`/`Document`, written **synchronously inside the fold** — five symmetric sites in `projection/entities.py:2927`, `:4844`, `:5031`, `:5392`, `:5760` | Postgres. `point`/`event`/`source` already have `embedding` + a GENERATED `embedding_vec vector(384)` + HNSW; **`object` and `subject` have NO embedding column at all** and the record *actively refuses* one (`chk_object_props_no_derived`, `chk_subject_props_no_derived`) — `#13220`'s measured table | `#13220` (owner ruling 2026-11-17) | ⛔ **all of `#13220`.** Zero comments, no PR, no branch. **This is the largest unowned piece in the landscape**, and its item 1 is the sharpest: `:Object`/`:Subject` fingerprints **exist only in FalkorDB**, so a record-based rebuild cannot restore them — a durability gap *against the decision that Postgres is the record*. |
| **Vector index** | **exactly ONE** vector index in the graph — `Point.embedding` (HNSW). `Object`/`Event`/`Source`/`Subject`/`Document`: **none** (§12.1b, `CALL db.indexes()` 2026-09-24; the creation call hardcodes the literal `'Point'` — `projection/__init__.py:21698`, and the inventory read filters `!= "Point"` at `:21579`) | **one filtered pgvector index in Postgres**, not five per-label HNSW structures (§12.1c's per-label argument); the index is transactional with the row | `#13220` (the ruling); `#11642` (the infra, built — `event`/`source` indexes exist but hold **0 rows**) | ⛔ **the graph-side copies' retirement** needs an explicit **reopen** of the 2026-11-10 KEEP ruling, and #13220 states it is *"out of scope without an explicit reopen"*. **No issue exists to hold that reopen** (§6b R1). |
| **Text index** | FalkorDB `db.idx.fulltext` on 5 labels (§12.1b) | **stays** in FalkorDB (`SEARCH-ARCHITECTURE.md` §3) | **M3 is MEASURED** — `SEARCH-ARCHITECTURE.md:275`: *"STATUS — M3 is measured (2026-11-13, `#12298`)"* — swapping the text leg earns no recall return; the ranking-distance qualifier followed 2026-11-17 (`#13099`) | ⚠️ What remains open is narrower than "unfiled": the 8–23 nDCG@10 figure still compares **foreign** datasets, and §6.2 says *"how far our hosted leg sits from that baseline is unmeasured"* (U6) |
| **What earns a vector (policy)** | `tortoise/embed_gate.py` — rule set **is** `value_gate.RULES`, one rule set two questions; flag-gated **off in code** (`TORTOISE_EMBED_GATE`) and armed in the shipped template (§12.2a) | pack `memory_granularity` declares the policy; the engine owns the predicate (§12.2a) | `#5008`, `#4899`, `#11086` (post-arming rate measurement) | ⛔ **the per-kind override block** — §12.2a names it *"still absent"*: no pack slot exists, no caller at the submission step holds the brief. The owner's own meeting-vs-commit example cannot be declared today. |
| **The write order** | journal → record → cache, enforced at three runtime doors (§17.1); eight named unjournalled lanes declared | same | `#9206`, `#9079`, `#9391` | ⛔ **a stated residual with no owner:** *"a RAW vendor client this package did not build … carries no hook, so the runtime doors are guard rails, not a proof"* (§17.1). The durable fix is `#7951`. |
| **Residency / window** | cache is COMPLETE; `residency.py`'s marker exists but **cannot fire** on a complete cache (§17.2); `TORTOISE_RESIDENCY` off | a measurement-derived window (`#13082`) | `#13084` (arm the per-tenant RAM cap, PR `#13102`); `#13103` (**blocked by #13084**, *"filed so it is not lost"*); `#9189` (`record-reconcile` — the production trigger) | — |
| **Dense retrieval leg** | the record leg is bounded (`#13133`, merged); the **graph** leg silently degrades to keyword-only under load — the mechanism is measured and reproducible, the **share** is not: `#13108`'s 45 % came from its own run, and a re-measurement at the eval's own 1,000-name scale read **0 % keyword-only in every arm, even at load 131**, reproducing only at 8,000 rows under load (**3.3 %, reason `timeout`**) — `docs/research/2026-11-17-13108-dense-leg/research-brief.md` §3.3 | record-first for similarity (§3), graph for traversal | `#13108` (reopens V3 *with numbers* — ⚠️ *"a reopen … not a local tweak"*), PR `#13121` merged (mechanism measured, wasted index attempt removed), `#13156` (a budget-cancelled leg is indistinguishable from a slow store in the trace), `#13169` | ⚠️ **No fixed share figure exists**; only the mechanism. Any lane quoting "45 %" is quoting one run over one population (U2) |
| **Narrative + raw turns** | `public.session_narrative`; turn rows are `public.point` with `cache_tier='record_only'`; the graph keeps `:Session` + counters (§9.1) | Postgres only (§9.1, D1) | `#9555`, `#10000`, `#10017`, `#10070` | — |
| **Identity / linking** | `entity_identity.resolve_or_mint` (opaque id minted once, `name` is a mutable natural key); `entity_resolver` ladder — exact/alias auto-resolve, fuzzy/semantic go to the **review queue** (`entity_resolver.py:56-64`) | unchanged by the storage move | `#12047`, `#12060`, `#9964` | — |
| **What gets written at all (volume)** | the extractor's selection pipeline; §12.2b lever 2 — *"do not store what we do not need"* — is the biggest lever that needs **no** engine change | fewer, better rows; §8's granularity is not negotiable | `#5087` (extractor v4, S0–S6), `#11002`, `#7949` (capstone) | ⚠️ **`#11002` is scope-approved but blocked on an unanswered OWNER question** — its own scoping says it *"is a reversal of a recorded decision (`#1536`'s 'S4 merges, not replaces')"*, and `#5087` already declares *"No S4"*. **The epic's DoD and the tree currently disagree**, pinned only by `tests/test_extractor_v4_s4_stage_claims.py`. Also: `#7949`'s **E1 premise is explicitly in question** (the narrative emitted *more* claims, +35 %). |
| **The read doors (record serving)** | three doors serve point/entity reads; the record leg is armed with `TORTOISE_RECORD_SERVE=prefer` in production | ⚠️ **SUPERSEDED 2026-11-18 — the cutover is ARMED, not deferred** (`STORAGE-ARCHITECTURE.md` §2.0, `#7970` comment [`6965853268`](https://github.com/daniel-ospina/tortoise/issues/7970#issuecomment-6965853268)); `prefer` measures the migration and `record` follows its count. What the 2026-11-07 ruling adopted separately is the dual-store SEARCH path | `#13093` (**OPEN** — *"stays open for the entity door only"*; the `prefer` arming surface landed in PR `#13096`), `#13133` (merged, bounds the dense leg), `#12901` (SDK retirement; waves 1–2 merged, 125 → 94), `#13084`/`#13103` (RAM cap → window) | — |

**Open-PR census at this reading** (`gh pr list --state open`, 11 open): **four** match the storage/embedding/vector/index/record keywords — **`#13229`** (the `#13224` fix, §3.1), **`#13210`** (`#7879`'s runner + `apply` — holes 1 and 2), **`#13214`** (a blocked allowance dimension's release conditions), **`#13124`** (ranker dispatches by row type). **None** touches pgvector schema, `record_vector.py`, the search engine's dense leg, or the extractor's write path. This document's own PR is a **fifth**, and it is docs-only.

**The one-line reading of the map.** Every *storage row* has a decision — and on the two newest ones the owner has just
decided while the work is barely started: **`#13220` has nobody on it at all** (0 comments, no branch), and **`#13082`
has three children in flight** (`#13083`'s census landed, `#13084` via PR `#13102`, `#13094`) but **no implementation of
the cap itself**. Every row with nobody on it is
**write-path discipline** (the chokepoint, the raw-client residual, the per-kind override), not storage placement.
That asymmetry is the whole reason a local fix to the subject path is the wrong move and a *write-path* framing is
the right one.

---

## 3. The latency verdict

### 3.1 What is measured

`#13224` measured 2026-11-17 on production `tortoise-y4mjjq` (app `0a827908936c`, `TORTOISE_RECORD_SERVE=prefer`,
`TORTOISE_RECORD_DSN` present): `POST /v1/subjects` **504 after 18.4 s**, retry **200 after 22.9 s**;
`POST /v1/points` ~1.1 s; `POST /v1/objects` ~1.4 s.

**The breakdown exists — and it is NOT on the issue.** `#13224` has **zero comments** and still says
*"Cause: UNKNOWN"*. The diagnosis is in **PR `#13229`**'s body (open, MERGEABLE, self-described as the `#13224` fix):

| measurement | value | source |
|---|---|---|
| an **in-process** subject create, warm | **8–10 ms** | PR `#13229` |
| the same create **inside the hosted handler**, 12k-node graph | **27–32 ms** | PR `#13229` |
| `EmbeddingModel.get()`, warm model cache, no download | **13.5 s** | PR `#13229` |
| the recorded cold load (`#1349`) | ~57 s | `embeddings.py:869` |
| `_LOAD_TIMEOUT_S` | 90 s | `embeddings.py:869` |
| a **warm** encode | 25–43 ms | PR `#13229` |
| the transport wait bound that produced the 504 | 10 s | `mcp_auth._TRANSPORT_WAIT_BOUND_S` |

> **"The cause is not the write. … The stall is the cold embedding-model load, paid on the write path's own request
> thread. … `Subject` was simply the door that arrived first; the same stall is reachable from any write."**
> — PR `#13229`

Reproduction (hosted ASGI app, real FalkorDB, pre-warm in flight): before → `#1 10016 ms HTTP 504`,
`#2 4796 ms`, `#3 205 ms`; after the fix → `#1 438 ms`, `#2 395 ms`, `#3 391 ms`. A/B over six creates inside one
13.5 s cold window: **before p50 49.7 ms / p95 20,414.2 ms → after p50 266.7 / p95 267.8 ms.**

`#2 > #1` before the fix is the **abandoned load thread**: the retry joins it and completes when the load lands —
*"which is why production's retry took 22.9 s, longer than the 18.4 s timeout that preceded it."*

### 3.2 This lane's independent check — the paths are the SAME

Before the landscape lane found PR `#13229`, I traced both paths end to end. **`POST /v1/subjects` and
`POST /v1/objects` are stage-for-stage the same code**, and the one subject-only block does not run for this call:

| stage | subject | object | same? |
|---|---|---|---|
| hosted route | `hosted_api.py:13888` | `:13820` | ✅ `_require_scope` → `_data_sdk_offloaded` (`:10303`, a **fresh per-request SDK**) → `_check_org_limit_off_loop(org,"points")` → `named_kind` → `_require_approved_kind` (`:13758`, a **whole-graph O(nodes) read**, *"documented to run per call"*, lines 13785–13787) |
| SDK alias | `create_subject` `sdk.py:54924` | `create_object` `:54970` | ✅ thin relays to `create_entity` |
| kind guard + layer note | `sdk.py:54078`/`:54092` | identical | ✅ `kind_guards.resolve` is pure and process-cached (`kind_guards.py:732-734`) |
| **subject-only** | `roleHolder`/`role_holder` pops, `resolve_role_holder`, `refuse_role_self_edge`, `wire_holds_role` (`sdk.py:54157-54246`) | — | ⚠️ **gated on `roleHolder` being supplied** (`:54180`), and `CreateSubjectRequest` declares **only** `name`/`subjectKind` (`hosted_api.py:13881-13885`). **Does not run.** |
| create lock + identity | `entity_create_lock("Subject", name)` `:54206` → `resolve_or_mint` (`entity_identity.py:547`) | `"Object"` `:54244`, same resolver | ✅ same predicate, same two indexed reads; both labels carry RANGE indexes on `name`/`canonical_name`/`resolved_from` (`projection/__init__.py::_REQUIRED_RANGE_INDEXES`, `:20340-20375`) |
| generic funnel | `_create_entity` `sdk.py:50433` → `_refuse_unapproved_kinds_in_props` (`:50473`) → `_sanitize_props` → `canonical_name` → event dict → `proj.apply` (`:50710`) → `_emit_event` (`:50785` / `:50767`) → `write_live_record` | identical | ✅ |
| fold | `_upsert_subject` `projection/entities.py:4821` | `_upsert_object` `:5004` | ✅ same three statements: conditional `clear_embedding`, one `MERGE`, `_persist_extra_props` (skipped when empty) |
| **embedding** | `encode_for_row(name, dim)` — **synchronous, inside the fold** `entities.py:4843-4851` (the call at **`:4845`**) | the **same call** at **`:5032`** (`:5030-5038`), identical comment | ✅ **both embed on create** |
| record | `write_live_record` → `record_projection.replay` — ~**3.8 ms/record** mean (18.9–20.1 → 3.82–3.91 ms), one pooled connection, `connect()` 0 (`record_writer.py:2749-2751`); **no embedding column is written by `record_writer.py` at all** | identical | ✅ |
| record schema | `idx_subject_scope` (`20261012000002:106`), `idx_subject_name` (`20261027000001:207-208`), RLS `subject_app_tenant` (`20261025000001:274-277`) | `idx_object_scope` (`20261012000002:65`), `idx_object_name` (`20261027000001:204-205`), RLS `object_app_tenant` (`20261025000001:268-271`) | ✅ identical index and policy shape |

**The independent reading and the measurement agree.** A ~20× gap between two stage-identical paths is not
explainable by anything the code branches on — and PR `#13229` says exactly that: it was *arrival order*, not path.

### 3.3 The verdict

> **INCIDENTAL.** The ~23 s is **not** a cost we chose. It is a **cold-start property of a dependency the write path
> calls** — `EmbeddingModel.get()` loading `bge-small` while the request thread blocks on it — amplified by a
> pre-warm that is a *daemon thread* and therefore does not gate readiness, and by a transport bound (10 s) shorter
> than the load (13.5–57 s) so the first request is *guaranteed* to 504 on a cold process.

**The structural/incidental split, stated precisely** — because `#13224`'s own framing offers *"synchronous
embedding"* as a structural candidate and it is half-right:

| | is it structural? | the deciding number |
|---|---|---|
| **the decision to embed synchronously on the write path** | **YES** — decided (§12.2a, `#5008`), and the destination **keeps** it (the model call stays on the write path after the vector move; only the *store* moves) | a **warm** encode costs **25–43 ms**. That is the price of the decision — an order of magnitude below the ticket's target, so **the decision is not what breaks the target.** |
| **the 23 s** | **NO** | the same create is **8–10 ms in-process, 27–32 ms in the handler** (PR `#13229`). Everything above that is the cold load. |

**⇒ The sharpest sentence in this document:** *the destination neither keeps nor removes this cost.* Storage placement
never had it. What removes it is **not blocking a write on model readiness** — which is orthogonal to whether the
vector lives in FalkorDB or Postgres.

**⭐ And this is the SECOND occurrence of one defect class, which is the durable lesson.** `#4055` (**CLOSED**) has the
title *"The 10s ask bound puts the one-time lazy embedder load inside the wait on selfhost — first ask after a process
start now 504s"*. The rule it produced is stated in the tree — *keep process INITIALIZATION out of a caller's wait
budget* (`embeddings.py:863-870`, beside `_LOAD_TIMEOUT_S`) — and it was applied to the **`ask`** door. **It was never
applied to the write doors**, so eighteen months later the identical stall surfaced on `create` and cost a P1. A fix
that lands at one caller of a seam and not at the seam is how the second occurrence is manufactured.

### 3.4 What `#13224` can act on without re-deriving

1. **Do not look for a subject-only stage in the SDK.** §3.2's table is the reading; the four hypotheses that
   assume a branch (entity linking, per-label dedup, a subject-only record write, a subject-only kind check) are
   **rejected by code**, and PR `#13229` rejects them by measurement too (entity linking 1.5 ms @6 nodes → 4.7 ms
   @2021, *"No curve"*; the kind guard 2.8–2.9 ms, *"identical for the Object door, which was fast"*).
2. **The fix in PR `#13229` is the right *class* of fix** — bound the wait, never let a create exceed its budget by
   two orders of magnitude. ⚠️ **But its trade is unowned — see §4 P2.**
3. **The kind guard is not the culprit, but its cost is scale-dependent and the PR's own sentence says so** —
   *"2.8–2.9 ms (1.3 ms with no pack records, **65 ms at 12k nodes**)"*. Quoting the small-graph figure against a
   production-size observation pairs two scales; the relevant reading is **65 ms at 12k**, three orders below the
   stall and worth a line in any breakdown.
4. **A residual PR `#13229` states itself:** *"Readiness still does not wait for the embedder; a deploy can therefore
   serve writes for the first seconds. This PR makes that window harmless rather than removing it."*

---

## 4. Proposals, ranked

Each: **what it changes** · **the decision it serves** · **what it forecloses** · **rough cost** · **class**.

### P1 — Relay PR `#13229`'s diagnosis onto `#13224` — **urgent**

- **Changes:** nothing in the product. Posts the measured breakdown (and the five hypothesis verdicts) as a comment
  on `#13224`.
- **Serves:** `#13224`'s own indicator 2 (*"a phase breakdown — where the ~20 s goes"*) and its explicit rule
  *"each must be ACCEPTED or REJECTED with a measurement"*. It is also the **DECISION RELAY** rule: an answer that
  lives only in a PR body is invisible to the lane that reads the issue.
- **Forecloses:** the failure where `#13229` closes unmerged and the measurement dies behind an issue that still reads
  *"Cause: UNKNOWN"* — leaving the next lane to re-run it.
- **Cost:** one comment. ~2 minutes.
- **CONTRADICTION TEST:** `§2.0` ✅ (no placement touched) · `§9.1` ✅ · `§12.1c` ✅ · `#4010` (no capture caps) ✅ ·
  off-by-default rules ✅ · MCP/SDK surface ✅ n/a. **No contradiction.** ⚠️ The only reason this is *urgent* rather
  than *housekeeping* is that the artifact it protects is the one thing `#13232` was asked for.

### P2 — Make a vectorless row **observable and exactly repairable** before relying on the cold-window trade — **urgent**

- **Changes:** `#13229` writes a row **without a vector** inside the cold window and relies on
  `graph-scripts/backfill_embeddings.py` to restore it. Three facts make that reliance currently unsound:
  1. **Nothing counts the gap.** A row with no vector is simply not returned by the dense leg; `#13156` records that a
     leg cancelled by its own budget *"is indistinguishable from a slow store"* in the trace.
  2. **The dense leg is already the weak half, but its measured share is disputed** — `#13108` reported **45 %** of eval
     windows losing the semantic half and recall@3 = 75.0 %, while a re-measurement at the eval's own 1,000-name scale
     read **0 % keyword-only in every arm**, reproducing only at 8,000 rows under load (**3.3 %**, reason `timeout`)
     (`docs/research/2026-11-17-13108-dense-leg/research-brief.md` §3.3). ⚠️ **The mechanism is what is reproducible; the
     45 % is one run over one population.** A silently-unindexed row composes with either number.
  3. **⛔ The repair tool has an open correctness bug.** `#12570`: the backfill write addresses nodes by a
     **non-unique** key — *"two `:Point` nodes with `id: 'dup'` … the last row's vector lands on both"* — and the
     `MATCH` ignores `kind_pred`, so a `Document`-surface row writes onto the provenance `:Source`. It is filed
     **category A — silent destruction of a correct value**, with an `xfail(strict=True)` pin.

  So: **a vectorless-row count (or a reconciliation number) plus `#12570`'s fix, before the trade is load-bearing.**
- **Serves:** §17.2's rule that *"truth == cache must be checkable, not assumed"* and that a gap must be *"OBSERVED,
  not merely recoverable"* — the trade is a new way to create exactly that kind of gap.
- **Forecloses:** a slow, silent recall regression that would be attributed to the model or to the embed gate rather
  than to the write path.
- **Cost:** a counter + a scheduled sweep. `#12570` is a bounded fix (address the row's own node, honour `kind_pred`).
- **CONTRADICTION TEST:** `§17.2` ✅ **serves** it (observability of a divergence is the section's whole subject) ·
  `§17.3` (a miss must never read as an absence) ✅ · `§2.0` ✅ · `#4010` ✅ · surface ✅ n/a. **No contradiction.**
  ⚠️ **Do not "fix" it by making the write wait again** — that reverses `#13229` and re-creates the 504.

### P3 — Start `#13220`, beginning with the **durability** half — **scheduled (largest item here)**

- **Changes:** gives the 2026-11-17 owner ruling an owner. Its own gaps, in the order they matter:
  1. **`object`/`subject` have no embedding column** and the record refuses one (`chk_object_props_no_derived`) —
     their fingerprints exist **only in FalkorDB**, so a record-based rebuild cannot restore them.
  2. `event`/`source` columns and HNSW indexes exist but hold **0 rows** — the infra is bought, the data is not.
  3. The search path does not read any of them from the record.
- **Serves:** §2.0 itself — *Postgres is the system of record* — against a measured hole (item 1 is a **durability
  gap**, not a performance one); plus `#13082`'s capability-first rule; plus V2 and V3's remaining halves, now
  answered.
- **Forecloses:** nothing recorded. ⚠️ It forecloses the *possibility* of a cheap retreat from item 1: adding the
  columns is a migration, and the record's `chk_*_no_derived` CHECK constraints are deliberate (they keep EP/derived
  scalars out of `props`). Widening them needs to distinguish "a vector" from "a derived scalar" — **that is the
  design question this proposal's cost lives in**, not the migration.
- **Cost:** a migration + the projection that writes the vector + the read leg. Item 2 is nearly free (write what the
  graph already holds). Item 1 is the real work.
- **CONTRADICTION TEST:** `#13220`'s owner ruling ✅ **it IS the decision** · `§2.0` ✅ · `§9.1` ✅ (vectors are not
  narrative prose) · `§12.1c` ✅ (the index lives in Postgres — exactly what #13220 does) · KEEP ruling
  2026-11-10 ✅ (writes continue; nothing is removed) · **⚠️ the one conflict #13220 itself flags:** retiring the
  *graph-side* copies requires an explicit **reopen** — so P3 must **stop at "write and read them in Postgres"** and
  leave the graph copies alone. **No contradiction, provided P3 does not over-reach into §6b R1.**

### P4 — State the sequencing in `STORAGE-ARCHITECTURE.md` §12.1b/§14.3: the index work is in **Postgres** — **scheduled, cheap**

- **Changes:** one paragraph making explicit what is currently only inferable: `#4997`'s *"make index creation
  label-generic"* framing was **FalkorDB**-shaped, `#11901`'s *"not earned"* was **FalkorDB**-shaped, and both are
  superseded by `#13220`'s Postgres ruling. A lane reading §12.1b today can still conclude *"add four FalkorDB HNSW
  indexes"* — which §12.1c's own cost argument forbids.
- **Serves:** §12.1c's decision; `#13082`'s placement rule; and the docs' own rule that a superseded citation must not
  read as live work.
- **Forecloses:** a lane spending RAM on four resident HNSW structures in a store we are actively trying to shrink
  (`#13082`: *"the product cannot exist"* at ~1 GB/user).
- **Cost:** one documentation edit, reviewed.
- **CONTRADICTION TEST:** `§12.1c` ✅ · `§2.0` ✅ · `#13082` ✅. **No contradiction.** Category **A** by our own A/B rule
  — the failure it prevents is unrecoverable rental spend, not friction.

### P5 — Close the per-kind embedding override slot (§12.2a's named absence) — **scheduled**

- **Changes:** adds the pack slot `embedding.overrides[{kind, embed}]` and threads the brief to the submission step,
  so a meeting with an authoritative calendar title earns a vector and a commit hash does not — declarable, not
  hard-coded.
- **Serves:** §12.2's text rule and §12.2a's *"the shape is BOTH"* (policy in the pack, predicate in the engine).
- **Forecloses:** nothing recorded — it is the declared remainder of a decided design.
- **Cost:** one pack slot + a caller-side carry. Small code, real design surface (a new pack clause needs the pack
  manifest's own review).
- **CONTRADICTION TEST:** `§12.2a` ✅ it is the decision's remainder · `§2.0` ✅ · `#4010` ✅ (narrows *embeddings*,
  never *capture* — the row still exists) · off-by-default ✅ · surface ⚠️ pack manifest ≠ MCP/SDK surface, but
  confirm that reading with the pack owner. **No contradiction.**

### P6 — Give §17.1's chokepoint an owner — **scheduled, and the least urgent of the six**

- **Changes:** starts the work §17.1 says does not exist. Measured: **387 mutating Cypher sites / 211 functions /
  32 files**; and §17.2 adds that the Postgres↔FalkorDB divergence surface *"has no mechanism designed yet"*.
- **Serves:** §17.1's write-path rule and §17.2's second surface.
- **Forecloses:** nothing — but it is **not** a prerequisite for `#13220` or for the subject latency, and treating it
  as one would stall both.
- **Cost:** the 387-site census is the estimate; the §17.1 ledger shows the *shape* (one declared lane per
  unjournalled writer), not the size.
- **CONTRADICTION TEST:** `§17.1`/`§17.2` ✅ the decision's own stated gap · `§2.0` ✅ · off-by-default ✅.
  **No contradiction.** ⚠️ File it as a **scope/ownership** question. A lane that starts rewriting 387 sites has
  misread a diagnosis as a mandate.

### P7 — The three write-path costs nobody owns, measured before they are optimised — **scheduled (diagnosis only)**

Three per-create costs surfaced in this reading that **no decision, no issue and no metric covers**. None is
subject-specific (§3.2), and none is the 23 s — they are offered because they are the *next* latencies, and because
two of them have no instrumentation at all:

| cost | where | what is known |
|---|---|---|
| **`_nudge_candidates`** — a 400-row `:Point` scan returning **full `content`**, then Python tokenisation of every row, on **every entity create** | `sdk.py:54865-54897`, called from the shared return at `:54604` | The product is an advisory field: *"Nudges are advisory only — surfaced in the write response, never enforced."* Gated only on the name having a ≥4-char token, so `create_subject("naturalPerson")` runs it. **Unbounded in bytes** (it returns `n.content`), and on a graph holding session-turn points that text is large. **No timer.** |
| **the approved-kind whole-graph scan**, per write that names a **namespaced** kind | `hosted_api.py:13790`, `sdk.py:54080`, `sdk.py:50478` | Documented cost: *"a WHOLE-GRAPH scan (`graph_installed_namespaces`: 8 property scans, ~6 ms at 300 nodes, O(nodes)), ~8–27 ms per call at 5k nodes"* (`sdk.py:50261-50271`); **65 ms at 12k nodes** (PR `#13229`). Bare kinds skip it — the skip is `sdk.py:50272-50275`, and only `eventKind` is attributable when bare (`pack_state.py:600`) — so a namespaced kind pays it **twice per request**. |
| **the per-request projection cold open** — every hosted request builds a fresh `TortoiseSDK`, whose `_get_proj()` runs `_schema_is_current()` and, when stale, `_ensure_indexes()` | `sdk.py:14804`, `projection/__init__.py:20410`/`20949` | `_schema_is_current()` is *"an unindexed label scan over `:Point` … paid on every fast-path probe"*; the sweep it guards *"pays ~26 round-trips of already-satisfied DDL on every construction"* (`:20413-20414`). ⚠️ **Quote the three-way re-measurement, not the old ladder:** the `1.4 / 8.1 / 26.9 / 66.2 ms` at 1k/30k/120k/300k figures are the **pre-#11682 one-disjunct shape**, and the source says so 20 lines later (`:20462-20476`); the shipped statement measures **1.25 / 2.74 / 7.91 ms at 1k / 5k / 20k** on module 4.20.6 — i.e. **~+26–38 % over the old shape**, about **+2.1 ms per construction at 20k Points**. |

- **Serves:** nothing recorded — and that is the point. It is the evidence a future decision would need.
- **Forecloses:** guessing. ⚠️ **Do not "optimise" `_nudge_candidates` by deleting a product surface** (`#888 W2`'s
  nudge is a deliberate feature) — the decision to keep or drop it belongs to its owner.
- **Cost:** three timings on one graph. Tiny.
- **CONTRADICTION TEST:** all five ✅ (it measures; it changes no placement, no cap, no surface). ⚠️ Two of the three
  are **category A candidates** on their own reading (`_nudge_candidates` return an unbounded text payload; the kind
  scan is 2× O(nodes)), and one of them (`_ensure_indexes` per request) is **directly served by the window** — a
  bounded cache makes the probe cheaper — so P7 feeds `#13082`/`#13103` rather than competing with them.

---

## 5. What the destination says about the ~23 s — and what it does not

The issue's own question, answered directly:

| cost | kept by the destination? | why |
|---|---|---|
| **synchronous embedding on the write path** | **KEPT** — and its *placement* moves, not its *presence*. The model call stays on the write path (`encode_for_row`, one seam, five folds). | pgvector's index is an ordinary transactional index (pgvector README: *"ACID compliance, point-in-time recovery, JOINs"*), so after the move the **cross-store coherence problem disappears** — a real gain, and precisely the surface §17.2 says has *"no mechanism designed yet"*. The **latency** of a warm encode (25–43 ms) is unchanged. |
| **the cold-load stall** | **NEITHER kept nor removed** — storage placement never had it. | It is removed by not blocking a write on model readiness (PR `#13229`), which is orthogonal to where the vector lives. **This is the sentence the issue needed.** |
| **record-first write order** | **KEPT** — it is the decision. | ~3.8 ms/record mean, 18.9–20.1 → 3.82–3.91 ms (`record_writer.py:2749-2751`); not a candidate for optimisation, it is why the cache is derived. |
| **entity resolution / identity** | **KEPT**, unchanged by the storage move. | Measured 1.5 ms @6 nodes → 4.7 ms @2021 — *"no curve"*. |
| **the three costs in P7** | **NOT ADDRESSED** by any recorded decision. | See P7. |

---

## 6. Do not do — the plausible wins that contradict the destination

Each names the decision it would reverse.

| # | the tempting win | the decision it contradicts | why it looks right and is not |
|---|---|---|---|
| **D1** | **"Add a vector index to `Subject`/`Object` in FalkorDB — it's one line."** | §12.1c (RESOLVED 2026-11-07: the index does **not** live in FalkorDB) + §2.0 + **`#13220`** (index them **in Postgres**) | It is one line and it would work. It would also rent four more resident HNSW structures (§12.1c) in a store `#13082` is trying to shrink from ~1 GB/user, for vectors `#13220` is moving. **The index work is real — its home is Postgres.** |
| **D2** | **"Make the write wait for the embedder again — a vectorless row is worse than a 23 s create."** | ⛔ **`#4055` (CLOSED)** — *"The 10s ask bound puts the one-time lazy embedder load inside the wait on selfhost — first ask after a process start now 504s"* — whose rule is stated in the tree: *keep process INITIALIZATION out of a caller's wait budget* (`embeddings.py:863-870`). Secondary: PR `#13229`'s **unmerged** measured trade (p95 20,414 → 268 ms). | It re-creates a guaranteed 504 on every cold process, and it reverses a ruling that already exists for exactly this shape. ⚠️ **It does NOT contradict §17.2** — re-blocking the write makes a vectorless row impossible, which serves §17.2 more strictly, not less; the earlier draft of this row said otherwise and was wrong. **The correct response to a vectorless row is to make it visible and repairable (P2), not to re-block the write.** |
| **D3** | **"Drop the `Subject`/`Object` embedding write — nothing indexes it anyway."** | Owner 2026-11-10 (`#11395` comment `6781921075`): **KEPT**, no pass may remove them *or stop writing them*; and now **`#13220`** (index them in Postgres) | The premise is true today (`Subject`/`Object` have no vector index, §12.1b) and the conclusion is forbidden **twice** — the removal ruling, and the newer ruling that says index them. |
| **D4** | **"Remove the approved-kind gate or the kind guard — they're whole-graph reads on a hot path."** | `#5475`'s gate (a write must not widen the graph's own pack approval set — an unvalidated `ns:kind` made a whole pack writable), §12.2a's "policy lives in the pack" | The reads are genuinely expensive (P7) and removing the gate is a **bypass**. **The fix is a cheaper invariant or a cache, never the gate's absence.** |
| **D5** | **"Put the narrative / raw turns back on the node — it's just a property."** | §9.1 (D1) + `CACHE_FORBIDDEN_COLUMNS` | The graph can never unload it (§2.1), no read path reads it back, and seven routes are denied **by name** because this was tried. |
| **D6** | **"Point the read path at Postgres now — one store is simpler."** | ⚠️ **HALF ORDERED, HALF REFUSED (2026-11-18).** The first half — the ordinary point read served from the record — **is what §2.0 orders** (`TORTOISE_RECORD_SERVE`, §14.2). The second half — *one store*, i.e. dropping the cache — contradicts §2.0 and §14.2: FalkorDB is demoted, never dropped, and stays the cache | The rename is the whole risk: **"the read path moves to the record" is the designed change, "there is one store" is not.** Traversal, writes and EP stay in the graph (§12.1c V1, §14.3). |
| **D7** | **"Move the text index to Postgres too — one store."** | `SEARCH-ARCHITECTURE.md` §3/§6.2: the text index **stays** (8–23 nDCG@10 behind BM25) | The vector move's momentum makes this look like the same change. It is a measured quality regression. |
| **D8** | **"Cap capture / sample writes to make the numbers look better."** | `#4010` — no capture caps | Listed because "write less" (§12.2b lever 2) slides easily from *write less noise* into *capture less*. |
| **D9** | **"Expose `residency` / add a tool so callers can see the cache is partial."** | ⛔ MCP/SDK surface changes need **Daniel's approval**; §17.2 already exposes `residency` as a response field, **off by default** | The capability exists. Adding a *tool* is a surface change and is gated. |
| **D10** | **"Treat `#4997`'s *make index creation label-generic* as the deliverable."** | **`#13220`** — the index goes in Postgres; `#11901`'s *"not earned"* was about **FalkorDB** | `#4997` is **CLOSED** and superseded. Re-implementing its FalkorDB framing would satisfy a ticket whose premise the owner has since replaced. |

### 6b. Reopen candidates — for the owner, not for adoption

Refusing an adoption is not a verdict on the finding. Both below are **accurate and valuable**, and both would
reverse a recorded decision — so they are routed, never adopted.

**R1 — Retire the graph-side `:Object`/`:Subject` vector copies.** `#13220` states it plainly: *"removing
`:Object`/`:Subject` graph vectors **requires reopening the 2026-11-10 KEEP ruling**"*, and it declares that *"out of
scope without an explicit reopen."* **No issue exists to hold the reopen.** The evidence for it is already in hand
and it is strong: ~19 MB of Object/Event vectors + the `:Point` HNSW are *"paid for in RAM"* (§12.1b), `#13082` says
a ~1 GB user *"cannot exist"* at $19, and once the vectors are written and indexed in Postgres the graph copies serve
the traversal only by being *stored*, not by being searched. **The reason it is a reopen and not a proposal:** the
2026-11-10 ruling was given against a *cheaper* argument (the vectors were unindexed and unread), and this argument is
*different* (they will be indexed and read — in the other store). **A different argument does not make it not a
reversal.** Let the owner rule.

**R2 — Defer the embedding off the write path entirely (a queue/outbox).** §17.1's order and §3's
`derived = replay(journal)` both assume the write completes with its payload. PR `#13229` shows a **bounded** version
is acceptable in practice (write without a vector, repair later), so the strict form is no longer unthinkable — and
**Postgres makes it cheaper than it is today**: an embedding written in the same transaction as the row needs no
cross-store reconciliation, whereas today deferral would create a window on a surface §17.2 says has *"no mechanism
designed yet"*. **Do not adopt, do not drop.** The external half (§8) is relevant: synchronous embedding is the
**common shape** among comparables, so the argument for deferral is ours to make on our own numbers, not industry
convergence.

---

## 7. Unknown — what this picture cannot say, and what would settle each

⛔ A confident synthesis of an incomplete picture is the failure this issue exists to prevent.

| # | unknown | what would settle it |
|---|---|---|
| **U1** | **The measured cause of the ~23 s is in a PR body, not on the issue, and the PR is unmerged.** If `#13229` closes without merging, the number is lost behind *"Cause: UNKNOWN"*. | P1. Nothing else — this is a relay, not a measurement. |
| **U2** | **Why the *first* request 504'd rather than merely being slow.** The load is 13.5–57 s and the transport bound is 10 s, so the 504 is *expected* — but whether the hosted lifespan pre-warm ran at all on that deploy (and why it had not finished) is not established in `#13229`. | A `EmbeddingModel.status()` reading at process start on the production image (`embeddings.py:1208`), or the deploy log's warm-up line. |
| **U3** | **Whether `_nudge_candidates`' 400-row full-content scan is milliseconds or seconds** on the production graph. It is unbounded in bytes and has no timer. | One timing of `_nudge_candidates(name, …)` for a real name on `tortoise-y4mjjq` (read-only). |
| **U4** | **How many whole-graph kind scans one hosted create actually pays** — I found three call sites (`hosted_api.py:13790`, `sdk.py:54084`, `sdk.py:50478`); the guard's abstain path decides whether all three fire, and I did not trace it to ground. It is also **payload-dependent** (bare kinds skip it). | One traced create with a counter on `graph_kind_namespaces`. |
| **U5** | ~~Whether the `#13224` benchmark sent namespaced or bare kinds~~ — **answered, and it was wrong to call it unknown**: the issue's own table names them, `naturalPerson` / `statement` / `goal`, and **all three are bare**, so the O(nodes) gate fired for **none** of the three requests and cannot explain the gap. | settled; the remaining question is U4 (whether all three call sites fire at all on a namespaced write) |
| **U6** | **The text index's deferred move now rests on a MEASURED result, not an unfiled plan** — M3 was measured 2026-11-13 (`#12298`: swapping the text leg earns no recall return) and the ranking-distance qualifier 2026-11-17 (`#13099`). What is still open is narrower: the 8–23 nDCG@10 gap is measured on **foreign** datasets, and `SEARCH-ARCHITECTURE.md` §6.2 says *"how far our hosted leg sits from that baseline is unmeasured"*. | a measurement of OUR hosted `db.idx.fulltext` leg against a BM25 baseline — not a new issue, since §6.2 already names it as the gap |
| **U7** | **Whether the graph's required RANGE indexes are all present on the production graph.** An absent `Subject.canonical_name`/`resolved_from` would turn every create's identity read into a full label scan — a trap the code documents (`projection/__init__.py:20344-20357`). | `CALL db.indexes()` on the hosted graph compared against `_REQUIRED_RANGE_INDEXES`. |
| **U8** | **What the per-kind embedding override (P5) costs in pack-schema review** — §12.2a names the slot absent but does not size it. | The pack manifest owner's read. |
| **U9** | **Whether the raw-client residual in §17.1 is reachable in the hosted deployment.** The doc says the doors are *"guard rails, not a proof"*. | A census of `FalkorDB(...)` constructions outside `guarded_client` across the hosted entry points. |
| **U10** | **The scale of the uncatalogued work.** 2,356 open issues against 11 open PRs (~214:1). Any claim here that something is *"unowned"* rests on keyword searches over a tracker that large. | Nothing in this lane's scope — but **read every "unowned" in §2 as "unowned as far as this reading reached"**, not as a census. |

---

## 8. Research record — rungs taken, rungs that failed

The external half was mandated to answer: how comparable systems place embeddings and vector indexes relative to the
write path (sync vs deferred), what latency budget they hold for a single-entity create, and how they keep a vector
index coherent with a relational record.

- **Rung 1 — UNAVAILABLE.** `mcp__seo-intelligence__perplexity_search` → `Unknown MCP server 'seo-intelligence'`.
- **Rung 2 — ATTEMPTED, UNAVAILABLE.** `web_search` with `model="sonar"` passed explicitly →
  `PERPLEXITY_API_KEY not set. Set it via PERPLEXITY_API_KEY env var, AGENT_MCP_ENV_PATH (path to .env file), or
  $AGENT_INFRA_PATH/../.env`. ⚠️ **The key exists** (`~/.env` holds `PERPLEXITY_API_KEY`), but the tool resolves it
  through **`AGENT_MCP_ENV_PATH`, which is unset in this process** — so the rung failed on a *harness configuration
  gap*, not a missing credential. Reported rather than worked around.
- **Rung 3 — USED (primary sources, `web_fetch`).**

  | source | what it settles |
  |---|---|
  | `pgvector/pgvector` README | **The coherence answer.** *"ACID compliance, point-in-time recovery, JOINs"* — the vector index is an ordinary Postgres index maintained **in the same transaction** as the row. **This is the structural difference from our two-store layout**: after the V1 move there is no vector-vs-record divergence window, which is exactly the surface §17.2 says has *"no mechanism designed yet"*. Also: an HNSW index *"can be created without any data in the table since there isn't a training step like IVFFlat"* — the move's index build needs no backfill ordering. |
  | `getzep/graphiti` — `graphiti_core/graphiti.py` (+ README) | **The write-path placement answer.** Graphiti's ingestion (`add_episode`) builds node and edge embeddings **inside the ingest call** (`create_entity_node_embeddings`, `create_entity_edge_embeddings`). **Synchronous embedding is the common shape among comparables**, not an outlier we invented. Its README frames the source stream as ground truth (*"Everything traces back to episodes"*) — the same record-first posture as §3/§17.1. |
  | `mem0ai/mem0` README | **The latency number, with a caveat.** Mem0 publishes **retrieval** p50 (0.88–1.09 s across LoCoMo/LongMemEval/BEAM) — **not** a write budget. ⚠️ **The widely-quoted "memory systems are ~1 s" figures are read-path figures and must not be used as a write budget.** Useful as a negative result: no comparable publishes a per-entity-create budget, so `#13224`'s `p95 < 1 s` target is **ours** and is contradicted by nothing found. |

⚠️ **What the external half does NOT establish:** none of the three is a two-store record/cache split, so **none can
tell us whether our latency profile is acceptable** — only that synchronous embedding is normal and that the
coherence half of our problem is a consequence of *our* split, not of the design space.

---

## 9. What this document does not claim

- It does **not** measure the subject path. The numbers in §3.1 are **PR `#13229`'s**, quoted; §3.2 is this lane's
  independent code reading, and they agree.
- It does **not** propose a storage change. §2 reports decisions already made, including two made in the last 48
  hours.
- It does **not** re-open V1, the hybrid, or the text index's home. §6/§6b name them as closed or owner-reopen-only.
- It does **not** survive the next merge unexamined: §3.2 is a reading at `d1bfef778f`, and PR `#13229` is unmerged,
  so §3.1's numbers describe a fix that is **not in `main`**.
- Its **"unowned"** claims are bounded by a keyword search over 2,356 open issues (U10), not a census.

**Sources.** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0, §2.1, §3, §9.1, §12.1b, §12.1c, §12.1d, §12.2,
§12.2a, §12.2b, §13, §14.2, §14.3, §17.1, §17.2, §17.3 · `docs/architecture/SEARCH-ARCHITECTURE.md` §3, §6.2,
§9, §10 · `docs/ONTOLOGY.md` §2, §3, §4 · code: `tortoise/hosted_api.py` (routes 13820–13935, kind gate 13758–13901,
SDK open 10138/10303, offload 12767), `tortoise/sdk.py` (create_entity 53975–54613, subject/object branches
54205–54251, nudge 54860–54892, `_create_entity` 50428, `_refuse_unapproved_kinds_in_props` 50368, pack-gate cost
50256–50266), `tortoise/projection/entities.py` (encode_for_row 2928/4845/5032/5393/5761, `_upsert_subject`
4821, `_upsert_object` 5004),
`tortoise/projection/__init__.py` (index DDL 20949/20996, schema probe 20410–20476, required range indexes
20340–20375, three-way re-measurement 20462–20476), `tortoise/entity_identity.py` (547, 957–984, 1115), `tortoise/entity_resolver.py` (56–64, 175, 658),
`tortoise/kind_layers.py`, `tortoise/kind_guards.py`, `tortoise/pack_state.py` (600, 711, 1170, 1198),
`tortoise/record_writer.py` (2653, 2749–2751), `tortoise/record_serve.py` (589), `tortoise/embeddings.py` (869, 965,
1097, 1208), `tortoise/embed_gate.py` (148), `tortoise/monitoring.py` (2076, 2143–2167) · migrations
`20261012000002_record_semantic.sql`, `20261025000001_record_tenancy_role_and_guc.sql`,
`20261027000001_record_semantic_id_key.sql`, `20261109000001_record_embedding_vector.sql` · research `docs/research/2026-11-17-13108-dense-leg/research-brief.md` §3.3 · issues `#7879`, `#4997`,
`#4999`, `#5087`, `#7949`, `#9721`, `#11821`, `#11901`, `#12901`, `#13082`, `#13083`, `#13084`, `#13093`, `#13094`,
`#13103`, `#13108`, `#13133`, `#13156`, `#13220`, `#13224`, `#13232`, `#11395`, `#12570`, `#4010`, `#5475`, `#7951`,
`#4055`, `#12298`, `#13099`,
`#4899`, `#5008`, `#11002`, `#11086`, `#9189`, `#9206`, `#9079`, `#9391`, `#9555`, `#10000`, `#10017`, `#10070`,
`#12047`, `#12060`, `#9964`, `#11642`, `#7970`, `#4051`, `#12570` · PRs `#13229`, `#13210`, `#13121`, `#13102`,
`#13247`.
