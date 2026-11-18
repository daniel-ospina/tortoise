---
title: "Search architecture — where the vector index lives, and what the graph engine keeps"
type: engineering
domain: platform
doc_status: live
created: 2026-11-07
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: search
aboutObjects: tortoise
---

# Search architecture — where the vector index lives, and what the graph engine keeps

**Change history for this document — the work it drove and the rulings' provenance — lives in
`SEARCH-ARCHITECTURE-CHANGELOG.md`.** This document states the design; it does not narrate its own revisions
or track the state of the issues it drove. (The review record whose corrections *changed* the decision is a
different artifact and stays where §10 points: on `#4051` and in the sections it names.)

**Decision (owner, 2026-11-07)** — items 1–3 are the owner's; **item 4 is the phase correction recorded in `#4051`
comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028)** (§1.5), because
comment [`6713838871`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713838871)'s own item 4 said
*"before launch":*

1. **The vector index and the embedding payload move to Postgres** (`pgvector`). This **resolves V1**
   (`#4997` / `STORAGE-ARCHITECTURE.md` §14.3): the vector index does **not** live in FalkorDB.
2. **The text index stays in FalkorDB for now.** Moving it to a Postgres ranker is a *measured*
   regression (§6.2), so it is deferred behind a measurement, not adopted on a hunch.
3. **Traversal, writes and EP stay in FalkorDB.** The Cypher surface — an order-of-magnitude **~1,900 statements**, an estimate rather than a census — is untouched.
4. **The graph stays complete for private-beta and becomes a bounded window only after M2 measures it AND a decision it cannot make on its own is taken**, sized by the tier's cost budget — §1.5, §7. ⛔ M2 has now been measured and it does **not** arm the window: the measurement says the cost is its eviction **on `:Object`** — on `:Point` it is the eviction **plus** the claim leg's own ceiling (§9.1, Finding 2) — and arming needs a decision §9.1 names (one of three routes) on a trigger §10 records (now fired), which this document does not make. The cost during private-beta is **knowingly above** the 30 % target (§8).

**The product goal, as this document records it:** a high quality search system with manageable cost, where
speed matters but is **not** the top criterion. That ranking is **this document's rationale, not a quoted
owner ruling** (no artefact carries the sentence), and every ordering below follows it; §5 states it as Q1–Q5
so a reader can disagree with the ranking explicitly rather than inherit it.

