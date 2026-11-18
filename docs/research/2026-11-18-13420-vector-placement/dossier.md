---
title: "Where the `:Object`/`:Subject` vectors live (R1) and whether the embedding leaves the write path (R2) — a decision dossier, not a decision"
type: engineering
domain: platform
doc_status: draft
created: 2026-11-18
updated: 2026-11-18
subjects.team: epistemic-team
issue: "#13420"
aboutSubjects: `:Object`/`:Subject` entity fingerprints, derived-entity embedding write path, graph-side vector copies, record (Postgres) vector ownership
aboutObjects: STORAGE-ARCHITECTURE.md §2.0/§12.1c/§14.3 V3/§17.1/§17.2, #11395 KEEP ruling and its 2026-11-17 reopen, #13220, #13082, #5004, #13417, #13422
---

# #13420 — the vector-placement decision dossier

**This is a dossier. It implements nothing, retires nothing, and changes no writer flag.** The owner makes
the decision; this document exists so that decision is made on measured ground rather than a summary of one.

Prepared 2026-11-18 at `6264dc670c` on branch `research/13420-vector-placement` (the citations were
re-derived after a rebase onto main; the code files cited are byte-identical between the original base
`340d500491` and this one — `git diff --stat 340d500491..6264dc670c -- tortoise/ supabase/migrations/` is empty).

## Method, rungs, and what this dossier could not do

- **Research first, as the dispatch mandated.** Every internal claim below is a `file:line`, an issue id, or a
  comment id. Where the picture is incomplete the word is **unknown**, followed by what would settle it.
