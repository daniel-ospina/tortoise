---
title: "Hosted prerequisites — what standing Postgres up as the system of record still needs"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-21
updated: 2026-10-21
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #7879, STORAGE-ARCHITECTURE.md §2.0, hosted deployments, Postgres record"
---

# Hosted prerequisites — what standing Postgres up as the system of record still needs

**Issue:** epic **#7879** (the Postgres record). **Ruling:** `docs/architecture/STORAGE-ARCHITECTURE.md`
§2.0 (owner, 2026-10-08). **Artifact type:** scoping/measurement. **No product code changes.**

**What this document is.** An **ordered checklist** of what must land before Postgres is Tortoise's
system of record *for the hosted product*, each item stating what it blocks, its size, its kind
(code / schema / ops / decision), and whether it gates the **first hosted customer** or may follow.

**What this document is NOT.** It is not a re-litigation of §2.0, and it is not a migration plan.
Two things are decided and are not reopened here: **Postgres is the record and FalkorDB is a derived
hot cache** (§2.0), and **there are no existing customers, so no migration-safety apparatus is
designed** (owner scope ruling, 2026-10-10, quoted at `04-projection.md:24`). A reader who finds an
item below implying either has found a defect in this document, not a proposal.

---

## 0. The decisions that bind this list — quoted, not paraphrased

> **§2.0 (owner, 2026-10-08) — the governing decision.**
> *"ok, so we do hybrid now, data goes to postgres and it's loaded to hot (falkor); for now we keep
> the limit in falkor high so we don't need to rush with enabling our queries to parse both falkor
> and postgres."*
>
> **§2.2 — the order.**
> *"The order is: fix the writing, land the Postgres record, then re-measure — the engine stays as
> the hot cache throughout."*
>
> **§3 — authority, and the one exception.**
> *"⛔ READ THE AUTHORITY COLUMN AS AUTHORITY, NOT AS LOCATION … Postgres is the system of record for
> EVERY layer, including the epistemic layer (`Point`, operators, `IMPL`/`NAND`, edges) and the
> entity layer (`Object`, `Subject`). ⚠️ The ONE exception is the EP SCALARS — `ep_alpha`, `ep_beta`,
> `confidence`, `posterior_*`, `baseline_*`: they are recomputed from the structure and never stored
> as authority."*
>
> **§4 — tenancy.**
> *"Decision: a shared tenancy — one Postgres database, one schema, rows scoped by tenant via
> row-level security."* … *"⚠️ Bypass rule, to state explicitly: RLS is bypassed by the table owner
> and by `service_role`. Say which role the app connects as, and why."*
>
> **§9.1 — raw text and the S1 narrative.**
> *"the narrative is not something we're suggesting to store in the graph (same as raw) but store in
> supabase."*
>
> **§12.1c — the vector question is OPEN, and it is the owner's.**
> *"⚠️ NOT DECIDED — owner's call."* (§14.3 **V1**.)
>
> **§14 — vendor.**
> *"The vendor — **Supabase Postgres** is the owner's recorded choice for the system of record (local
> default now, Supabase post-beta)."*

### 0.1 Contradiction test — run first, and its result

Three items a naive prerequisite list would contain **would each reverse a recorded decision**, so
they are excluded as prerequisites and recorded as such:

| candidate prerequisite | the decision it would contradict | verdict |
|---|---|---|
| **"serve reads from Postgres / cut over the read path"** | §2.0, verbatim: *"we keep the limit in falkor high so we don't need to rush with enabling our queries to parse both falkor and postgres."* | **Deferred by the ruling** — `#7970` is OPEN and *"DEFERRED by §2.0"*. Not a first-customer gate. |
| **"put embeddings in pgvector"** | §12.1c: *"⚠️ NOT DECIDED — owner's call."* §14.3 V1. | **A decision, not a prerequisite.** Embeddings are already durable without it (item A5). |
| **"backfill the existing FalkorDB graph into Postgres"** | Owner scope ruling 2026-10-10: *"there are no existing customers. No migration-safety apparatus is designed or claimed here."* | **Not designed, on purpose.** The dual-write already exists by construction (§3/§17.1; item R1 arms it); a backfill is not it. |

---

## A. What is already in place — and which half of it is LIVE

Measured on `8767b1c8b` (`origin/main`, 2026-10-21). Every claim below was read in the tree or run;
the runs and their output are in **§G**.

### A1. The record schema — **EXISTS, 9 tables + 3 views**, and it is the artifact §17.5 pins

| table / view | file:line | layer |
|---|---|---|
| `node_identity` | `supabase/migrations/20261012000001_record_event_stream.sql:52` | the `(label, key)` FK parent |
| `journal_event` | `…01_record_event_stream.sql:81` | 1 — event stream (append-only) |
| `source` | `…01_record_event_stream.sql:144` | 1 — event stream |
| `event` | `…01_record_event_stream.sql:217` | 1 — event stream |
| `session` | `…01_record_event_stream.sql:275` | 1 — event stream |
| `object` | `supabase/migrations/20261012000002_record_semantic.sql:21` | 2 — semantic |
| `subject` | `…02_record_semantic.sql:68` | 2 — semantic |
| `point` | `supabase/migrations/20261012000003_record_epistemic.sql:50` | 3 — epistemic |
| `relation` | `…03_record_epistemic.sql:166` | 3 — epistemic (**reified edges**). A row lands when a journaled record carries the edge — an **edge record**: `DirectEdgeCreated` (`IMPL`/`NAND` — nothing else is accepted, `sdk.py:19691`), `EntityLinked` (`about*`), and `DirectEdgeRepoint` on supersede; or, since #9553, a **node record's envelope directive**: `CONTAINS` (`record_projection._ENVELOPE_RELATIONS` emits the `relation` row after the Point's `node_identity`). Everything else is R5's work: the point-property-derived families (`extractedFrom`, `mitigated_by`, and `about*` **when no `EntityLinked` record exists** — the props copy alone lands no row), an operator's own `IMPL`/`NAND`/`INPUT` (an `OperatorAdded` snapshot is a `point`-row upsert), `references` (neither a point property nor journaled), and the structural predicates the edge lane emits no record for (`ownedBy`, `hasPart`, `dependsOn`, `wasDerivedFrom`, … — `projection/edges.py:735-745`) |
| `record_event_stream`, `object_current_status`, `subject_current_status` | `supabase/migrations/20261012000004_record_procedural.sql` | 4 — procedural (**views, no table** — `Object.status` is a cache and is computed, never stored) |