**Decision (owner, 2026-11-17) — the embeddings of every label the search serves belong in Postgres, including the ones that carried no index when the ruling was given, and the search is to
be aligned with that** (`#13220`). The owner's words: *"if indexing improves search for
Object/Subject/Event of course we want that, and we just need it in Postgres and the search aligned with
that and everything armed and working"* … *"we should likely also index source also in postgres"*. ⚠️ **Note the owner's own hedge — *"likely"* — and that this document records it as ANSWERED anyway:** no alternative was named, and the mechanism is identical to the labels decided without a hedge, so the word is read as pacing (*"we should probably do this too"*) rather than as doubt about whether. What stays open is not **whether** a `Source` gets a vector but **which text** one with no session narrative embeds — V2's remaining design choice, named in the `STORAGE-ARCHITECTURE.md` §14.3 V2 row this sentence cites next. A reader who takes *"likely"* as real doubt should reopen that choice, not this decision. ⚠️ **And the `…` between the two quoted spans is a JOIN, not an elision:** it reproduces exactly what `#13220`'s body does between them, and neither span is truncated — so a reader must not expect words to have been removed from inside either sentence, and must not reconstruct any. The body is the artifact of record for this ruling; the quotation below is checked against it, not against a transcript no reader can open. **This
answers the two questions that were still open in `STORAGE-ARCHITECTURE.md` §14.3:** **V2** — a `:Source`
gets one — and **V3's remaining half** — `:Object`/`:Subject` get one, with `:Event` named
beside them. ⚠️ **It is a PLACEMENT ruling, and placement is what makes it cheap:** the record's
mechanism is an `embedding_vec` column plus a pgvector index on the table that owns the rows, the shape
`public.point`/`public.source`/`public.event` already carry — **`#11901`'s *"not earned"* verdict answered
the FalkorDB question** (four more HNSW structures, five total, on provisioned RAM with no eviction —
`STORAGE-ARCHITECTURE.md` §12.1c) **and does not reach a pgvector index.** ✅ **The 2026-11-10 KEEP ruling's prohibition is what binds today** (owner, `#11395` comment `6781921075`): `:Object`/`:Subject`
keep their stored embedding and **no pass may remove it or stop writing it**. The 2026-11-17 ruling above decides the
**INDEX** half — yes, in Postgres — and its state is **decided, implementation in flight (the decision is recorded at `#13220`)**; ⚠️ **and the ruling that governs removal is the same day's SECOND one, which
supersedes the KEEP ruling for the GRAPH COPY only** (`#11395` comment `6956793300`, `OVERRIDES:` recorded there): that graph copy's removal becomes permissible only once the
record holds **and** indexes that label — record first (`#13220`'s step 1), graph removal second, **in a later release and
never in the same one** — a condition not met while implementation is
in flight, so today nothing may be removed and nothing may stop being written — and `:Event`/`:Source` are neither protected by the KEEP ruling nor
retired by this second one. ⚠️ **Two rules, stated as two:** that comment's authorisation to RETIRE a graph copy is
scoped — `:Object`/`:Subject` only — while its record-first condition (the record must hold and index a
label's vectors, and nothing else about that label's storage may be decided in the same release that lands them) is SEQUENCING, addressed to
every label and scoping none, so a label-scoped authorisation never narrows it. **What it adds
to this document is COVERAGE, not a new leg:** §3's mechanism, §6.1's filter guard and §6.2's
text-index decision are unchanged, and the work it orders (the columns and the writer, the record-served
dense leg, the arming) is not a question this document decides.

⛔ **The read half is not a switch for `:Object`/`:Subject`, and the way it fails looks like a WIN.** `search_engine._STATUS_FILTERED_LABELS` is `("Point", "Object", "Subject")` (`search_engine.py:148`), so both graph branches drop a superseded / deprecated / archived / retracted entity out of the dense leg — and the record cannot yet express that: `record_vector._STATUS_COLUMN` has no entry for either label (`record_vector.py:358`, so `_status_predicates` emits NO clause for them), and the fold it would have to read (`public.object_current_status` / `subject_current_status`, `20261012000004_record_procedural.sql:56`) answers only `live` / `superseded`, collapsing `deprecated` / `archived` / `retracted` to `live`; `chk_object_props_no_derived` refuses a status column in the props tail. ⛔ **And the value is not reachable by filtering `op='restatus'`** — `classify_entity_mutation_op` labels a mutation by precedence (`supersededBy` > `name` > `status`), so `update_entity(obj, name=…, status='archived')` journals `op='rename'` CARRYING `state.status='archived'`; `cache_projection.py:3026` states the rule — *a consumer wanting every status transition must read `state["status"]` and must NOT filter on `op`* — after that exact filter answered LIVE for an archived node. **So adding those two labels to `record_vector.LABEL_SERVED` before that gap is closed returns exactly the rows `#689` exists to hide, and it reports as better recall.** The status fold comes first; `_status_predicates`'s own docstring is the standing rule (*the record leg must not be a loophole around `#689`*). ⛔ **And there are TWO record-side dependencies, not one — closing the vocabulary alone would not open the gate.** `#10193`: the record's own application role has **no SELECT on the procedural views**, so `public.object_current_status`/`subject_current_status` are unreadable at runtime and the cache restates the fold in Python instead (`cache_projection.DERIVED_FROM_RECORD`, `:1061`, fed by `read_record_view`, `:3674`) — and that restatement derives only *pointer present → `superseded`*, silent → `live`, i.e. **it inherits the same two-value blind spot**. `#13335`: neither the fold nor the restatement can represent `deprecated`/`archived`/`retracted`. So the gate opens only when BOTH hold: the views are readable by the app role, and whichever path the read takes can express four terminal states.

**What that means per label — the gates are not the same, so *"every served label"* is not *"every label at
once"*:**

| label | the record leg answers it (`record_vector.LABEL_SERVED`) | terminal-status gate on the dense leg | what has to be true before the record can serve it |
|---|---|---|---|
| `Point`, operator | **yes** — the `point` table, with the operator identity predicate | **yes**, and expressible: `_STATUS_COLUMN['point']` reads `status` (+ the legacy `outdated` flag) | no *structural* blocker — the status gate is already expressible in SQL. What remains is the record's own **no-backfill shortfall** (`record_vector.ARMING_ENV`'s comment, `record_vector.py:181`: a record holds a vector only for content written *after* the move is deployed; `cutover_armed`, :431, is the switch that decides it), which is why this is a cutover SWITCH and not a default |
| `Source`, `Document` | **yes** — the `source` table, split on `document_kind` | no — `Source` is not in `_STATUS_FILTERED_LABELS` | the vector has to ARRIVE: the column and the HNSW index already exist (`20261109000001_record_embedding_vector.sql`, `#11660`) — so V2 asked for something already built, and the owner's own word, *"likely"*, is about pace rather than about missing machinery — and **the one word *"source"* covers TWO search labels**: `document` is the same table split by `document_kind`, so indexing `source` indexes Document's rows in the same column and the same index. Document is therefore **not** a second decision and not a second migration; a lane that treats it as one is asking a question the ruling already answered by not distinguishing them |
| `Event` | **yes** — the `event` table | no — `Event` is not in the list either | same as `Source`: column and index exist, the vector has to arrive |
| `Object` | **no** | **yes, and NOT expressible** — no status column, and the fold answers only `live`/`superseded` | the status fold first (paragraph above), then the label — and the column half exists — `20261117000001_record_object_subject_embedding.sql` (`#13265`) carries it, giving `object` the `embedding` column, the generated `embedding_vec(384)` and `idx_object_embedding_vec_hnsw`. So the widest remaining gap is **not** the column — and **the column has not filled itself**: it arrives EMPTY, and every row a `record_runner` re-fold creates stays NULL (`20261117000001`'s header, *"THE COLUMN ARRIVES EMPTY, AND THAT IS THE HONEST STATE"*; `record_projection.replay()` does not fill it on its own), so **written** is a separate condition from **indexed** — the same no-backfill shortfall the `Point` row names. The gaps, in order: write coverage, then the **PREDICATE** (the fold), then the label in `LABEL_SERVED` — **never the other way round**: adding the label before the fold is closed is not a weaker sequencing, it is a **recall regression**, because the record would return the rows `#689` exists to hide and report them as better recall |
| `Subject` | **no** | same as `Object` | same as `Object`, column included — `20261117000001` adds `embedding`, `embedding_vec(384)` and `idx_subject_embedding_vec_hnsw`; what is missing is, in the `Object` row's order: write coverage first — `20261117000001`'s column arrives EMPTY for `subject` too, so every row a re-fold creates stays NULL — then the app role's SELECT on the status views (`#10193`), then a fold that can express its terminal states (`#13335` — the views fold only `superseded`/`live` at `20261012000004_record_procedural.sql`), and the label in `LABEL_SERVED` last |

⛔ **And *"index source"* splits in two at the writer, which the table's one row hides — and BOTH halves now have a rule (PR `#13473`).** The **document** half's rule is `DocumentCreated` → `_build_search_text(title, summary, topics)`: it CALLS the graph's own composer (`projection/entities.py:847`, the function `_upsert_document` writes into `_searchText` at `:5423`) instead of re-implementing it, which is what retired `#13412`'s `title + content` drift. The **plain `:Source`** half (`document_kind IS NULL`) used to be **deliberately absent from that dict** and is now present — `SourceCreated` → the payload's `_searchText` ALONE (`derived_embedding._source_embedding_parts`; ⛔ NOT the fold's `_searchText or title`, which on a `SourceCreated` resolving onto a DOCUMENT row would rewrite a `_build_search_text` vector — the skip arms on that rule state all three). ⚠️ **And the rule is keyed on BOTH `:Source` writers, the `SourceVersioned` retitle/version transition included** — that transition binds the new `_searchText` into the same `_search_text` column, so a create-only rule left the row's `embedding` describing the previous version's text while its FTS leg searched the new one (the `#13473` review's F1). ⚠️ **Nothing else would repair it:** a STALE vector is not a VECTORLESS one, so `#13417`'s counting/backfill pass cannot see it — re-embedding when the embedded text changes is the only mechanism that reaches it, and it is what comparable systems converge on. That leaves the fork this paragraph used to pose **answered in one direction and open in the other**: the record alone carries a plain `:Source` vector, because `_upsert_source` still computes none, so the record's dense leg can return sources the graph leg never had — the divergence the old comment refused, now taken deliberately under §2.0's ordering (**the record is truth, the graph its derived CACHE**). `STORAGE-ARCHITECTURE.md` §14.3 V2's row carries the residual honestly: a live≠rebuild difference, since `source` is a SERVED label and a record-sourced rebuild does write `n.embedding` (`cache_projection.node_statement`) where the live fold writes none. Giving the graph leg a vector is not ordered by the ruling and is not claimed here. ⚠️ **And the fork still has to answer for CALLER vectors** — ⛔ and the measured **0** is a MEASUREMENT, not a structural impossibility: a caller can put a vector on a plain `:Source` today: `create_source(url, kind, props={"embedding": […]})` funnels it through `_persist_extra_props` and `SET n += $extra`, measured live, with no width check (`embed_writer_register.py:993-1016`, measured live). ⚠️ **And that entry scopes its own `#5004` defence, and the scope excludes this call:** the caller-vector contract is what makes the store legitimate for `tortoise.api::add_point`, while `create_source` *"rides the same passthrough without the width check, which is the difference `#12180` records"* — and of that gap the entry says *"this entry does not close it"*. So the ungated `create_source` write is an acknowledged open difference, **not** a contract that sanctions it. So the population can stop being 0 without `_upsert_source` changing at all, and the fork above has to answer for **caller vectors** too: they already land on the graph side, and the record only holds them if the caller writes the record path as well. Column and index being present is not a rule that would ever fill them.

**⇒ The arming order follows from the table: `Point`/operator → `Source`/`Event` → `Object`/`Subject`** — and it is a **READINESS** order, not a rollout order, because the switch cannot do what the word suggests: `cutover_armed()` reads ONE process-wide flag (`record_vector.ARMING_ENV`) and `searcher()` returns a callable that takes `entity_type` per call, so the flip turns on **every** label in `LABEL_SERVED` at once. There is no per-label arming today; the only per-label control is editing `LABEL_SERVED`, which is a deploy and not a flag. The consequence is the one that matters for sequencing: **the weakest served label gates the flip** — `Document` (`document_kind IS NOT NULL`) being unready blocks arming for `Point`, so the order below is what must be true per label BEFORE the single flip, never a way to arm one label while another stays on the graph, the
last two gated on the status fold — not the order the ruling names the labels in (`Object`/`Subject`
first), which would spend the longest stretch of the work on the two with the most still blocking them,
and the shortest on the one that is already able to answer.

**⇒ And *"armed and working"* is four conditions per label, in this order — it is not one switch:**
**(1)** the vector is WRITTEN into the row (a writer that fills the payload column), **(2)** the index is
PRESENT on that column, **(3)** the label is SERVED by the read (`LABEL_SERVED`, with its status predicate if
the label is in `_STATUS_FILTERED_LABELS`), and **(4)** the cutover SWITCH is on (`cutover_armed`, default
OFF — and off for a measured reason: the record has no backfill, so switching early drops pre-existing
material out of the dense pool — and it is the one condition this document cannot schedule: arming is
**downstream of `#11691`'s arming gate and `#13082` P0-b's RAM cap**, not parallel to them, so "the code is
ready" is not "we may flip it"). **A label missing any one of the four is not armed, however the other three read** — four green checks can
each be about a different label, which is the shape that produces a confident "done".

**⇒ And *"working"* is not condition (4) reaching ON — it is the leg's behaviour AFTER the switch, per label, and this document owes it a definition because the ruling names it separately:** the read answers as `pgvector` rather than falling through (`MECHANISM_PGVECTOR`, recorded where the record leg answers; `scan_fallback` **after** arming is the signature of *armed and not working* — the one observable that survives every other check being green). **A `VectorShortfall` that does not raise is necessary and never sufficient** — the assertion is snapshot-blind to a record that is merely behind (§9), so it cannot certify this on its own. And **M1 stays the SHIP gate it is defined as**, not a monitor: it is re-run whenever the writer, the index or a status predicate changes, and what is watched continuously is the trace (mechanism per label), because a recall number nobody re-measures is a memory, not a measurement. **"Working" is a standing condition rather than a ship gate** — *"and working"* is present tense — so a label can be armed on day one and stop working on day twenty, and the trace is the only place that shows it. This is the distinction the four conditions draw one line up: **armed is a state you can check once; working is a state you have to keep checking.**

**⛔ And *"everything"* needs a ruler, because the code carries two label lists that disagree.** The set the search can be **asked** for is the seven lowercase types gated in `security.VALID_ENTITY_TYPES` and again in `sdk.py`'s `entity_type` check — `point`, `event`, `subject`, `document`, `object`, `operator`, `source`. The pack registry declares a **different** seven, capitalized, in `pack_registry.VALID_ENTITY_TYPES` — `Subject`, `Object`, `Action`, `Point`, `Event`, `Document`, `Source`. They differ in both directions: `operator` is searchable (and record-served) yet absent from the registry list, while **`Action` is in the registry list and is not searchable at all** — it appears **zero** times in `search_engine.py` and has no `LABEL_SERVED` key. So the per-label table above is total over the **search** set (five served + the two gated), and *"everything armed"* means that set: a registry-typed entity the search cannot be asked for is not an unarmed label, it is a label outside this question. Naming the ruler matters because the two lists will drift — a launch that adds an eighth search type without touching either list is the shape that makes *"everything"* unfalsifiable.

**Sibling documents:** `STORAGE-ARCHITECTURE.md` owns authority per layer (§2.0/§3) and the cost model
(§17.4); `CONSUMPTION-MODEL.md` owns metering; **this document owns where each search leg executes, and
what the graph engine holds.**

---

## 1. The method ruling that made this decidable

**We have no dataset.** The only figures available come from dogfooding runs of **superseded product
versions**. Owner ruling, 2026-11-07: *"it doesn't matter if 17% is 10 or 30%, what matters is the
engineering principles. Then we'll measure and optimise after beta launch."* — relayed in `#4051` comment
[`6713312036`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713312036), which records
it as *"The owner's ruling"*; ⚠️ that comment's **v1 scope** (search AND traversal in Postgres, no hot cache)
was withdrawn by comment
[`6713838871`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713838871) — the method
ruling quoted here was not, and §1.5's phase split is recorded in comment
[`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028).

⇒ **No decision below rests on a measured proportion of the graph.** The share a hot cache would hold is
an **output** we measure from real tenants, never an input. **No proportion of OUR graph is a valid input:** the
vendor's own field list shows the readout is incomplete (FalkorDB's `GRAPH.MEMORY USAGE` is blind to the HNSW
`vectorset` module — `#5331`, `docs/evidence/10000-turns-bytes/README.md` — so it under-reports the vector bytes,
while `INFO memory`'s `used_memory` counts them; the two instruments are not interchangeable, and a figure from
one is not comparable to a figure from the other), so a proportion computed from one is a subtraction across
instruments and tenants rather than a measurement. A published FalkorDB breakdown puts non-index structure at
**41 %**, and it is a vendor-published breakdown of a *different* population (a graph without our label mix), so
**no range we could state would be ours**. It is not an input to anything here.

**⚠️ PROVENANCE — the EXTERNAL figures this document leans on.** Each of the following is a **third-party published measurement, not ours and not reproduced here**: the **41 %** non-index structure; the **0.0015 nDCG@10** spread at 381,559 vectors; the **0-of-10** filtered-ANN result and its **0.975/0.847** relax-order recall; the **8–23 nDCG@10** Postgres-FTS-vs-BM25 gap; the cache studies behind §7's policy table (**62.4 %** naive-pinning miss vs **60.2 %** LRU, and 1-/2-hop closure prefetch raising hit ratio); the **4.9–5.9×** rebuild-peak range; and the **~54 %** hit rate the best trace-aware policy reached for 2-hop expansions; the engine's **no per-graph
memory cap** and its **`NODE_CREATION_BUFFER` of 16 384 slots** (§7's #1-risk row). They are cited by RESULT — their specific citations are **owed before this document is quoted as measurement evidence**, and the fix is to attach them, not to drop the claims. **Anything that reads as *our* measurement names its pin inline**, and a figure with neither a citation nor a pin is not evidence yet.

---

## 1.5 The phases, named — because "beta" was doing two jobs

The two constraints that sequence the work differ in kind, and only the second is quoted:

> *"measure and optimise after beta launch"* — the phase split this section implements is recorded in `#4051`
> comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028). The
> header's four-part decision is comment
> [`6713838871`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6713838871) **items 1–3**, with
> **item 4** being that phase correction — because `6713838871`'s own item 4 said *"a budget-derived window
> **before launch**"*, which `6714119028` supersedes. The four-part decision is **not** `6713312036`, which
> `6713838871` supersedes.

and, as **this document's** sequencing obligation rather than a quotation: **nothing ships to private-beta until
the guardrails in §1.5 are in place**, because a window armed without them is a redesign performed in front of
customers.

So the phases are separate, and each carries its own graph state:

| phase | who is using it | graph state | what is decided there |
|---|---|---|---|
| **pre-release** (now → private-beta invite) | nobody external; dogfood + internal | **complete** | build the architecture and the guardrails; measure §9's M1, M4 |
| **private-beta** (the upcoming release) | invited customers | **complete** | the product is live; cost per tenant is above the 30 % target and accepted as a beta-scale cost (§8). §9's M2, M3, M5 measure here |
| **post-measurement** | the same customers | **windowed** | the window is sized, armed and tuned **on real data**, per *"measure and optimise after beta launch"* |

⛔ **What that sequencing obliges — and it is the part that is easy to get wrong:** the machinery that makes
a windowed graph *safe* must ship **before** private-beta even though demotion is **off**. Otherwise
"optimise after launch" becomes a risky change made in front of live customers, and they experience it as a
quality change. **Live before private-beta:**

1. the **residency marker armed** (§7) so any answer computed over an incomplete view is *labelled* — the
   machinery exists and is currently **off** (`TORTOISE_RESIDENCY`);
2. the **seed-materialisation path** (§7), so a walk can start from a claim that is not resident;
3. the **rebuild headroom** measured and reserved (§7) — the #1 production risk;
4. the **shortfall assertion** on the vector leg (§6.1), without which a beta customer can silently receive
   fewer results than exist;
5. the **`null`-not-0.5 rule** for absent belief state (§6.3) — absent state is `null` with
   `ep_state: "absent"`, never the neutral Beta(1,1) mean 0.5, on every read/rank surface §6.3 names;
6. the **two truncation reason strings** (§7) — `degraded: "insufficient_graph"` / `degraded:
   "truncated_boundary"` — because item 1 only makes an incomplete answer *labelled*, not *labelled with a
   reason a caller can branch on*.

**Cost during private-beta, plainly:** a complete graph costs **\$11.01/tenant** at the 40-tenants-per-instance
density — RAM \$8.75 + shared project line \$2.13 + record \$0.13 (§8.1) — against a \$5.70 storage budget, i.e.
193 % of it — the honest per-tenant total (comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028), the phase
split, states 154 % as the beta cost; on this section's own basis that figure is the **RAM line alone** — §8.1's *"the
same, RAM line alone"* row — against the 193 % total). ⚠️ **It is also above the product's older recorded constraint** (*"a \$19 price with < \$9 total
cost per user"*, `STORAGE-ARCHITECTURE.md` §1), because \$11.01 > \$9 — and at beta-scale density it is higher
still (a 1 GB instance holds ~5 complete solo tenants at the §8.1 basis of 200 MB provisioned each, so the RAM
line alone is ~\$14.60 at the 1 GB tier's \$73). **This is a knowing,
bounded overspend on a small, known tenant count**, taken because the alternative — arming a window nobody has
measured — risks the quality this document ranks first. §7 records both ceilings so neither is mistaken for
the other.

---

## 2. First principles — what a hot copy is actually for

A hot copy can buy only three things:

1. **Lookup by key** — a B-tree answers in microseconds. A cache buys nothing.
2. **Find by similarity** — needs an index. Postgres holds it on disk, and the operating system keeps the
   hot pages in memory **as part of the database we already run**. A separately rented RAM index buys a
   *guarantee*, not a capability.
3. **Walk the connections** — the only genuine candidate, and **not because the data is big**: a walk is
   *dependent steps*, so the win is keeping the whole walk inside one engine, **not** keeping bytes in RAM.

**"Inside one engine" ≠ "in RAM".** That is why the vector index can move to Postgres without losing the
walk: the walk stays a graph query in FalkorDB, reading nodes and edges, and only the *similarity
candidates* come from Postgres. Two systems **will be** in the path either way — on the read side once the
vector move lands, and on the write side already.

**The price multiple decides the rest** (§17.4.1; vendor pages verified 2026-10-10): FalkorDB rents
**provisioned RAM at \$43.75/GB-month on the 8 GB tier (\$73 on the 1 GB STARTUP tier)**, and a graph may occupy
**75 %** of it ⇒ **\$58.33 per GB of graph data at the 8 GB tier** (the 1 GB tier's equivalent is \$97.33 — the 778.7×
`STORAGE-ARCHITECTURE.md` §17.4 prints; 584× is the pure RATE ratio there). Postgres disk is **\$0.125/GB**.
**~467× at the 8 GB tier.** An embedding's *bytes* are the same size in either store — which is
the point, not a defence: at these rates **every** byte of payload is 467× dearer in RAM, so the payload moves
with the index and is the bulk of the saving, not a rounding error beside it.

**And the vector move is quality-neutral on the evidence.** At 381,559 vectors — inside our range —
independent measurement puts pgvector/Qdrant/OpenSearch **within 0.0015 nDCG@10 of exact search**; the
benchmark's own conclusion is *"choose the engine for operations, not for quality."* — a third-party
conclusion, carried in §1's PROVENANCE list with its citation owed. No published case
shows a RAM-resident index returning better answers at this scale.

---

## 3. What runs where, after this decision

| job | home | change |
|---|---|---|
| authoritative state (claims, entities, connections, operators, text, embeddings, turns, narrative) | **Postgres** | none — already the record |
| **vector search** — ANN over embeddings, on EVERY served label (`Point`, `Event`, `Object`, `Source`, `Subject`, and the `Document` rows that are `:Source`'s other kind — owner 2026-11-17, `#13220`, above) | **Postgres** (`pgvector` + HNSW) | **MOVES** — was `db.idx.vector.queryNodes` (2 executing sites), and was one label's index |
| **text search** — over 7 fields / 5 labels | **FalkorDB** (`db.idx.fulltext` — RediSearch's TF-IDF scorer, documented as BM25-style behaviour in `tortoise/sparse.py`) | **stays** — see §6.2; the move is deferred behind M3 |
| **multi-hop traversal** (2-hop epistemic minimum: claim → operator → claim) | **FalkorDB** (Cypher) | none |
| **writes, projections, backup/restore** | **FalkorDB** (Cypher) | none |
| **EP state and computation** | state on graph nodes; **arithmetic in Python** | none — the EP-scalar carve-out stands (§6.3) |

**⚠️ Nothing about the epistemic or entity layer moves — it is already the record, and it stays there.**
`public.point` (claims **and** operator Points, `is_operator`), `public.relation` (every edge, including
`IMPL`/`NAND`), `public.object`, `public.subject`, `public.source`, `public.event` and `public.session`
are all Postgres tables, and Postgres is their authority (§4). What the graph holds is a **derived copy**
of that structure, and it keeps holding one — because the walk, the operator mediation and EP all run over
it. **This decision moves the vectors and *nothing else***: the structural copy is retained (complete for
private-beta, then windowed), which is exactly why the Cypher surface — an order-of-magnitude estimate, see the decision bullet — is untouched.

**The executing surface of the vector move is small and that is why it is tractable:** two
`db.idx.vector.queryNodes` calls (both argument orders), the vector-index creation, and the ranking code
behind them. Everything else in the search path is unchanged.

**What the move costs, stated so it is not discovered late:**

- `public.point/source/event.embedding` (and, since #13220, `public.object/subject.embedding`) is **`jsonb`** and **stays `jsonb`**, and **`pgvector` appears in 2 of 88
  migrations** (`20261109000001_record_embedding_vector.sql`, `#11642`, and `20261117000001_record_object_subject_embedding.sql`,
  `#13220` — the `object`/`subject` fingerprint columns, GENERATED vectors and HNSW indexes, owner ruling 2026-11-17; the denominator is 88 at this head — `#11827`'s
  `20261110000001_record_point_retire_as_of_11827.sql` added no vector, and neither do the 83rd, #12764's
  `20261114000001_per_org_postgres_bytes.sql`, nor the 84th and 85th, #12894's
  `20261116000001_record_cursor_allocator.sql` and `20261116000002_record_cursor_meter_grant.sql`, the 86th IS one (#13220's), nor the 87th, #13277's `20261117000002_postgres_residency_gauge.sql`, nor the 88th, #13315's `20261117000003_postgres_residency_zero_vanished.sql` — the forward migration correcting the residency refresh's zeroing, which adds no table, no vector and no embedding column) — so this WAS a full index build and a
  backfill, not a query edit. ⛔ **UPDATE AT THIS HEAD (`#11642`, measured on pgvector 0.8.7 / Postgres 16.15):**
  the payload column is deliberately NOT converted — a `jsonb → vector(384)` type change would rewrite the
  record's write and read seams and could not represent the caller-supplied embeddings of other widths that
  PR #3018 stores verbatim. The ANN-indexed half is a **GENERATED `embedding_vec vector(384)`** beside it.
  Measured: backfill 20,000 rows **6.9 s**; HNSW build at `maintenance_work_mem` = 64 MB **4.0–4.2 s**
  (index 39 MB); the same build at 4 MB **37.4 s** with pgvector's "no longer fits into maintenance_work_mem"
  warning — it COMPLETES, so the build degrades in time and never into an OOM; top-10 parity against exact
  scan on a clustered 20k corpus **25/25 probes identical**; a cold logical `pg_dump`/`pg_restore` of the
  20k vectors including the index **10.9 s**, against the **436.5 s** untuned / **191.8 s** tuned `vectorset`
  rebuild this replaces (`#7846`, which also deadlocked the instance). **§6.1's shortfall assertion is now
  load-bearing in a second way:** a filtered ANN under pgvector's DEFAULT `hnsw.iterative_scan = off` returns
  **0 rows where 152 match** (measured, `LIMIT 500`); `strict_order` returns 150. `tortoise/record_vector.py`
  sets it explicitly and asserts the shortfall.
- **A walk starts from seeds.** Under a bounded window (§7) a seed may not be resident, so the walk must
  **materialise its seeds before traversing** — a write on the query path — priced in §7 with the rebuild
  headroom that makes it survivable, which §1.5 ranks as the #1 production risk.

---

## 4. Authority (unchanged, cited not restated)

`STORAGE-ARCHITECTURE.md` §2.0/§3: **Postgres is the system of record for every layer.** FalkorDB is a
derived cache, never authoritative; deleting it loses nothing recoverable from Postgres — with EP's
recomputed scalars as the one declared exception. Nothing here changes authority.

---

## 5. What "high quality search" means here, ranked

| # | property | why it outranks what follows |
|---|---|---|
| **Q1** | **Traversal is complete and correct** — a walk sees every connection that exists, and EP is computed over a closed component | a truncated graph does not return a worse answer, it returns a **different** one: a missing node silently becomes a prior (`coalesce(ep_alpha, 1.0)`) |
| **Q2** | **Nothing is silently invisible** — a search finds a claim or says it could not look | a shortfall reporting success is the failure class §17.3 exists to forbid |
| **Q3** | **Both retrieval legs work** — lexical and semantic, on all searchable labels | see §6.2: the lexical leg is the one a move would damage |
| **Q4** | **Latency**, measured | matters; never outranks a wrong or missing answer |
| **Q5** | **Cost inside the 30 % rule** | a hard ceiling, and the last discriminator |

---

## 6. The three things that can go wrong, and what each requires

### 6.1 Filtered vector recall — the top quality risk (move required, guard required)

Every tenant's vectors share one index, filtered by `org_id`/`graph_id` — the archetypal **correlated
filter** for HNSW post-filtering, where measurement elsewhere shows **0 rows of 10 returned at 0.1 %
selectivity**, both at stock settings and with `iterative_scan` tuned; relaxed-order scanning recovers
0.975 at 100k vectors but **0.847 at 1M**.

**Required before private-beta (§1.5):** a **per-tenant-size filter strategy** (exact scan for small tenants using the
record's existing `(org_id, graph_id)` index, partial index for the few large ones, partitioning above),
and a **shortfall assertion in the read path** — a tenant holding ≥ k rows must never quietly receive
fewer. A recall-only test passes while the product under-returns.

### 6.2 Text ranking — why the text index does NOT move yet

`ts_rank`/`ts_rank_cd` have **no IDF and no length normalisation**; independent measurement
puts Postgres full-text **8–23 nDCG@10 points behind BM25**, with its hybrid scoring **below
vector-only** on all three datasets.

**What this does and does not claim about our own scorer.** The FalkorDB leg is RediSearch's **TF-IDF**
scorer, whose behaviour `tortoise/sparse.py` documents as *"the BM25-style rank behavior the epic's scope
prescribes"*. ⚠️ **The 8–23-point gap is measured against a BM25 implementation, and how far our hosted
`db.idx.fulltext` leg sits from that baseline is *unmeasured* — that is what M3's score distributions are
for.** So this document does **not** claim our leg *is* the comparator. The repo's own measurement of a lexical
leg (`tortoise/bm25.py`,
`#8488`) found **neither rarity nor length** priced into the scoring, measured on the **embedded
(falkordblite) lane** — not on the hosted `db.idx.fulltext` leg, which is unmeasured here. What the move
decisively changes is the **order** the fusion consumes, not the score scale: `rrf_fusion` is RANK-based
(`RRF(d) = Σ w_i / (k + rank_i)`, `search_engine.rrf_fusion` — and it DISCARDS the leg's own score outright,
`for rank, (pid, _score) in enumerate(ranked)`), so the `{"vector": 1.5}` + implicit 1.0
weights are multipliers on a rank and a rescale leaves them untouched, and on the multi-leg arm a caller's
`threshold` is compared against the **fused** score (`sdk.tortoise_fts_query`, its RRF arm's `fused = {… if score >= threshold}` under "Apply threshold filter to RRF scores"), never against the leg's own.
⚠️ The magnitude does enter ONE arm, and it is stated because a later lane would otherwise be surprised
by it: when a single leg answers or the read is a full scan, a caller's `threshold` IS compared against
that **leg's raw score** (`sdk.tortoise_fts_query`, its single-leg/full-scan arm; `threshold` bounded `0.0–1.0` by that method's own validation, shipped default `0.0` = filter
off) — there a rescale changes which rows clear the floor. Either way a different scorer **re-ranks** the
lexical leg, and that re-ordering — not a lost scale — is what M3's score distributions must measure.
Independently of all of it, `to_tsvector` cannot reproduce the product's tokenizer behaviour
(`ERR-4032` → `err4032` **plus** the adjacency phrase, `#8933`).

**A separate leg the move does not touch:** the in-memory sklearn **TF-IDF** fallback (`tortoise/sparse.py` — what `sparse.py` calls "the embedded
backend", a different leg from the falkordblite lane named above) is outside `db.idx.fulltext` entirely, so "the text index stays" says nothing about it.

**Therefore:** the text index **stays** until **M3** measures a BM25-capable Postgres option (extensions
are candidates, not assumptions) with **score distributions**, not only ranks, plus the identifier
fixture. Moving it opportunistically would trade a known cost win for a measured quality loss — the
opposite of the ordering in §5.

> **STATUS — M3 is measured (2026-11-13, `#12298`).** The qualifiers above are resolved **in the respect
> this measurement answers — recall@k on the FTS leg in isolation — and on that question this
> decision is unchanged**: a BM25-capable Postgres option was measured against the shipped leg on one
> paired population, and moving the index earns no recall return. With the query terms held constant
> the swap moves recall@3 by ~1 point (not significant), by **zero** at k=15, and is **−0.67 pts at
> k=5** — a sign change, not a floor — while the whole 25-point swing the deployable arm shows
> reproduces **inside the current index**. ⚠️ **What is still NOT measured**, so "resolved" is not read
> as more than it is: the qualifier that asked *how far our leg sits from the 8–23-point BM25 baseline*
> is a **ranking-distance (nDCG)** question, and M3 measured recall and score distributions, never
> nDCG. That ranking distance is now measured — **against the Postgres candidates, on our own corpus**
> (see the STATUS note below, `#13099`); the 8–23-point figure itself still compares foreign datasets and
> is untouched by that measurement. The leg's
> actual defect is a **tokenizer contract**, not a scorer or a ranking baseline, and it is filed as
> `#12350`. Numbers, method and the artifacts: `docs/research/2026-11-13-12298-m3-fts-leg/`; the
> changelog entry records what changed and what did not. ⛔ **This note does not decide `C3` (SPLADE)**
> — that pre-authorisation is ruled in the epic and pre-registration that carry it (`#12186` /
> `#12192`), and the measurement is the evidence before it. ⛔ **Two conditions, and the measurement
> meets only the second.** The pre-authorisation's falsifier sentence states a **closure** condition
> (BM25 does *not* beat the current scorer), and that condition is **not triggered** — a deployable
> BM25-in-Postgres does beat it (0.9633 vs 0.7133). What *is* met is the pre-authorisation's
> **trigger**, which is why C3 is neither closed nor proceeding until the owner rules. ⛔ Tracker
> facts — which issues carry the ruling and what state they are in — are recorded in
> `SEARCH-ARCHITECTURE-CHANGELOG.md`, and no statement here turns on them. Correcting the closure
> recorded in `SEARCH-ARCHITECTURE-CHANGELOG.md` is the owner's; it is not a lane's.
>
> **STATUS — the ranking-distance qualifier is now measured (2026-11-17, `#13099`).** The qualifier the
> note above leaves open — *how far our leg sits from the 8–23-point BM25 baseline* — is a
> **ranking-distance (nDCG)** question, and it has been measured on the repo's **committed deterministic
> corpus** (`#1144`: seed 42, 2000 points, 24 topics, the 100 committed oracle queries and the committed
> grade function) against the Postgres candidates that now exist, on a **Docker FalkorDB** lane (the
> environment the `#1144` baseline's own notes name authoritative). On the class carrying the committed
> qrels (n=100), `ts_rank`/`ts_rank_cd` land **+2.32 nDCG@10 pts (90 % CI −0.93 … +5.46)** — **inside the
> noise band** — and are **significantly ahead at depth**: **+9.23 nDCG@100** [+6.68, +11.85] and
> **+13.58 recall@100** [+10.46, +16.78]. `pg_search` BM25 has the same shape. **No Postgres option loses
> significantly on any query class.** So **the reason this section gives for keeping the index does not
> reproduce against the leg we actually run.** ⚠️ **The 8–23 figure itself is untouched:** it compares
> Postgres FTS to BM25 on three foreign datasets, while this compares **our leg** to Postgres **on our
> corpus** — a different claim, and the one a decision here can rest on. The place the in-graph leg
> genuinely trails **at the head** is exact-name lookup (**+4.87 nDCG@10**, CI excluding 0, n=96) —
> ⚠️ **but that class's head metric SATURATES**: every Postgres arm returns `nDCG@10 = 1.0000` on all
> 96 queries, so +4.87 is the remaining headroom (baseline 0.9513 → ceiling 1.0), not a margin between
> candidates; the candidates separate at depth there — `nDCG@100` 0.9873 (`pg_ts_rank` / `pg_ts_rank_cd`)
> and 0.9947 (`pg_bm25`), with `pg_bm25` reaching `recall@100 = 1.0000` (two metrics, not one). At
> depth it trails on the keyword class too (**+9.23 nDCG@100**, above), so "it only loses on names"
> would be the wrong reading. The mechanism is
> recorded with a worked example: a document matching 2 of a query's 7 terms scores 9.0 while the
> document matching all 7 scores 4.14 and ranks 99th — the `#8488`/`#12350` defect class, fixable in
> place rather than a reason the store is wrong. ⛔ **This note decides nothing and reverses nothing — the
> decision above STANDS.** What it supplies is the **figures for the trigger this section names**, and the
> reopen is argued on `#4051` / `#13082` phase 2 with them; adoption from a measurement lane is not a route.
> Evidence, ops facts (index sizes, build times, licence, preload) and the limits:
> `docs/evidence/13099-text-gate/`.

### 6.3 EP state — the carve-out stands, and the fix is not to store it

`ep_*`, `confidence`, `posterior_*` and `baseline_set` are **recomputed from the structure and never stored
as authority**; the record schema carries **no column** for them and its `chk_point_props_no_derived`
`CHECK` **refuses them by name** (`supabase/migrations/20261012000003_record_epistemic.sql:128-130`, whose
table comment restates the carve-out). ⚠️ **The one narrowed exception, stated so this section does not
overstate the carve-out:** `baseline_source` / `baseline_alpha` / `baseline_beta` **are** columns —
PRIMARY when `baseline_source = 'set-by-author'`, because an author-set prior is a non-reconstructible
input rather than a derived value (the `#7955` correction to `STORAGE-ARCHITECTURE.md` **§17.5**, paragraph
“`baseline_source` is NOT unconditionally DERIVED”, which says the carve-out is **not** reopened by it).
That ruling stands and nothing here reverses it.

**Which is why the window's unit of RESIDENCY must be a CLOSED component (§7):** a closed subgraph has no missing
boundary, so recomputing EP for it is **exact**, not approximate. Residency and the EP carve-out
therefore point the same way — and no schema change is needed.

**A claim with no persisted belief must not answer the neutral Beta(1,1) mean 0.5.** Answering it would
make an absent claim indistinguishable from a computed one at the point where ordering happens. The rule
is **absent state is `null` with `ep_state: "absent"`, never 0.5**, and it holds
on every read/rank surface: `annotate_ep_batch`, the canonical `why` `ep` block (`tortoise/why.py`),
`GraphRanker`, `StateRanker`/`recall_ranking`, `GapsRanker`/`gaps_ranking`, `SubgraphExpander`, and the
event/object aggregate reads; the `analyze` templates refuse an absent claim rather than admitting it at a
coalesced 0.5. The vocabulary is declared ONCE (`search_engine.EP_STATE_*` +
`ep_has_belief_cypher`/`ep_state_cypher`/`ep_state`/`ep_state_resolve`), and
`tests/test_11702_absent_belief_state.py` pins the property across every one of those surfaces, in both
directions (a *measured* 0.5 keeps its 0.5).

**How the rankers honour it, stated exactly** — because "null" alone under-specifies an ORDER:

- **absent rows sort AFTER every row that has a belief** (`recall_ranking.belief_informed_order` is
  `false` on them). A multiplicative gate cannot express "absent" as a factor — `conf**exp` ranges over
  `(0, 1]`, so the multiplicative identity a missing factor would use is that axis's **maximum**, which
  would rank "nothing measured this" above a claim measured at 0.9. The refusal is therefore a sort
  TIER, not a fabricated factor.
- **`GraphRanker`'s absent row drops the belief TERM and keeps the remaining term's WEIGHT**
  (`0.6·inst_norm` / `0.5·connectivity`), never the unweighted term — which would let absence (0.75)
  outscore a measured 0.5 (0.65), the same defect pointed the other way.
- **`min_confidence`**: an absent claim fails any floor above `0.0` and is kept by the `0.0` default.
  `navigation.py`'s `confidenceMin` and the notification gate (`volunteer._eligible`) follow the same
  rule — the gate is stricter, requiring a belief at **every** floor: you do not interrupt about a
  claim you have no belief about. **`confidenceMin` applies to EVERY family the profile lines up,
  not only to Points**: an Object / Event / Subject neighbour (or a Source with no `documentKind`)
  has no persisted belief either, so it is excluded by any floor above `0.0` — where the pre-#11702
  `node.get("confidence", 0.5)` admitted it at any floor ≤ 0.5. That is this rule applied
  *uniformly*, not a divergence from it: restoring the `0.5` "because it is not a Point" would be the
  same fabrication one family over. Pinned (non-Point arms, including a non-Point that DOES carry a
  `confidence` mirror and must still clear the floor) in
  `tests/test_11702_absent_belief_state.py::test_entity_profile_floor_reads_a_posterior_only_belief`.
- **PRESENCE is a property of the value's operands, not of a column existing.** A belief exists when the
  mirror (`n.confidence`) is set, **or** both coalesced operands are set and `α + β > 0`. So a stored
  `(0, 0)` pair (Beta(0, 0) is not a distribution) and a HALF pair (`posterior_beta` with no α, whose
  mean the `coalesce(…, 1.0)` default used to invent as 1/4) are both **absent**. Without this rule one
  claim got four answers — `0.5` in the `ep` block, `None` in `recall_ranking`, `absent` in
  `gaps_ranking`, and **`NaN`** from `graph_ranking`'s unguarded `α/(α+β)` division, serialised onto the
  wire as a bare `NaN` token.
- **The one stated exception**, recorded on #11702
  ([decision comment](https://github.com/daniel-ospina/tortoise/issues/11702#issuecomment-6781252637)):
  `sdk.get_confidence` / `TortoiseEP.compute_confidence` still answer the Beta(1,1) prior mean under an
  explicit `require_calibration=False` — that is the EP read, not a read/rank surface. Consequence, so
  nobody "fixes" it later: `get_confidence` and `search` disagree for an ABSENT claim by design, and
  #2206's agreement indicator holds only for the population that has a belief.

### 6.4 The noun's rank signal — entity rows are scored on their CLAIMS, on identity, and never on a point's signals

`GraphRanker._fetch_signals` dispatched on `entity_type` with two branches until `#12184`: `event` got
event signals and **everything else got point signals**. So `rerank(..., entity_type="object")` — and
`"subject"`, `"source"`, `"document"` — silently scored entity ids with a point's signals. `StateRanker`
had the same shape in its own dispatch (only `object` was routed; every other label fell to points).
Nothing raised, and the output was plausible, which is what made it a defect rather than a bug report: a
point's signal is its **own** persisted belief and its **operator** edges; an entity has neither, so every
entity row answered `confidence: null` / `degree: 0` and the graph term was a CONSTANT. A constant is not a
neutral default — it is the claim "the graph does not distinguish these rows", made by a read that never
looked at the edges that could.

**The decision (this section is the record `#12184` asks for).** A noun's rank signal is, in this order:

| signal | what it reads | why it is the entity's and not a point's |
|---|---|---|
| **identity** | exact equality under `entity_resolver.normalize_entity_name` between the row's identity surface (its `content` — see below) and the query | the entity's whole surface IS its name (a Point's is `content` too, which is why the term is entity-only). ⚠️ The ranker reads `SearchResult.to_dict()`, which carries **no `name` key**, so the compared surface is `content` (`_entity_row_name` falls through to it) — and `content` is filled by the SDK's own entity fetch: from `n.name` for Object/Subject, and from **`n.title`** for `source`/`document` (`sdk.py`'s `entity_data` branches). So on a source/document row the term compares the TITLE — RARE (the query must equal the whole title) but LIVE, not inert: when it fires, that row sorts ahead of every scored row. `_searchText`, the column the Source fulltext index is declared on, is a DIFFERENT column and is never read by this term. Equality here is the identity ladder's own rung-1 verdict, so it is a categorical ORDER DIMENSION ahead of the score — never a tuned weight, and never a fuzzy similarity (a second similarity constant is a REFUSED row, `#11901` §7). |
| **degree** | INBOUND claim count — `about*` edges into the entity (`extractedFrom` too, for a `:Source`) | a Point's degree is its operator connectivity; an entity's is its USE. "How much the graph refers to this" is the structural signal a point read cannot produce. The `about*` family is `ABOUT_EDGE_TYPES` (`aboutSubject`, `aboutObject`, `aboutAction`, `aboutEvent`, `aboutPoint`, `aboutDocument` — the same six `sdk._ABOUT_TYPES` mirrors), plus `extractedFrom` for a `:Source`. `aboutSource` is NOT in that family, so a Source's degree counts the claims extracted FROM it and its `aboutDocument` in-edges, never its `aboutSource` ones. ⚠️ That is a BOUNDARY, not an oversight to tidy away here: `ONTOLOGY.md` §3.9 lists `aboutSource` among the `about*` predicates, so the two lists are not the same list, and widening this family would move the point and event arms with it. |
| **confidence** | the MEAN persisted belief of those inbound claims | an entity has no belief of its own; what the graph knows about it is what its claims say. `null` + `ep_state: "absent"` when none carries one (§6.3) — the term then does not participate, and keeps its weight rather than taking the whole boost. |
| **recency** | the row's `createdAt`/`startedAt` | unchanged, shared with points. |

**The dispatch is now total and refuses.** `signal_family_for()` maps every label in
`security.VALID_ENTITY_TYPES` (`operator` → point is a routing DECLARATION — an operator IS a `:Point`),
and any other label raises `UnsupportedEntitySignals` **before** the defensive `except` that answers
`None`. The difference matters: `None` means "the read did not answer" and the caller ranks with neutral
defaults; a refusal means the caller asked for a ranking that cannot exist. `StateRanker` routes through
the same function, so the two rankers cannot drift apart again.

**Disclosure, because "ranked" and "answered" are different facts.** `signals_read` says the read
answered; `signals_informative` says the answer DISTINGUISHED the rows. An entity read that answers
`(degree 0, confidence null)` for every row sets the second to `false` and logs — the order is then
relevance-only, and the caller can say so instead of presenting a constant as a graph judgement. A read
that answers **no rows at all** is the SAME verdict (`false`): the gate is the request, not the row
count, because "no answer" distinguishes the rows even less than a flat one. `null` is reserved for the
cases with no subject: a label with no entity signal set, no projection to read, or a read that RAISED.

**What this does NOT decide.** It does not put the entity signals on the DEFAULT noun path: they reach
`order_by="graph"` and any caller that constructs `GraphRanker(..., entity_type=...)`; the capture prior
(`extractor_v2._fts_rows`) still reads the fused relevance order. Wiring an entity pass into that
pre-truncation seam is a separate, measured change (`#12184`'s measured arms:
`docs/research/2026-11-17-12184-entity-rank/`), and the eval graph is a NAME CORPUS with no claims, so it
measures the identity dimension only — its graph terms are inert there **by construction** and the artifact
says so (`signals_informative: false`).

---

## 7. The window — what the graph holds once it must be bounded

**Why the window exists at all:** a *complete* graph costs **\$11.01/tenant** against a **\$5.70** budget
(30 % of \$19) — **193 %, and its RAM line alone is \$8.75 (154 %)** (§8.1; the RAM line is NOT the tenant's
cost, which §8.1 says explicitly). Density does not fix it — `$58.33` is already the DENSEST tier's per-GB rate, and density cannot
shrink a tenant's own graph bytes; it only spreads the *fixed* lines. **The window is what makes the pricing work at scale.**

⚠️ **Where the \$5.70 comes from, because the document it amends records a different ceiling.** `STORAGE-ARCHITECTURE.md` §1
records the product constraint as *"a \$19 price with < \$9 total cost per user"*, and its M2 row prices against a
"\$9 → \$19 budget"; the **30 %-of-price** target here is the owner's 2026-11-07 sizing rule for the
**storage AND shared-project lines together** (the owner's words cover falkor and postgres in one budget, §8),
and it is **strictly tighter** than that ceiling (\$5.70 < \$9). Both are quoted so neither is mistaken for the other:
against the older **\$9 total-cost ceiling** a complete graph's **RAM line** is **~97 % of it** and its **total is ~122 %** (above it), while against the **\$5.70 storage target** the RAM line is **154 %** and the total **193 %** — and
the target is what governs the window. **The rule's recorded home is §8**, which quotes the owner's own words
(`#4051` comment [`6712622492`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712622492))
and derives every threshold from them — so the target is no longer an unhomed figure. §2.0's marker states the
same two figures (the §8.1 total of \$11.01 against the \$5.70 target) rather than citing this section; the
derivation itself is §8 below. ⚠️ The owner's words bound **solo** (30 % of \$19 = \$5.70); carrying the rule to pro and team
is this document's generalisation, recorded in the same thread (comment
[`6712718679`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712718679)), and the
thresholds for those two tiers are therefore a derivation, not a quotation.

**⚠️ Why it is nonetheless NOT armed at private-beta (§1.5):** private-beta runs the graph **complete** by
phase rule, so nothing decides the beta here; §9's M2 measurement is what gates the **post-measurement**
phase, and it does not arm the window (§9.1). The bounded-cache
literature measures latency and hit rate, not answer quality, and the best trace-aware policy reached ~54 %
hit rate for 2-hop expansions — so arming it on a guess is exactly the quality risk this document ranks
first. Private-beta therefore runs **complete** and **above the 30 % target, knowingly** — **193 % of it on the
total per-tenant basis, §8.1** (the RAM line alone is 154 %, and it is also above the older <\$9 total-cost
ceiling) — bounded by a small known tenant count, and the window is armed only when M2 says it is safe — which it does not: §9.1 measures the cost as exactly the eviction **on `:Object`**, where `found@deep` equals the resident share in every arm; on `:Point` the cost is the eviction **plus** the lexical leg's own ceiling (~11.7 points of it at `tail@0.5`, where `found@deep` is 0.410 against a resident share of 0.5267), so arming additionally needs a decision no measurement can make — §9.1 names the
three shapes, and §10 records the trigger that has now fired. **The guardrails that make it
safe ship before private-beta regardless**, so the eventual switch is a configuration change with measured
justification, not a redesign in front of customers.

⭐ **What the §12.1c decision buys that the window cannot buy itself: a per-tenant memory cap becomes
expressible.** A cap needs eviction, and eviction needs a demoted subgraph to come back cheaply — the
`DUMP` → `DEL` → `RESTORE` cycle. While the HNSW index lived *in the graph*, that cycle was
**instance-blocking**: the restore **rebuilds the index on the serving thread**, so the whole engine stops
answering for the rebuild's duration, with no progress line and no error — 30 000 nodes, index-free
`RESTORE` **0.17 s** (worst bystander `PING` **53.3 ms**) against index-bearing **2.62 s** (bystander
**2 497.7 ms**, i.e. the outage IS the restore), and at 90 000 nodes it outlived a 10 s client read bound
(`docs/evidence/7846-indexed-graph-restore/README.md` §3, §3a, §4a; `#7846`). ⚠️ **Read that record, not the
`#7846` issue body** — the body's "permanent wedge" does **not** reproduce (its first harness's graph carried
no populated index, so it measured nothing), the index **is** carried inside the graph key's serialized
payload (a restored shard is not vector-blind), and the block is avoidable by dropping the index **before**
the `DUMP` (**0.069 s** / **69.4 ms** bystander at 20 000 nodes, against **2.955 s** / **2 899.4 ms** raw).
With the index and its payload in Postgres (§12.1c), a graph dump has **no vector index to rebuild at all**,
so that whole class of block is gone rather than mitigated — ⚠️ measured for the VECTOR index; whether a
fulltext or range index blocks a restore the same way is not measured in that record. **So §7's window is a
scheduling question, not a feasibility one — *for the vector index***, which is the only index the decision
removes. ⚠️ **The window KEEPS the fulltext index in the graph** (decision item 2, §6.2), so the index that
stays is exactly the one that record did not measure: if a kept `db.idx.fulltext` — or a range — index blocks
a restore the way the vector index did, then the window is a feasibility question again, and that leg is
**M4's** to settle rather than this document's to assume. The residual cost is exactly what §1.5 item 3
already reserves: re-materialising a window still peaks at **4.9–5.9×** its resting size (the policy table below),
and that peak is a graph-memory peak, not an index one.

**The unit of RESIDENCY is a CLOSED component, not a node.** A pinned hub whose neighbours were evicted
pays for the node and still cannot complete the walk. Pinning closed neighbourhoods (1–2 hops) also
makes EP recomputation exact (§6.3).

⚠️ **This window is RESIDENCY, not retention, and the distinction is load-bearing.** Demoting a subgraph
removes it from the *cache*; it stays in the record. Nothing a customer keeps is deleted, no copy is
dropped, and `#3853` — *"No default window, of any length. … there is no default retention period on user
content"* (`docs/retention-and-deletion.md` §"A different axis: the product-usage record (#3843)", its
*"No default window, of any length"* bullet; the general rule it rests on is §"The promise": *"R1 forbids
retaining user **content** for any length"*) — is untouched: §4
records that `FalkorDB` is a derived cache, never authoritative, so §11 excludes retention because there
is **none here**. Read §7 as sizing what is *resident*, never as a TTL on what is *kept* — a per-node TTL
is the bound `docs/architecture/CONSUMPTION-MODEL.md` forbids as *"any bound that shrinks what a customer
may keep, however it is denominated"*, and it is not this.

**The policy, as researched:**

| rule | why |
|---|---|
| **retain closed components / 2-hop closures** | closure is what makes a walk and an EP recompute correct; measured cache studies show 1-/2-hop closure prefetch raises hit ratio for **every** policy tested |
| **cap the pin: count, bytes, AND a degree ceiling** | unconditional hub pinning is a *measured* anti-pattern ("they can thrash each other"; naive pinning scored 62.4 % miss vs LRU at 60.2 %). Degree is **U-shaped** — helpful mid-tail, harmful at the head; a single huge hub can eat a 77 MB window |
| **window-local touch counters + admission control** | recency beats static degree ranking, and adaptive policies beat every static one measured; eviction must be budget-triggered, not per-request, or the cycle "demote a leaf → the next query needs it → re-materialise → evict another leaf" becomes a storm. ⚠️ **The counter is WINDOW-LOCAL cache state — a different artifact from `tortoise/read_ledger.py`.** That ledger is TENANT-WIDE and is explicitly not a product input: `STORAGE-ARCHITECTURE.md` §17.4.6 states that reading it for any product decision *"would be a violation"* (it exists to measure `f`). Wiring it as the eviction signal is exactly the violation it names. |
| **reserve ≈6× the window in provisioned RAM for rebuild peaks** | **third-party measurement — citation owed (§1)**: re-materialising into FalkorDB peaks at **4.9–5.9× the resting size** — a 77 MB window can transiently demand ~377–454 MB, and the engine has **no per-graph memory cap** (**vendor-internal claim — citation owed (§1)**). This is the #1 production risk. Do not shard the window into many small graphs (`NODE_CREATION_BUFFER` costs 16 384 slots each **— vendor-internal, citation owed (§1)**) |
| **a walk materialises its seeds first** | search returns ids from Postgres; a non-resident seed must be written into the graph before traversing — a write on the query path, which is why the headroom above is mandatory |
| **label what was truncated** | a confidence within 2 hops of a truncated boundary is **refused**; beyond that it may be returned as degraded. ⚠️ **The two reason strings this rule needs are OWED, not shipped, and each names its own outcome:** the degraded RETURN carries `degraded: "truncated_boundary"`, and the REFUSAL carries `degraded: "insufficient_graph"` (the resident graph cannot support the claim at all). Neither string appears in any module in the tree at this head, and `STORAGE-ARCHITECTURE.md` §17.3 records `degraded` as one of four terms whose ONE home, `tortoise/status_vocabulary.py`, forbids a fifth, so the *reason* has to attach to `degraded` rather than mint a term. **Required before private-beta (§1.5).** What DOES exist is the marker's on/off mechanism (`residency.py`, `not-hot`, `TORTOISE_RESIDENCY`) and it is currently OFF |

⚠️ **The honesty item:** the bounded-cache literature measures **latency and hit rate, not answer
quality**, and the best trace-aware policy reached ~54 % hit rate for 2-hop expansions. **Whether a
windowed graph harms retrieval quality is answered nowhere in that literature** — §9 M2 measures it for
our graph, and its result is §9.1. Private-beta runs the graph **complete** (§1.5) regardless: §9.1's
measurement is what gates the post-measurement phase, not what gates the beta.

---

## 8. Cost — per tier, derived from the 30 % rule

Rates per §17.4.1 (vendor pages, **verified 2026-10-10 — re-check before a published tier; a rate move moves
every number here proportionally**): FalkorDB **\$43.75/GB-month provisioned** at the 8 GB tier, and a graph
may occupy **75 %** of it ⇒ **\$58.33 per GB of graph data**; Postgres disk **\$0.125/GB**; plan `$25` +
compute `$60` per project.

**The rule, in the owner's own words** (`#4051` comment
[`6712622492`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712622492), 2026-11-07):

> *"i will put the price tier of the solo to \$19 and 30% of that is our per-month cost budget. So we need to
> include falkor and postgres in that. there's overage after they exceed a threhsold, but for now we want to
> set the trheshold fo psotgres and falkor so cobined they don't use up more than 0.3\*19 per month"*

So every threshold below is **derived, not chosen**, and the derivation is one line — because only one of the
two stores is expensive. ⚠️ **Read the scope honestly:** the owner's ruling names **solo** and gives one number,
\$5.70 = 30 % of \$19. Applying `0.30 × price` to **pro and team** is **this document's generalisation**, recorded
in the same thread (comment
[`6712718679`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6712718679)) rather than
spoken by the owner — ⚠️ and read that comment for what it does, not for what it claims: it *applies* the rule
(`30 % of that tier's price`) to pro and team while calling the general form *“the owner's”*. The **derivation of
the pro and team rows is this document's**, and a reader who needs authority for those two numbers has it only as
the rule's form carried to another price, not as a ruling on pro or team.

For a tier priced `P`, with a record allowance of 1 GB, the threshold must be solved *together with density*,
since the shared project line is `$85 ÷ tenants`:

```text
provisioned MB per tenant  p = (0.30·P − $0.125) ÷ ($43.75/1000 + $85/8000)
graph window               = 0.75 · p                       # a graph may occupy 75 % of provisioned RAM
tenants per 8 GB instance   = 8000 ÷ p                      # this also sets the shared project line
```

| tier | price | 30 % budget | provisioned / tenant | **window target** | tenants per 8 GB instance | RAM \$ | shared \$ | record \$ | **total** | **% of price** |
|---|---|---|---|---|---|---|---|---|---|---|
| **solo** | \$19 | \$5.70 | 103 MB | **≈77 MB** | 78 | \$4.49 | \$1.09 | \$0.13 | **\$5.71** | **30.0 %** |
| **pro** | \$25 | \$7.50 | 136 MB | ≈102 MB | 59 | \$5.93 | \$1.44 | \$0.13 | **\$7.50** | **30.0 %** |
| **team** | \$149 | \$44.70 | 820 MB | ≈615 MB | 9.76 | \$35.87 | \$8.71 | \$0.13 | **\$44.71** | **30.0 %** |
| free / anon | \$0 | **\$0** | — | **0** | — | — | — | — | — | funded from paid margin, or no cache |

⚠️ **The basis, because the two earlier figures in this section do not count the same components.** Every
cell above is a **TOTAL per tenant** — RAM **plus** the shared project line **plus** the record — because a
per-byte cost compared against the whole budget is an error this document has already had to fix. The
**window target** column is a **budget-derived target, not a measurement**: no windowed graph exists, and the
window is armed only after M2 (§1.5, §7), and M2's answer is that it must not be armed as specified (§9.1). And **the 150 MB graph figure those targets scale from is an
ESTIMATE** from `GRAPH.MEMORY USAGE` — a sampling instrument — so it is a dated estimate, not a bound. ⚠️ **Its
known omissions bias ONE way:** `STORAGE-ARCHITECTURE.md` §2.1 records that it is blind to the HNSW `vectorset`
(measured 8.5×–21.7× under-report on the index share, ~2× on the total) and excludes per-graph/Redis-key
overhead, so the real graph is larger than measured; only the SAMPLING error itself runs both ways.

**The record allowance is not a trade — it is a rounding choice.** The record is **467× cheaper per byte**
(\$58.33 ÷ \$0.125 = 466.7), so **1 GB costs \$0.125 — printed as \$0.13 — i.e. 2.2 % of solo's \$5.70 budget — and displaces only ≈2 MB of
window.** Set it by what the product wants to promise and state it beside the window.

### 8.1 The two states, on the same basis

| | **complete graph (private-beta, §1.5)** | **windowed (post-measurement, §1.5)** |
|---|---|---|
| graph per solo tenant | ~150 MB — **an estimate**, see the basis note above | ~77 MB — the **target** the formula above yields |
| provisioned per tenant | 200 MB | 103 MB |
| tenants per 8 GB instance | 40 | 78 |
| RAM + shared + record | \$8.75 + \$2.13 + \$0.13 = **\$11.01** | \$4.49 + \$1.09 + \$0.13 = **\$5.71** |
| vs the \$5.70 budget | **193 %** — above, and accepted knowingly | **100 %** — the budget, to the cent after rounding (the unrounded model is \$5.70; the printed cells round up by \$0.01) |
| the same, RAM line alone | \$8.75 = **154 %** of the budget | \$4.49 = **79 %** of it |

⛔ **The last row is the one that misleads, and it is here deliberately.** The **RAM line alone** already
exceeds the whole budget for a complete graph (154 %), and the earlier text quoted that as if it were the
tenant's cost; the honest figure is the **total, 193 %**. Both are printed so the weaker claim cannot be
mistaken for the stronger one.

⚠️ **And the Postgres side is not free of unknowns, only of cost:** under both states the dominant
**unmeasured** lines are compute tier, provisioned IOPS and egress, driven by the **miss rate** — which
nobody has measured (§17.4.6). Everything above is the **storage and shared-project** lines and says so —
the `shared $` column is `plan $25 + compute $60` per project divided by tenants, where $60 is an **assumed**
compute line and the **tier choice itself is unmeasured**: a higher compute tier raises that column and the
total with it.

⚠️ **These are derived thresholds, not published prices.** `product/pricing.json` is the price artifact of
record and currently carries **solo = \$9**, so nothing here is live until that changes.

---

## 9. Measurement plan

⛔ **This document's M1–M5 are ITS OWN set, and are not `STORAGE-ARCHITECTURE.md` §15's M1–M9:** there M2 is
the invoice, M3 the junk share, M4 self-hosting and M5 the (now answered) location question. A bare `M4` in §1.5
means *rebuild peak* here and *self-hosting* there, so resolve every M-reference against the document that
prints it.

| # | measurement | decides |
|---|---|---|
| **M1** | vector move: recall@10 vs exact ground truth, p95, **and the shortfall rate at 1 % / 10 % / 50 % selectivity** — **measured PER LABEL over the served set, because the labels do not start from the same place:** `Point`/`Operator` were already ANN-served (the graph's one HNSW index), `Event`/`Source`/`Document` were unindexed graph-side and move to `pgvector` when the record serves them, and `Object`/`Subject` stay on `scan_fallback` until the status-fold gap closes (§ above). | whether the move ships — a recall-only test is not sufficient (§6.1), and neither is a `Point`-only one: grading the move on the label that was already indexed says nothing about the labels whose mechanism is the point of the move. The line-set that makes it decisive contains **known-terminal** entities: without them, a leg that returns rows it should hide looks identical to one that hides correctly (`#689`) |
| **M2** | **retrieval quality under a windowed vs complete graph**, on a fixed query set | the gate that ARMS §7 (post-measurement) — the literature has no answer; the result is §9.1 |
| **M3** | text ranking: a BM25-capable Postgres option vs the current `db.idx.fulltext` scores, **score distributions** + the `#8933` identifier fixture | whether the text index ever moves (§6.2) |
| **M4** | rebuild peak memory and latency per tenant at the real window size, **and whether a KEPT `db.idx.fulltext` or range index blocks a restore the way the removed vector index did (§7)** | the 6× headroom (§7), the #1 risk — and whether §7's window is a feasibility question after all |
| **M5** | the **miss rate** per tenant (a DIFFERENT M5 from `STORAGE-ARCHITECTURE.md` §15's, which asks where the vector index lives and was answered 2026-11-07) | the compute/IOPS/egress tier |

⛔ **What M1 does NOT cover, and must not be read as covering: model identity.** Neither leg compares `embedding_model`/`embedding_revision` — the dimension is the only gate on either side (`record_embedding_vector` returns NULL for anything that is not 384 long, the same semantics as the graph's `required_embedding_dim`, `#4280`), and no record table carries a model column (`grep embedding_model supabase/migrations/*.sql` → nothing). So a corpus holding vectors from two models is ranked **across two spaces** after the move exactly as it is today, with a plausible-looking ranked list either way. The move is neutral on this — it neither creates nor fixes it — but "aligned" does not include it, and M1's line-set needs at least one stale-model row or the measurement cannot see the difference at all.

⛔ **Nor does the shortfall assertion measure lag — it is snapshot-blind to it in the ordinary case.** The assertion counts what the filter selects **on the same connection and in the same transaction** as the read it checks (`record_vector.py:701-715`: `count(*)` on the same table, same cursor) — but that transaction pins no snapshot here: this read sets no isolation (`record_serve.open_record` and the `_default_connect` beneath it pin only a transaction-local `statement_timeout`), so the pair sits at the connection default — READ COMMITTED unless the DSN says otherwise — and each statement then takes a fresh snapshot, so a projection committing between the two makes the count exceed the rows and raise a spurious `VectorShortfall`. (The `#12894` guard, `record_allocator.require_read_committed`, binds the ALLOCATION path, not this read.) That is the ONE way this assertion can see lag, and it sees it as a failure rather than as lag. So a record that has *not yet applied* the journal reports `available == len(rows)` and raises nothing: the shortfall it exists to catch (§6.1) and a record that is merely behind are the same reading. Atomicity is **caller-dependent, not structural** — the projection seam spans the journal append only if the caller injects an open connection (`record_writer.RecordWriter.write`, its `conn` parameter), and `last_applied_seq` (mechanism 2 of `STORAGE-ARCHITECTURE.md` §3's divergence list) exists in `consistency.py` and is not read by this leg. So *aligned* has a **temporal axis** that no instrument here measures yet, and the correct action is to measure whether a caller leaves the pair non-atomic before adding a watermark read — not to assume it does, and not to assume it cannot.

⛔ **And do not "improve" the fallback's telemetry — its ambiguity is a decision (`#4999`).** When the record leg raises, the read falls through to the graph branches and is recorded as `scan_fallback` — **the same value** a never-served label carries. A fifth mechanism distinguishing "record leg failed" from "label was never served" looks like an obvious fix and is not: the alert surface's question is *"an index was expected and was not used"*, and splitting the value splits that question into two series, one of which fires on `:Object`/`:Subject` by design. The fallback's cost changes with the **sequencing**, not with its label: once the record serves the leg (**step 1**) it is a brute-force scan over the retained `embedding` property — latency, not correctness — and if that property goes away with the graph copy (**step 2**, a later release) the same exception returns an **empty answer with `reason=no_embeddings`, not an error**: the graph branch returns `[]` without raising (`search_engine.py:4335-4337`, `:4348-4360`), so it degrades silently unless the trace is actually read — the same "reports as a WIN" class this document exists to catch.

### 9.1 M2 — measured, and it does not arm the window

The record is `docs/evidence/12186-m2-windowed-quality/` (README + artifact + the instrument
`tools/window_quality_m2.py`). Its result is a **design input**, so it is stated here and not only there.

**The cost is exactly the eviction, and nothing compensates.** On the capture-prior population
(`:Object`, 1000 nodes, 300 fixed queries, lexical leg) `found@deep` **equals** the resident share in
**every windowed arm of both policies** — so on that population a node the window keeps is always still
findable at depth 50, and a demoted node is never
returned at any rank **of the depth-50 read** (`found_deep_of_demoted` is 0.0 in every **windowed** arm of both populations — the
`complete` arm has no demoted queries, so its 0.0 is a zero denominator and not an observation). ⚠️ The five
`tail` arms read 91.3 / 77.3 / 51.7 / 23.3 / 8.7 at coverage 0.90 / 0.75 / 0.50 / 0.25 / 0.10; the
`random` arms equal **their own** resident shares 91.7 / 76.3 / 49.7 / 25.3 / 12.0, so quote a figure
with the policy that produced it — they are not one series.
⛔ **Read every `:Object` figure below as the shape `#11901` CHOSE, not as a production rate.** That
population is a deliberately class-stratified oversample — 60 % `definite`, 30 % `ident`, 10 % `path` —
while this corpus's real mix is ~8.0 % / ~3.4 % on the two minority classes (`names_harvested`: 12,557
and 5,314 of 156,449), and `build_population`'s own docstring says the composite "is **not** an estimate
of the production mix". Nothing in the bundle weights it; it is kept because it makes M2's curve
comparable with the recall figures the epic already carries. ⛔ That equality is `:Object`-only.
On `:Point` it does **not** hold: keeping 52.7 % of claims finds only 41.0 %, because the claim leg has its
own ceiling (below) — so on claims the loss is the eviction **plus** a ranking weakness the window did not
cause, and the two must not be added into one number. (The identity **coincides** at exactly one windowed
`:Point` arm, `random@0.1`, where both are 0.11; it falls short in every other one, so it is a `:Object`
law, not a `:Point` one.) At §8's own solo target (≈77 MB against an ≈150 MB
complete graph, i.e. about half) **recall@3 falls 88.3 % → 46.0 %** and the top-1-is-a-different-node
rate **rises 27.7 % → 41.0 %** (a tie-counting rate: read its direction, not its decimal — the band
is 9.85 pp, stated in the record). On the claim population (`:Point`) `found@deep` falls **71.3 % →
41.0 %**, and a demoted claim query never comes back empty: it returns **some other claim** at rank 1.
⛔ Read that as a property of the LEG, not as a window effect — `:Point` `empty` is 0.0 in every arm
including `complete`, so the wrong-answer rate is 100 % for any demoted subset by arithmetic, and the
complete graph already reads 93.0 % `misbind@1` (the record states the same caveat in bold). It is
still the consequence that matters — a miss costs a new node, a wrong top-1 is how the duplicate the
epic exists to prevent is minted — but it is not a difference the window makes.

⚠️ **"Never returned at any rank" is a statement about this configuration, not a law.** Demotion here is
`DETACH DELETE`, and the only tier **in this read path** that could still return a demoted node at a rank
— `fallback_snapshot`'s 60 s
in-memory corpus — is invalidated explicitly by the instrument (the record still serves *anchored* reads
of a demoted node by design; what it cannot do is mint one as a candidate). So the zero is evidence for the
invalidation hook (Finding 6, `#13123`), not proof that no compensating leg could ever exist. The
stronger reading would need a run in which a demotion is performed WITHOUT that invalidation.

**No static eviction policy is a lever — and recency is UNTESTED here.** A smarter static ranking recovers
only a few percent of what the window costs: the orders differ by at most 5.0 points on any gated metric
(recall@3 46.0 vs 46.3 at 0.50; 81.0 vs 81.7 at 0.90), the cold-tail order is **ahead** on some cells
(`found_deep` 51.7 vs 49.7 at 0.50) and **behind** where the resident set is small (`recall@3` 20.3 vs 25.3
at 0.25)
— against a collapse of **42.0 to 50.3 points** at that target size depending on the order, so the
choice of static order
moves nothing that matters.
⛔ Do not read that as "the orders are indistinguishable": they are not, and "agree at every coverage"
was an error this record carried for several revisions.

⛔ **The order tested is not recency.** On this corpus the cold-tail policy is a **static popularity
count** — there is no read ledger to build a recency policy from — so §7's actual claim, that *recency*
beats static degree ranking, was **NOT re-tested by M2** and this section does not overturn it on the
retrieval axis. What the measurement bounds is the **static** axis: no static re-ranking recovers more
than a few percent of the loss. (The policy table's hit-rate result is a separate matter; recency's own effect on retrieval
quality remains open, and is M3/M5's.)

**The loss carries no signal.** `extractor_v2._fts_rows` — the capture path — requests neither
`read_status_out` nor `_truncation_out`; `TORTOISE_RESIDENCY` is off; and **no code path emits** this
section's two reason strings (`truncated_boundary`, `insufficient_graph` — specified here and in §1.5, and
nowhere in the read path). So §5's **Q2 (*"Nothing is silently invisible")** is violated for each silent
loss, and the demotion converts to a prior through `coalesce(ep_alpha, 1.0)` where a neighbour is gone —
§5's Q1.

**The leg that decides it is the text leg, and it is the one §6.2 keeps in the graph.** A
`db.idx.fulltext` index covers the nodes the graph holds; the record does not serve set-shaped reads
(`record_serve.ANCHORED_READS` marks `whole-graph` and `anchored-neighbours` as not served). §7's
seed-materialisation repair therefore cannot reach a demoted candidate: the index cannot mint an id for a
node it does not index, so there is nothing to materialise. The repair is sound only for a candidate
generator that reads the record — which the vector leg becomes when it moves (§3) and the text leg does
not.

**One mechanism a window must drive, found by this measurement:** `fallback_snapshot`'s 60 s in-memory
corpus is invalidated by `SDK._mark_dirty` and **not** by a bare Cypher `DELETE`, so a demotion performed
outside the write surfaces leaves the degraded path serving the demoted corpus — measured as recall
above the arm's own resident share. Any operator that demotes must invalidate it.

⚠️ **What §9.1 does NOT settle.** The **vector** leg is not bounded once it moves to Postgres, and this
run did not exercise it — `record_vector.ARMING_ENV` is unarmed and the artifact records
`retrieval.mode = "keyword_only"`, so every figure is the **lexical** leg's. ⛔ **The `:Point` queries
are SELF-RETRIEVAL** — the corpus carries no labelled query set, so each claim query is a contiguous
span of that claim's own content. That measures *"can the claim containing this phrase be found"*,
which is the retrieval question, and it is **NOT a measure of answer quality**: the `:Point` rates
(15.7 % / 71.3 % complete) are an UPPER bound on this leg, not end-to-end search quality. Coverage here
is a NODE share, while §8 budgets BYTES — so reading 0.50 as §8's target is an ASSUMPTION, not an
identity. And the lexical leg's own ceiling is low on claims even complete
(recall@3 15.7 %, `found@deep` 71.3 %) — the ranking matter **M3** owns, not a window effect. And the
record carries its own reproducibility bound, in the artifact: across three runs of identical input
`found@deep` moved by at most 0.33 points, `recall@k` by at most 1.0, and the tie-counting rates by at
most 9.9 — the gaps the verdict rests on run 13.3 to 48.3 points, and the two quantities the verdict turns
on (`found_deep_of_demoted == 0` in every **windowed** arm — the `complete` arm has no demoted queries, so
its 0 is a zero denominator, not an observation — and `recall@k <= resident_share`) held in every run.
⛔ **`found@deep == resident_share` does NOT hold in every arm** — that equality is `:Object`-only. On
`:Point` the lexical leg's own ceiling keeps `found@deep` AT OR BELOW the resident share in every arm:
**nine of the ten** windowed arms fall short, and the tenth (`random@0.1`) merely **coincides** at 0.11
— none exceeds it, and the complete graph is 0.7133 against 1.0. So quoting the equality as a universal
invariant is the same overclaim as the `:Point` 100 % wrong-answer figure: a property of the LEG, read as
a property of the window (Finding 2).

**Consequence for §7 — and §10's reopen trigger has FIRED.** Arming needs a decision this measurement
cannot make on its own. §10 lists *"M2 showing a windowed graph loses retrieval quality beyond an
accepted bound"* among what reopens the §7 decision, and on this record that trigger is MET: the bound
is the whole evicted share, so any bound below it is exceeded. §7 is therefore **reopened**, and if the
window is to be armed one of three things must be true — the text index moves with the
vectors, which **contradicts §6.2's decision item 2** and is therefore a **reopen** in that item's own
home argued with this evidence, never a silent adoption; or the window excludes the search-indexed labels
and is sized against §8's target on that basis; or every demoted answer is labelled with a reason and
repaired from the record, which needs a record-served set read the record does not have.

---

## 10. What would reopen this decision

The review record (four passes; verdicts and corrections) lives on `#4051`; the corrections that *changed the
decision* are stated where they bite — §1 (no ratio of our own graph is an input), §6.2 (text does not move) and
§6.3 (EP state is not stored).

**What would reopen this decision:** M2 showing a windowed graph loses retrieval quality beyond an
accepted bound; or M1 failing the filtered-recall bar; or a measured vendor/cost change that makes a
complete graph affordable.

---

## 11. Out of scope

Tier prices and overage values; the compute/IOPS/egress tier; retention (`#3853` — §7's window is
*residency*, not retention: nothing is deleted); the session cap
(`#4010`); the `record_only` tier's scope beyond `:Point` (`#10017`); **the implementation order** —
deliberately last, and **this document's** sequencing choice, not a quoted ruling: canonical docs first, so a
lane never implements against a superseded architecture. (Its shape matches the phase correction in `#4051`
comment [`6714119028`](https://github.com/daniel-ospina/tortoise/issues/4051#issuecomment-6714119028).)
