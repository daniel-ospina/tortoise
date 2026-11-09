---
title: "Hosted prerequisites — what standing Postgres up as the system of record still needs"
type: plan
domain: engineering
doc_status: draft
created: 2026-10-21
updated: 2026-10-23
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "epic #7879, issue #9553, STORAGE-ARCHITECTURE.md §2.0, hosted deployments, Postgres record"
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
> **§12.1c — the vector question is DECIDED (2026-11-07), not open.**
> *"✅ DECIDED 2026-11-07 — the vector index does NOT live in FalkorDB. Vectors and their payload move to
> Postgres (`pgvector`); traversal, writes and EP stay in the graph."* (parenthetical elided: `#4997`;
> `docs/architecture/SEARCH-ARCHITECTURE.md`; §14.3 **V1**.) ⚠️ The `⚠️ NOT DECIDED — owner's call` wording this block used
> to quote is **SUPERSEDED, kept as the record** — it is what the question said *before* the ruling, and a lane
> quoting it today would conclude the opposite of the decision.
>
> **§14 — vendor.**
> *"The vendor — **Supabase Postgres** is the owner's recorded choice for the system of record (local
> default now, Supabase post-beta)."*

### 0.1 Contradiction test — run first, and its result

Two of the three items a naive prerequisite list would contain **would each reverse a recorded decision**, and the
third was **a decision rather than a prerequisite** — which §12.1c now ADOPTS (2026-11-07). All three are therefore
excluded as prerequisites, and recorded as such:

| candidate prerequisite | the decision it would contradict | verdict |
|---|---|---|
| **"serve reads from Postgres / cut over the read path"** | §2.0, verbatim: *"we keep the limit in falkor high so we don't need to rush with enabling our queries to parse both falkor and postgres."* | **Deferred by the ruling** — `#7970` is **CLOSED COMPLETED (2026-10-25)** and its *CUTOVER* is what stays *"DEFERRED by §2.0"*. Not a first-customer gate. |
| **"put embeddings in pgvector"** | §12.1c **ADOPTS it** (decided 2026-11-07: *"the vector index does NOT live in FalkorDB"*) — so there is no contradiction left; what remains is **sequencing**, not a reversed decision. | **A decision, not a prerequisite — and now DECIDED, in favour.** It is off this epic's critical path (N2), and embeddings are already durable without it (item A5). |
| **"backfill the existing FalkorDB graph into Postgres"** | Owner scope ruling 2026-10-10: *"there are no existing customers. No migration-safety apparatus is designed or claimed here."* | **Not designed, on purpose.** The dual-write already exists by construction (§3/§17.1; item R1 arms it); a backfill is not it. |

---

## A. What is already in place — and which half of it is LIVE

Measured on `8767b1c8b` (`origin/main`, 2026-10-21). Every claim below was read in the tree or run;
the runs and their output are in **§G**.

### A1. The record schema — **EXISTS, 10 tables + 3 views**, and it is the artifact §17.5 pins