**Supersede chains are in.** `point.status` is a PRIMARY column with the lifecycle journaled;
`object`/`subject` carry `superseded_by` / `superseded_at` as PRIMARY columns, and the procedural
layer is the fold over them (`…04_record_procedural.sql` `object_current_status`).

**EP weights are correctly ABSENT.** `public.point`'s `chk_point_props_no_derived` refuses
`ep_alpha`, `ep_beta`, `c_cal`, `confidence`, `posterior_*`, `baseline_set`, `content_hash`,
`inherited_at`, `ep_dirty*` (16 keys in all — the other five are `_graph_id`, `_nid`, `lastDreamedAt`, `reason`, `version`) **by name as props keys** (`…03_record_epistemic.sql:128`); the columns themselves do not exist, so a
writer adding one meets a SQL error rather than this CHECK. **Where belief lives:** EP
confidence is computed in Python over the structure (§2.0 clause (a));
`tortoise/ep.py` is its home, and `baseline_source`/`baseline_alpha`/`baseline_beta` are the one
narrowed PRIMARY exception — an author-set prior is a non-reconstructible input (§17.5).

### A2. The journal → record projection — **EXISTS and is registry-complete**

`tortoise/record_projection.py` (1,824 lines): a declarative mutation algebra with one registry entry
per journal record type, totality as a registry property, idempotence by statement shape
(`INSERT … ON CONFLICT (org_id, graph_id, node_key) DO UPDATE`), and the property→column decision
**read from the migrations on disk** rather than restated (`RECORD_MIGRATIONS`,
`tortoise/record_projection.py:164`; `table_columns()`, `refused_props_names()`).

`PointsMerged` and `EntityMutated op=rename` are **declared refusals**, and `ConfidenceChanged` is a
**`NoRecordEffect`** — the EP carve-out in executable form (`04-projection.md` §1).

### A3. The write path — **EXISTS, WIRED, and measured working end-to-end**

| piece | file:line |
|---|---|
| the seam | `tortoise/record_writer.py:920` (`write_live_record`), `:654` (`_default_connect`) |
| arming surface | `tortoise/record_writer.py:232` — `TORTOISE_RECORD_DSN`, **default OFF** |
| production call sites | `record_writer.write_live_record` (`EventAPI._emit` — the v1 extractor, `ingest`, `docs_miner`, `mining`, CLI), `tortoise/mining.py:939`, `tortoise/sdk.py` (`_emit_event`) |
| tenant carrier | `record_writer.use_record_tenant`, installed by `tortoise/mcp_auth.py:110`; by-value on the hosted REST lane |

**Measured (§G, run 2): the SDK's public capture path wrote layers 1–3 into a real Postgres** — layer 4 is views, and
the run left `subject` 0 and `relation` 0 (A8, R4, R5), so it does **not** demonstrate edge or Subject
coverage:
36 `journal_event` · 2 `source` · 4 `event` · 2 `session` · 4 `object` · 4 `point` · 17
`node_identity`, with 4 embeddings. The seam is **fail-soft by construction** (the journal append
already committed; a failure is counted on `monitoring.metrics()["record_write_failures"]`, never
raised) and §17.1-correct (record after the append, before the cache fold).

### A4. Record → cache, and the coherence markers — **EXISTS as mechanism**

- `tortoise/cache_projection.py:608` `read_record_view` — the record's keyed view (Interface B input).
- `tortoise/projection/__init__.py:16271` `FalkorProjection.rebuild_from_record` — the record→graph
  projection, sharing the destructive-token gate and the wipe with `rebuild_all`.
- §17.2's **completeness marker** (`tortoise/residency.py`) and the **residency router**
  (`tortoise/residency_router.py`), both off by default.
- §17.2's **reconciler with a production trigger**: `tortoise/record_reconcile.py`,
  `python -m tortoise record-reconcile` (`tortoise/__main__.py:11110`), exit `0` clean / `3` divergent
  / `2` unanswerable.

### A5. Embeddings — **durable today, not searchable, and that is not a defect**

`embedding jsonb` on `source` (`…01:175`), `event` (`…01:233`), `point` (`…03:66`). **No migration and no module uses `pgvector`** — `grep -riE 'pgvector|vector\('` over all 67
migrations returns **0**, and `git grep -il pgvector -- 'tortoise/*'` is **0**. The word itself
appears in prose only — e.g. this document, §12.1c's open question, this epic's
`03-schema.md`, and the competitor notes. So the record is a **durability carrier** for vectors, not a retrieval index — which is
exactly right while §2.0 keeps the cache fully populated and **V1 is undecided** (§12.1c/§14.3).

### A6. Metering / billing — **EXISTS and is live, org-scoped**

`public.metering_records` (`supabase/migrations/0014_metering_records.sql:13`), plus
`metering_unmetered_increments` (`…20260927000001:76`), and the record-aware forward migrations
`20261001000005_metering_graph_storage.sql`, `20261013000001_metering_retrieval_columns.sql`,
`20261001000004_metering_embedding_columns.sql`, `20261015000001_postgres_storage_footprint.sql`.
Tenancy was renamed to `org_id` by `20260915000001_tenancy_team_to_org.sql`. **No new billing table
is needed for the Postgres record.**

### A7. The DB-level proof — **EXISTS and passes**

`supabase/tests/pglite/validate.mjs` replays a committed 39-record journal fixture **twice** against
the migrated schema and compares the tables byte for byte, asserting the EP scalars are absent and an
undeclared record type **fails**. Measured (§G, run 3): **`✅ ALL MIGRATIONS + BOTH TEST SUITES +
SPOT CHECKS PASSED`**, including `✓ #7969: the record projection replays 39 journal records, is
idempotent, total, and stores no EP scalar`.

