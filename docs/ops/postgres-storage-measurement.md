---
title: "Postgres storage footprint + per-org analytics row growth (#4492)"
type: operations
domain: economics
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-supabase-control, tortoise-monitoring
aboutObjects: tortoise, docs/architecture/STORAGE-ARCHITECTURE.md
created: 2026-10-15
updated: 2026-11-14
---

# Postgres storage footprint + per-org analytics row growth (#4492) — and per-org bytes from what we WRITE (#12762)

> **This is a MEASUREMENT, not a policy.** It answers two questions the B7
> cost-structure audit found unanswerable: *how large is the control plane's
> Postgres?* and *which org is producing `analytics_events` rows?* There is **no
> price, tier boundary, quota, cap, dashboard or entitlement** here, and **no
> retention decision** — the two SQL functions only read.

## Why this exists

Two cost dimensions had **no primitive at all**:

- **The control plane's on-disk footprint was unmeasured.** `git grep
  "pg_database_size\|pg_total_relation_size\|row_count"` over `tortoise/` →
  empty. The Supabase tier we must buy could only be sized on guesswork.
- **`analytics_events` volume was counted by OUTCOME, not by org.**
  `monitoring.ANALYTICS_OUTCOME_COUNT` labels by terminal outcome only, with no
  org label, so the org that produced the rows could not be identified — an org
  generating disproportionate telemetry volume was indistinguishable from a
  quiet one.

Both facts were measured on `audit/cost-structure-coverage` @ `f0d004914` and
re-verified on `main` while this landed.

## The two read paths

Both are **read-only** Postgres functions (`SECURITY DEFINER`, executable only
by `service_role`) in migration
`supabase/migrations/20261015000001_postgres_storage_footprint.sql`. Each returns
**one `jsonb` value**, so PostgREST's `db-max-rows` cap cannot truncate the
result — a silently short read would *understate* a footprint, which is the
fail-open a measurement cannot have.

### 1. Footprint — total + per-table bytes

```sql
select jsonb_pretty(postgres_storage_footprint());
```

Returns `database_bytes` (the whole database, every schema), `captured_at`, and
`tables[]` — ordered by `total_bytes` descending, each with:

| field | meaning |
|---|---|
| `name` | the table |
| `total_bytes` | `pg_total_relation_size`: heap + indexes + TOAST |
| `heap_bytes` | `pg_relation_size`: the table's main fork |
| `index_bytes` | `pg_indexes_size`: the table's indexes |
| `live_rows` | `pg_stat_user_tables.n_live_tup` — a **planner statistic**, see below |

The useful cost subset is one query, e.g.:

```sql
select t->>'name' as table, (t->>'total_bytes')::bigint as bytes
  from jsonb_array_elements(postgres_storage_footprint() -> 'tables') as t
 where t->>'name' in ('analytics_events', 'metering_records', 'organizations');
