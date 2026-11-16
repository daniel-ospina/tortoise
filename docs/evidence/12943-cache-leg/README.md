# #12943 — the cache leg, measured

Every number here was produced by `measure.py` against a **throwaway** tenant
(one run-unique `org_<hex>` / `g_<hex>` pair per run; `public.journal_event` is
append-only by trigger, so a fixed pair cannot be reused), a real FalkorDB and a
real Postgres carrying the record's own migrations.

```
provision_record.py   a throwaway record Postgres: the record's migration list
                      (read from record_projection.RECORD_MIGRATIONS) plus
                      LOGIN on the record's APPLICATION role
measure.py            one tenant, written through the hosted data plane's own
                      seam, counted in BOTH stores
last-run.json         one captured run
```

⛔ The app role is not a convenience. `record_projection` REFUSES a connection
that BYPASSES RLS (#9600), and the first cut of this harness connected as the
owner — so it measured that refusal instead of the cache leg. Seeding
`public.organizations`/`graphs` and counting rows use the owner DSN; the WRITE
path uses `tortoise_record_app`, because the rows it files are the subject.

## The measurement

Same tenant, same writes, same record. The only difference is `TORTOISE_DB_URI`.

| | record `object` / `point` / `journal_event` | cache `Object` / `Point` | `monitoring.metrics()` |
|---|---|---|---|
| `TORTOISE_DB_URI` **set** | 2 / 3 / 6 | **2 / 3** | `record_writes_applied: 0 → 6`, no failure |
| `TORTOISE_DB_URI` **empty** | 2 / 3 / 6 | **0 / 0** | `record_writes_applied: 0 → 6`, no failure |

## Residue this harness leaves

Per run: one `org_<uuid>` / `g_<uuid>` row pair in `public.organizations` /
`public.graphs`, one `journal_event` history (append-only by trigger, so it
cannot be cleared), and one cache graph `org_<org>_<gid>`. All run-unique, all
on whatever throwaway instances the DSNs point at, and nothing is ever removed —
a harness that deletes rows is a harness that can delete the wrong ones.

## Hypothesis verdicts

The second row is the shape #12943 reported for its **in-process** writes — 8
objects / 39 events / 5 operators in Postgres and **0 in FalkorDB** — and
`/data/tortoise.db` is created with its mtime inside the run, which names the
store those writes went to.

⚠️ **The issue's cache column is 1, not 0, and that 1 matters.** Its own table
says the cache held exactly one object, *"the one written through **REST**
(`POST /v1/objects`) — so the REST door wrote both stores and the in-process
door wrote only the record."* So the deployed app demonstrably HAD a shared
cache, and the split is per PROCESS, not per deployment:

* a REST write issued by the deployed app → lands in the shared FalkorDB, and
  in the record. ✅
* an in-process `_data_sdk` write issued by a process WITHOUT
  `TORTOISE_DB_URI` → lands in the record, and in that process's embedded
  file. ⛔

That reading is an **inference from the issue's numbers plus the code**, not a
measurement of the deployment: this lane has no access to the hosted app (no
`fly` CLI on this box), so what is measured here is the CODE PATH — the same
`_make_sdk` + `_bind_record_tenant` pair, run twice, with and without the URI.
What the inference buys is the diagnosis: a cache with 1 of 9 writes is not a
deployment that lacks a cache, it is a process that was not writing to the
deployment's cache. Whoever runs the repair should confirm it on the app with
`monitoring.metrics()["cache_leg_failures"]`, which is now the instrument that
answers it without guessing.

**So the cache leg does not fail. It succeeds, into a store the record is not
in.** `TORTOISE_DB_URI` picks the cache and `TORTOISE_RECORD_DSN` picks the
truth; nothing compares them, and every counter on `metrics()` counts the
record, so the deployment reads healthy while the derived copy holds nothing.

## The issue's four hypotheses, each accepted or rejected

**H1 — "the cache leg is not on the `_data_sdk`/SDK path at all" — REJECTED.**
`create_object` → `create_entity` writes the GRAPH first (the `apply-first`
declared lane, §17.1) and only then appends the record through `_emit_event` →
`_write_record_projection`. Measured: 2 objects / 3 points in BOTH stores on the
coherent deployment.

**H2 — "the cache write is attempted and fails silently" — ACCEPTED in
substance, REJECTED as stated.** It does not fail; it MISROUTES (see above),
and a misroute that returns success is strictly worse than a failure, because
nothing raises and nothing counts. The second half of H2 — *"if it is not
counted, that is a second defect"* — is accepted outright and was true: there
was no cache-leg counter anywhere on `monitoring.metrics()`. Fixed in this lane
(`cache_leg_failures`).

**H3 — "the graph handle resolved to a different namespace than the record
tenant" — REJECTED for the namespace, ACCEPTED one level up.** Both are derived
from one credential (`_data_sdk` → `_data_graph_name` + `_bind_record_tenant`),
and the measured graph name is `org_<org>_<gid>` in both runs. The handle
resolved to a different **store**, not a different namespace — which is why
`record-reconcile --cache-graph` looked in a graph the writes never touched.

**H4 — "a deferred/async projection that had not run yet" — REJECTED.** There
is no queue to drain: the graph write is synchronous and precedes the record's.

## Two things the issue asked to CHECK rather than assume

**`record-reconcile` runs.** Exit 3, JSON emitted, on a branch that ships no
`docs/` to the image: the placement table is read from the packaged
`tortoise/_placement_table.json`, so #10663's fix is live.

**The record's own failures are counted.** `record_write_failures` carries
`no_tenant` and `write_failed` and they fired while this harness was being
built (a no-journal process, then an owner-role connection). The counter gap is
specific to the cache.

## `record-reconcile` on a freshly-written tenant — NOT clean, and why

A pair written entirely through the sanctioned SDK door (2 Objects, 2 Points,
1 `IMPL` operator with a DECLARED label):

```json
{
  "cache_side": "cache",
  "checked": 5,
  "declaration_coverage": "complete",
  "declaration_source": null,
  "divergent": 1,
  "divergent_keys": [
    "Point:<operator id>"
  ],
  "extra_in_cache": 0,
  "field_mismatches": 2,
  "form_normalized": 2,
  "graph_only": {"content_hash": 2, "embedding": 2, "ep_dirty": 2, "ep_dirty_at": 2},
  "graph_only_total": 8,
  "missing_in_cache": 0,
  "not_hot": 0,
  "truth_side": "record",
  "verdict": "divergent"
}
```

The two fields: `operator` (record `{'inputs': [...], 'op_type': 'IMPL'}`, cache
absent) and `pointKind` (record `''`, cache absent). Every operator write is
affected, for two different reasons — a record-only props-tail name the live
fold never writes but a record-sourced rebuild WOULD, and a placed column the
two writers default differently.

This is a DIFFERENT root from #12943 (store selection) — it needs a §17.5
placement decision or a record narrowing — so it is filed as **#12949** with
this measurement rather than folded into the cache-leg change. The lane does
**not** report its DoD's reconcile item as met: on a write set that includes an
operator, the verb reports `divergent` today.