### A8. What is *aspirational* rather than live — read this before trusting A1–A7

| thing that looks live | its actual state | evidence |
|---|---|---|
| **the record, in the hosted deployment** | ⛔ **NOT ARMED.** `TORTOISE_RECORD_DSN` appears **nowhere** in `fly.toml`, `entrypoint.sh`, `.github/workflows/deploy-hosted.yml`, or the runbook's Secrets Matrix. Unset ⇒ `NOT_ARMED` ⇒ *"zero statements, no connection attempt"* — the record path is inert on the deployment, so its behaviour is unchanged. | `grep -rn TORTOISE_RECORD_DSN fly.toml entrypoint.sh .github/workflows/ docs/infra-runbook.md` → empty |
| **the end-to-end proof, in CI** | ⛔ **NEVER RUNS.** No CI job sets `TEST_RECORD_DB_URI` (the test file says so itself, and `grep` over `.github/` agrees), **and** `psycopg2` is the optional `[postgres]` extra — absent from the default dev/CI env, so the two Postgres legs `importorskip` and **skip even when a Postgres is given**. | measured: run 1 → `57 passed, 2 skipped`; run 2 with `--extra postgres` → **`2 passed`** |
| **the edge / operator half of the record** | ⚠️ **`relation` = 0 rows** on the live capture measurement, and the number is a consequence of **R4**: the capture *does* write journaled `EntityLinked` records (each one a `RelationUpsert` (`record_projection`)), but their endpoints are the claim `PointAdded` rows, which that capture's `search_keys` disagreement gets refused — so the relations have nowhere to land. `04-projection.md` §3 *additionally* declares the point-property-derived edge family *"not materialized by this increment"*. | §G run 2; R4 |
| **the `session` record** | ✅ **FIXED (#9264).** `capture_redactions` was `jsonb` while the producer emits an **integer**, so the `SessionRecorded` carrying it was refused (`DatatypeMismatch`) and counted on `record_write_failures`; the module refuses to coerce (`record_projection._jsonb_or_scalar`), which is what kept it visible. The forward migration `20261022000001_record_session_capture_redactions_integer.sql` narrows the column to `integer` (every other `public.session` counter was already `integer`), and the tolerance entry for it is deleted — `_SCHEMA_DISAGREEMENTS` now carries `search_keys` only, which is the remaining half of R4. | migration `20261022000001`; `tests/test_9078_record_written_by_production_path.py` `_SCHEMA_DISAGREEMENTS = {"search_keys": …}` |
| **rebuild the graph from the record** | ⚠️ A **method with no CLI verb and no production caller**. | `FalkorProjection.rebuild_from_record` — `grep` finds no `__main__` verb |
| **RLS as the tenancy boundary** | ⚠️ **Policies exist; nothing sets the GUC.** `app.current_org_id` is set only in `supabase/tests/*.sql` — the migrations that define the policies only *read* it, via `current_setting(…, true)` — and **no Python path sets it**. Which role the record connects as is **R6's decision** and cannot be read off this census — it is the DSN's user, and no DSN is set today (`NOT_ARMED`), so no role is in force. §4's bypass rule fires for the table owner and for `service_role` (R1's arming DSN names `postgres`; the record migrations' own comment says the write path connects as `service_role`), while R6's third option — a **non-bypass** app role — would put RLS in force instead. | `grep -rn app.current_org_id tortoise/ tools/` → 0 setters |
| **the record's durability** | ⛔ **NO MODULE OF OURS BACKS UP THE RECORD.** The modules that archive today copy **the graph** — RDB/BGSAVE plus a local copy of the journal's `events.jsonl` (`tortoise/backup.py`), and the Cloudflare-R2 logical dump (`hosted_backup.py`, `backup_sweep.py`, the self-hosted sweep `selfhost_backup.py`). `docs/durability-posture.md`, the page that records durability for every deployment, has **no row for the record** (four deployments, none of them Postgres; it never mentions Supabase). Whether the Supabase project backs it up is **unverified** — the tree's statements about that are plan-level and about the **control plane**, not the record (`docs/plans/2026-08-08-669-align.md:21`, `docs/ops/registry-backup-dr.md:1051`) — so this is R9's owner decision, not a verified backup. | §3; §14.1 **O1**: *"DURABILITY = the store's backup"*; R9 |

---

## B. The ordered checklist

Sizes are single-lane estimates: **S** ≤ 1 day · **M** ≤ 1 week · **L** > 1 week.
**Gate** = `BLOCKER` (first hosted customer) or `FOLLOW` (may land after).

### R1 — Arm the record in the hosted deployment · **S** · ops + decision · **BLOCKER**

**What:** set `TORTOISE_RECORD_DSN` on `tortoise-y4mjjq` to the project's **pooler** DSN
(`postgresql://postgres.<ref>:…@aws-0-<region>.pooler.supabase.com:5432/postgres`, `?sslmode=require`
— the shape `.env.example:305` already documents for `TORTOISE_AUDIT_DSN`), add it to the runbook
Secrets Matrix and to the deploy workflow's secret-provisioning step, and **leave `TORTOISE_RECORD_SINGLE_TENANT` unset** — unset is already multi-tenant, which is
what makes the env tenant pair be refused rather than silently used
(`tortoise/record_writer.py:577`; setting `=0` only restates the default).
**What it blocks:** **everything that needs a written record.** Until this is set the record receives
nothing, so no item in this list that depends on reading one is observable in production — R2 is the
exception, because the schema lands on a dispatch whether or not R1 has happened.
**Not needed:** the driver. `Dockerfile.hosted:86` installs `'.[postgres]'`, so `psycopg2` is already
in the hosted image — checked rather than assumed, because the natural guess was that it is not.
**Decision needed:** which Supabase project hosts the record. §4 decides **one project, tenant-scoped
rows** — so this is the existing project, and *"one database per team"* is refused on cost grounds
(§4's trap).

### R2 — Land the schema on the hosted project, and keep the name gate honest · **S** · ops · **BLOCKER**

**What:** apply the migrations. `supabase-deploy` **applies** them only on `workflow_dispatch` —
`supabase db push --linked --include-all`, gated on
`github.event_name == 'workflow_dispatch'` (`.github/workflows/supabase-deploy.yml:239-243`); its
`push` trigger on `supabase/**` runs the drift checks, not the apply — so the record tables will
land on the next dispatch
whether or not R1 has happened. Two couplings to respect: the record reader is **name-gated** on
`RECORD_MIGRATIONS` (`record_projection.py:164`, no glob) so a **new** `.sql` is invisible to the
image until its name is added there; and `deploy-hosted.yml` deliberately does **not** trigger on
`supabase/migrations/**` for that reason.
**What it blocks:** any write at all; and a schema change that silently no-ops in the image.
**Decision needed:** none.

### R3 — CI proof: arm `TEST_RECORD_DB_URI` and the `[postgres]` extra · **S** · code/ops · **BLOCKER**

**What:** a Postgres service container in CI, `TEST_RECORD_DB_URI` pointed at it, and the `postgres`
extra in the test env.
**What it blocks:** every item below **regressing silently**. This is not hypothetical — it is the
mechanism by which a **new** #9264-class schema↔producer disagreement would hide: the leg that would
catch it **skips**, twice over (§G run 1 vs run 2). ⚠️ What survives there now is *one* tolerated pin, `point.search_keys` (`_SCHEMA_DISAGREEMENTS`, A8) — #9264's own entry is deleted — so arming this leg does not turn that one red; **R4** deletes the remainder.
**Cheapest item in the list, and it is the one that makes the rest verifiable.** Land it early.

### R4 — Fix the two schema↔producer disagreements (#9264) · **M** · schema or code + decision · **HALF DONE**

> **`session.capture_redactions` is FIXED** (forward migration `20261022000001`, the column narrowed to
> `integer`, tolerance entry deleted). What remains here is `point.search_keys`.

**What:** `session.capture_redactions` was `jsonb`, the producer emits an **integer** — so the `SessionRecorded` carrying
`capture_redactions` was **refused** (`DatatypeMismatch`, counted on `record_write_failures`) and the
row kept the column **NULL**; `point.search_keys` is `jsonb`, the producer emits a flattened
**string**. #9200's sibling class. Decide per field which side is authoritative and fix it;
the tolerance entries in `tests/test_9078…` (`_SCHEMA_DISAGREEMENTS`, now `search_keys` only) are
held by a **self-reddening assertion**: a **schema-side** fix fails the test until the entry is deleted, while a
**producer-side** fix leaves the entry green-but-dead — so prefer the schema side, or delete the
entry as part of that fix.
**What it blocks:** a **complete** session record (the redaction count was lost until #9264's
migration) and any Point that carries extracted `search_keys` (still refused ⇒ still lost). Both
are silent losses the journal heals, so they are invisible without R3.

### R5 — Materialise the point-property edge family into `relation` · **L** · code · **BLOCKER**

**What:** `relation` exists with real composite FKs into `node_identity`, and the registry already
handles `EntityLinked` / `DirectEdgeCreated` / `DirectEdgeRepoint`. What is **declared missing**
(`04-projection.md` §3) is the edge set **derived from point properties** — `extractedFrom`,
`about*`, `mitigated_by` — which live on the point row's props today, so a rebuild from the record
alone reproduces the claims and **not the topology**. §3 lists a fourth, `references`, which this document
narrows: ⚠️ **`references` is in neither place** — it is not a point property and it is not journaled
either (`link_source_to_entity`/`create_edge` write it bare) — and the structural edge lane emits no record for
`ownedBy`, `hasPart`, `dependsOn`, `wasDerivedFrom` and the rest of that class (`projection/edges.py:735-745`,
measured there as `edges: 0` after a rebuild; `aboutSource`/`aboutAction` ARE carried, by `EntityLinked`).
For those the fix is a **producer** — the record type, the fold and the resolver already exist as
`DirectEdgeCreated`, and nothing emits them from those doors. That half has **no live home**: `#9079` closed
on 2026-10-21 with the §17.1 write-door fix, not this one, so it is recorded on the root ([comment 6323627886](https://github.com/daniel-ospina/tortoise/issues/7879#issuecomment-6323627886)). ⚠️ **One more family is missing for its own reason, and one has been closed.** `CONTAINS` — the capture's
`contains_session` rides the **journal envelope**, so a *journal* rebuild restored the edge while a
*record*-sourced rebuild could not, because the projection dropped that key (`record_projection.py`'s
`_RECORD_ONLY_KEYS`) and emitted no `relation` row for it. ✅ **Closed by #9553 (R5 class 2):** the envelope
directive now lands a `relation` row (`_ENVELOPE_RELATIONS`, ordered after the Point's `node_identity`
because both endpoints are composite FKs into it), and `supabase/tests/pglite/validate.mjs` replays a
live-ordered pair to assert the row. The endpoint gate is SHARED rather than invented at the record
layer: a blank / NUL / lone-surrogate `contains_session` is dropped by **both** folds
(`projection._writable_container_id` ← `_link_session`, mirrored by
`record_projection._writable_endpoint_id`), and the blank case is measured — an indexed graph cannot
match a whitespace-only key (**#9599**), so the fold would mint a duplicate `:Session` and land no
edge while Postgres held the row. That harness now runs in CI on a projection-only change too (`ci.yml`'s
`pglite` gate → `schema-drill`), not only on a migration one. An
**operator's** `IMPL`/`NAND`/`INPUT` edges are still missing: `OperatorAdded` is a `point`-row upsert whose inputs sit in
`point.props` and no journaled edge record carries them, so they never reach `relation` at all.
**What it blocks:** the epic's own acceptance — *"the graph engine can be destroyed and rebuilt
without data loss"*. NAND / IMPL / mitigation topology is the thing that would be lost, and §8 makes
operator topology non-negotiable (a fused claim cannot be `NAND`ed).
**Decision needed:** none in principle; the `about*` stored-vs-derived ruling is already made (owner,
2026-09-23: *"keep them for now, we optimise with users"*) and **KEEP** is the instruction — this item
*implements* that ruling for the record, it does not revisit it.

### R6 — Enforce tenancy: state the role, set the GUC · **M** · code + decision · **BLOCKER**

**What:** every record table has `ENABLE ROW LEVEL SECURITY` + a `*_read_org` policy on
`current_setting('app.current_org_id', true)` and a `service_role` `ALL` policy
(`…01_record_event_stream.sql:115` ff. — every table, and each one `REVOKE ALL … FROM anon,
authenticated, public` + `GRANT SELECT … TO authenticated`). **No code sets that GUC** — so RLS cannot be the record's tenancy boundary today, and the boundary
is the writer's `org_id` value plus the `node_identity` FK. Which role the record connects as is **this
item's** decision (A8 states the same): R1's DSN would make it the table owner and the migrations'
comment expects `service_role`, both of which bypass, while a non-bypass app role would put RLS in
force. The `#669` research brief documents the mechanism — *"a shared non-bypass app role +
`current_setting('app.current_org_id')` GUC … gives RLS-enforced isolation for backend queries
without per-user connections"* (`docs/plans/2026-08-08-669-research.md` §2, item 1) — but its own
§4 leaves the **access path** open and recommends `service_role` plus defence-in-depth at cutover,
i.e. exactly the bypass this item exists to ask about. The mechanism is documented; the decision
is not taken.
**What it blocks:** §4's tenancy decision being **provable** for hosted. §4's bypass rule asks
exactly this question and the tree cannot answer it today.
**Decision needed:** the role the record connects as, and whether RLS is defence-in-depth or the
boundary. This is the one item whose *answer* is a policy call rather than an implementation detail.

### R7 — Raw text and the S1 narrative in Postgres (§9.1) · **L** · schema + code · **BLOCKER**

**What:** **there is no dedicated table for it** — the text is carried as data, in the `point.content`
column and in the layer-1 `journal_event.payload` mirror of the record that wrote it (see below) —
`grep -riE 'narrative|raw_turn|raw_text|transcript'` over all
67 migrations returns **0**; `public.session` carries `turn_count` and **no turns**. §9.1 is explicit
that *"the narrative is not something we're suggesting to store in the graph (same as raw) but store
in supabase"*, and §9.4 makes the **link** load-bearing (*"at answer time, send the source's VERBATIM
text, not the extracted claim"* — measured 15.9 / 22.0 pts).
**What it blocks:** §9.1's placement rule, the verbatim-evidence fetch path, **and the destroy-the-
graph acceptance itself** — the record has **no narrative-first home**: a captured turn's text
is carried as data — an episodic `point.content` row (`…03_record_epistemic.sql:56`; the §G run wrote
4) and the `journal_event.payload` that mirrors the record, plus, since `#3947` (CLOSED), the journal
entry a `rebuild_all` replays — so what a *record*-sourced rebuild cannot reproduce is the §9.1
narrative and its redaction count, not "the raw text" in the abstract. This item is not in the epic's four scope items; it is a §9.1 requirement, and that gap is
the finding (see §E).
**Decision needed:** none — §9.1 and the 2026-09-23 owner quote already place it. Only *which table
shape* is open, and that is an engineering choice inside the existing schema discipline.

### R8 — A restore path from the record: an operator verb and a drill · **M** · code + ops · **BLOCKER**

**What:** `FalkorProjection.rebuild_from_record` (`projection/__init__.py:16271`) is a method with
**no CLI verb and no production caller**; there is no runbook section and **no drill**. The repo's own
precedent is that restores fail at the *artifact* boundary, not at the API: **`#3895`** (CLOSED
COMPLETED, 2026-09-25) is the drill that restored **9,687 / 10,000** edges, and it failed because
its logical dump filtered the **node** export but not the **edge** export, so **313** edges
referenced export-skipped nodes and the artifact was unrestorable by construction — not because
the graph was the only durable store.
Also add the **`cache_reachable=False` (`cache-unreachable`)** and **`not-hot`** paths to the runbook — a declared-partial projection makes
`hits == 0` no longer establish `empty` (§17.3).
**What it blocks:** the epic's acceptance, and the ability to answer *"the graph is gone, restore it"*
at 03:00.

### R9 — Back up the record, and test the restore · **M** · ops + decision · **BLOCKER**

**What:** no module of ours backs up the record. `tortoise/backup.py` (module docstring: *"JSONL archiver +
FalkorDB BGSAVE"*), `hosted_backup.py` (Cloudflare R2), `backup_sweep.py` (the cron driver) and the self-hosted sweep `selfhost_backup.py` archive the **graph** — `backup.py` additionally copies the local journal's `events.jsonl` — and none of them backs up the record.
§3 and §14.1's **O1** record the decision: **durability = the store's backup; rebuildability = the journal** —
so promoting Postgres to the record **moves the durability obligation onto Postgres**, and no
module of ours holds it (whether the paid plan does is unverified — every statement in the tree
about it is plan-level, and A8 names two of them). Two halves — Supabase's PITR (a plan-tier fact, and therefore an owner decision on cost) versus an
independent off-box logical dump to Cloudflare R2 on the existing sweep — **plus** a drill that proves
whichever is chosen.
**What it blocks:** durability of the new system of record. This is the item most likely to be
mistaken for "already done" because the backup machinery *looks* present — it is present, for the
wrong store.
**Decision needed:** whether PITR alone is the posture, or an independent dump is required. §3 and §14.1's
**O1** do not distinguish them.

### R10 — Connection pooling on the record write path · **S/M** · ops + code · **FOLLOW**

**What:** `_default_connect` (`record_writer.py:654`) opens **one connection, one transaction per
journal record**, and the module states the cost in place: *"~one connection + commit and ~4 ms of
plan materialization"* — `table_columns()` is **deliberately uncached**, so every record re-reads the
migrations. No pool exists.
**What it blocks:** capture latency at volume. **Not a first-customer gate** — hosted capture is off
by owner ruling (2026-10-14) and the beta volume is unmeasured. **But it must land before the per-record cost of a real
write volume can be measured.** Cheapest fix is the pooler DSN plus caching the column map; batching is the larger
change and needs the ordering-across-three-doors question answered.
**Decision needed:** none to start; the batching shape needs a measurement first.

### R11 — Land the byte gate, then wire the meter, before any allowance is configured · **S/M** · code + ops/config · **FOLLOW**

**What:** the byte-unit ruling (`#4495`, **CLOSED 2026-10-19**; top of `STORAGE-ARCHITECTURE.md`) replaced the node cap, and
its own hard rule is decisive: *"The meter must be wired into the gate before any allowance is
configured"*. ⚠️ **The gate is NOT on `main`** — it lives on the unmerged
`feat/5331-node-to-byte-cap` branch, where it is **fail-closed** and raises `QuotaCheckError` when no
reading is supplied (`STORAGE-ARCHITECTURE.md:86-91` (the standing statement: the **meter** is fail-soft, the **gate** is fail-closed and raises `QuotaCheckError`) — the `STORAGE-ARCHITECTURE-CHANGELOG.md` lines at `:341-352` are the same statements kept as a build record, under that file's "Removed wording" archive), and
`grep` finds `max_storage_bytes` in **no** module here. So the item has two ordered halves:
land the gate (code, from that branch), then wire the meter into it — and only then configure
an allowance. Configuring the key today is inert rather than a 500; configuring it after the gate
lands but before the wiring would **500 every points-gated write**.
**What it blocks:** pricing/allowances. Not a first-customer gate; a gate on the **first allowance**.
**Decision needed:** prices — explicitly *not* set by this document or that ruling; the owner
calibrates after beta from measured consumption.

## C. Not items — recorded so they are not re-proposed

| # | what | why it is not on the list |
|---|---|---|
| **N1** | **The read path and its cutover** (`#7970`, **OPEN**) | §2.0 defers it in the owner's own words (§0.1). The instruments exist and are off. |
| **N2** | **`pgvector` / moving the vector index into Postgres** (`#4997`, **CLOSED 2026-10-05** — its analysis is what this item rests on; §12.1c **V1**, §14.3) | **NOT DECIDED — owner's call.** Embeddings are already durable as `jsonb` (A5). Note the settled blocker if it is decided: **`pgvectorscale` is unavailable on Supabase** (§14), so it would be `pgvector` proper. |
| **N3** | **A FalkorDB → Postgres backfill / migration-safety apparatus** | Owner scope ruling 2026-10-10: no customers, no apparatus designed. The dual-write is journal-first by construction (§3/§17.1). |
| **N4** | **Journal completeness** (`#5089`) | A **precondition owned by another epic** (this epic's *blocked-by (soft)*) — ⚠️ **and that epic CLOSED COMPLETED on 2026-10-12**, so the soft block is discharged *as a ticket*. What is not discharged is the measurement: §17.1's surface (**387 mutating sites / 211 functions / 32 files**) is quoted here, not re-verified by this lane. Named, not re-filed: a projection replayed from an unfaithful journal is faithfully wrong. |
| **N5** | **The record's failure counter has no alert** | Real (§A3: fail-soft, counted only on `monitoring.metrics()`), but **not** a gate while the cache is fully populated — a lost record is healed by replay. Becomes a gate if the cache is ever allowed to go partial. |

---

## D. Cost inputs — measured vs estimated, per figure

**Every figure carries its base, and its date wherever the measurement has one** — rows measured in
this lane carry both; figures quoted from a prior measurement carry the method and the section that
measured them, plus that section's date where it pins one. **`MEASURED` means a number in this repo with a method;
`ESTIMATED` means a number this lane could not produce.**

| input | value | basis | status |
|---|---|---|---|
| resident nodes (dogfood/prod graph) | **89,701** | `MATCH (n)`, 2026-09-25 (§17.4.2) | **MEASURED** |
| quota-counted nodes | **24,978** | the quota predicate; `§17.4.2`, 2026-09-25 | **MEASURED** |
| `GRAPH.MEMORY USAGE` | **143 MiB** (indices 46 MiB) | sampling estimate, `SAMPLES`=100; **blind to the HNSW index** (§2.1, dated 2026-09-24, carries the earlier ~140 MB reading with those caveats; the 143 figure is the top ruling block of 2026-09-25 (`STORAGE-ARCHITECTURE.md:63`, which writes it in **MB**; §17.4.2 restates it in MiB at `:1693`; §1 itself is a different section)) | **MEASURED (estimate by construction)** |
| bytes per resident node | **1,672 B** | 143 MiB ÷ 89,701 | **MEASURED** |
| in-graph text | **16 MB** | §2.2 breakdown | **MEASURED** |
| in-graph vectors + indices | **51.6 MB** stored vectors; **45 MB** of indices — the **total** index footprint, of which only the `Point.embedding` HNSW index is vector-driven (§12.1b: exactly one vector index in the graph). ⚠️ These belong to §2.2's ~141 MB reading; the ruling block gives this pair as **143 MB with 46 MB of indices** (`STORAGE-ARCHITECTURE.md:63`) and §17.4.2 restates it in MiB (`:1693`), and the instruction to use that pair sits at `:460` (**§7**'s state-value entry, where it contrasts the two readings) — so quote 143/46, not 141/45 | §2.2 / §12.1b (2026-09-24) | **MEASURED** |
| stored vectors, by label | **12,657** (`:Object` 7,859 + `:Event` 4,798, ≈19 MB) — and only `Point.embedding` is indexed | `CALL db.indexes()`, §13 | **MEASURED** |
| Points carrying any content | **49.0%**; **1,121.4 B** mean over those, **549.1 B** over all (5,696,946 B over 10,374) | `§17.4.6` / `graph-content-census.json`, 2026-10-17 | **MEASURED** |
| Points carrying a vector | **39.96%** (4,145 / 10,374) | same census; `§17.4.6`, 2026-10-17 | **MEASURED** |
| record footprint per tenant (Postgres) | **0.1916 GB** = **2,136 B/node on disk** (heap + indexes + TOAST) | bench on the committed schema; `§17.4.6`, 2026-10-17 | **MEASURED — a LOWER BOUND** (no TOAST regime; vector written at 6 dp vs the journal's real 21.9 chars/float) |
| record row width | **1,263–1,266 B** median, **1,421–1,423 B** mean | `§17.4.6`; `docs/evidence/7983-cost-magnitudes/README.md`, measured 2026-10-17 | **MEASURED** |
| keyed miss cost | **5.2 blocks** mean (4 median, 7 max), **2,312 B** on the wire, **0.048–0.053 ms** server | DETOASTED, 200 samples; `§17.4.6` / `docs/evidence/7983-cost-magnitudes/README.md`, 2026-10-17 | **MEASURED** |
| full-scope rebuild (one table) | **115,605 blocks / 947.0 MB / 2,629–2,694 ms** at 89,701 rows | DETOASTED; a per-table **floor**, not the rebuild total; `§17.4.6` / `docs/evidence/7983-cost-magnitudes/README.md`, 2026-10-17 | **MEASURED** |
| **edge count** | — | per-type populations ARE recorded — **27,310** `aboutObject`, **4,748** operator-joining, **14,567** `extractedFrom`, measured on the live graph — per-type: `EXTRACTOR-V4-ARCHITECTURE.md` §2.4.3 gives the **27,310** `aboutObject` and the **14,567** `extractedFrom`, `STORAGE-ARCHITECTURE.md` §11.3 the **4,748** joining edges — that figure's source pins **no** measurement date (2026-09-23 there is the owner-ruling date in the section heading, not a measured-on stamp); §2.4.3's two figures ARE dated 2026-09-23 (the `extractedFrom` figure also in `#4333`'s brief, 2026-09-24). ⚠️ **27,310 is the `aboutObject` population, not the edge total** — it is the same figure §11.1's `edges` column shows for "today", so a reader who takes that column as the total will sum the three and get 46,625. What is missing is the **total**: `tools/edge_census.py:224` runs the per-type `MATCH ()-[r]->()` census, but no in-tree record keeps a census of the **live** graph (the tool's probe-graph receipt is `docs/research/2026-09-25-4503-edge-accounting/measurement.md`), and the drill's **10,000** edges (`#3895`) is ONE graph's manifest count inside a real archive artifact (`node_count` 9,997 / `edge_count` 10,000), not a census and not the fleet total | ⛔ **ESTIMATED / UNMEASURED** (the total) |
| journal bytes per record | **9,176 B** per create (92% the vector's decimal text), **196 B** per revision. ⚠️ The source's headline **≈8.8 MB per 1,000 creates** does not follow from these inputs — 9,176 B × 1,000 = **9.2 MB**, and its own after-placeholder growth (8,606 B) gives **8.6 MB** — so only the order of magnitude (≈9 MB per 1,000; **8.6–9.2 GB** at 1,000,000) | `tools/journal_growth.py`; `STORAGE-ARCHITECTURE.md` §5.2, 2026-10-17 | **MEASURED** (per-record size; **the rate is not**) |
| **writes/day** | — | **hosted capture is off by owner ruling 2026-10-14**, so the hosted write rate is **zero today and no historical rate exists**. The per-record size above is the only measured input. | ⛔ **UNMEASURED** |
| **reads/day** | — | no tenant serves a workload. `tortoise/read_ledger.py` is the instrument; its one exercise (**f = 8.64%**, 200 proxy queries over 10,457 keys, one graph, one date) is explicitly *"NOT `f` FOR THE COST MODEL"* (§17.4.6) | ⛔ **UNMEASURED** |
| tenants per project (density) | — | the term that decides `ρ` — **unmeasured**; `ρ` spans **3.55 → 710** across 1,000 / 40 / 5 tenants (§17.4.6) | ⛔ **UNMEASURED** |

**⇒ Three of the five quantities a cost model needs — writes/day, reads/day, tenant density — are
unmeasured, and the reason is one fact, not three: no tenant serves a workload yet.** The two volume
figures that *are* measured are our own data at our own scale — one live dogfood read (**143 MiB**) and one
bench **planted** at that graph's node count (**0.1916 GB/tenant**, so a different population from
the live mix) — not a customer's. **No vendor recommendation is offered here** — §14 already decides Supabase
Postgres, and §17.4 already prices both stores.

---

## E. The shortest path to a hosted customer

**Order — and the reason each item sits where it does:**

1. **R1** arm the record → *nothing exists to observe until this*
2. **R2** land the schema → *R1 without R2 writes nothing, counted on `record_write_failures` and
   never raised*
3. **R3** CI proof → *so items 4–9 cannot regress silently; measured, this is how a NEW #9264-class disagreement would hide*
4. **R4** the schema↔producer disagreements (*one* left: `point.search_keys`) → *so the session/point record is complete*
5. **R6** tenancy enforced → *the bypass rule must be answered before the first hosted customer's data
exists*
6. **R5** the edge/operator family → *before the "destroy the graph" claim can be made*
7. **R7** raw text + S1 narrative in Postgres → *for the same claim: the record has no narrative-first
home — the text is carried as data (an episodic `point.content` row and its `journal_event.payload`),
so a record-sourced rebuild loses the narrative, not the text*
8. **R8** a restore verb + a drill → *a restore that has never been run is not a restore*
9. **R9** back up the record → *the durability obligation moved, and no module of ours holds it*

**What can wait:** **R10** (pooling — gated on a captured tenant write volume, not a date), **R11** (the byte gate and the
meter → allowance wiring — gates the first *allowance*, not the first customer), **N1** (the read path, by
§2.0), **N2** (`pgvector`, by open decision V1), **N5** (the failure alert, while the cache is full).

**Does this agree with the document's own order?** **Partly — and the gap is instructive.**

- **It agrees on step 2.** §2.2's *"fix the writing, land the Postgres record, then re-measure"* puts
  the record second, and every item above sits inside that step. The list does not move it earlier
  or later.
- **It says nothing about step 1.** *"Fix the writing"* is the extractor lane and **#5089**
  (N4 here). It is a **precondition this list inherits**, not one it discharges — and its ticket
  is **CLOSED COMPLETED (2026-10-12)**, so what is inherited is the residual measurement, not an
  open dependency: the record is
  faithful to the journal, and the journal is only as complete as §17.1's chokepoint.
- **⚠️ Step 3 cannot run, and no step in the document's order accounts for that.** *"Then
  re-measure"* means M2 (the invoice), M8 (`f`) and M9 (`ρ`) — and all three need a tenant serving a
  real query mix. **Hosted capture is off by owner ruling (2026-10-14)** and no self-hosted tenant
  serves a workload, so the measurement window does not exist. **The document's three steps are
  missing a fourth: turn capture on.** Until it exists, §17.4's `f = 1`-conditional arithmetic cannot
  be closed, and the split's economics remain a sweep (§17.4.3/§17.4.6).
- **⚠️ The epic's scope is narrower than the ruling's requirements.** #7879's four scope items are
  the schema, the write path, the projection and the read path. **R7 (§9.1 raw text/narrative) and R6
  (§4 tenancy enforcement) are in none of the four.** They are requirements of the same ruling, carried by
  no epic — the same failure mode that caused #7879 to be filed (*"the gap is a missing home"*).

---

## F. What this lane did NOT do

- **No external research leg.** The escalation ladder's rung 1 (`perplexity` MCP) is not installed in this
  harness's MCP configuration, and this lane ran no external search. **The external leg was not
  performed**, and nothing below pretends to a vendor-sourced fact. Everything in §D comes from this
  repo. The questions the external leg would have asked, left unasked and unanswered: Supabase's
  pooler connection limits and their interaction with session-level `SET` (R6 makes the GUC
  session-scoped, and a transaction-mode pooler is where that breaks), PITR retention by plan tier
  (R9), and `pgvector` index-build behaviour at our size (**only if V1 is decided**).
- **No product code changed.** No migration, no module, no test. The **three runs in §G** (two pytest runs plus the
  PGlite validation) were run and reported; none of their files was modified.
- **No issue filed.** An item that names an issue **as its home** gives that issue's state and its
  closing artifact where one exists (a bare citation — a brief, a decision record — need not); an
  item that names no home is a candidate on this list rather than a peer. A CLOSED home is named only where its closing artifact is the evidence the item rests on
  (N2 on #4997's analysis, N4 on #5089's journal work) — never because the item is believed fixed.
  Per the standing rule, a checklist item with no home gets a comment on the root (**#7879**)
  rather than a peer issue; R5's is [comment 6323627886](https://github.com/daniel-ospina/tortoise/issues/7879#issuecomment-6323627886).
- **No measurement of the live dogfood graph.** Every live figure in §D is quoted from a prior
  measurement, with its base and — for the rows that have one — its date, because this lane has no access to the production FalkorDB.

---

## G. The runs behind §A (reproducible)

**Environment:** branch `docs/postgres-prerequisites`, based on `8767b1c8b` (then `origin/main`);
`postgres:16-alpine` on `127.0.0.1:15499`; `TORTOISE_TEST_CARVE_OUT=1` (the P4 carve-out — a
URI-less run refuses by design).

**Run 1 — the record suite, default env (the CI-shaped run):**

```text
uv run pytest tests/test_record_schema_placement.py tests/test_record_projection.py -q
→ 118 passed in 4.57s

uv run pytest tests/test_9078_record_written_by_production_path.py -q   # TEST_RECORD_DB_URI set
→ 57 passed, 2 skipped in 187.36s
```

⚠️ **The 2 skips are the two Postgres legs, and they skipped with a database available** — `psycopg2`
is not in the default env (`ModuleNotFoundError`), so `pytest.importorskip("psycopg2")` fires. This is
R3.

**Run 2 — the same suite with the `[postgres]` extra:**

```text
uv run --extra postgres pytest tests/test_9078_record_written_by_production_path.py::test_the_public_capture_path_writes_the_record \
    …::test_the_public_capture_path_writes_the_epistemic_layer -q
→ 2 passed in 146.75s
```

Row counts in the record afterwards (§A3, §A8):

```text
journal_event 36 · source 2 · event 4 · session 2 · object 4 · subject 0 · point 4
relation 0 · node_identity 17 · points with embedding 4 · points with content 4
session.capture_redactions → NULL on both rows            ← R4 / #9264, PRE-FIX measure
point.embedding type → jsonb                              ← A5 (no pgvector)
```

⚠️ The `capture_redactions` row above is what that run **measured**, kept as measured. After
\#9264's forward migration the column is `integer`, so a re-run writes the count instead of NULL.

**Run 3 — the DB-level proof on real Postgres (PGlite):**

```text
cd supabase/tests/pglite && npm install && npm run validate
→ ✅ ALL MIGRATIONS + BOTH TEST SUITES + SPOT CHECKS PASSED
  ✓ #7969: the record projection replays 39 journal records, is idempotent, total,
    and stores no EP scalar
```

**Census commands (the "not in place" claims):**

```text
grep -rn TORTOISE_RECORD_DSN fly.toml entrypoint.sh .github/workflows/ \
  docs/infra-runbook.md                                                       → (no output)
grep -riE 'pgvector|vector\(' supabase/migrations/*.sql | wc -l              → 0
grep -riE 'narrative|raw_turn|raw_text|transcript' supabase/migrations/*.sql → 0
grep -rn 'app.current_org_id' --include=*.py tortoise/ tools/                → 0 setters
grep -rn 'rebuild_from_record' tortoise/__main__.py                          → 0
grep -rn 'rebuild_from_record(' tortoise/ | grep -v 'def '                  → 0 call sites
grep -rniE '\bpostgres\b|psycopg|pg_dump' tortoise/backup.py tortoise/hosted_backup.py \
  tortoise/backup_sweep.py tortoise/selfhost_backup.py                            → 0
```