```

### 2. Per-org analytics row growth

```sql
select jsonb_pretty(analytics_events_rows_by_org(now() - interval '7 days'));
```

Returns `since`, `until` (null when the window is open-ended), `total_rows` and
`rows_by_org` — an **exact** `count(*)` per org, returned as a JSON **object
keyed by `org_id`**. `jsonb` stores an object's keys in its own canonical order
(length, then bytewise) and discards any aggregate `ORDER BY`, so **object key
order is not a guarantee** — read `rows_by_org` as an unordered mapping, not a
ranking. The window is **half-open** `[since, until)`. An empty window is a
measured `0` and `{}` — never an absent key.

## The attribution boundary — read this before quoting a per-org byte figure

> ### ⚠️ PARTLY OVERRIDDEN by #12762 — read the next section first
>
> **OVERRIDES:** the position that *"Per-org BYTES would be an ALLOCATION, not a
> measurement"* — replaced by a **write-time measurement of stored bytes**
> (`sum(pg_column_size(row))` over the org's own journal rows,
> `tortoise/postgres_bytes.py`), because a byte written for an org and measured
> as stored is that org's own byte. What survives of the old position is exactly
> its original target: apportioning the database **total** by a proxy is still
> an allocation, and index / dead-tuple / page-slack / TOAST-chunk-header bytes
> are still **not** attributable per org — they are published as a labelled
> residual and never folded into a per-org figure.

| dimension | grain | how it is attributed |
|---|---|---|
| storage **bytes** — the database footprint | database / table | not per-org (unchanged, #4492) |
| storage **bytes** — what an org WROTE | per org | **measured** at write time (#12762, below) |
| analytics **rows** | per org | `rows_by_org` from the growth function |

**Postgres records no per-org byte ownership** — the *server* does not, and that
is still true. What the server cannot do, the **writer** can: the org that wrote
a row is known at the write, so the bytes of the row it stored are its own. That
is a measurement of the account's own stored content, not a share of a total.

## Per-org bytes from what we WRITE (#12762)

### The TOAST question, settled by measurement — and the issue's premise is FALSE

Issue #12762 was filed on the premise that `sum(pg_column_size(...))` reports
the **18-byte TOAST pointer** for a value moved out of line, a ~100×
undercount.
Two sources were said to disagree, so this was measured rather than cited.

**Setup:** containers `postgres:17.12` and `postgres:16.9`, plus the repo's
PGlite harness (`18.3`), a table shaped exactly like `public.journal_event`
(`org_id`, `graph_id`, `seq`, `event_type`, `payload jsonb`). Three engines, same
answer.

| payload | JSON text | `pg_column_size(payload)` | out-of-line chunks | chunk_data bytes | `pg_column_size(row)` |
|---|---|---|---|---|---|
| small | 47 | 59 | 0 | 0 | 118 |
| 4 KB repetitive text | 4,045 | **114** | 0 | 0 | 174 |
| 1,536-float vector | 15,428 | **7,177** | 4 | 7,177 | 7,241 |
| 19 KB incompressible | 19,245 | **19,256** | 10 | 19,256 | 19,320 |

1. ⛔ **`pg_column_size` does NOT report the pointer.** The 19 KB row is out of
   line (10 chunks, `pg_column_toast_chunk_id` non-NULL) and reports **19,256** —
   its chunk sum exactly. The disagreeing sources resolve to *full stored size*.
2. **It reports the COMPRESSED stored size** where compression applies —
   15,428 → 7,177 out of line, and 4,045 → **114** compressed *inline* (the only
   real “shock undercount”, and it is simply the physical truth).
3. ⇒ **`pg_column_size(row)` is already a per-row, per-org, stored-byte
   measurement**, and no TOAST pointer can fool it.

### The trap that IS live: double counting

`pg_column_size(row)` **already contains** an out-of-line value's bytes. Adding
the TOAST chunk bytes double counts — measured 7,241 + 7,177 = **14,418** for a
row that occupies 7,241. `chunk_bytes` is therefore returned by both functions
**for disclosure only** and is never added to a per-org total; both parsers
refuse a reply where `chunk_bytes > row_bytes` (the shape that double count
produces).

### The naive write-time measure over-counts (the falsifier's target)

`INSERT … RETURNING pg_column_size(payload)` sees the datum **before** the row is
stored — pre-TOAST, pre-compression, and for `jsonb` in its uncompressed binary
form. For our own record shape it reports **24,606** bytes for a payload that
occupies **7,177**: a 3.4× **over**-count, in the direction that over-bills. The
meter therefore reads the row BACK (one indexed read per write segment, in the
caller's transaction) instead of taking the free-looking `RETURNING` value.

⚠️ The gap is **shape-dependent**: on the 19 KB incompressible payload
`RETURNING` gives 19,260 against 19,236 stored — a 1.001× difference. The
falsifier in `supabase/tests/20261114000001_per_org_postgres_bytes.sql` uses the
**vector** shape and additionally asserts the gap is at least 2×, so the pin
cannot silently become toothless.

### The two halves, and the residual

| piece | what it is | where |
|---|---|---|
| **meter** | `sum(pg_column_size(row))` over the journal rows a write segment just wrote, in the caller's transaction | `journal_event_bytes_for_rows(org, graph, seqs)` → `postgres_bytes.measure_written_rows` → `metering.record_postgres_bytes_written` |
| **audit** | the same figure for the whole table, per org, later and independently | `postgres_per_org_bytes()` → `postgres_bytes.per_org_bytes` |
| **residual** | heap page slack, dead tuples, the PK index, TOAST chunk tuple headers | `postgres_per_org_bytes()` → `residual` (**never** folded in) |

**Residual, measured** on a mixed 404-row / 3-org set: Σ`pg_column_size(row)` =
2,940,364 B; chunk_data = 2,917,583 B in 1,614 chunk rows; TOAST relation
3,309,568 B; heap 32,768 B; index 40,960 B; total 3,506,176 B → **attributed
83.9%**, un-attributable **16.1%**.

**Reconciliation direction.** `postgres_bytes.reconcile` compares the
accumulated counter with the audit and names which way they disagree:

- counter **below** the audit → `unmetered_bytes`, disclosed, **not an error**
  (rows written before the meter existed, or a write that bypassed the seam);
- counter **above** the audit by more than 1% → **not ok**: the two sum the same
  per-row figure, so an excess is the double count or a fabricated number, and
  the reading says so rather than quietly under-billing.

**Only the rows THIS transaction inserted.** The journal's insert is
`ON CONFLICT (org_id, graph_id, seq) DO NOTHING` and the table is append-only, so
a write that LOSES a position collision (reachable between two durable journals
— #9307) stores nothing, while those positions still exist with bytes. Summing
by position alone would charge the org a **second time**, and no row count
could reveal it. The meter therefore filters `xmin = pg_current_xact_id()`: measured,
an own insert matches and a suppressed conflict carries the older xid. It must
be called **in the write's own transaction** (outside one it reports 0 rows —
that is the contract, not a bug). ⛔ **It runs under a `SAVEPOINT`**, and the
reason is the one thing swallowing its exception cannot buy: a failed statement
leaves a PostgreSQL transaction **ABORTED**, so the write's own `commit()`
becomes a ROLLBACK and the whole record write disappears while the door reports
`APPLIED` (#12781, `main` red at `9e934efad5` with ten empty-record failures).
`ROLLBACK TO SAVEPOINT` undoes only the failed read and the rows — inserted
BEFORE the savepoint — still commit. MEASURED on PG16 with the meter refused
(`InsufficientPrivilege`): **0 rows** committed without the savepoint, **1 row**
with it. ⚠️ **NOT when the savepoint bookkeeping itself cannot be issued**
(`25P02` on an already-aborted transaction, `25P01` outside a block, a dead
session): the transaction is unusable at that point, its `commit()` still rolls
the write back, and `_meter_statement` says so at WARNING rather than at DEBUG —
DISCLOSED, not repaired. Nothing in the meter can repair it; only the caller
knows whether its transaction is still worth committing.

The savepoint does **not** skew attribution, which is why it is a savepoint and
not a second connection: the meter is a READ, and PostgreSQL keeps the
**top-level** xid for a subtransaction that performs no write. MEASURED on PG16:
inside the savepoint `pg_current_xact_id()` returned the same `919` as before it,
the row's `xmin` stayed `919`, and `journal_event_bytes_for_rows` still reported
`rows: 1`. (The earlier wording here — *"under a SAVEPOINT it under-counts"* —
is true only of a subtransaction that itself WRITES, i.e. of a savepoint placed
BEFORE the insert, which is not what this does. ⛔ The same sentence still stands
in the header of `20261114000001_per_org_postgres_bytes.sql`, which is
APPEND-ONLY (`check-migration-append-only` refuses a modified migration) and
therefore cannot be corrected in place: this document and
`postgres_bytes.measure_written_rows`'s docstring are the correction, and neither
the header's "takes no savepoints today" nor its "under-counts" clause is a
reason to remove the savepoint.) **Both orderings are pinned, not argued** — and
by TWO artifacts, because neither alone closes the loop:
`supabase/tests/20261114000001_per_org_postgres_bytes.sql` §7b pins
PostgreSQL's **semantics** (`rows: 1` for savepoint-after-insert, the shipped
order; `rows: 0` for savepoint-before-insert, the hazard), while
the **shipped CALL ORDER** — that the meter runs after the insert it measures —
is pinned on the source by
`tests/test_postgres_bytes.py::test_the_meter_call_lands_AFTER_the_insert_it_measures`.
§7b is a static script and never calls the module, so it stays green if the
Python ordering inverts; the source pin is what reds then. An **autocommit**
connection
takes no savepoint: each statement is its own transaction, so a failure there
aborts nothing the caller owns.

**Cost, stated so it is not re-derived as waste.** A metered write becomes THREE
statements where it was one (`SAVEPOINT` + the read + `RELEASE`), i.e. **+2 round
trips per metered segment**. The live door commits once per record
(`write_records([record], …)`, `record_writer.write_records`), so that is +2
per record — on the same path whose per-record latency the connection pool was
introduced to cut (18.9–20.1 ms → 3.82–3.91 ms; `record_writer._default_connect`).
The delta is inherent to protecting the write and is the right trade, but ⛔ it is
NOT a reason to delete the savepoint: that re-creates #12781.

**Measured, then RECORDED after the commit.** `RecordWriter.write` only
*measures*; the ledger increment happens where the commit is known
(`write_live_record`, strictly after `connection.commit()`), because the ledger
lives on another store — charging it before the commit would leave a
rolled-back write's bytes on the org's figure forever, and the counter refuses
negatives, so there is no repair path. A caller that injects its own connection
owns that decision and receives the figure on `RecordWrite.written_bytes`. The
`tools/backfill_copy_turns.py` backfill path therefore lands **unmetered** on
purpose (a migration is not usage), and `reconcile` discloses it.

**What the meter does not do.** It measures rows written from the day it lands.
It does not backfill, does not prune, and does **not** arm `allowance_gate`.

**The gauge the gate reads (`#9559`).** The audit above is a whole-table
aggregation with a per-row TOAST lookup, so it can never run on a write path —
which is why `postgres_storage` shipped blocked for want of a figure the gate
could afford. `#9559` supplies it as a SNAPSHOT: `org_postgres_residency`
(migration `20261117000001`) holds one row per org with the bytes the audit
ATTRIBUTED to it and the instant it measured them, refreshed **offline** by

