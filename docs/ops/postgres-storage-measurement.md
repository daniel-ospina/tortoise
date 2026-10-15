---
title: "Postgres storage footprint + per-org analytics row growth (#4492)"
type: operations
domain: economics
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise-supabase-control, tortoise-monitoring
aboutObjects: tortoise, docs/architecture/STORAGE-ARCHITECTURE.md
created: 2026-10-15
updated: 2026-10-15
---

# Postgres storage footprint + per-org analytics row growth (#4492)

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

| dimension | grain | how it is attributed |
|---|---|---|
| storage **bytes** | database / table | **not per-org** |
| analytics **rows** | per org | `rows_by_org` from the growth function |

**Postgres records no per-org byte ownership.** A per-org byte split is
therefore an **ALLOCATION**, not a measurement — a policy applied to a stated
total. This doc publishes none, and neither does the code: inventing one here
would be exactly the mistake the #5045 substrate exists to prevent. If an
allocation is ever wanted, it belongs in the substrate's declared dimensions
(`docs/ops/cost-allocation.md` is the precedent for how an allocation is
declared and named as one), not silently inside a byte counter.

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
from tortoise import postgres_storage

reading = postgres_storage.measure_postgres_storage(control_plane)   # bytes
growth = postgres_storage.read_analytics_row_growth(                  # rows
    control_plane, since="2026-05-01T00:00:00+00:00")
growth.for_org("org_abc123")
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
```

Linked: parent substrate **#5045**; sibling dimension lanes **#4488** (embedding
encode), **#4489** (retrieval), **#4490** (machine time), **#4491** (egress),
**#4493** (shared-SaaS allocation); unit ruling and graph meter **#5331**.