- **Search tool:** `web_search` with `model="sonar"` was **attempted and could not run** —
  `PERPLEXITY_API_KEY not set` (the tool reads it from the process environment; `zsh` is also absent on this
  box, so the research skill's `zsh -ic` credential recipe cannot be used). Rung 1
  (`mcp__seo-intelligence__perplexity_search`) is **UNAVAILABLE: `Unknown MCP server 'seo-intelligence'`**
  (re-confirmed by `mcp_load` this session). **Rung 3 (`web_fetch`) on primary sources was used instead** —
  vendor source code and vendor documentation, cited inline. A rung-2 sweep would widen the comparable set;
  it would not change the structural finding in §4.
- **Graph-first (research skill Step 0.0b): unread — no query ran.** Both `/v1/search` queries returned
  `HTTP 504` twice ("server's wait budget exceeded"), and `~/.pi/agent/state/TORTOISE-GRAPH-BRIEF.md` is
  **absent on this box** (the dispatch's mandate-preflight says so, and it is correct). So this dossier does
  **not** claim to have consulted the graph, and nothing here is a re-derivation of a held point.
- ⛔ No writes to `live-v5` or any live graph. No deletions. No vector removal. No MCP/SDK surface change.

## §0 — one correction to the brief, before anything else

The brief frames **R1** as *"retire the graph-side copies"*, reversing the **2026-11-10 KEEP ruling** unless
that ruling is reopened explicitly. **That framing is one ruling out of date, and the difference decides the
recommendation.**

- **2026-11-10** — KEEP (`#11395` comment `6781921075`): *"`:Object` and `:Subject` keep their stored
  embedding; it gets a real lookup index in Postgres, and the noun-matching step uses it to propose
  candidates. **No pass may remove those embeddings, and no pass may stop writing them.**"*
- **2026-11-17** — **REOPENED AND APPROVED** (`#11395` comment `6956793300`, owner approval, `OVERRIDES:`
  recorded on that issue): *"the `:Object`/`:Subject` embeddings may now be **removed from the graph and stop
  being written there**, once the record holds and indexes them"*, in two ordered steps — *"record first,
  graph removal second, in that order and not in one release"*.

⇒ **R1 is not an unopened reversal.** It is an **already-authorised, ordered step whose precondition is
unmet**, and the honest question is not *"may we?"* but *"is the condition satisfied, and is `:Event`/`:Source`
covered or not?"* — which is a different question with a different answer, and both are answered in §1/§2.

⛔ Two things this correction does **not** do: it does not make removal permitted today (the condition is
unmet — §1.2), and it does not settle `:Event`/`:Source`, where the merged design document and `#13220`'s own
body say opposite things (§1.5, an owner-lane question already recorded on `#13220`).

## §1 — What we already decided, and what we have already built

### 1.1 The decisions in force (quoted, with ids)

| # | decision | id / date | what it says about this issue |
|---|---|---|---|
| **D-a** | **KEEP** — the entity fingerprint is part of the product | `#11395` comment `6781921075`, owner, 2026-11-10 | removal and the stopping of the write are both refused; the fingerprint is kept *"not because [it] is currently earning its keep — that is unproven in either direction"* |
| **D-b** | **REOPEN GRANTED for the graph copy** | `#11395` comment `6956793300`, owner, 2026-11-17 | graph copy may be removed / stop being written **once the record holds and indexes that label**; record first, graph removal second, **never in one release** |
| **D-c** | **index them, in Postgres** | `#13220` (CLOSED COMPLETED 2026-11-17), owner | *"if indexing improves search for Object/Subject/Event of course we want that, and we just need it in Postgres and the search aligned with that and everything armed and working"*; the index half of §14.3 V3 and V2 |
| **D-d** | **the vector index does not live in FalkorDB** | `STORAGE-ARCHITECTURE.md` §12.1c (`docs/architecture/STORAGE-ARCHITECTURE.md:1169`), owner, 2026-11-07 | four further resident HNSWs on the most expensive resource we have are ruled out; a pgvector index on a table the record already owns is the cheap shape |
| **D-e** | **capability-first placement** | `#13082`, owner, 2026-11-16 | *anything no graph query reads is Postgres-only by default*; RAM ≈ **584×** the price of disk; **~78 MB of RAM per solo tenant** is the ceiling |
| **D-f** | **presence is ownership** | `#5004` (`CLOSED`, consolidated under `#5048`) + `tortoise/embeddings.py:1806-1815` | a record-TRUTH vector (a `Point`'s) must never be dropped on a slow embedder — a cold `None` there is a **recorded CLEAR that a rebuild reproduces** |
| **D-g** | **the bounded write** | PR `#13229` (issue `#13224`), 2026-11-17 | derived-entity folds wait `WRITE_EMBED_BUDGET_S = 0.25 s` then write the row **without** a vector; p95 **20,414 ms → 268 ms** |

### 1.2 What is actually built at this head (measured by reading the code at `340d500491`)

| fact | where |
|---|---|
| `public.object` / `public.subject` carry `embedding` (jsonb) + generated `embedding_vec vector(384)` + HNSW | `supabase/migrations/20261117000001_record_object_subject_embedding.sql` |
| the **live write seam** derives and fills them | `tortoise/record_writer.py:1755` (`derived_embedding_columns`), called at `:2092`; `tortoise/record_projection.py:3165` (`DERIVED_COLUMNS = {object: {embedding}, subject: {embedding}}`) |
| the **graph fold** still writes `o.embedding`/`s.embedding` on every write, `bounded=True` | `tortoise/projection/entities.py:4836` (`_upsert_subject`, encode at `:4866`) and `:5025` (`_upsert_object`, encode at `:5054`) |
| the **record does NOT serve** `:Object`/`:Subject` on the dense leg | `tortoise/record_vector.py:164-174` — `LABEL_SERVED` is `point`, `operator`, `source`, `document`, `event` only; the exclusion and its reason (`#689` terminal-status cage) are stated at `:98-127` |
| the **graph leg still reads** the graph copy — by **brute-force scan**, not by an index | `tortoise/search_engine.py:4439-4446` — the scan branch: `MATCH (n:{label}) WHERE n.embedding IS NOT NULL … vec.euclideanDistance(stored_embedding_vector_expr('n', …), _qv)`. The index leg at `:4147` (`vec.cosineDistance(node.embedding, …)`) is **not** where these labels are served: `:4087-4092` reports `scan_fallback` and skips the `queryNodes` attempt for a label with no vector index, by design (`#4999`, `#13108`). `:148` — `_STATUS_FILTERED_LABELS = ("Point", "Object", "Subject")`, the terminal-status filter that is the reason the record cannot serve them yet |
| the **reader half of `#13220` closed unlanded** | `#13422` (measured this head: `DERIVED_COLUMNS` writes what `LABEL_SERVED` will not read) |
| the **`:Source` write rule is missing entirely** | `#13418` — `record_writer._RECORD_EMBEDDING_TEXT` (`tortoise/record_writer.py:1730`) has no `SourceCreated` key |
| the **document vector already diverges between stores** | `#13412` — graph encodes `_searchText` (title+summary+topics), the record's fold still encodes `title + content` |

### 1.3 What keeping costs — and what it costs *twice*

- **Rent.** `#13082`: RAM is ≈**584×** disk ($73/GB-month vs $0.125/GB), with a **~78 MB** ceiling for a solo
  tenant. `#13220` measured, 2026-11-17 on `instance-yxwgk64it`: **7,865 `:Object`** and **4,893 `:Event`**
  embeddings in the graph, **no vector index** on either, full scan 13–17 ms, reported `ok`.
- **A second cost nobody has priced: the derived vector is now encoded twice per write.** Disclosed in two
  places rather than buried — the migration header and `record_writer.derived_embedding_columns`' docstring
  (`tortoise/record_writer.py:1815-1825`): *"on an ARMED record every live `ObjectRegistered`/`SubjectAdded`/
  `EventRecorded`/`DocumentCreated` write pays TWO model calls for one text"*, because `encode_for_row`
  memoises nothing. **No figure is quoted in the source, and none is measured here** — the source says so
  explicitly. ⚠️ This is the one cost that **R1 would remove outright** and that R2's "narrower" option
  (§3, option C2) would remove without any placement change.
- ⚠️ **No per-label census exists at this head.** The `12,657 vectors ≈ 19.4 MB` figure is a **2026-09-24**
  count whose own instrument docstring forbids re-quoting it (`STORAGE-ARCHITECTURE.md:1650`); a 2026-10-08
  reading on graph point `1a11cb1dd2c-873e2b26263d` found **3,742 embeddings / 5.48 MiB total**, with no
  per-label split. **The two are not a series.** The instrument that settles it is
  `tools/dod_5090_probe.py --census` on a **named** graph.

### 1.4 What a rebuild would lose if the graph copies went — measured, not guessed

Two rebuild routes, two different answers, and they are **not** interchangeable:

1. **Journal replay** (`rebuild_all`) — the derived pair's vector *is recomputed in the fold* from the payload
   `name` (`entities.py:4866`, `:5054`; `#7845`). A JSONL replay therefore re-creates it: **nothing is lost.**
   The journal never carried it, and does not need to.
2. **Record → cache restore** (`python -m tortoise record-restore`) — **this path encodes nothing**, and it
   **drops** the property: `cache_projection`'s refused-props arm still lists `embedding` for both labels, so
   `node_from_row(Object, {…, embedding})` returns **no** `embedding` prop. Stated in the migration header
   (⛔ *"THE SPLIT, STATED PLAINLY: the column closes gap 1's INDEXABILITY half … and NOT its RESTORE half"*)
   and owed as a follow-up there.

⇒ **That is the sharpest fact in this dossier for R1: if the graph copy is removed before the restore half
gains an encoder, `record-restore` lands `:Object`/`:Subject` nodes without vectors, and nothing on that path
will ever put them back.** That is not a new risk — it is the exact failure shape the 2026-11-10 ruling
existed to prevent, one release later.

### 1.5 The `:Event`/`:Source` question is an owner-lane disagreement that main has only *half* closed

`#13220`'s own body says their graph copies *"may be retired under `#13082`'s rule once the record serves
them"*, and its step 3 orders it. The merged `STORAGE-ARCHITECTURE.md` said the opposite (*"nothing here
retires them either: no ruling does"*). `#13220`'s last comment recorded both sides and asked the owner for
wording.

**Update at `6264dc670c` (PR `#13395`, merged 2026-11-18):** main has taken the **release boundary** back
(*"in a later release and never in the same one"* is stated again at several sites) and the §14.3 lead now
separates the two rules explicitly — *"the record-first condition is general; the written authorisation to
retire a graph copy is scoped, and that comment gives it for `:Object`/`:Subject` only"*. ⚠️ **The residual is
narrower but not gone:** the V3 row still reads *"nothing here retires them either: no ruling does"*
(`:1650`), which is true about **owner rulings** and false as an answer to *"what orders this work?"* —
`#13220`'s step 3 does. **R1's narrower variant (§2.4, option C) cannot be scoped before that sentence is
settled**, and this dossier does not re-litigate it — it names it.

## §2 — R1 · retire the graph-side `:Object`/`:Subject` vector copies

### 2.1 The decision it would reverse — and the one that already reversed it

**Reversed (as stated 2026-11-10, `#11395` comment `6781921075`, owner Daniel):**

> **The fingerprint on a remembered noun is part of the product.** `:Object` and `:Subject` keep their stored
> embedding; it gets a real lookup index in Postgres, and the noun-matching step uses it to propose candidates.
> **No pass may remove those embeddings, and no pass may stop writing them.**

**Already reversed for the graph copy (2026-11-17, `#11395` comment `6956793300`, owner approval, `OVERRIDES:`
recorded there):**

> the `:Object`/`:Subject` embeddings may now be **removed from the graph and stop being written there**, once
> the record holds and indexes them … **What it does NOT authorise:** removing the property *before* the record
> holds it (that would delete the only copy — the exact failure the original ruling existed to prevent) …
> Sequencing: record first, graph removal second, in that order and not in one release.

⇒ A lane acting on R1 today reverses **D-b's sequencing**, not D-a. Both are owner rulings; the later one wins.

### 2.2 What has changed since 2026-11-10 — evidence only

| change | evidence | does it satisfy D-b's condition? |
|---|---|---|
| Columns, generated vector, HNSW index exist for both labels | migration `20261117000001` | **half** — "indexes" yes; "holds" only for **rows written after deploy**, which is not a statement about the pre-existing population (the migration's own header: *"THE COLUMN ARRIVES EMPTY"*), and `STORAGE-ARCHITECTURE.md:1640` says so (*"\"the record holds them\" is a statement about ROWS, not about columns"*) |
| The live writer fills them | `record_writer.derived_embedding_columns` (`:1755`, called `:2092`) | yes, on the live path only |
| **The record does not *serve* them** | `record_vector.LABEL_SERVED` (`:164-174`) excludes both; `#13422` | ⛔ **no** — `#13220`'s ordered step 2 (*"the record demonstrably serves it"*) is unmet |
| The graph legs still read the graph copies — a brute-force scan (`:4439-4446`), not an index | `search_engine.py:4439-4446` + `:148` | ⛔ **no** — the copy is the *only* serving copy for these labels today |
| The **resolution** half is now measured — **the falsifier's recall half only** | `#11901`: the capture prior's `limit=3` returns the exact stored entity **184/300 = 61.3 %** hybrid (55.3 % keyword-only); widening 3→15 reaches **73.0 %**; surface-variant pairs attach **0.0 %** in phase-1 exact-`_norm` at every k. The dense leg helps by class (definite descriptions **down** 76.1 %→62.8 %; identifiers **up** 21.1 %→46.7 %; paths 33.3 %→96.7 %) | **Not an answered falsifier.** The KEEP ruling's falsifier asks for *"the similarity leg on and off … and whether the duplicate-entity rate moves"*; `#11901` measured candidate recall@k in **phase 1 only** (`STORAGE-ARCHITECTURE.md:1650`: *"the phase-2 LLM resolver … is unmeasured here … never the duplicate rate itself"*). ⇒ **the recall half is measured and positive; the duplicate-rate half and the end-to-end capture path are unrun** (§2.6) |
| `:Event`/`:Source` retiredness is now contradicted between two merged texts | §1.5 | not a condition — a **wording** decision for the owner |

⇒ **The precondition is unmet, and it is unmet in the one way that matters**: removing the graph copy today
removes the copy the dense leg actually reads for these two labels (`LABEL_SERVED` sends the caller to the
graph branch), which would silently delete the mechanism D-a was written to protect — before the record has
ever answered a query for them.

### 2.3 SOTA — where comparable systems put vectors, and what they do when row and vector disagree

Verified from primary sources (rung 3, `web_fetch`). Convergence is **evidence, not authority**; it points
*with* D-a/D-b, not against them, so the contradiction test is not engaged by any of it (§5).

| system | where the vector lives | second copy? | disagreement handling |
|---|---|---|---|
| **Graphiti / Zep** (the comparable our own §12.2c table records) | **on the graph node, in the graph store, indexed there.** Neo4j: `CALL db.create.setNodeVectorProperty(n, "name_embedding", …)`; FalkorDB: `SET n.name_embedding = vecf32($entity_data.name_embedding)` — the vector is named in the node's own save statement | **none** | **no second store to disagree with**: the vector is written by the node's own save statement, so there is nothing to reconcile. (⚠️ not a universal: the Neptune arm joins an empty list to `""` when the embedding is absent, and the Neo4j arm omits the vector write when `has_aoss` is set. The finding is *one copy, co-written*, not *mandatory*.) Source: `getzep/graphiti` `graphiti_core/models/nodes/node_db_queries.py` |
| **mem0** | entities *"stored once and embedded"*; graph is platform-native, no external graph DB; the **earlier** shape connected an external graph store and has been retired | **none stated**; the docs assert single storage of the entity | not described — **unknown** from primary docs |
| **Microsoft GraphRAG** | embeddings produced in the **offline indexing pipeline** (`graphrag index`), a batch build, not a per-request write | none — one derived index set | re-index; no online reconciliation |
| **Neo4j native vector index** (general pattern) | vector is a **node/relationship property**, indexed in-graph | none | not applicable |

**What the field converges on, stated carefully:** the comparable systems **do not run two authoritative
copies of one row's vector and reconcile them**. Either the vector is co-located with the row it describes
(Graphiti, Neo4j, and our own Postgres tables), or it is produced once in a batch index (GraphRAG). Our
current shape — a vector in the graph **and** in the record, for the same row, with no reconciliation between
them — is the outlier, and `#13412` is that outlier producing a live divergence (two different texts embedded
for one document).

⚠️ **What this does NOT license:** Graphiti keeps its vector in the graph because *its graph is the record*.
Ours is not (§2.0) and cannot afford to be (§13082, 584×). The transferable finding is **one authoritative
copy per row**, not **the graph is the right home**.

**Unknown (what would settle it):** whether any comparable **repairs** a stale vector in place, and by what
mechanism. Nothing found in primary sources. What would settle it: a code read of Cognee's graph adapter
(whether its graph nodes carry embeddings at all) and of Graphiti's summary-update path (`update_community` /
episode-summary regeneration — whether a changed summary re-embeds by overwrite or by append). `#13417` is
our own answer in progress (count + unique-key backfill).

### 2.4 Options, and what each forecloses

| # | option | forecloses |
|---|---|---|
| **A** | **Adopt now** — stop writing `:Object`/`:Subject.embedding`, strip the property, retire `:Event`/`:Source` too | ⛔ **the dense leg for two labels** (`LABEL_SERVED` has no record route; `#13422`) — a measured recall regression framed as a saving; ⛔ **record-restore fidelity** for those labels (no encoder on that path, §1.4); ⛔ **contradicts D-b's own sequencing** (*"not in one release"*), i.e. it is a second reversal, not the granted one |
| **B** | **Do not adopt** — keep both copies and the double encode | the RAM rent (7,865 + 4,893 vectors, unindexed); the **double encode** on every derived-entity write; the **two-store divergence** class (`#13412`, `#13418`) stays open |
| **C** | **Adopt a narrower version — retire `:Event`/`:Source` graph copies only**, leaving the ruled pair alone | requires the `#13220`-vs-merged-doc wording decision (§1.5) before it can even be scoped; and `:Source`'s record vector is **not derivable at all today** (`#13418`), so this option is *smaller than it looks* and would ship a hole for plain sources |
| **D** | **Hold R1 as an authorised, ordered step with a named trigger**, and spend now on the four things that make the trigger testable (§2.5/§2.6) | the rent and the double encode persist until the trigger fires; and "authorised but not scheduled" is exactly the state that decays into drift |

### 2.5 Recommendation — **D**, with C held behind the wording decision

**Recommend `D`: R1 is not adopted now, and not refused.** The 2026-11-17 reopen already authorises it; what is
missing is the **precondition it names**, and the precondition is missing for a reason that is itself a defect
(`#13422`: the reader half of `#13220` closed unlanded). Retiring the copy before the reader exists would delete
the only serving copy and then report the loss as a storage win.

**The trigger, stated so it can be checked rather than remembered** — R1's step 2 becomes actionable when **all
four** hold:

1. `record_vector.LABEL_SERVED` carries `object` and `subject` **and** the record leg applies the `#689`
   terminal-status cage (`#13422`'s own gate).
2. **The pre-existing population has crossed.** *"The record holds them"* is a statement about **ROWS, not
   columns** (`STORAGE-ARCHITECTURE.md:1640`): the migration's column *"ARRIVES EMPTY"* and only the live write
   seam fills it, so the `:Object`/`:Subject` vectors written before the deploy are **not** in the record. **A
   one-time copy of those rows is part of step 1**, not step 2 — otherwise step 2 deletes history that never
   crossed, which is the exact deletion the sequencing exists to prevent, one release later and harder to see.
3. `record-restore` encodes or restores the vector for those labels, so a record→cache rebuild is not
   silently vectorless (§1.4).
4. A per-label census exists on a **named** graph (`tools/dod_5090_probe.py --census`), so the removal's
   saving and its blast radius are both counted instead of quoted from retired arithmetic.

**The honest case against D:**

- R1 is **already approved**; leaving it unscheduled risks reading as inertia, and the `#13220` history shows
  exactly how a granted ruling sat unimplemented for weeks.
- The rent is real *today*, at 584× the price of disk, against a ~78 MB solo ceiling.
- The **double encode** is a per-write cost we are paying right now and neither option A nor D removes; only the
  narrower C2 (§3.4) does, and it needs no placement decision at all.
- ⚠️ And the strongest counter-argument to the whole "keep until the record serves" logic: if `:Object`/`:Subject`
  are cheap to **recompute from `name` in the fold** (§1.4), the graph property is *not* a durable artifact at
  all — it is a cache of a pure function. Under D-e (*anything no graph query reads is Postgres-only*) that
  argues for retiring it **now** and letting the fold recompute on replay. **What defeats that argument is
  measured, not sentimental**: the dense leg *reads* it today (`search_engine.py:4439-4446`), so a graph query does
  read it, and `#11901` measured that mechanism's **recall** half at 61.3 % exact-name recall (73.0 % at k=15). The
  principle in D-e is satisfied by the *current* shape; it would be violated by removal, not by keeping.

### 2.6 The smallest experiment that settles the open part

**Does the graph-side fingerprint earn its keep on real captures?** This is D-a's own falsifier, and it has
never been run end-to-end:

- **Population:** the 1,000-name population and the harness already committed by `#11901`
  (`tools/candidate_recall_11901.py`, two committed artifacts — hybrid and keyword-only control).
- **Treatment:** on a **throwaway** graph (never `live-v5`), run the capture-time noun-matching step twice:
  (a) as today, (b) with `:Object`/`:Subject` graph copies absent (simulating R1's end state), measuring
  candidate recall@k for the exact-name population and the 419 generated surface-variant pairs.
- **Settles:** whether the dense leg adds candidates the name/alias rungs do not, and whether recall drops
  toward the keyword-only 55.3 % baseline — i.e. whether the fingerprint is load-bearing for the mechanism D-a
  protected. **Revert the scaffolding; no PR carries it.**
- **Cost:** one instrument run + one throwaway graph. **Not** a production measurement, and it does not touch
  the live graph.

⚠️ It does **not** settle the `:Event`/`:Source` question (that is §1.5's wording decision) and it does not
settle the **restore** gap (§1.4), which needs a code fix, not an experiment.

## §3 — R2 · defer the embedding off the write path entirely

### 3.1 The decision it would reverse

R2 changes an **invariant**, not a ruling sentence. Two recorded statements, both load-bearing:

- **`STORAGE-ARCHITECTURE.md` §3 / §17.1** (`:320`, `:1729`): the architecture is `derived = replay(journal)`,
  and §17.1's rule is that **the write** is the one place state is minted — *"two writers"* is the defect class
  the chokepoint exists to prevent. A write that completes **without** its payload makes a derived row a
  function of process state, not of the journal.
- **`#13229` (merged, 2026-11-17)** is the **current ruling's narrow form** — a **bounded** wait, **0.25 s**,
  then write without a vector, **on the four derived-entity folds only** (`tortoise/embeddings.py:1712`, the arm
  at `:1852`). ⚠️ The sentence *"the decision to embed synchronously IS structural"* is **`#13232`'s synthesis
  comment** (2026-11-17), not `#13229`'s; the decision it names has its recorded home at
  `docs/architecture/STORAGE-PICTURE-2026-11-17.md:158` → **§12.2a / `#5008`**, and `#13229` is the PR that
  implemented the bounded form. Attribute it there, not to the PR.

### 3.2 What has changed since — and what has *not*

**Changed (measured):**

- `#13229`/`#13224`: the 18.4 s 504 was **incidental** — the cold model load (13.5 s warm-cache, ~57 s cold,
  `#1349`) paid on the request thread. **A warm encode is 25–43 ms**, an order of magnitude inside the
  `p95 < 1 s` target. ⇒ **The latency argument for R2 is much weaker than it looked**: the write path is not
  slow by design, it was slow once, at cold start, because a daemon pre-warm did not gate readiness.
- `#13417` (filed) makes the bounded path's cost **observable and repairable**: a count of rows written with no
  vector, and `#12570`'s backfill keyed on a unique row identity (today it writes by a **non-unique** key, so
  two nodes sharing an id both receive one vector).
- Postgres makes full deferral cheaper **in principle** — the vector and the row can land in one transaction
  with no cross-store window. **Not measured on us**: no instrument has compared "embed inside the write
  transaction" against "enqueue and embed after".

**Not changed (and this is the load-bearing half):**

- `encode_for_row`'s **`bounded` defaults to `False`**, and the bound is **suppressed inside a replay window**
  (`tortoise/embeddings.py:1728-1800`, `_in_replay_scope`). The docstring states why in one sentence that
  settles most of R2: *"bounded defaults to **False** (record-TRUTH callers — the Point journal stamp under
  `PRESENCE IS OWNERSHIP`, `#5004` — must never take a `None` for a slow embedder, because a cold `None` there
  is recorded as an explicit CLEAR that a rebuild reproduces and no graph-side backfill can reach it)."*
- ⇒ **For the four derived-entity folds, R2's admissible content is already implemented.** For `Point`, R2
  contradicts D-f/#5004 unless it is redesigned (the write would have to record "vector pending, not cleared",
  a new record semantic).

### 3.3 SOTA — two families, and we are in neither

| family | who | shape |
|---|---|---|
| **embed at ingest, synchronously** | Graphiti/Zep — the node's own save statement names the vector (`SET n.name_embedding = vecf32($entity_data.name_embedding)` on FalkorDB; `db.create.setNodeVectorProperty(n, "name_embedding", …)` on Neo4j, `graphiti_core/models/nodes/node_db_queries.py`), so the embedding and the node's properties are written together and there is no second store to reconcile | the write is not complete until the vector exists; **no repair path needed** |
| **embed in an offline batch index** | Microsoft GraphRAG (*"indexing can be an expensive operation"*; the pipeline builds the index) | the "write" *is* the index build; retrieval reads a finished artifact |

⇒ **Neither family does our bounded shape** ("write the row now, maybe without a vector, repair later"). That
shape is **our deviation**, justified by a measured 13.5–57 s cold load that neither family pays (they embed
from an API or a batch job, not from a local model on a request thread).

⚠️ **Unknown:** whether any comparable runs an outbox/CQRS deferral for embeddings, and what its repair
guarantee is. Not found in primary sources at rung 3; a rung-2 sweep could settle it. What would settle it on
our side is not a comparable — it is the measurement in §3.6.

### 3.4 Options, and what each forecloses

| # | option | forecloses |
|---|---|---|
| **A** | **Adopt full deferral** — every write returns before its vector, a worker embeds and backfills | for `Point`, **D-f/#5004**: a cold `None` is a recorded CLEAR that a rebuild reproduces — so A needs a new record semantic ("pending") or it is a presence violation; ⛔ and it re-opens the divergence class §17.2 exists to observe (**OBSERVED**, not assumed: today nothing counts vectorless rows — `#13417`). Also: with the derived pair it **changes nothing** (§3.2 — already bounded) |
| **B** | **Do not adopt** — keep the bounded write + `#13417` | the write still pays a **warm** encode (25–43 ms) and — worse — **two** of them (`record_writer` + graph fold, §1.3). Also leaves the cold window's cost merely *observable*, not removed |
| **C1** | **Narrower: keep the bound, extend it to any remaining synchronous caller, and make `#13417` the contract** | nothing material — this is the status quo plus the repair half |
| **C2** | **Narrower and unclaimed: share ONE encode between the record writer and the graph fold** | this is the **measured** waste (§1.3): two model calls for one text on every derived-entity write. It needs no placement decision, no ruling reversal, and no deferral — it is a seam change. It does **not** foreclose anything |

### 3.5 Recommendation — **B + C2** (do not adopt full deferral; remove the double encode)

**Recommend: do not adopt full deferral** (option B), because for the entities where it is admissible it is
**already the recorded behaviour** (§3.2), and for `Point` it contradicts `#5004`'s presence-is-ownership
contract, which is a **record semantic**, not a latency trade. **Then do C2**: one encode per write for the
derived pair, which is the only *measured* waste in this whole dossier that neither R1 nor R2 addresses.

**The honest case against:**

- **Full deferral is the honest end-state for a system whose embedder can be 57 s cold.** A bound of 0.25 s is
  a bet that the model is warm; the recorded `#13108` measurement (dense leg drops to keyword-only in **45 %**
  of eval windows, recall@3 = 75 %) says the read path already pays for that bet, and `#13156` says a
  budget-cancelled leg is **indistinguishable from a slow store** in the trace. Deferral with a real queue
  would make the write's p95 independent of the model rather than bounded by a constant we chose.
- **The 584× cost argument cuts the other way too:** an embedder running in a request thread is a resident
  model held for the request's sake; a worker is one process serving many writes. That is a *hypothesis* —
  nothing here measures it, and this dossier will not present it as a finding.
- **The status quo's "observable" is currently an unfiled promise**: `#13417` is filed, not landed, and until
  it lands a vectorless row is still invisible — so "B + `#13417`" is a recommendation that depends on work
  that does not exist yet. That is a real cost of B, not a footnote.

### 3.6 The smallest experiment that settles the open part

**Is the warm encode actually a cost we need to remove, and is the double encode real?**

- **Population:** a **throwaway** graph + record (never `live-v5`); ~200 derived-entity writes
  (`ObjectRegistered`/`SubjectAdded`/`DocumentCreated`/`EventRecorded`).
- **Instrument:** count `encode_for_row` invocations per write (a counter at the seam, reverted after), and
  time (i) the fold's encode, (ii) `derived_embedding_columns`' encode, (iii) the write's total wall time, on a
  **warm** model.
- **Settles:** whether the double encode is 2 model calls per write as the docstrings assert (**nobody has
  measured it**, and both docstrings say so), and what share of a warm write it is. If it is material, C2 is
  the cheapest real win on the board. If it is noise, R2's whole premise (write-path cost) is weaker still.
- **Revert the instrumentation before any PR.** No production flag changes.

## §4 — The prior-proposal sweep: which earlier alternative is each candidate?

The brief asks whether R1/R2 are proposals we have already considered and rejected, in different clothes.
**They are not the same proposals — but two of their sub-shapes are, and the difference is decidable from the
record.**

| earlier alternative | where it was considered | is R1/R2 the same thing? |
|---|---|---|
| **§12.1c option 1** — vectors stay in the graph (status quo of that debate) | `STORAGE-ARCHITECTURE.md:1169`, 2026-09-24 → **not** adopted 2026-11-07 | **No.** R1 *retires* the graph copy; option 1 *keeps* it. R1 is closer to option 2 applied one level deeper |
| **§12.1c option 2** — vectors move to Postgres, graph keeps node + edges (the index decision) | same, **adopted 2026-11-07** (D-d) | **R1 is option 2's unexplained remainder**: option 2 moved the *index*; the *copy* was left behind by D-a for a reason that has since been spent (D-b). R1 finishes the move option 2 started — it is **not** a new proposal |
| **§12.1c option 3** — a dedicated vector store | same, rejected: *"a third system to run — and it buys nothing over option 2 at our size"* | **No**, but note: **R2's full deferral with a worker + queue is a fourth runtime**, and that objection applies to it as much as to option 3 |
| **`20261109000001`'s candidate (b)** — one `public.embedding` table across six labels | `supabase/migrations/20261109000001_record_embedding_vector.sql` (block *"WHY `public.object` AND `public.subject` GET NO VECTOR COLUMN"*) | **No.** Rejected for a reason R1 must respect: a second row per vector the tenancy-erasure cascade must reach (*"the erasure spans both stores"* defect). R1 removes a copy; candidate (b) adds one |
| **`20261109000001`'s candidate (a)** — add the column to `object`/`subject` | same block, rejected then (*"populated by NOTHING"*) | **Already reversed** 2026-11-17 by `20261117000001`, with **two** `OVERRIDES:` markers. Not R1 — it is R1's precondition |
| **`#4997`** — *"stop storing OR index them"* framing | `:1127`; closed 2026-10-05 as the **defect census** | **No — and it is the false choice the record already rejects**: *"keep the vectors, make them searchable, and put them where RAM is cheap"* |
| **`#11901`** — *"the FalkorDB index is not earned"* | §14.3 V3 row; measured 2026-11-12 | **Not R1.** It answers *index or scan*, in **FalkorDB**; it does not answer *keep or remove the copy* — and its own measurement is what shows the fingerprint is **used** |
| **R2's earlier form** — *"synchronous embedding is the structural cause of the stall"* | `#13224`'s framing, offered as a structural candidate | **Rejected as a diagnosis** by PR `#13229`: the cause was the cold load on the request thread; *"`Subject` was simply the door that arrived first"*. R2 today is **the same proposal with the diagnosis removed** — which is why §3.2's warm-encode evidence matters: what remains is a design preference, not a measured defect |

**⇒ The honest summary of "what is genuinely new":**

- **R1 is old** — it is the remainder of the 2026-11-07 decision, and the 2026-11-17 reopen already granted it.
  What is new is not the proposal; it is that **its precondition is now *almost* met and measurably is not**
  (`#13422`, §1.2). **R1 in "different clothes" is a risk worth naming: it must not be implemented as an
  immediate removal, because that is D-b's sequencing reversed.**
- **R2 is old too, and its original justification has been withdrawn.** What is new is that Postgres makes
  deferral *cheaper*, which is an argument about **cost of implementation**, not evidence about need.

## §5 — The contradiction test, with the working shown

Run **first**, on both candidates, before cost/quality/fit. A candidate that contradicts a recorded decision is
**not adoptable**; the route is a reopen, argued.

| decision | R1 (retire the graph copies) | R2 (defer the embedding) |
|---|---|---|
| **(a) 2026-11-10 KEEP** (`#11395` `6781921075`) | **Contradicted as stated, and the contradiction is already resolved**: D-b (2026-11-17, `OVERRIDES:` recorded) supersedes it for the graph copy. ⇒ R1 is **adoptable only as D-b's step 2** (record first, later release, never same release). Adopting it *now* contradicts **D-b**, not D-a | not engaged — R2 touches no stored embedding's existence |
| **(b) §12.1c + `#13220`** — the vector lives in Postgres, indexed there | **Not contradicted** — R1 removes a *non-indexed* copy in the graph; it adds no in-graph index. ⛔ A variant that keeps the copy **and indexes it in FalkorDB** *is* contradicted (§12.1c, four resident HNSWs; `#13082` ≈584×) — that is D1 in `#13232`, and it is **not** adopted here | **Not contradicted.** It matters here only as the reason §3.2's "Postgres makes deferral cheaper" is true at all: same transaction, no cross-store window |
| **(c) `#4010`** — no capture caps | not engaged | **not engaged in principle, engaged in practice**: a deferral queue that drops or coalesces under backpressure *is* a capture cap. Any R2 design must state its backpressure behaviour in the same PR, or it is `#4010` with extra steps |
| **(d) off-by-default safety rules, one-level-deep keys** | not engaged — R1 changes what the graph stores, not what a caller may write | not engaged — `encode_for_row`'s gate (`#5008`) and `embed_gate` are untouched by a scheduling change |
| **(e) `#5004` presence-is-ownership on record-TRUTH vectors** | not engaged for `:Object`/`:Subject` (their vectors are **DERIVED**, recomputable from `name`; §1.4) — but a removal that also stopped the **recompute** would delete the mechanism, so the recompute in the fold must stay | ⛔ **contradicted for `Point`**: a deferred `Point` write produces a cold `None` that is *"recorded as an explicit CLEAR that a rebuild reproduces"* (`tortoise/embeddings.py:1806-1815`). ⇒ R2 is adoptable **only for the derived-entity folds** — where it is **already implemented** (§3.2) |

⇒ **Both candidates survive the test only in a narrowed form**: R1 as D-b's gated step 2, R2 as the already-landed
bounded path. Neither survives as an unqualified "adopt".

## §6 — What this dossier does not know

1. **Whether the graph-side fingerprint earns its keep on real captures.** `#11901` measured the *prior's*
   recall; nobody has measured the capture path with the dense leg on vs off. §2.6 is the instrument.
2. **The per-label vector census at this head.** No post-2026-09-24 per-label census exists; the retired
   `12,657` figure is not quotable and the 3,742-total reading has no split (§1.3).
3. **The real cost of the double encode.** Both source docstrings say *"two model calls for one text"* and both
   say it is unmeasured. §3.6 settles it.
4. **Whether the record's `:Object`/`:Subject` vectors equal the graph's.** They are derived from the same
   `name` through the same `encode_for_row` seam, and `tests/test_13220_record_label_embeddings.py` pins the
   text expressions — but **no test compares the two stores' bytes for these labels**. What would settle it: a
   parity test over one write, both stores, byte comparison. **Until then "the same vector" is a code-reading,
   not a measurement** — and `#13412` is proof that this class can diverge.
5. **Whether `:Event`/`:Source` graph copies are retirable** — the merged design doc and `#13220`'s body say
   opposite things; `#13220`'s last comment asks the owner for wording (§1.5).
6. **What comparables do on vector/row disagreement** — no primary source found (§2.3).
7. **`record-restore`'s vectorless landing** for these labels is *stated in the migration header*, not
   measured here. What would settle it: one `record-restore` on a `test_` graph, then read the node's props.

## §7 — The one-page answer, if the owner wants only that

- **R1 — do not adopt now; it is already authorised and its precondition is measurably unmet.** The 2026-11-17
  reopen (`#11395` `6956793300`) granted graph-copy removal *once the record holds and **indexes** them* — and,
  separately in the same comment, *"**After the record serves them**: stop writing …"*. **Both clauses are
  quoted because the grant words the condition two ways and this dossier applies the stricter one (serve, not
  merely index)** — `#13220`'s last comment records that the *indexes* reading is *"a condition satisfied at the
  end of step 1"*, which would admit removal in the same release the grant forbids. On either reading, the record
  has the **columns** and the live writer fills **rows written after deploy** — it does **not** yet hold the
  pre-existing population (`:1640`), and **does not serve either label** (`LABEL_SERVED`, `#13422`). Removing
  the copy today deletes the only serving copy for two labels and then reports the loss as a saving. **Adopt the
  trigger in §2.5, fund the four conditions that make it checkable, and run §2.6's experiment to settle whether the
  fingerprint earns its keep.**
- **R2 — do not adopt full deferral; the admissible half is already shipped.** The four derived-entity folds
  are already bounded (0.25 s, `#13229`), and `Point` is excluded by a *record semantic*
  (presence-is-ownership, `#5004`) rather than by a preference. **What is left, and is unclaimed, is the double
  encode**: two model calls for one text on every derived-entity write. Fix that (C2) and land `#13417`; that is
  the measured win, and it needs no ruling reversed.