```sql
select * from public.postgres_residency_snapshot();
```

The write path does one primary-key lookup — `metering.get_postgres_residency` →
the gate's `_postgres_stored_bytes` — which is O(1) in the database's size, and
that affordability is what the block required. **The residual is never
apportioned**: only the measured per-org figure is stored.

⛔ **The snapshot has an age, and the gate enforces a bound on it.** The dimension
declares `stale_after_seconds` (24 h, the same evidence-age bound the graph gauge
uses). A reading older than that, or one with no `measured_at` to age, is
**refused** — never enforced.

⛔ **"No row" is THREE answers, and the reader names which one it is**
(`unmeasured_reason`, the vocabulary `get_capture_usage` already uses). Flattening
them is either a fail-open or a deadlock, so they are separate:

| `unmeasured_reason` | when | what it means | gate outcome |
|---|---|---|---|
| — (`measured: true`) | a row exists for the org | the audit's figure, aged by `measured_at` | compared against the cap |
| `absent` | no row for this org, **but other orgs have rows** | the gauge has reported and this org's rows store nothing — a **true zero**, stamped with the gauge's NEWEST refresh | **served** as 0 bytes, and **refused as stale** once that refresh ages past the bound |
| `unreported` | the snapshot table is **EMPTY** | nothing has been measured for **any** org | **refused** (a named no-reading error) |
| `read_error` | the control plane failed | our fault | **refused** (named, logged at ERROR) |