> **⚖️ Re-derived 2026-10-22 (#9555).** The `Measured on 8767b1c8b` stamp above scopes the
> measurements; the census in this heading and the row set below were re-read at this head, which is
> why the count is **10** and the tenth row (`session_narrative`) sits after `relation`. Both halves
> are now pinned against the migrations on disk — `tests/test_record_schema_placement.py`,
> `::test_the_prerequisites_a1_census_matches_the_record_migrations` — so neither the count nor a
> missing row can go stale silently again (the same defect the `docs/00_index.md` pin catches one
> artefact over).

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
| `relation` | `…03_record_epistemic.sql:166` | 3 — epistemic (**reified edges**). A row lands when a journaled record carries the edge — an **edge record**: `DirectEdgeCreated` — `create_direct_edge` writes `IMPL`/`NAND` with it, and since #9553 the structural-edge lane writes every predicate it can mint through it, declining only a write whose ENDPOINT is a `:Source` (the canonical shape of `aboutDocument`/`aboutSource` — see below) — `EntityLinked` (`about*`), and `DirectEdgeRepoint` on supersede; or, since #9553, a **node record's own carrier**: `CONTAINS` (the capture's ENVELOPE directive, `record_projection._ENVELOPE_RELATIONS`), `extractedFrom` (the Point's PROPERTY, `record_projection._POINT_PROPERTY_RELATIONS` — declared for all four of the fold's point-materialisation types, because `apply()` re-derives it on its promote arms too and the record follows `apply()`, with the #5256 anchor in `relation.source_version`, written ON CREATE only, so the conflict path never touches the column), and the OPERATOR topology the descriptor carries — `IMPL`/`NAND`/`hasPart`, its reverse `INPUT`, and the canonical `mitigated_by` mirror (`record_projection._OPERATOR_RELATION_RECORDS`, endpoints resolved across the four epistemic labels) — all emitted after the Point's `node_identity`, because the endpoints are composite FKs into it. ✅ **Class 4 closed by #9553**: the edge lane's two mints now JOURNAL a flat `DirectEdgeCreated` (`{src, tgt, edge_type, source_label, target_label}` — labels are additive for the record and ignored by pass-2b, which resolves them itself), so every predicate the lane can mint lands a `relation` row when its endpoints are addressable (`RECORD_CARRIED_STRUCTURAL_RELS` is the whole lane write set, 23 predicates; PGlite leg in `supabase/tests/pglite/validate.mjs`). ⚠️ TWO qualifications, both measured: (a) a write whose endpoint resolves to a `:Source` is DECLINED at the mint and REPORTED through `projection._log_identity_skip` (`drops_record=True`, so it is COUNTED, not just logged) — `SOURCE_ENDPOINT_CANONICAL_RELS` names the two whose CANONICAL target is a `:Source` (`aboutDocument`, `aboutSource`), and that shape is a FOLD limitation (`EPISTEMIC_LABELS`/`p.id`), tracked in #9834; the predicates themselves are carried, and both ARE journaled between epistemic endpoints (measured); (b) after a SUPERSEDE the routes diverge — see the supersede paragraph in §R5's class-4 block, and #9836. `TAGGED` has no RECORD carrier (no record `tag` table for `relation.tgt_key`'s composite FK) although a JOURNAL rebuild restores it from the Point's `tags` property (measured) — and it is SILENT: `_sync_tags` reaches neither the lane nor this producer and warns nothing, so it is named here and in §R5 rather than warned at the write |
| `session_narrative` | `supabase/migrations/20261023000001_record_session_narrative.sql:122` | — **NOT a node table** (no `node_identity` FK): §9.1's verbatim-narrative home, **#9555**/R7. Keyed `(org_id, graph_id, session_id)`, cascading from **`public.graphs`** — not from `public.session`, see R7's DECIDED block for why, and for what a live capture does and does not carry into it today (no journal carrier for `story_arc` yet ⇒ a `NULL` `narrative`) |
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

`tortoise/record_projection.py`: a declarative mutation algebra with one registry entry
per journal record type, totality as a registry property, idempotence by statement shape
(`INSERT … ON CONFLICT (org_id, graph_id, node_key) DO UPDATE`), and the property→column decision
**read from the migrations on disk** rather than restated (`RECORD_MIGRATIONS` in
`tortoise/record_projection.py`; `table_columns()`, `refused_props_names()`).

`EntityMutated op=rename` is the remaining **declared refusal** (`PointsMerged` was one until
`#9892`: it projects now — one hard delete per loser, the graph fold's own `DETACH DELETE`),
and `ConfidenceChanged` is a **`NoRecordEffect`** — the EP carve-out in executable form
(`04-projection.md` §1).

### A3. The write path — **EXISTS, WIRED, and measured working end-to-end**

| piece | where — cited by SYMBOL, never by line |
|---|---|
| the seam | `tortoise/record_writer.py` — `write_live_record`, `_default_connect` |
| arming surface | `tortoise/record_writer.py` — `RECORD_DSN_ENV` (`TORTOISE_RECORD_DSN`), **default OFF** |
| production call sites | `record_writer.write_live_record` (`EventAPI._emit` — the v1 extractor, `ingest`, `docs_miner`, `mining`, CLI), `tortoise/mining.py` (`_emit_event`), `tortoise/sdk.py` (`_emit_event`) |
| tenant carrier | `record_writer.use_record_tenant`, installed by `tortoise/mcp_auth.py`; by-value on the hosted REST lane |

⛔ **Symbols, not line numbers, on purpose.** Every number this table used to carry had drifted onto
unrelated text — a bare `)`, a `contextvars` line, a tuple member, a blank line — because a line
number in a living module is a claim that decays on the next merge. A symbol cannot re-stale. That is
the rule `docs/00_index.md` records for `sdk.py`, applied here where it was still missing.

**Measured (§G, run 2): the SDK's public capture path wrote layers 1–3 into a real Postgres** — layer 4 is views, and
the run left `subject` 0 and `relation` 0 (A8, R4, R5), so it does **not** demonstrate edge or Subject
coverage:
36 `journal_event` · 2 `source` · 4 `event` · 2 `session` · 4 `object` · 4 `point` · 17
`node_identity`, with 4 embeddings. The seam is **fail-soft by construction** (the journal append
already committed; a failure is counted on `monitoring.metrics()["record_write_failures"]`, never
raised) and §17.1-correct (record after the append, before the cache fold).

### A4. Record → cache, and the coherence markers — **EXISTS as mechanism**

- `tortoise/cache_projection.py:608` `read_record_view` — the record's keyed view (Interface B input).
- `tortoise/projection/__init__.py` (`FalkorProjection.rebuild_from_record`; the verb that calls it is `tortoise/record_restore.py`) — the record→graph
  projection, sharing the destructive-token gate and the wipe with `rebuild_all`.
- §17.2's **completeness marker** (`tortoise/residency.py`) and the **residency router**
  (`tortoise/residency_router.py`), both off by default.
- §17.2's **reconciler with a production trigger**: `tortoise/record_reconcile.py`,
  `python -m tortoise record-reconcile` (`tortoise/__main__.py:11110`), exit `0` clean / `3` divergent
  / `2` unanswerable.

### A5. Embeddings — **durable today, not searchable, and that is not a defect**

`embedding jsonb` on `source` (`…01:175`), `event` (`…01:233`), `point` (`…03:66`). ⛔ **THIS PARAGRAPH WAS TRUE UNTIL `#11642` AND IS KEPT AS THE MEASURED RECORD OF WHAT THE MOVE REPLACED — do not read it as the state of this head.** At the census point **no migration and no module used `pgvector`**. **At this head** the same census is: `grep -riE 'pgvector|vector\('` over all 81
migrations returns **29** (it returned **0** at that pin), all 29 in ONE file — `20261109000001_record_embedding_vector.sql` (the `CREATE EXTENSION`, the `record_embedding_vector` converter, three `vector(384)` GENERATED columns and three HNSW indexes) — and `git grep -il pgvector -- 'tortoise/*'` is **4**, in `record_projection.py`, `projection/__init__.py`, `record_vector.py` and `search_engine.py` (it was **0**). `#11642` implemented V1. The word itself
appears in prose only — e.g. this document, §12.1c's decided record, this epic's
`03-schema.md`, and the competitor notes. So the record is a **durability carrier** for vectors, not a retrieval index — which was
exactly right while §2.0 kept the cache fully populated, and **V1 is now DECIDED** (§12.1c/§14.3, 2026-11-07):
the retrieval index and the embedding payload move to Postgres, so this row's *reason for being a carrier only*
is gone — the record becomes the home of the index, not just of the bytes, **once the migration lands**.

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
| **the record, in the hosted deployment** | ✅ **ARMED — AND THE ACT IS AUTHORISED (owner, 2026-11-03).** *At the census point below* it was `NOT_ARMED`, and the 2026-11-01 ruling that then forbade provisioning is **SUPERSEDED** (quoted at R1 below, relabelled — kept as the record of what was overridden, not as an instruction). ⚠️ **The census-point statements in this file — and the live-state sentences in `09-record-tenancy-decision.md` — describe the tree that was, not the deployment now.** Read any statement here that does not carry its own measurement date that way; **this list is NOT exhaustive**, and an enumeration that claims completeness makes everything unlisted read as vetted-current (that is how the last stale sentence survived a sweep scoped to "the files this PR touched"). The sites since labelled and dated **in place**: the RLS row *in A8* ("`NOLOGIN` until ops grants LOGIN"), the §G paragraph that said the name was staged and the secret absent, **R6**'s read-back note, **R7** item 2, and **R8**'s drill attribution. `GET /health` re-measured **2026-11-05T13:11:13Z** reports `"record":{"armed":true,"ok":true,…,"record_write_failures":{},"record_non_journal_skips":{},"record_position_unproven":0}` on build `fc6a8a64b` / `migrations_head: 20261030000001_oauth_consent_tickets` — so *"the remaining half is the secret itself"* is **no longer outstanding**. ⚠️ **Read that emptiness for exactly what it is:** it falsifies *"writes are failing"* (the `write_failed: 32` this cell used to quote is a 2026-11-02 reading) but it **cannot establish that a row was written** — the runbook's own rule, *"`record_write_failures: {}` is NOT evidence that a row was written"* — and it does **not** attribute the drop's fix (the dropped rows recorded on `#9548` — 302 at 2026-11-02, 331 in the 2026-11-03 decision — remain unreaped). ⚠️ **The build this cell used to name is SUPERSEDED:** it read *"this build predates `#10648` / `fafb8fdcc` and does not carry it"* on build `fc6a8a64b` / `migrations_head: 20261030000001_oauth_consent_tickets` — true until the deploy at **2026-11-05T23:14:03Z**. Re-read **2026-11-05T23:54:47Z**: `build.commit_sha` = **`46ea687dc`**, `migrations_head` = **`20261102000002_record_point_search_keys_text`**, and `git merge-base --is-ancestor fafb8fdcc 46ea687dc` exits **0** — so the deployed code **does** carry `#10648`. ⛔ **`migrations_head` is the highest migration the IMAGE CARRIES, not the applied schema** (the runbook's own `migrations_head` row); whether `20261102000002` is applied is a database fact read from `supabase_migrations.schema_migrations` — and it **WAS measured, 2026-11-05T23:07:38Z**: the repo's fail-closed drift gate (run `42117073101`, the deploy that shipped `46ea687dc`) read that ledger in prod and printed `OK (no blocking drift)` with no warn-only and no remote-ahead note, so **both `20261102000001` and `20261102000002` are RECORDED APPLIED**. ⛔ The gate reads **version ROWS only** — its own note, *"`supabase migration list --linked` — version ROWS only, so it cannot tell you whether the objects exist"* — so the **OBJECTS remain unread**: recorded is not proven-applied. The full reading, its corroboration and the run that may have recorded them are in the runbook, §"Arming the Postgres record". Re-read `/health` before reusing any of those values — a build sha and a migrations head are measurements, not standing facts. `TORTOISE_RECORD_DSN` appeared **nowhere** in `fly.toml`, `entrypoint.sh` or `.github/workflows/deploy-hosted.yml`; the runbook **documented** it (its Secrets Matrix row and the "connecting role" example, #9600) and never provisioned it — documented, not provisioned. Unset ⇒ `NOT_ARMED` ⇒ *"zero statements, no connection attempt"* — so the record path **was** inert at the census point and its behaviour **was** unchanged then. **#9548 (2026-10-23) landed the arming surfaces** (deploy stages it from its own GitHub secret, manifest declares it, runbook + `fly.toml` document it) — at the census tree the remaining half was the secret itself; it is now set and the app is armed (see the opening of this cell), so that clause is history, not the live state. ⚠️ Any re-arming (authorised since 2026-11-03, and already done — see the opening of this cell) must name R6's application role (`tortoise_record_app`, `NOBYPASSRLS`) — never `postgres`/`service_role`, which bypass RLS and would make the tenancy boundary false for the only path that writes customer data ([`09-record-tenancy-decision.md`](09-record-tenancy-decision.md)). | `grep -rn TORTOISE_RECORD_DSN fly.toml entrypoint.sh .github/workflows/ docs/infra-runbook.md` → **5 lines at `ff6379eb2`** — an ancestor of this branch's base (the #9601 merge, 2026-10-23), and **NOT** the tree immediately before #9548 (that is `047c91f7a`, the arming commit's parent) — **not empty, and not one: 10** at `047c91f7a`, the tree that answers this question. The five hits at `ff6379eb2` are `docs/infra-runbook.md:382` and `:528` (#9557/R9's `record-backup` documentation), `:2774` (#9556/R8's §9 `record-restore` operator example — an **invocation**), and `:2971`/`:3004` (#9600's Secrets Matrix row and its §"The record's connecting role" example — the name **documented**, not provisioned). ⚠️ **The count is per tree:** **0** at this branch's start point `d5c71a470`, **1** at `473ff8a6f0`/`7761252f` (after #9556/R8, before #9600), **3** at `c5cbcbb4f` (#9600/#9554), **5** at `ff6379eb2`, **10** at `047c91f7a` (the arming commit's parent) — see the supersession note under the census commands, which is the one place the series is maintained. Line numbers are `ff6379eb2`'s and drift as the runbook grows. No hit in `fly.toml`, `entrypoint.sh` or `.github/workflows/`. |
| **the end-to-end proof, in CI** | ✅ **ARMED by #9551** (PR #9772). The dedicated `test-record-postgres` job provisions a throwaway `postgres:16` on an ephemeral loopback port (`.github/actions/postgres-provision`), sets `TEST_RECORD_DB_URI`, installs the `[postgres]` extra and asserts **zero** skips from the junit XML — so the two Postgres legs run instead of skipping, and an armed-but-skipping job cannot read as green. It is a REQUIRED leg of `python-ci-gate`. | measured on the arming tree (the collection is **70** — 61 record legs + the 9 CI-leg tests this change adds): unarmed → `68 passed, 2 skipped`; armed → **`70 passed, 0 skipped`**, the record migrations applied inside that run |
| **the edge / operator half of the record** | ⚠️ **`relation` = 0 rows** on the live capture measurement, and the number is a consequence of **R4**: the capture *does* write journaled `EntityLinked` records (each one a `RelationUpsert` (`record_projection`)), but their endpoints are the claim `PointAdded` rows, which that capture's `search_keys` disagreement gets refused — so the relations have nowhere to land. `04-projection.md` §3 *additionally* declares the point-property-derived edge family *"not materialized by this increment"*. | §G run 2; R4 |
| **the `session` record** | ✅ **FIXED (#9264).** `capture_redactions` was `jsonb` while the producer emits an **integer**, so the `SessionRecorded` carrying it was refused (`DatatypeMismatch`) and counted on `record_write_failures`; the module refuses to coerce (`record_projection._jsonb_or_scalar`), which is what kept it visible. The forward migration `20261022000001_record_session_capture_redactions_integer.sql` narrows the column to `integer` (every other `public.session` counter was already `integer`), and the tolerance entry for it is deleted — and #10620 narrowed `point.search_keys` to `text` — the `_SCHEMA_DISAGREEMENTS` vocabulary AND its detector were DELETED in #10620, so no allowance remains (both halves of R4 fixed). | migrations `20261022000001`, `20261102000002`; `tests/test_9078_record_written_by_production_path.py` (the vocabulary and its detector are DELETED, #10620) |
| **rebuild the graph from the record** | ✅ **The operator half exists now** — `python -m tortoise record-restore` (`tortoise/record_restore.py`, #9556/R8), with `docs/infra-runbook.md` §9 and its drill table (the drill's *armed-record* row is still **NOT MEASURED**, #9548/#9551). ⛔ **The production door is still blocked**: the wipe runs #2944's L2 name guard and the disposable-graph registry that would admit `org_*` graphs is not landed, so today only a `test_`-named graph can be restored. | `grep -rn 'rebuild_from_record' tortoise/__main__.py` → 1 (the verb's docstring); `grep -rn 'rebuild_from_record(' tortoise/ \| grep -v 'def '` → 1 (`record_restore.py`, the verb's caller) |
| **RLS as the tenancy boundary** | ✅ **SHIPPED by R6 (#9554) — the GUC is set by the shipped code, and the connecting role is the non-bypass one.** `record_projection.scope_connection()` is the single prologue both record doors install *before their first plan statement* — the write door (`replay()`) and the cache read door (`cache_projection.read_record_view()`) — and it (a) sets `set_config('app.current_org_id', $1, true)` transaction-locally, then (b) on ONE statement reads that GUC back AND reads the connecting role's posture `pg_roles`/`pg_has_role` — **refusing** a superuser, `BYPASSRLS`, a member of either, or the owner of a record table (`RecordProjectionError: … BYPASSES RLS`), and refusing a GUC that did not take. The role is `tortoise_record_app` (`NOBYPASSRLS NOSUPERUSER`), created by `20261025000001_record_tenancy_role_and_guc.sql`, which ships it `NOLOGIN` — and ⛔ **that sentence used to read "it is `NOLOGIN` until ops grants LOGIN + a password … no production traffic exercises the boundary": measured false since 2026-10-26.** The grant WAS made out of band and the credential with it (`#9548` comment `6407834085`: `tortoise_record_app.<ref>` as `LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE`, verified by connecting as the role), so the boundary **is** exercised by production writes — though ⛔ **not because of the 302 `22P02` failures**, which this cell used to cite: an invalid-JSON cast fires for any **non-bypassing** role — `scope_connection()` refuses `postgres`/`service_role` before the first statement, so those never reach the cast — so it proves the role PASSED the posture gate and an `INSERT` was attempted, never WHICH non-bypassing role made it (the role proof is `6407834085`'s connect-as-the-role). ⚠️ **What remains unproven is the proof, not the posture:** no `journal_event` + `point` row has been shown to land (an empty `record_write_failures` cannot stand in for one), and the 302/331 dropped rows are unreaped. ⛔ **Do not re-run the grant to "finish" it:** `\password tortoise_record_app` ROTATES the live credential the deployed DSN names and needs the GitHub **and** Fly secrets re-set in the same change. The old reading below is kept because it is what the census said before R6: §4's bypass rule fires for the table owner and for `service_role` (the record migrations' own comment still says the write path connects as `service_role`, which R6 superseded). | `grep -n 'def scope_connection' tortoise/record_projection.py` → 1; `grep -rn 'set_config(' tortoise/record_projection.py` → the `TENANT_GUC_SQL` prologue; pins `tests/test_record_tenancy_guc.py` + PGlite `supabase/tests/20261022000001_record_tenancy_posture.sql` |
| **the record's durability** | ✅ **R9's mechanism is on main — the record now HAS a backup module.** `tortoise/record_backup.py` and its one trigger, `python -m tortoise record-backup`: `pg_dump --data-only` over the record's ten tables → AES-256-GCM → R2 under `record-backups/`, with a restore **drill** that compares per-table counts. Documented in `docs/infra-runbook.md` §3.5, with a row of its own in `docs/durability-posture.md`. The graph archives (`tortoise/backup.py` — RDB/BGSAVE plus a local copy of the journal's `events.jsonl` — `hosted_backup.py`, `backup_sweep.py`, the self-hosted sweep `selfhost_backup.py`) still copy **the graph**, and are not this. ⚠️ **Two gaps remain, and neither is a missing module**: no cron invokes the verb yet (the window is the operator's manual cadence — `docs/durability-posture.md` states it as UNBOUNDED), and the drill has not been run against the **hosted** Supabase record, so *"the artifact restores, verified per table"* is measured while *"the hosted record has been restored"* is not. ⚠️ It reads the **same** `TORTOISE_RECORD_DSN` and **refuses a pooler DSN** (`record_backup.require_direct_dsn`, exit 2): a dump/restore session needs the **direct** connection (`db.<ref>.supabase.co:5432`), so it is invoked with `--record-dsn <direct DSN>` — the app's pooler value is not usable here. Whether the Supabase plan's own PITR also covers the record stays an owner question; the tree's statements about it are plan-level and about the **control plane** (`docs/plans/2026-08-08-669-align.md:21`, `docs/ops/registry-backup-dr.md:1051`). §R9's checklist item below was written **pre-landing** and still reads as unbuilt — its mechanism half is what landed. | `grep -n 'def require_direct_dsn' tortoise/record_backup.py` → 1; runbook §3.5; `docs/durability-posture.md` (the record row); §14.1 **O1**: *"DURABILITY = the store's backup"*; R9 |

---

## B. The ordered checklist

Sizes are single-lane estimates: **S** ≤ 1 day · **M** ≤ 1 week · **L** > 1 week.
**Gate** = `BLOCKER` (first hosted customer) or `FOLLOW` (may land after).

### R1 — Arm the record in the hosted deployment · **S** · ops + decision · **BLOCKER**

> ✅ **AUTHORISED (owner, 2026-11-03) — AND THE ARMING HALF IS ALREADY DONE. READ THIS BOX BEFORE THE
> SUPERSEDED QUOTE UNDER IT.** On **2026-11-03T19:06:10Z** the owner **superseded** the 2026-11-01
> postponement **for this issue**, in a comment on `#9548` headed
> `## DECISION (owner, 2026-11-03) — no customers on the hosted deployment; arming it is authorised`
> ([comment](https://github.com/daniel-ospina/tortoise/issues/9548#issuecomment-6612968196)), which
> carries its own marker:
>
> > **OVERRIDES:** the standing "hosted postponed" ruling — superseded for this issue by the owner's
> > explicit clarification that no customer is on the service and that the beta is blocked on P0/P1 closure.
>
> So R1 **is** work to pick up, and the runbook's
> [Arming the Postgres record](../../infra-runbook.md#arming-the-postgres-record--tortoise_record_dsn-epic-7879-r1-9548)
> — its AUTHORISED box and its provisioning block — is the live procedure.
> **What is left is not the arming.** The secret is set on both the GitHub and Fly surfaces and the app
> has reported `armed: true` since at least **2026-10-26** (measured on `#9548`, comment `6407834085`);
> `GET /health` re-measured **2026-11-05T13:11:13Z** reports `armed: true` with
> `record_write_failures` **EMPTY**. ⚠️ That emptiness **falsifies "writes are failing"** (the
> `write_failed: 32` this item used to quote is a 2026-11-02 reading) and **cannot establish that a row
> was written** — see the runbook's own rule, *"`record_write_failures: {}` is NOT evidence that a row
> was written"*, and its capture-verification step. ⚠️ **The live blocker is a missing CREDENTIAL, not a
> ruling:** the 2026-11-03 decision's non-negotiable **first** step is a verified backup, and
> `TORTOISE_RECORD_BACKUP_DSN` / `TORTOISE_RECORD_SCRATCH_DSN` are absent, and the DSNs this deployment
> does provision are **DOCUMENTED to have the shape the dump's FIRST gate refuses**: `create_record_backup`
> runs `require_direct_dsn` **before** `require_bypassing_role`, and the shape prescribed for both is a
> pooler (`.env.example`'s HOSTED block for `TORTOISE_AUDIT_DSN`; the app's pooler DSN for
> `TORTOISE_RECORD_DSN`), which gate 1 refuses — so gate 2 is never reached. ⚠️ **That is a documentation
> INFERENCE about a SHAPE, not a measurement of a value:** no lane in this fleet has read either value
> (`#9548` comment `6613140019` records that its box held none at all). If a held value does not follow its
> documented shape the inference is simply **false** — and then the backup is possible today with a
> credential already held. Read the value before treating this blocker as settled; the runbook's credential
> table states the same bound. ⚠️ **Do not go hunting for a brand-new ROLE** — a bypassing role
> already exists; what is missing is a DIRECT connection string for one. The runbook's Secrets-Matrix section
> carries the same correction as a table (row-by-row, gate-by-gate). The accurate state is **`AUTHORISED-BUT-BLOCKED-CRED`, not postponed**; a lane
> reading this item must report the missing backup credential on `#11193`, never re-derive "postponed".
> ⛔ **That pointer used to name `#9548`, which was CLOSED `COMPLETED` on 2026-11-06T01:57:25Z** — the
> arming it names did land, so the residual blocker had no open home and a lane following this sentence
> reported into a closed issue. `#11193` is the provisioning act's issue (`TORTOISE_RECORD_BACKUP_DSN`).
>
> ---
>
> ⛔ **SUPERSEDED — kept as the record of the ruling that was overridden, not as an instruction.**
> What follows is the 2026-11-01 postponement **as it stood**: `#9548`'s `Owner decision` comment
> ([verbatim](https://github.com/daniel-ospina/tortoise/issues/9548#issuecomment-6554166515), quoted
> with its headline and operational point 3 in the runbook note above) says, in the owner's words:
> "**Do not arm, provision, or point anything at the hosted store.**" — and names this item explicitly
> as **not** work to pick up. Its `OVERRIDES:` target was epic #7879 **R1**, this item, and it **was
> superseded on 2026-11-03** (AUTHORISED box above).

**What:** set `TORTOISE_RECORD_DSN` on `tortoise-y4mjjq` to the project's **pooler** DSN
(`postgresql://tortoise_record_app.<ref>:…@aws-0-<region>.pooler.supabase.com:5432/postgres`, `?sslmode=require`
— the shape `.env.example`'s "HOSTED (post-#669 flip, #771)" block documents for `TORTOISE_AUDIT_DSN`), add it to the deploy
workflow's secret-provisioning step (the runbook's Secrets Matrix row already landed with #9600 — see
A8), and **leave `TORTOISE_RECORD_SINGLE_TENANT` unset** — unset is already multi-tenant, which is
what makes the env tenant pair be refused rather than silently used
(`RECORD_SINGLE_TENANT_ENV` in `tortoise/record_writer.py`; setting `=0` only restates the default).
> ⚠️ **The user is `tortoise_record_app`, NOT `postgres`** — corrected by R6's decision
> ([`09-record-tenancy-decision.md`](09-record-tenancy-decision.md), #9554). `postgres` is the
> **table owner** and bypasses RLS, which would make §4's tenancy decision unimplemented for the one
> path that writes customer data. The role exists after the migration applies (`NOLOGIN`, no
> password). ⛔ **This sentence used to present "granting it `LOGIN` + a password" as an outstanding operator
> step — it is DONE (2026-10-26, `#9548` comment `6407834085`), and the runbook's step 1 now says so.** The
> step is kept only as the rotation / re-mint reference; re-running it rotates the live credential the
> deployed `TORTOISE_RECORD_DSN` names and requires re-setting the GitHub **and** Fly secrets.
**What it blocks:** **everything that needs a written record.** Until this is set the record receives
nothing, so no item in this list that depends on reading one is observable in production — R2 is the
exception, because the schema lands on a dispatch whether or not R1 has happened.
**Not needed:** the driver. `Dockerfile.hosted:86` installs `'.[postgres]'`, so `psycopg2` is already
in the hosted image — checked rather than assumed, because the natural guess was that it is not.
**Decision needed:** which Supabase project hosts the record. §4 decides **one project, tenant-scoped
rows** — so this is the existing project, and *"one database per team"* is refused on cost grounds
(§4's trap).

**Progress (2026-10-23, #9548) — the surfaces landed; the secret has since been set.** The three
non-secret halves of this item are in the tree: `deploy-hosted.yml` binds and stages the variable
from its own same-named GitHub secret (guarded `-n`, like every other optional secret) and probes it
into `GH_SECRETS_PRESENT`; `fly-managed-secrets.txt` declares it `gh-secret:`; the runbook's Secrets
Matrix carries a row plus an "Arming the Postgres record" section (DSN shape, the two
never-do-this rules, the verify recipe); `fly.toml`'s `[env]` block documents that it is a SECRET and
must never be an `[env]` key. What then remained was **the provisioning act itself**, which no lane
could perform — the value is a credential in the operator's Supabase console. That act **has since
been performed**: the app reports `armed: true` (the `write_failed: 32` once quoted here is a
2026-11-02 reading; `/health` re-measured 2026-11-05T13:11:13Z reports `record_write_failures` EMPTY,
which falsifies "writes are failing" but does not establish that a row was written) — and it is
✅ **authorised, not postponed**: the 2026-11-03 owner decision superseded the 2026-11-01 ruling
quoted at the head of this item. ⚠️ **Re-running it is not neutral and is not the remaining step:**
`\password`-style re-minting rotates the credential the deployed DSN names, so the GitHub **and** Fly
secrets must be re-set in the same change or the record stops authenticating; the remaining step the
decision names is its backup-first precondition. A value hand-set on Fly instead of staged from the
GitHub secret is reported `UNSOURCED` and **blocks the deploy** — that ordering rule still governs
any re-arming. `TORTOISE_RECORD_SINGLE_TENANT` is deliberately staged nowhere and a test pins that
(`tests/test_deploy_workflow.py::test_the_record_is_not_armed_single_tenant_on_the_hosted_app`).

### R2 — Land the schema on the hosted project, and keep the name gate honest · **S** · ops · **BLOCKER**

**What:** apply the migrations. `supabase-deploy` **applies** them only on `workflow_dispatch` —
the `supabase db push --linked --include-all --yes` step in
`.github/workflows/supabase-deploy.yml`, gated on `github.event_name == 'workflow_dispatch'`; its
`push` trigger on `supabase/**` runs the drift checks, not the apply — so the record tables will
land on the next dispatch
whether or not R1 has happened. Two couplings to respect: the record reader is **name-gated** on
`RECORD_MIGRATIONS` (`record_projection.py`, no glob) so a **new** `.sql` is invisible to the
image until its name is added there; and `deploy-hosted.yml` deliberately does **not** trigger on
`supabase/migrations/**` for that reason.
**What it blocks:** any write at all; and a schema change that silently no-ops in the image.
**Decision needed:** none.

### R3 — CI proof: arm `TEST_RECORD_DB_URI` and the `[postgres]` extra · **S** · code/ops · **BLOCKER** · ✅ LANDED (#9551 / PR #9772)

**What:** a throwaway Postgres in CI — LANDED as `.github/actions/postgres-provision`, an ephemeral
loopback-bound container (`tests/test_runner_ports_6673.py` forbids a `services:` block in this
workflow) — `TEST_RECORD_DB_URI` pointed at it, and the `postgres` extra in the test env.
**What it blocks:** every item below **regressing silently**. This is not hypothetical — it is the
mechanism by which a **new** #9264-class schema↔producer disagreement would hide: until #9551 armed it,
the leg that would catch it **skipped**, twice over (§G run 1 vs run 2). ⚠️ There is NO tolerated pin left: the `_SCHEMA_DISAGREEMENTS` vocabulary AND its detector were DELETED (#9264 closed `capture_redactions`; #10620 narrowed `point.search_keys` to `text`), so arming this leg reddens any new schema/producer disagreement instead of tolerating it.
**Was the cheapest item in the list, and the one that made the rest verifiable** — armed by #9551
(PR #9772): `test-record-postgres` provisions the database, installs the extra, and asserts zero
skips, as a required leg of `python-ci-gate`.

### R4 — Fix the two schema↔producer disagreements (#9264, #10620) · **M** · schema or code + decision · **DONE**

> **Both halves are FIXED.** `session.capture_redactions` was narrowed to `integer` by
> `20261022000001` (#9264). `point.search_keys` was narrowed to `text` by
> `20261102000002_record_point_search_keys_text.sql` (#10620), and the `_SCHEMA_DISAGREEMENTS`
> vocabulary and its detector were DELETED in #10620 — this row's own guidance ("prefer the schema side") is what both fixes followed.
> ⚠️ **The second half cost 302 dropped point records on the hosted deployment, and `/health`
> reported `ok: true` throughout** (32 at ~14:24Z → 302 by ~19:17Z on #10620's measurement). The
> drops are recoverable from the journal: replayed through the fixed projection the same records
> land (measured on a throwaway `postgres:16`: 0/5 rows before the column narrowed, 5/5 after).
> The `relation` = 0 figure above is a PRE-FIX measurement and must be re-taken.

**What:** `session.capture_redactions` was `jsonb`, the producer emits an **integer** — so the `SessionRecorded` carrying
`capture_redactions` was **refused** (`DatatypeMismatch`, counted on `record_write_failures`) and the
row kept the column **NULL**; `point.search_keys` WAS `jsonb` (now `text`, #10620), the producer emits a flattened
**string**. #9200's sibling class. Decide per field which side is authoritative and fix it;
the tolerance vocabulary in `tests/test_9078…` (`_SCHEMA_DISAGREEMENTS`) and its detector are
DELETED (#10620). It WAS held by a **self-reddening assertion** (a **schema-side** fix failed the
test until the entry was deleted, while a **producer-side** fix left the entry green-but-dead), and
the ruling that assertion existed to enforce is the one that closed both halves: **prefer the schema
side**. Every refusal in that loop is now a HARD FAILURE — there is no allowance left to widen.
**What it blocks:** a **complete** session record (the redaction count was lost until #9264's
migration) and any Point that carries extracted `search_keys` — that one was refused for **302
rows in 5 h** before #10620 narrowed `point.search_keys` to `text` (the producer's form), so both
halves are now carried. Both were silent losses the journal heals, which is why they were
invisible without R3.

#### Where the array arm is deliberately BROADER than the write path (the non-`str` element divergence)

`20261102000002_record_point_search_keys_text.sql`'s `USING` arm is bootstrap arithmetic on rows that
already exist, and for an ARRAY it applies the write path's rule (per-element `strip`, blank elements
dropped, joined by one space, order preserved) **for every element a producer can emit**. One shape
diverges on purpose: a **NON-`str` ELEMENT** inside an array (#10648 review P3).

`jsonb_array_elements_text` renders a nested object/array as its own JSON text (`{"k": "v"}`) and
yields SQL `NULL` for a JSON `null`, so `["a", null]` lands as `a` (an element with no text is
dropped) and `["a", {"k": "v"}]` as `a {"k": "v"}`. The write path's restatement
(`record_projection._record_column_value`) is `str(item)` — i.e. `a None` and `a {'k': 'v'}` — which
are **PYTHON REPRS**. Reproducing a repr in a persisted column would store one language's artifact as
the record's canonical form (a `replay(journal)`-divergent value, and `rebuild_from_record` copies
this column back into the graph), so the migration keeps the element's own JSON text and drops only
an element that HAS no text. It is unreachable from any producer (`search_keys` is `list[str]`), so
only a pre-existing `jsonb` row — the old `_jsonb_or_scalar` path, or a hand-written row — can hold
one. The two ends are pinned together by
`tests/test_record_searchkeys_10620.py::test_the_array_arms_non_str_elements_are_the_stated_divergence`,
so a change to EITHER side goes red.

⛔ **Why this lives here and not in the migration header.** The header used to state it, and it is the
paragraph a future lane reads before deciding the arm is "the SAME rule" and tidying it — but
migrations are **append-only history** (`.github/scripts/check-migration-append-only`), and the file
had already landed on `main` before the statement was written. Editing it would redden the gate on a
file that has already been applied. So the divergence is stated here, and
`tests/test_record_searchkeys_10620.py::test_the_divergence_is_STATED_in_the_doc_the_next_lane_reads`
reds if this paragraph is deleted while the arm still diverges.

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
`DirectEdgeCreated`, and nothing emitted them from those doors. That half had **no live home**: `#9079` closed
on 2026-10-21 with the §17.1 write-door fix, not this one, so it is recorded on the root ([comment 6323627886](https://github.com/daniel-ospina/tortoise/issues/7879#issuecomment-6323627886)).
✅ **CLOSED by #9553 (R5 class 4): the producer now exists.** The lane's two mints journal
`DirectEdgeCreated` (`projection/edges.py::_record_structural_edges`), so `ownedBy`, `hasPart`,
`dependsOn`, `wasDerivedFrom`, `references` between epistemic endpoints and the bare epistemic-targeted
`about*` mints all land a `relation` row and are restored by the journal rebuild too — the `edges: 0`
measurement above was re-taken and is **FIXED**, not blessed. `projection/edges.py:735-745` is therefore a
HISTORICAL anchor: the current ledger is `RECORD_CARRIED_STRUCTURAL_RELS` /
`SOURCE_ENDPOINT_CANONICAL_RELS` (the two whose canonical target is a `:Source`), the record
route's CI gate is the PGlite leg in
`supabase/tests/pglite/validate.mjs`, and the behaviour is pinned by
`tests/test_9553_class4_structural_edge_records.py`. Two shapes remain UNCARIED, both declared: a write whose
endpoint is a `:Source` (`aboutDocument`/`aboutSource` canonically; a `:Source`-anchored `references`) — a FOLD
limitation, #9834 — and `TAGGED`, which has no RECORD carrier although a JOURNAL rebuild restores it from the
Point's `tags` property (measured; record-only gap) — §R5's
class-4 block below names them and why, with `#9834` for the fold work. ⚠️ **One more family is missing for its own reason, and one has been closed.** `CONTAINS` — the capture's
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
`pglite` gate → `schema-drill`), not only on a migration one. ✅ **Closed by #9553 (R5 class 3) — the operator topology.** `OperatorAdded` is a `point`-row
upsert whose inputs sit in `point.props` and no journaled EDGE record carries them, so the record
materialised the operator's `point` row and emitted no `relation` row: a journal rebuild restored the
`IMPL`/`NAND`/`mitigated_by` structure and a record-sourced rebuild restored the claims without it —
and `compute_operator_weight` reads exactly those edges, so the loss was a belief change. The Point's
`{op_type, inputs}` descriptor is the carrier (`_create_edges` reads the same key), and the record
mirrors it value for value: the `op_type`→predicate map (four composition spellings → `hasPart`), the
reverse `INPUT`, and the two mitigation gates (`is_non_operator_payload` AND
`has_usable_mitigation_strength`, restated for the STANDALONE load and pinned value-for-value). The
endpoints resolve across the **four** epistemic labels (#7902) with each label's CANONICAL key
(`Point`/`Event` → `node_key`, `Subject`/`Object` → `id` since #9881), so `relation.src_label` is a fact looked
up rather than a `Point` assumption, and the conflict path is `DO NOTHING` because the fold attaches no
attribute to these edges beyond `idx`. ⛔ The input gate is `_writable_id`'s (a blank key is
ADMITTED), not the blank-refusing `_writable_endpoint_id` of the two neighbouring families: those
address an endpoint with an INLINE indexed match where FalkorDB misses a whitespace-only key
(#9599), while `_create_edges` probes an operator input with a label-disjunctive `WHERE` — a scan —
and lands the edges for one (measured through the fold). The stricter rule here would make the record
poorer than the graph on the edge this change carries. Residuals: an input the fold STUBS lands no row (a stub has no
record row — the `absent` verdict), and a promote snapshot with no `operator` KEY lands none,
exactly as `_promotion_point_with_operator` reaches `OperatorPromoted` only — while a snapshot whose
key is present but holds `None`/a non-`dict` lands none too, because that synthesis gate is
`"operator" in p` and `_create_edges` refuses a non-`dict`. One stated BOUND, on the POORER side:
the fold MERGEs an edge pair per node when an id resolves to more than one, and the record files one
row under the first `EPISTEMIC_LABELS` match.
**What it blocks:** the epic's own acceptance — *"the graph engine can be destroyed and rebuilt
without data loss"*. NAND / IMPL / mitigation topology is the thing that would be lost, and §8 makes
operator topology non-negotiable (a fused claim cannot be `NAND`ed).

✅ **Partially closed by #9553 (R5 class 1) — `extractedFrom`.** The provenance family now lands
one `relation` row per ref (`record_projection._POINT_PROPERTY_RELATIONS`), because that is the
fan-out `_link_source` performs (many-to-many, `#3263`), and the `#5256` read-version anchor rides
`relation.source_version` — the PRIMARY column the cache projection maps back to `r.sourceVersion`
— from the Point's own `sourceVersionTransit` carrier. Three parity details are load-bearing: the
target resolves to the SAME Source the fold picks — `resolve_source_key`'s canonical identity,
matched on `url`, on the **canonical spelling**, **or** on `canonical_url` (THREE candidates — the
same triple `_source_key_resolution` binds for the row key: the graph's step 2 adopts by EITHER
spelling, and a pre-`#9553` row filed under the canonical spelling carries `canonical_url IS NULL`, so
a two-arm rule leaves exactly that population invisible and a variant then files a SECOND row),
PREFERRING the canonical match and ordered by `node_key` in **byte** order
(`ORDER BY node_key COLLATE "C"`) — which is that resolver's own order (it queries `canonicalUrl`
first and returns there; the raw spelling is the adopt-on-touch fallback) and its own tiebreak
(`ORDER BY s.url LIMIT 1`, whose comparator is byte order — a bare `ORDER BY`'s LOCALE collation is
not the graph's answer), never the `EntityLinked` `id` fallback; that parity also required the projection to DERIVE
`source.canonical_url`, because `projection/entities.py::_EntityHandlers._SOURCE_HANDLED` lists `canonicalUrl` as writer-derived and
no payload may carry it; the ref
container and per-member gates mirror `_link_source`'s (a dict fans out to nothing, `#8171`); and
the blank/NUL rule is the same `_writable_endpoint_id` the CONTAINS gate uses, justified by the
same `#9599` indexed-key measurement (`:Source(url)` is indexed too).

**Two residuals, stated so a green suite is not read as coverage of them.** (a) The Source's record
must precede the Point's — the live order, since the capture materialises the session Source first
(`sdk.py::_materialize_session_source`); replayed the other way the insert finds no `source` row and
lands nothing, the `absent` verdict every relation already gives (a record-sourced rebuild is fed in
journal order, `#9190`). (b) A ref naming a Source the record never saw — `_link_source` MINTS an
**unjournaled** stub for one (`_mint_source_stub`, `#5024`) — has no `(Source, key)` identity for
`relation.tgt_key`'s composite FK at all, so the record lands nothing by design: minting a node from
an edge directive would materialise a Source no record created. Same absent verdict, same reason, as
the CONTAINS envelope family (`#9594`). (c) **The fold's two routes disagree about which RECORDS
carry the family, and the record follows `apply()`.** `apply()` materialises a Point from four arms
and three of them reach `_upsert_point_edges` — `PointAdded`/`OperatorAdded` (`_upsert(p)`) and
`PointPromoted`/`OperatorPromoted` (`_upsert(gated_promote_payload(…))` /
`_upsert(_promotion_point_with_operator(…))`, neither payload wrapper dropping `extractedFrom`) —
while `rebuild_all`'s pass 2 re-derives the family for the two CREATION types only. So
`_POINT_PROPERTY_RELATIONS` declares all four, because declining the promote rows would leave the
record POORER than the graph on the chronological engine `rebuild` / `recover_from_log` run, for
exactly the `#2256` population whose promote IS the operator's only record. A full rebuild
re-deriving no promote edge is the divergence that follows, and it is on the pass-2 side.

**The `#5256` anchor is written ON CREATE ONLY**, as `_anchor_on_create` does: the INSERT carries
it and the conflict path does not write the column at all, so a replay of the same edge (a
re-emitted `PointAdded`, a promote snapshot, or a second ref of one Point resolving onto the same
Source) keeps the first anchor on both routes. Every other relation attribute stays
latest-record-wins — those are edge state the later record legitimately corrects, not a recorded
read version.

✅ **Also closed by #9553 (R5 class 3) — `mitigated_by`, with its pair.** It is NOT a
standalone point property: `_create_edges` emits it as a MIRROR of the operator's `IMPL` half, so it
lands exactly where the operator topology lands (see the class-3 paragraph above) — one family, one
fix, and the edge census agrees with the journal's for both halves rather than for one.
The remaining class-1 families stay open for reasons that are NOT this fix's to invent: `about*` needs
`_create_about_edges`' live name→label cascade (`EntityLinked` already carries it when the capture
resolved it); `TAGGED` targets a `:Tag` node and the record has no `tag` table for
`relation.tgt_key`'s composite FK to point at.
✅ **Also closed by #9553 (R5 class 4) — the structural predicates, from a PRODUCER.** The class the
issue describes as *"the record type, the fold and the resolver already exist; nothing emits them"* is closed
where it said it had to be: `projection/edges.py::create_edge` and `create_about_edge` (and therefore the three
typed wrappers, and every door that reaches them — `sdk.create_edge`, `hosted_api`, `mining`, `onboarding`) now
call `_record_structural_edges`, which hands each written edge to the recorder `TortoiseSDK._get_proj` binds
(`_bind_structural_edge_recorder`, beside `_bind_journal`, and only when a journal exists — a projection
built without a journal writes nothing, and a REPLAY never emits even on a bound one).
`TortoiseSDK._record_structural_edge` journals §4.4's flat `DirectEdgeCreated`. Three properties are load-bearing and each is pinned by a leg: the descriptor names
`source_label`/`target_label`, because `relation.src_label`/`tgt_label` are bound VALUES and each label resolves
its endpoint through its own key column (a `Subject` keys `name`, so a descriptor naming only `Point` would land
no row — the silently-empty shape); the addresses are the fold's own probe (`EPISTEMIC_LABELS` swept on `p.id`),
so a `:Source` endpoint is DECLINED and disclosed rather than half-journaled; and the emission carries no
relationship attributes at all (these edges have none, §9.6), so pass-2b cannot SET a topology the live graph
never had.

**A REPLAY NEVER JOURNALS** — and that is a gate, not a hope. `apply()` and `rebuild_all`'s pass-2b drive
`replay_about_links` → `create_about_edge`, which IS one of the two mints, on the SDK's own BOUND projection, so
without a guard a recovery WRITE would append to the append-only journal it is reading: one `DirectEdgeCreated`
per journaled link per rebuild, unbounded, and built from the endpoints resolved AT REPLAY TIME (any live↔replay
drift would become new durable truth). Measured before the guard: a 4-record journal re-grew to 5, 6, 7 over
three successive rebuilds. `_record_structural_edges` therefore returns early when
`projection.replay_window_open()` — the SCOPE-STACK depth #9391 built, so a nested lane (`_prewipe_*`,
`_graph_metadata_write_scope`, the lane's own write scope) cannot shadow it. The new suite pins the journal's
line count across two rebuilds.

**Measured round trip.** The record route has a CI GATE, not just a claim: a PGlite leg in
`supabase/tests/pglite/validate.mjs` replays a `PointAdded` + `SubjectAdded` + the descriptor the producer emits
and asserts exactly one `relation` row — `Point/p1 -> Subject/team-x`, i.e. the Subject filed by its **name**
(the per-label key resolution), re-replaying idempotently. On a local throwaway Postgres holding the record
migrations plus a local FalkorDB, the same family set was measured end to end: live graph = journal-sourced
rebuild = record-sourced rebuild, **1 : 1 : 1** for `ownedBy`, `dependsOn`, `hasPart`, `wasDerivedFrom`,
`references` and the bare `aboutObject`, with `public.relation` going from **0 rows for every one of them** to
one row each (the zero re-measured on the same harness before the change: `relation` = 3 rows total, `CONTAINS` 2 and `extractedFrom` 1). A `capture_session` on that harness leaves one residual mismatch —
`aboutObject` and
`extractedFrom` short by the captured turns — and it is NOT this family: the captured Point itself never reaches
the record, because its `search_keys` string met `point.search_keys jsonb`. That was **R4**'s remaining
half, FIXED by #10620 (the column is now `text`; the `tests/test_9078_…` tolerance vocabulary is
DELETED with it), so the edges that Point carried have no endpoint to resolve only on a deployment
that has not applied `20261102000002`. Named here so it is not read as an edge defect.

**What is still not carried, with why** — this is the "0 families without a stated reason" answer: TWO families
and ONE shape.

1. `TAGGED` — a `:Tag` node target, and `NODE_TABLES` has no `tag` table for `relation.tgt_key`'s composite FK:
   a schema decision, not a projection one.
2. a `:Source`-anchored `references` written by `link_source_to_entity` — the fold addresses endpoints by
   sweeping the four EPISTEMIC labels on `p.id`, and a `:Source` is url-keyed and outside that sweep. Per-WRITE:
   a `references` between epistemic endpoints IS carried.
3. `aboutDocument` / `aboutSource` — NOT a family with no carrier: the producer journals both (measured; a test
   pins it), but their CANONICAL shape targets a `:Source`, which the fold's sweep cannot address, so that shape
   is declined per write and REPORTED through the package's one skip reporter (`SOURCE_ENDPOINT_CANONICAL_RELS`).
   Making a `:Source` addressable is a FOLD change — **#9834** (with `TAGGED`).

**Family 2 is not silent**: `structural_edge_record_carrier` answers the record half per relation, the carried
set is the lane's own write set (asserted at import), and a declined write goes through the package's ONE skip
reporter — `projection._log_identity_skip`, with `absent_is_ordinary=False, drops_record=True` — which both
reports the endpoint and RECORDS the skip, so a run boundary can COUNT what a rebuild will not restore instead
of reading a log (`SOURCE_ENDPOINT_CANONICAL_RELS` names the two predicates whose canonical shape that hits).
A SELF-EDGE is skipped for the fold's own `E2E-11.6` reason and warned once per relation by
`TortoiseSDK._record_structural_edge` — the mint did write the live self-loop, so a silent skip would be a
live ≠ rebuild divergence with no diagnostic.

⛔ **`TAGGED` IS silent, and that is stated rather than papered over.** It has NO RECORD carrier (no `tag` table
for `relation.tgt_key`'s composite FK) — but it is NOT lost: `_sync_tags` is called by the replay fold's pass 2 and
re-mints the edges from the Point's `tags` property, so a JOURNAL rebuild restores them (measured: live
`['alpha','beta']` = rebuild). The gap is the `relation` row only, which is why it is a record-shape decision and
not a durability bug. `_sync_tags` reaches neither the lane nor this producer and warns nothing, so it is named
here rather than warned at the write.

⚠️ **One uncarried writer is outside this lane and stays open**: `sdk.backfill_about_entities` mints
`aboutSubject`/`aboutObject`/`aboutEvent`/`aboutDocument` through `_create_about_edges` → `wire_about_link` (a
bare MERGE), journaling nothing and setting no `aboutEntities` snapshot prop — so neither carrier restores them.
It is a different door (not `create_edge`/`create_about_edge`) and is filed as **#9833** rather than folded
into this change.

⚠️ **Supersede: the skip is gated on what the supersede DID to that edge** — direction, order, AND the
delete-leg's blind spot. Three shapes had to be measured, and each was a live ≠ rebuild divergence until the gate
learned it (`_supersede_took_this_edge`):

1. **direction** — leg 2a (`IMPL`/`NAND`) moves BOTH directions and always journals a repoint, so a superseded
   endpoint on either side means "moved"; leg 2b (`SUPERSEDE_TRANSFERRED_RELS`) queries only the OUTGOING side, so
   an INCOMING edge (`(other)-[aboutPoint]-> old`) is never moved and must be replayed (measured: a rel-keyed gate
   dropped it — live `p2->p1 = ['aboutPoint']`, rebuild `[]`).
2. **order** — leg 2b moves what EXISTS at supersede time. An edge MINTED at an already-superseded id
   (`create_edge`/`create_about_edge` have no terminal-endpoint guard, unlike `create_direct_edge`'s #2422
   refusal) gets no repoint and is kept live at the old point, so the guard compares the creation's journal seq
   with the supersede's and replays it.
3. **the delete-leg's blind spot** — for a replayed post-supersede creation whose edge ALSO has a 2b repoint
   descriptor, that descriptor's delete-leg (which cleans pass-2's resurrection and cannot tell it from a fresh
   replay) removed it again. The creation fold now records `(src, edge_type, RESOLVED target node)` and the
   delete-leg is exempted for those keys — while still performing the successor-side transfer, because live holds
   BOTH (the moved edge at the successor, the re-assertion at the old point; measured).

Measured across 16 combinations (rel × direction × before/after supersede), plus the exists-and-is-re-asserted
shape, plus (round 2) every LABEL FAMILY a target can belong to — Point, Object, Subject, **Event**, **Source**
— and the
target-superseded shape: everything agrees live↔rebuild except the residuals below.

- **journal route, `wasDerivedFrom`/`aboutAction`** — **CLOSED by #9836**: MOVED live by 2b, not derivable, and
  now journalled as the ID-ADDRESSED family (the target is addressed by its OBSERVED label's own identity
  property — `id` for Point/Subject/Object, **`eventId` for an `Event`** because the meeting guard can give two
  distinct Events one `id`, **`url` for a `:Source`** — with that label riding `target_label`) — so the
  journal rebuild has the edge at the successor, as live does. ⚠️ The residual is a target whose identity
  property is ABSENT — an `Event` the journal never gave an `eventId` — for which NO descriptor is emitted
  (never half-journaled), so live MOVES the edge while **both** rebuild routes hold nothing. ⛔ The `:Source` arm is the review's finding: the
  first cut declined that target and the census read `LIVE [(new, url)] / JREBUILD []`, i.e. live ≠ rebuild on
  a shape `create_edge` produces (it type-checks nothing). An endpoint the record cannot address would still die
  at rebuild — but "cannot be addressed by `id`" is not "cannot be addressed", and a Source IS addressable by
  its `url` on both routes.
- **record route, EVERY moved structural rel** — **CLOSED for the derivable set by #10191**, and closed for the
  ID-ADDRESSED family by #9836:
  the record applies a 2b descriptor as a MOVE (`DELETE` at the superseded point + `INSERT` at the
  hop `superseded_by` names — one hop per descriptor, so a chain reaches the terminal row as each hop runs — `ON CONFLICT … DO NOTHING`), so `relation` follows the edge to the successor too. ⛔ The boundary that remains
  is the PRE-FIX JOURNAL: a supersede journaled before #9836 carries no descriptor for those two rels, so on that
  journal the record keeps the row at the pre-supersede point (live moved it to the successor). A shape the producer DECLINES at the mint (`_record_addressable`) holds
  nothing at all, and the `:Source`-targeted shape is one of those rather than the only one: the same gate also
  declines any endpoint whose value is not that node's own `id` (an `eventId`-only legacy `Event`). ⛔ That mint
  gate is about the `DirectEdgeCreated` CARRIER, not about the supersede MOVE: a `:Source`-targeted edge of the
  ID-ADDRESSED family still lands no creation row, but the 2b repoint descriptor is url-keyed and DOES move
  whatever row is there (`#9836` review round 2).
  ⚠️ The retired claim here read *the record's `DirectEdgeRepoint` is a plain upsert* — true before #10191, and
  it pre-dated #9553 for the families class 1 (`extractedFrom`) and `EntityLinked` already carried.

Closing what remains needs the supersede move journaled for non-derivable rels AND the record taught to model a
move for them too (delete-old + write-successor): **#9836** — LANDED in this lane for all eight moved rels
(the family's descriptor is `ID_ADDRESSED_STRUCTURAL_RELS`; the no-self-edge `delete_only` leg landed in
review round 2).

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

> **✅ DECIDED (2026-10-22, #9554) — this item's answer is recorded, and the code now implements
> it.** The record's application path connects as **`tortoise_record_app`** (`NOBYPASSRLS`, not the
> table owner, not `service_role`), and **RLS is the boundary** — not defence-in-depth. The tenant
> GUC is `set_config('app.current_org_id', $1, true)`, issued **transaction-locally** by
> `record_projection.replay()` from the same `org_id` it projects, so the writer cannot disagree
> with its own connection and the posture is pooler-mode independent. `postgres`/table owner and
> `service_role` remain bypassing **on purpose** for schema, database-level backup/restore (R9) and
> the erasure RPC — but not for R8's `record-restore` verb, which reads through the read door and so
> connects as the app role. Full record, with the rejected candidates and the vendor evidence:
> [`09-record-tenancy-decision.md`](09-record-tenancy-decision.md). Proven by
> `supabase/tests/20261022000001_record_tenancy_posture.sql` (PGlite) and
> `tests/test_record_tenancy_guc.py`.
> ⛔ **The guarantee is enforced by code on BOTH doors, and verified rather than assumed**
> (#9600 review cycle 2): `record_projection.scope_connection()` sets the GUC, reads it back, and
> refuses a connection whose `current_user` is superuser, `BYPASSRLS`, a member of such a role, or
> the **owner of a record table** — so a DSN naming `postgres` fails closed on the first write or
> read instead of quietly bypassing every policy. The same scope is installed by the **read** door
> (`cache_projection.read_record_view`, i.e. `tortoise record-reconcile`), which shares R1's one DSN:
> before it, a reconciler armed with the app role WOULD have read zero rows, landed every cache key
> in `extra_in_cache` with `missing_in_cache = 0` (the chokepoint-violation signature, not a lag),
> and reported a coherent pair as divergent. That is the counterfactual the read-back prevents — it
> was never an observed event, because at the census point the DSN was unset on the deployment.
> ⚠️ **SUPERSEDED (2026-10-26):** the record has been **ARMED** since then (`#9548` comment
> `6407834085`: `armed: true`, `tortoise_record_app` granted `LOGIN`), so the read-back now runs on the
> live deployment's connection — the one production write ATTEMPTS traverse. ⛔ **That is not the same
> as "a connection that carries production writes"** (this sentence used to say exactly that, and it
> read as *the write path works* — the empty-counter-as-health misread this section exists to close):
> an attempt is not a landing, and an empty `record_write_failures` is *not* evidence that a row was
> written. What is still not observed is a landing
> `journal_event` + `point` row.
> ⚠️ **This corrects R1's DSN above**: its `postgres.<ref>` user is the table owner and would
> bypass RLS. R1 must arm
> `postgresql://tortoise_record_app.<ref>:…@…pooler.supabase.com:5432/postgres?sslmode=require`
> (the role's `LOGIN` credential is provisioned per `docs/infra-runbook.md`). ⚠️ **SUPERSEDED
> (2026-10-26):** this sentence read *"R1 is unarmed at the time of writing, so this is a correction
> to a plan, not a migration"* — R1 has **been armed** since 2026-10-26 (`#9548` comment
> `6407834085`), so the correction is in force on the live connection, not merely on the plan. ⛔ Do
> **not** re-run the grant to "finish" it: `\password tortoise_record_app` rotates the credential
> the deployed DSN names (R1 records the procedure and that hazard).

**Decision needed:** none — decided (above). The remaining work this box named was R1's DSN, and
**R1 was armed on 2026-10-26** (`#9548` comment `6407834085`): nothing in R6 is outstanding, and what
R1 keeps is the rotation hazard, not a pending arming.

### R7 — Raw text and the S1 narrative in Postgres (§9.1) · **L** · schema + code · **BLOCKER**

**What — as filed 2026-10-21, ⛔ superseded by the DECIDED block below (#9555, 2026-10-22):** at the
`8767b1c8b` measurement **there was no dedicated table for it** — the text was carried as data, in the
`point.content` column and in the layer-1 `journal_event.payload` mirror of the record that wrote it
(see below) — `grep -riE 'narrative|raw_turn|raw_text|transcript'` over all
65 migrations returned **0** (at this head, 81 migrations, the same command matches it on 62 lines — 7 in `20261025000001_record_tenancy_role_and_guc.sql` (R6's grants and
tenant policy for that very table), 36 in `20261023000001_record_session_narrative.sql` and 19 in
`20261024000001_record_session_narrative_carrier.sql` — the table this
item's DECIDED block adds);
`public.session` carries `turn_count` and **no turns**. §9.1 is explicit
that *"the narrative is not something we're suggesting to store in the graph (same as raw) but store
in supabase"*, and §9.4 makes the **link** load-bearing (*"at answer time, send the source's VERBATIM
text, not the extracted claim"* — measured 15.9 / 22.0 pts).
**What it blocks:** §9.1's placement rule, the verbatim-evidence fetch path, **and the destroy-the-
graph acceptance itself** — the record had **no narrative-first home**: a captured turn's text
is carried as data — an episodic `point.content` row (`…03_record_epistemic.sql:56`; the §G run wrote
4) and the `journal_event.payload` that mirrors the record, plus, since `#3947` (CLOSED), the journal
entry a `rebuild_all` replays — so what a *record*-sourced rebuild could not reproduce was the §9.1
narrative (its redaction count followed once #9264's `integer` column landed), not "the raw text" in
the abstract. This item is not in the epic's four scope items; it is a §9.1 requirement, and that gap is
the finding (see §E).
**Decision needed:** none — §9.1 and the 2026-09-23 owner quote already place it. Only *which table
shape* is open, and that is an engineering choice inside the existing schema discipline.

**DECIDED (2026-10-22, #9555) — the shape, and why.** One table, **`public.session_narrative`**
(migration `20261023000001_record_session_narrative.sql`), keyed `(org_id, graph_id, session_id)` with
an `ON DELETE CASCADE` foreign key into **`public.graphs`** — ⛔ **not** into `public.session`, and that
is a regression pin: the narrative's carrier is a `:Source`/`:Event` record that *names* its session,
and the record's own `source.session_id` column has no such constraint (the live capture writes the
transcript Source through raw Cypher, so a session id can arrive before, or without, a session row).
An FK into `public.session` aborted the **whole record batch** on the live door (source row, journal
position, everything) and wedged a replay of that record. The graph FK keeps the tenancy anchor and
the erasure cascade (organizations → graphs → this table) without making the row's existence depend on
write order.

| column | carrier (§3: PRIMARY iff the journal carries it) |
|---|---|
| `narrative` | the commit's `story_arc`, carried by `SessionNarrated` — the record #9555's follow-up added to the layer-1 commit path (see the ✅ block below; when this table landed the class was *PRIMARY-WITHOUT-CARRIER*, and the journal was extended, which is what that block records) |
| `summary` | the same record's `summary`, projected ONLY alongside the narrative (the capture's own session `:Source` sets `summary` to a display descriptor over the session's TOPIC lexicon — never a copy of the turn's prose, in a word-delimited script; a space-less (CJK) run can arrive whole — which is not the narrative) |
| `capture_redactions` | `SessionRecorded.capture_redactions` (`#4911`) — **integer**, because a count is not an envelope. It lands: the carrier's `session.capture_redactions` was narrowed to `integer` by `20261022000001` (`#9264`, fixed — see the `session` record row in §A2), so the `SessionRecorded` that carries this count reaches the record instead of being refused; a count this column cannot hold is still written as NO count, never a 0 or a coerced shape |
| `turn_count` | `SessionRecorded.turn_count` |

⛔ **The transcript is NOT duplicated here.** The raw turns are already first-class record rows
(episodic `public.point`, `node_key = '<session>_t<i>'`), each with its own identity and journal
carrier; a second per-turn table would duplicate a carrier that exists — the “double the anchor count
for the same input” cost §9.1 rejects one level up — and two copies of the same text can only drift.
`tortoise/record_read.py` SERVES the transcript from those rows (`session_text`), by the same
suffix-shape rule `sdk._capture_turn_ids` applies (`{session}_t<all digits>`), so D7's “raw turn text
comes back” is measured against the record's own turn rows.

**Deletion follows the record's own delete path.** `hosted_api._delete_session_sync` journals one
`EntityMutated` (`op=delete`) per removed turn Point plus one `SessionDeleted`, whose declared
mutation removes the session row and the `session:` Source stub, so the projection emits the matching
`DELETE` for `session_narrative` — the table is not a node table
and carries its own statement rather than a `companion_deletes` template. Tenant erasure is the other
path (organizations → graphs → this table, `ON DELETE CASCADE`).

**Why the two carriers cannot clobber each other.** One row is built by two records (the narrative
record owns the text, `SessionRecorded` owns the counts), so each upsert names **only its own columns**
in the `DO UPDATE SET` — otherwise whichever record arrived second would write NULLs over the first
one's half, i.e. a replay whose result depends on arrival order. Pinned by applying both arrival orders
through `replay_plan` to a row model and comparing the resulting row (the PGlite assertion that the
narrative survives its **session row** being deleted pins the FK dependency separately — a different
property, and it is stated there for that reason).

**Verification:** `tests/test_9555_session_narrative.py` (DB-free: schema shape, tenancy posture, the
statement shapes, order-independence, the reader reading the record only) plus the PGlite assertions in
`supabase/tests/20261012000001_record_schema.sql` §6b (RLS, the graph-FK refusal, the session-row
independence, the negative count, the `authenticated` write refusal, and a cross-tenant read returning
nothing). Both were shown to redden under a mutation (RLS disabled; the count CHECK removed).

**✅ R7's REMAINDER LANDED (#9555, follow-up).** Both items below were the
item's own remainder and both are now built. The original text is kept beneath
them, because a stale "does not land here" note is how the next lane rebuilds
work that already exists.

1. **The narrative's journal carrier EXISTS.** `hosted_api._execute_commit_writes`
   journals `SessionNarrated` (`record_projection.NARRATIVE_RECORD_TYPE`, declared
   `NoRecordEffect` — §9.1 keeps the narrative outside the graph) right after the
   raw-Cypher MERGE it describes, carrying `sessionId` + `story_arc` + `summary`.
   Its record effect is the SHAPE-KEYED secondary projection, so the write path is
   still ONE path (`sdk._emit_event` → the live door → `record_projection.replay`):
   a record was added, no writer was.
2. **A caller serves from the record.** `tortoise/record_text.py` composes
   `record_read` for §9.4's fetch, and `python -m tortoise record-text` is its
   operator verb (exit `0` served / `3` readable-and-absent / `2` no verdict —
   the 3-versus-2 split exists because a tenancy-filtered read returns 0 rows
   SILENTLY, so an unreadable SCOPE must never be reported as an absent SESSION).
   ⛔ The hosted ask/answer path's own verbatim fetch is NOT wired and is not
   claimed: it is an MCP/SDK SURFACE change, which `AGENTS.md` puts behind
   Daniel's approval.
3. **Measured, not asserted — with the ARMING stated, because a leg that skips
   everywhere defends nothing:** `tests/test_9555_narrative_carrier.py` (carrier,
   projection, §9.4 fetch, and the verb's exit codes run AS THE APP ROLE) arms in
   CI's `test-record-postgres` job, which asserts zero skips.
   `tests/test_9555_narrative_cache_rebuild.py` — the derived cache DESTROYED and
   rebuilt from the record alone, narrative and redaction count identical across
   the wipe — needs **both** a record Postgres and a FalkorDB, and **no CI job
   arms both** (the armed record job provisions no cache, and every FalkorDB job
   sets no record DSN), so it runs only on a manually-armed lane. Measured there,
   on a throwaway `postgres:16` plus a private lane FalkorDB: that file is **1
   test, 0 skipped** (`--collect-only` is the authority — it is one leg, and one
   is what it takes to destroy and rebuild the cache), and the two files together
   are 23, 0 skipped. ⛔ The gap is named rather than papered over: until a job arms both,
   a regression in the record→cache narrative restore would merge green.

**⚠️ THE ORIGINAL REMAINDER, kept verbatim so the two statements above stay checkable:**

1. **The narrative had NO journal carrier in this tree.** `story_arc` is written to the *graph* by the
   layer-1 commit's raw Cypher (`hosted_api.py`'s document/event MERGEs) and raw Cypher is not a
   journal record (§17.1's unjournalled-writer ledger), so a live capture lands
   `turn_count` and `capture_redactions` (both carriers are `SessionRecorded`, which reaches the record
   since #9264 — see the column table above) with a
   **NULL `narrative`**. The projection is correct and total;
   its narrative carrier does not exist yet. The fix is a producer on the layer-1 commit path (journal
   the derived `:Source`/`:Event` write with `summary`/`story_arc`/`sessionId`), which is
   capture-path work and must not be improvised alongside a schema change.
2. **No caller serves from the record yet.** Wiring the read arm into `rebuild_from_record`'s operator
   verb (R8, `#9556`) and into the ask/answer verbatim path is where D7's end-to-end measurement runs,
   and the production record DSN was unarmed at the time (R1, `#9548`) — **it has since been armed**
   (2026-10-26, `#9548` comment `6407834085`; see R1), so this paragraph narrates the `#9638` tree.

So the **home, the projection and the record-only reader** landed in #9638; D7's measurement did not
close with that change, and #9555 stayed open on (1) and (2) — both of which have since landed, as the
✅ block above states.

### R8 — A restore path from the record: an operator verb and a drill · **M** · code + ops · **BLOCKER**

**What:** `FalkorProjection.rebuild_from_record` (`projection/__init__.py`) is the record→cache seam.
⛔ **Landed** (#9556): `python -m tortoise record-restore` is its production caller, the composition
(its four refusals, its measured counts, its exit codes) is `tortoise/record_restore.py`, the runbook
section is `docs/infra-runbook.md` §9, and a drill table states which steps are measured. **Not
landed, and the reason this item stays open:** the production graph name (`org_*`) is refused by
`#2944`'s L2 name guard until the disposable-graph registry exists, and the drill's armed-record row is
unmeasured — ⚠️ **and the blocker is not the arming.** Both attributions this sentence used to carry are
*satisfied*: `#9548`'s hosted arming has been measured since 2026-10-26 (`/health` `armed:true`), and
`#9551`'s CI `TEST_RECORD_DB_URI` is armed and closed 2026-10-23. What is missing is a **captured
session that actually wrote a `journal_event` + `point` row** — an empty `record_write_failures`
cannot stand in for one — and, for the record leg, a backup-capable **direct** DSN.
⚠️ **SUPERSEDED, kept as the record of what this row claimed:** it read *"the drill's armed-record row
is unmeasured (#9548 hosted arming / #9551 CI `TEST_RECORD_DB_URI`)"* — i.e. blocked on two things
`docs/infra-runbook.md` §9's drill table states are both satisfied, which is the disagreement this
correction closes. The repo's own
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

**What:** ✅ **the mechanism LANDED (#9557/R9):** `tortoise/record_backup.py` + the `record-backup`
verb dump the RECORD to Cloudflare R2, `docs/durability-posture.md` now carries a row for it, and
`§A8` states the same. What is **NOT** landed is the operational half — **no cron invokes the verb**
(the posture row's RPO reads *UNBOUNDED today*) and **no drill has run against the hosted Supabase
record**. So R9 is no longer "no module holds it"; it is "the module exists and nothing runs it".
Historical note, kept because it is why the item was a blocker: `tortoise/backup.py` (module
docstring: *"JSONL archiver + FalkorDB BGSAVE"*), `hosted_backup.py` (Cloudflare R2),
`backup_sweep.py` (the cron driver) and the self-hosted sweep `selfhost_backup.py` archive the
**graph** — `backup.py` additionally copies the local journal's `events.jsonl` — and none of them
backs up the record. §3 and §14.1's **O1** record the decision: **durability = the store's backup;
rebuildability = the journal** — so promoting Postgres to the record **moves the durability
obligation onto Postgres**. Whether Supabase's own PITR covers it is still unverified (every
statement in the tree about it is plan-level, and A8 names two of them) — that remains the owner
decision on cost, and it is independent of the dump now shipped.
**What it blocks:** durability of the new system of record. This is the item most likely to be
mistaken for "already done" because the backup machinery *looks* present — it is present, for the
wrong store.
**Decision needed:** whether PITR alone is the posture, or an independent dump is required. §3 and §14.1's
**O1** do not distinguish them.

### R10 — Connection pooling on the record write path · **S/M** · ops + code · **FOLLOW**

**What:** ~~`_default_connect` opens **one connection, one transaction per
journal record**, and the module states the cost in place: *"~one connection + commit and ~4 ms of
plan materialization"* — `table_columns()` is **deliberately uncached**, so every record re-reads the
migrations. No pool exists.~~ ✅ **the mechanism LANDED (#9558/R10, PR #11386).** The pool now lives
BEHIND the private hook `_default_connect` (so the read door reuses it too), the column map is cached
on a per-file STAT identity — `(name, mtime_ns, size, inode, ctime_ns)`, NOT a content hash (a
content-preserving stat rewrite inside one inode-clock tick is the one shape it cannot see, and a
digest was measured at ~30–36 % of ONE consult's share of the win and rejected), and the cost was MEASURED before
and after with one instrument against two trees: **49.72–52.86 → 255.68–261.61 records/s**, per-record
mean **18.89–20.10 → 3.82–3.91 ms**, `connect` calls per record **1.00 → 0.00**, server-logged
`connection authorized` **+66 → +2** (`docs/evidence/9558-record-write-cost/`, whose eight committed
`runs/*.json` every PROBE figure is arithmetic on — the `connection authorized` counts are not
among them: they are a `grep -c` delta on the container's own log, the artifact's exception item 1).
⚠️ The BEFORE tree is 164 commits behind **this branch's base `23d227c534`** (the denominator is
the base, not the AFTER tree's own parent `a4d1a358e2`, against which the same command reads
168) — 7 of them touching the measured files, which makes the delta conservative rather than
controlled. ⚠️ Two residual
facts a later lane should not re-derive: the column-map CALL counts are unchanged at 1/record each (the WORK
dropped, not the calls — a caller-held map is the deferred lifecycle change), and 0.68 ms/record of cache-consult
overhead remains in two halves: ≈0.33 ms resolving the base (`_migrations_dir()` → `_complete()`, fifteen
`Path.exists()` per consult) and ≈0.33 ms of fingerprint passes, because `replay_plan` drives both cached
readers and each consult pays both — the filed fix (#11388) saves ONE fingerprint pass (0.160–0.169 ms) and
leaves the resolution half in place. (An earlier revision of this line said "0.32–0.34 ms of pure stat",
which was the whole warm call attributed to the fingerprint; the RESOLUTION half it now rests on (median 0.169 ms, measured on one box at that
PR's head) is item 6 of the artifact's SEVEN "items that are neither" — printed not committed, because a wall-clock file
must name the tree it was taken on, re-takeable by `fingerprint_cost.py --column-consult` — while its siblings ARE committed
(the fingerprint pass in `fingerprint-cost.json`, the warm total in `runs/*.json`'s `table_columns_isolated_ms`); the second
reader's 0.79x price ratio is item 7, a SENSITIVITY the probe cannot re-take (it records that reader's call count, never its
time). 0.68 ms/record charges BOTH cached consults, since `replay_plan` drives both readers.) Batching the per-record insert was explicitly deferred, not forgotten (see
above).
**What it blocks:** capture latency at volume. **Not a first-customer gate** — hosted capture is off
by owner ruling (2026-10-14) and the beta volume is unmeasured. **But it must land before the per-record cost of a real
write volume can be measured.** ~~Cheapest fix is the pooler DSN plus caching the column map; batching is the larger
change and needs the ordering-across-three-doors question answered.~~ ✅ **Superseded by what landed:** the fix was an
IN-PROCESS pool behind `_default_connect` plus a stat-keyed column-map cache — not the pooler DSN, which remains a
deployment option nobody has needed (the measured cost was the connect+auth handshake itself, which the pool removes at
the client). Batching is still the larger change and still needs the ordering-across-three-doors question.
**Decision needed:** none — the item is landed; batching's shape (and #11388's fingerprint hoist) are the open scopes.

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
| **N1** | **The read path and its cutover** (`#7970` — **CLOSED COMPLETED 2026-10-25**; the *cutover* is what is deferred) | §2.0 defers the cutover in the owner's own words (§0.1). The instruments exist and are off (`TORTOISE_RECORD_SERVE` unset). |
| **N2** | **`pgvector` / moving the vector index into Postgres** (`#4997`, **CLOSED 2026-10-05** — its analysis is what this item rests on; §12.1c **V1**, §14.3) | **DECIDED 2026-11-07 — ADOPTED** (`docs/architecture/SEARCH-ARCHITECTURE.md`): the vector index and its payload move to Postgres (`pgvector`). What is not decided is WHEN — not on this epic's critical path. Embeddings are already durable as `jsonb` (A5). Note the settled blocker now that it is decided: **`pgvectorscale` is unavailable on Supabase** (§14), so it would be `pgvector` proper. |
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
| `GRAPH.MEMORY USAGE` | **143 MiB** (indices 46 MiB) | sampling estimate, `SAMPLES`=100; **blind to the HNSW index** (§2.1, dated 2026-09-24, carries the earlier ~140 MB reading with those caveats; the 143 figure is the top ruling block of 2026-09-25 (`STORAGE-ARCHITECTURE.md`, the unnumbered block at the top of the document, which writes it in **MB**; §17.4.2 restates it in MiB in its measured-inputs table)) | **MEASURED (estimate by construction)** |
| bytes per resident node | **1,672 B** | 143 MiB ÷ 89,701 | **MEASURED** |
| in-graph text | **16 MB** | §2.2 breakdown | **MEASURED** |
| in-graph vectors + indices | **51.6 MB** stored vectors; **45 MB** of indices — the **total** index footprint, of which only the `Point.embedding` HNSW index is vector-driven (§12.1b: exactly one vector index in the graph). ⚠️ These belong to §2.2's ~141 MB reading; the ruling block gives this pair as **143 MB with 46 MB of indices** (`STORAGE-ARCHITECTURE.md`, the same unnumbered block) and §17.4.2 restates it in MiB — the two readings are contrasted in that block, in its *"Per-node figures carry different denominators"* paragraph — so quote 143/46, not 141/45 | §2.2 / §12.1b (2026-09-24) | **MEASURED** |
| stored vectors, by label | **12,657** (`:Object` 7,859 + `:Event` 4,798, ≈19 MB) — and only `Point.embedding` is indexed | `CALL db.indexes()`, §13 | **MEASURED** |
| Points carrying any content | **49.0%**; **1,121.4 B** mean over those, **549.1 B** over all (5,696,946 B over 10,374) | `§17.4.6` / `graph-content-census.json`, 2026-10-17 | **MEASURED** |
| Points carrying a vector | **39.96%** (4,145 / 10,374) | same census; `§17.4.6`, 2026-10-17 | **MEASURED** |
| record footprint per tenant (Postgres) | **0.1916 GB** = **2,136 B/node on disk** (heap + indexes + TOAST) | bench on the committed schema; `§17.4.6`, 2026-10-17 | **MEASURED — a LOWER BOUND** (no TOAST regime; vector written at 6 dp vs the journal's real 21.9 chars/float). ⚠️ Cut WITHOUT #10017's `cache_tier`/`idx_point_graph_tier`: the per-tenant footprint becomes ≈2,234 B/node (+4.55%), so `ρ` and every break-even derived from this line move with it — measurement in #10070 comment 6426999081, basis note in the 7983 evidence README — the delta is the partial INDEX, not the column |
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
4. **R4** the schema↔producer disagreements (**DONE** — #9264 + #10620) → *so the session/point record is complete*
5. **R6** tenancy enforced → *the bypass rule must be answered before the first hosted customer's data
exists*
6. **R5** the edge/operator family → *before the "destroy the graph" claim can be made*
7. **R7** raw text + S1 narrative in Postgres → *for the same claim: the record has no narrative-first
home — the text is carried as data (an episodic `point.content` row and its `journal_event.payload`),
so a record-sourced rebuild loses the narrative, not the text*
8. **R8** a restore verb + a drill → *a restore that has never been run is not a restore*
9. **R9** back up the record → *✅ the module + verb LANDED (#9557); what is open is the cron that runs it and a drill against the hosted record — see R9 above*

**What can wait:** **R10** (pooling — gated on a captured tenant write volume, not a date), **R11** (the byte gate and the
meter → allowance wiring — gates the first *allowance*, not the first customer), **N1** (the read path, by
§2.0), **N2** (`pgvector`, decided 2026-11-07 — the move is not on this epic's critical path), **N5** (the failure alert, while the cache is full).

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
  (R9), and `pgvector` index-build behaviour at our size (**now owed unconditionally — V1 was DECIDED on
  2026-11-07**, so this is no longer conditional on it).
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
what **R3** was about. #9551 (PR #9772) armed the leg **in CI only** — the job supplies the DSN and the
extra — so a LOCAL default-env run still reports these 2 skips, exactly as this run does.

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
point.embedding type → jsonb                              ← A5 (no pgvector)   ⚠️ STILL jsonb after #11642 — the vector is a GENERATED `embedding_vec` beside it
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
  docs/infra-runbook.md                    → (no output) when taken; ⚠️ 1 line after #9556/R8 (see below)
grep -riE 'pgvector|vector\(' supabase/migrations/*.sql | wc -l              → 0   ⚠️ 28 after #11642 (one file: 20261109000001_record_embedding_vector.sql)
grep -riE 'narrative|raw_turn|raw_text|transcript' supabase/migrations/*.sql → 0   ⚠️ see below
grep -rn 'app.current_org_id' --include=*.py tortoise/ tools/                → 0 setters   ⚠️ see below
grep -rn 'rebuild_from_record' tortoise/__main__.py                          → 1 (the verb's docstring)
grep -rn 'rebuild_from_record(' tortoise/ | grep -v 'def '                  → 1 (`record_restore.py` — the `record-restore` caller)
# ⛔ Both were 0 before #9556 landed the operator verb. The residual gap is NOT this census: it is
# that a production graph name (`org_*`) is refused by #2944's L2 guard (see refusal 4 in runbook §9).
grep -rniE '\bpostgres\b|psycopg|pg_dump' tortoise/backup.py tortoise/hosted_backup.py \
  tortoise/backup_sweep.py tortoise/selfhost_backup.py                            → 0
```

⚠️ **The first census line is superseded by #9548 (2026-10-23)** — over that command's own path set
(`fly.toml entrypoint.sh .github/workflows/ docs/infra-runbook.md`) it now returns the deploy
workflow's bind/probe/stage lines, the runbook's row and section, and `fly.toml`'s `[env]` comment.
⛔ **It was NOT empty immediately before #9548 — and it was not one line either.** Measured over
that command's own path set at **`ff6379eb2`** — an ancestor of this branch's base (the #9601
merge, 2026-10-23), and **NOT** the tree immediately before #9548 — that tree reads
**5 lines**, all in the runbook, none of them an arming surface:

1. `docs/infra-runbook.md:382` — #9557/R9's `record-backup` section: the DSN is read from the
   environment. **Documentation** of a verb, one lane over;
2. `docs/infra-runbook.md:528` — the same, in R9's drill commentary;
3. `docs/infra-runbook.md:2774` — #9556/R8's §9 `record-restore` operator example
   (`export TORTOISE_RECORD_DSN=…`): an **invocation**, not an arming surface;
4. `docs/infra-runbook.md:2971` — #9600's Secrets Matrix row (`| TORTOISE_RECORD_DSN |`): the name
   **documented** as required-when-armed, never provisioned;
5. `docs/infra-runbook.md:3004` — #9600's §"The record's connecting role" example
   (`TORTOISE_RECORD_DSN=postgresql://tortoise_record_app.`): the DSN the operator is told to build.

⚠️ **The count is per tree — pin the tree or the number is a stale flag, and pin BOTH ends of an
interval or it silently re-stales.** It is **0** at this branch's start point `d5c71a470`, **1** at
`473ff8a6f0`/`7761252f` (after #9556/R8, before #9600), **3** at `c5cbcbb4f` (#9600/#9554);
**5** at `ff6379eb2` (an ancestor of this branch's base — the #9601 merge, 2026-10-23 — which
added the two `record-backup` mentions), **10** at `047c91f7a` (the commit immediately before
the #9548 arming landed in `785bb2b59`), and **20** after it. The pre-#9548 reading is
**10 lines** at `047c91f7a` — the number the "before #9548" comparison is against, and it is
NOT the 5 this note used to quote (that is the `ff6379eb2` ancestor, 12 commits earlier, which
is the "pin one end of an interval" error this paragraph exists to warn about). An earlier version of
this note pinned `c5cbcbb4f` and **3**, and was stale by the very next main-side doc edit: R9's two
lines were invisible to the pin because nothing bound the interval's upper end. ⚠️ The `:NNNN`
above are `ff6379eb2`'s line numbers and they drift as the runbook grows — re-measure by grepping
the name against a named sha, not by trusting the offset.
Stating all of it is the whole point of this note: a later lane re-running the census must be able to
tell a `record-restore` invocation **and** a *documentation* line apart from a #9548 arming surface —
and the "1 line" framing silently merged #9600's two documentation hits into a story about one
operator invocation, which is how the "the name is written nowhere" claim got re-certified across a
tree where it was false.
The manifest declaration (`TORTOISE_RECORD_DSN  gh-secret:TORTOISE_RECORD_DSN` in
`.github/scripts/fly-managed-secrets.txt`) is the fourth surface, and it is **not** in that path set —
naming the file is what returns it:

```text
grep -rn TORTOISE_RECORD_DSN .github/scripts/fly-managed-secrets.txt   → 1 line
```

`entrypoint.sh` is deliberately still empty: R1's components are the
three surfaces above, and boot-time arming output is not part of it. What the old grep stood in for
(`NOT_ARMED`) was a **census reading, and it is not one state** — which is why the sentence that stood here was
wrong twice over. *At `ff6379eb2`* the name was **documented, and nowhere on an arming surface** — 5 lines in the runbook, and
none in `fly.toml`, `entrypoint.sh` or `.github/` (`git grep TORTOISE_RECORD_DSN ff6379eb2 -- fly.toml
entrypoint.sh .github/` returns nothing), so `NOT_ARMED`: not staged, not provisioned. (⛔ **Not "the runbook
only"** — `git grep -c TORTOISE_RECORD_DSN ff6379eb2` also matches `.env.example`,
`config/ci-surfaces.yml`, `docs/00_index.md`, this file, `09-record-tenancy-decision.md`, six
`tortoise/*.py` modules and the migrations/tests — **21 files in total** at that tree, so this
list is illustrative, not exhaustive. The *arming-surface* claim is the one the
grep supports.) *After #9548 landed the surfaces* the name was **staged** and the secret still absent.
⛔ **Neither is the state now:** the GitHub and Fly secrets both exist and `/health` reports `armed: true`, so this
paragraph is the census point's evidence, not a present-tense fact. ⛔ **The remaining census
lines were re-measured at this head, not assumed unchanged — and two of them no longer return what
the block states**: the `narrative|raw_turn|…` grep (0 → non-zero since #9555) and the
`app.current_org_id` setter count (0 → the hits #9554 shipped; see the notes below). The `pgvector`,
both `rebuild_from_record` and backup-module lines still return the stated counts.

⚠️ **Re-measured 2026-10-22 (#9555): the third command is no longer 0.** The block above stands as
what was run at `8767b1c8b`; at this head the same `narrative|raw_turn|raw_text|transcript` grep
returns **62 lines, all in** `20261025000001_record_tenancy_role_and_guc.sql` (7), `20261023000001_record_session_narrative.sql` (36) and `20261024000001_record_session_narrative_carrier.sql` (19) — R7's table, the tenth
record table (§A1). The other commands were re-run at this head and still return the counts the
block states, with the one exception below (`app.current_org_id`).

⚠️ **Re-measured 2026-10-24 (#9554, merged in from `main`): the fourth command is no longer 0.**
The block's `grep -rn 'app.current_org_id' --include=*.py tortoise/ tools/ → 0 setters` is the
pre-R6 reading. R6's tenancy boundary now ships the GUC, so the same grep returns
**2 lines at `ff6379eb2`** — `tortoise/record_projection.py:227` (`TENANT_GUC =
"app.current_org_id"`) and its docstring at `:3269`, the offsets `c5cbcbb4f` (R6's tenancy boundary)
introduced — and **13 lines at this head** (#11642's `tortoise/record_vector.py` added the thirteenth:
its module docstring names the GUC when it explains why the searcher can never invent a tenant — the
tripwire doing its job): `54a3e7abe` (R9's backup role must BYPASS RLS) added three
`tortoise/record_backup.py` mentions, #9555 (R7's narrative fetch) added three more —
`tortoise/record_text.py` ×2 and `tortoise/__main__.py` ×1 — and #10017's copy added the ninth,
in `tools/backfill_copy_turns.py`: the comment on its floor read that states why the read must go
through `scope_connection` at all (a `tools/` mention is in this grep's scope, so a new tool that
names the GUC moves this count too — that is the tripwire working, not a flake), plus the two
the copy's `cache_tier` statement added (the `_RECORD_ONLY_TIER_SQL` comment, and the refusal
message that names the GUC as the reason a wrong tenant's UPDATE matches zero rows instead of
erroring) — 9 → 11 when that statement landed, and #10000's turn read added the twelfth,
`tortoise/record_read.py` (`session_turns`' docstring, recording WHY the tenant scope is installed
by the turn read itself: an unscoped connection reads ZERO rows under this policy, so the scope
cannot be the opener's caller's job) — 11 → 12. ⛔ The sites are named by CONTENT and
NOT by offset: the projection's two have moved on every change that touched that file (this note
listed `:227`/`:3269`, then `:228`/`:3296`, then `:230`/`:3359`, and #9555's own registry entry moved
them again) — a third offset in a note whose next sentence says the offsets are unusable is how a
census note starts certifying a line nobody can find. The
setter is `TENANT_GUC_SQL`, `set_config('app.current_org_id', $1, true)`, installed by
`record_projection.scope_connection()` before the first plan statement of both record doors. The
sites are named by CONTENT rather than by offset because the offsets move on every edit, and the
count carries its tree because a bare count re-stales on the next main-side edit — that is the
failure this note exists to close. That is why the row above reads *"RLS as the tenancy boundary —
SHIPPED"* while this census line still says zero; the census measured `8767b1c8b`, and
[`09-record-tenancy-decision.md`](09-record-tenancy-decision.md) is the decision that moved it.