⛔ **The zero-by-absence is STAMPED, not unbounded** — and that is the difference
between a deadlock fix and a silent permanent bypass. The snapshot table only ever
GAINS rows (orgs are not deleted here), so "the gauge has reported at least once"
becomes true forever after a single run. A zero carrying no instant would then
serve every never-snapshotted org at 0 bytes for good, while an org WITH a row of
the same age is refused — the operator's signal pointing the wrong way. So the
reader returns the newest `measured_at` with the absence, the meter emits a
`MeterReading(used=0.0, measured_at=<that>)`, and the dimension's existing
`stale_after_seconds` ages it. That is also why the dimension does **not** declare
`absent_is_zero`: that declaration routes the unageable `ABSENT` sentinel around
the bound, which is right for a cumulative flow (a fresh window genuinely has no
age) and wrong for a gauge.

Why `absent` is served and not refused — and why that is not a fail-open: the
snapshot row is **derived from the org's rows**, and rows only exist once a write
is served. Refusing `absent` refuses the very write that would create the row, so
a fresh tenant's **first** write would be refused forever. Measured on the sibling
gauge in CI (`#13084`/`#13102`): a paid tenant's first `POST /v1/points` returned
**HTTP 500** from exactly that collapse. `absent_is_zero` is declared on the
dimension so the gate consumes the answer in one place, and it is safe **strictly
because** the empty table is `unreported` and still refuses — otherwise every org
would compare as 0 bytes and pass any cap.

⛔ **Nothing schedules the refresh yet.** That is a deploy decision (no migration
here declares `pg_cron`; an external caller is an operator's choice), and it is
disclosed rather than assumed: while the table is empty the gate reads
`unreported` and refuses a configured allowance, so an unscheduled snapshot
**over-blocks** instead of billing from a guess. One successful refresh is what
turns "nothing measured anywhere" into "this org stores nothing, served". Schedule
it — and verify a real org's row exists — before arming.

**What still blocks arming.** Exactly one thing, and it is the owner's: the
`free`/`anon` included storage amounts are recorded as `DERIVED, NOT
OWNER-CONFIRMED` in `product/pricing.json`. The gate declares the live state in
`allowance_gate.RELEASE_CONDITIONS`; `#12762`/`#9559` are measurement and
instrument only — `product/pricing.json` and the tier boundaries are untouched.

**Retention.** `#3843`/`#3853` (“no retention window of any length”) bind
`analytics_events` (migration 0004's immutable trigger). They say nothing about
`journal_event` bytes, and this change adds no prune to either.

## Precision — what is exact and what is an estimate

| figure | quality |
|---|---|
| `database_bytes`, `total_bytes`, `heap_bytes`, `index_bytes` | **exact** on-disk sizes |
| `live_rows` | **estimate** — `n_live_tup` is refreshed by autovacuum/`ANALYZE` and can lag the truth |
| `total_rows` / `rows_by_org` | **exact** — a real `count(*)` |

`live_rows` is an estimate and is reported as an `int|null` — `null` means "the
planner did not report it", never 0. **Neither figure is invoice-grade**: the
vendor bill is measured by the platform, not here.

## RETENTION — `analytics_events` has no retention path (the finding)

**Stated plainly, because the issue asked for it explicitly: there is no
retention policy for `analytics_events`, and none can be added without changing
an append-only guard.**

- `supabase/migrations/0004_analytics_events.sql` installs a **statement-level**
  `BEFORE UPDATE OR DELETE` trigger (`analytics_events_immutable`) that
  `RAISE EXCEPTION`s, **with no role gate** — not even `service_role` can delete
  a row. `DELETE FROM analytics_events …` fails at any age.
- The owner ruled (#3843, #3853) that the per-capture/per-team usage record is
  **kept**, and that product-usage analytics *"can't be fully deleted but gets
  decoupled from your identity (which is fully deleted)"*. The decoupling is the
  identity deletion: `analytics_events` has **no FK** to `organizations`, so no
  cascade reaches it, and the hard-deleted `organizations` row is what carried
  the name. The surviving row keeps only a random `org_id`.
- `tortoise/retention_indicator.py` states the same in code: *"no row is deleted,
  pruned or purged by this module at any age. The owner's standing ruling is 'no
  retention window of any length'"* — there the word *retention* names team
  **behaviour**, not a data window.

**Consequence for anyone reading the growth number:** the table grows
**monotonically by design**. A figure that looks large is not a leak to sweep —
it is the intended record. Adding a prune is a product/legal decision, not a
measurement change; this work deliberately did not add one, and the PGlite
assertion suite reds if the append-only guard ever disappears.

## Reading it from code

`tortoise/postgres_storage.py` is the consumable wrapper — it calls the two RPCs
through the control-plane seam (`SupabaseControlPlane.rpc_value`) and returns
readings that **carry their caveats** (`ATTRIBUTION_NOTE`, `PRECISION_NOTE`,
`RETENTION_FINDING`) so a figure cannot be quoted without them:

```python
from tortoise import postgres_bytes, postgres_storage

reading = postgres_storage.measure_postgres_storage(control_plane)   # bytes
growth = postgres_storage.read_analytics_row_growth(                  # rows
    control_plane, since="2026-05-01T00:00:00+00:00")
growth.for_org("org_abc123")

# #12762 — the per-org half. The meter runs on the record write path; these are
# the two reads, and the reconciliation that puts them side by side.
audit = postgres_bytes.per_org_bytes(control_plane)          # per-org + residual
audit.for_org("org_abc123")
postgres_bytes.reconcile("org_abc123", metered_bytes=..., audited_bytes=...)
```

Both callers are **fail-soft**: a dead control plane or a malformed reply returns
a reading with `ok=False` and an `error`, never a raise and never a fabricated
zero.

## What this is NOT

- **Not a price, cap, tier or quota.** No rate, boundary or entitlement is
  touched; nothing is refused or throttled; `product/pricing.json` is untouched.
- **Not a dashboard or UI.** The read path is the SQL function; the Python
  wrapper exists so the figure is consumable and testable.
- **Not a counter.** No Prometheus metric is registered for either dimension, and
  in particular `ANALYTICS_OUTCOME_COUNT` is deliberately **not** given an org
  label. The issue's diagnosis was that "the org that caused the rows cannot be
  identified from the metric"; the fix here is an EXACT per-org `count(*)` a
  person can query, not an org-labelled time series. A `{org=…}` child would
  make the org knowable only for the orgs the label cap admits, and the per-org
  label-admission policy is exactly the question **#5045 records as OPEN** (the
  #4493 `MAX_ORG_LABELS` stateless convention vs the #4491 `EGRESS_MAX_ORGS`
  persistent one). Settling it inside one dimension is how the convention forks
  again, so it is left to the substrate — and a registered-but-fabricated metric
  would read as a measurement while being a lie (the #4493 lesson).
- **Not a prune.** See the retention finding above.

## Verifying it

```bash
# The two functions, against the real SQL (no Docker):
cd supabase/tests/pglite && npm ci --no-audit --no-fund && npm run validate

# The wrapper + the SQL↔Python contract (hermetic):
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_postgres_storage.py -q

# #12762 — the per-org meter, the audit and the falsifier (needs the DB lane):
uv run python tools/test_lane.py run -- tests/test_postgres_bytes.py -q
```

The **falsifier** — a multi-KB TOASTed payload must be counted at its full
stored size — is `supabase/tests/20261114000001_per_org_postgres_bytes.sql`
(§3–§5): it proves the pointer claim is false *with its numbers*, then proves
the naive `RETURNING` write-time measure is 3.4× the stored size on the shape we
actually write, and that both the row+chunks double count and a pre-TOAST
payload are **refused**. It goes red if the meter is ever rebuilt on either
naive method.

Linked: parent substrate **#5045**; sibling dimension lanes **#4488** (embedding
encode), **#4489** (retrieval), **#4490** (machine time), **#4491** (egress),
**#4493** (shared-SaaS allocation); unit ruling and graph meter **#5331**;
per-org write-time bytes **#12762** (reopens the #4492 grain position above).
