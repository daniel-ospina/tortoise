# #12943 — the cache leg, measured

Every number here was produced by `measure.py` against a **throwaway** tenant
(one run-unique `org_<hex>` / `g_<hex>` pair per run; `public.journal_event` is
append-only by trigger, so a fixed pair cannot be reused), a real FalkorDB and a
real Postgres carrying the record's own migrations.

⛔ **Both rows below are runnable as committed** — this was not true of the
first two drafts of this file, and the gap mattered both times: the URI-less row
is the one that *is* the defect, and a harness that stack-traces (or exits 1 on a
missing variable) when asked to reproduce it is a table of inferences under a
provenance claim.

Three knobs make the second arm expressible:

* **`CACHE_COUNT_URI`** names the server the cache is **counted** on, separately
  from `TORTOISE_DB_URI`, which decides where the SDK **writes** (default:
  `TORTOISE_DB_URI`). Without it the defect arm is unmeasurable — the write path
  must run URI-less, so there is no URI left to count with. ⛔ **Arm A is only
  meaningful when `cache_count_uri_matches_write_target` is `true`**: a
  `CACHE_COUNT_URI` naming a different server reads `0 / 0` and looks exactly
  like arm B. The harness records that boolean so the claim cannot be
  transcribed wrongly.
* **`OWNER_RECORD_DSN`** — the RLS-bypassing owner, for seeding and counting
  only. It is required and is not optional politeness: the app role below cannot
  see across tenants, so counting through it would answer 0 for the wrong reason.
* **`EVENTS_BASE`** — where the per-run JSONL journal is written (default
  `.evidence-events/`, gitignored). **Scratch, not evidence**: each run appends a
  run-unique `org_<hex>/events.jsonl`, and the record rows were projected from
  it. `SUMMARY_OUT` — where the JSON capture goes (default `last-run.json`), and
  how the two committed captures below were produced.

Both rows, copy-pasteable (fill the four DSNs/URIs from your own instances):

```bash
export TEST_RECORD_DB_URI='postgresql://tortoise_record_app:…@127.0.0.1:33943/record'
export OWNER_RECORD_DSN='postgresql://record:…@127.0.0.1:33943/record'
export CACHE_COUNT_URI="$LANE_URI"          # e.g. docker://:pw@127.0.0.1:16419/db

# ARM A — shared cache: count and write on the SAME server
SUMMARY_OUT=last-run-arm-a-shared-cache.json \
  TORTOISE_DB_URI="$LANE_URI" python measure.py

# ARM B — the defect: record armed, write path URI-less, cache still COUNTED
SUMMARY_OUT=last-run-arm-b-local-cache.json \
  TORTOISE_DB_URI='' python measure.py
```

```
provision_record.py                 a throwaway record Postgres: the record's
                                    migration list (read from
                                    record_projection.RECORD_MIGRATIONS) plus
                                    LOGIN on the record's APPLICATION role
measure.py                          one tenant, written through the hosted data
                                    plane's own seam, counted in BOTH stores
last-run-arm-a-shared-cache.json    the ARM A capture, verbatim
last-run-arm-b-local-cache.json     the ARM B capture, verbatim
last-run-arm-c-reconcile.json       the ARM C capture (reconcile arm)
reconcile-arm-c-clean.json          `record-reconcile --json` on ARM C's graph:
                                    verdict=clean, divergent=0, exit 0
reconcile-arm-b-with-operator-12949.json
                                    the same command on a graph that ALSO has an
                                    operator Point: divergent=1 — #12949, and
                                    the section below says why that is not a
                                    lost write
both-arms.json                      the columns the table below reads, from
                                    those two captures, plus the
                                    same-server check arm A depends on
```

## The measurement

Same tenant, same writes, same record. The only difference is `TORTOISE_DB_URI`.

| | record `object` / `point` / `journal_event` | cache `Object` / `Point` | `record_writes_applied` | `cache_leg_failures` |
|---|---|---|---|---|
| `TORTOISE_DB_URI` **set** | 2 / 3 / 6 | **2 / 3** | `0 → 6`, no failure | **`{}`** |
| `TORTOISE_DB_URI` **empty** | 2 / 3 / 6 | **0 / 0** | `0 → 6`, no failure | **`{"cache_not_shared": 2}`** |

The last column is the fix's own instrument, and reading it beside the others is
the whole point: **`record_writes_applied` is identical in both rows (0 → 6, no
failure)** — the record accepted every write either way, which is why nothing
downstream could tell the two rows apart. Only the cache count differs, and only
`cache_leg_failures` says so in a number.

⚠️ **`2`, not `1`, and that number is evidence for the counter's unit.**
`hosted_api._make_sdk` builds an **anchor** SDK and a **per-request** SDK on its
embedded path (`hosted_api.py`, both `TortoiseSDK(db_path=…)` calls), so one
harness write path is two constructions. ⚠️ That is the **cold-process** number —
the anchor is built only on a `_FALLBACK_KEEPALIVE` miss or a stale path, so a
warm process yields one construction per request. Every `measure.py` run is cold. That is exactly why the unit is *SDK
constructions* and is **not** comparable with the write count beside it — a unit
mislabelled "writes" would have made this row read as 2 stranded writes when five
objects were written.

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
deployment's cache.

**How to confirm it — on the right process, or the `0` lies.** Read
`/health`'s `cache_leg_failures` (or `monitoring.metrics()`) on the process that
**performed** the write. ⛔ A process whose cache IS shared reports `{}` by
construction (`cache_leg.py`'s predicate returns `None` on any cache URI), so the
deployed app's own `/health` reading `{}` falsifies **nothing** about a
same-deployment worker that has no `TORTOISE_DB_URI` — and reading the app's
number instead of the writer's is the same false-negative this change exists to
end, one level up.

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

## Does the write actually RECONCILE? — yes, and here is the one caveat

`record-reconcile --json` compares the record's view to the cache's node by node.
Run on a **freshly-written** graph (arm C, `measure.py --skip-operator`):

```json
{
  "checked": 4, "divergent": 0, "divergent_keys": [],
  "missing_in_cache": 0, "extra_in_cache": 0, "field_mismatches": 0,
  "form_normalized": 2, "graph_only_total": 8,
  "declaration_coverage": "complete", "verdict": "clean"
}
```

```bash
# the capture, reproduced
SUMMARY_OUT=last-run-arm-c-reconcile.json TORTOISE_DB_URI="$LANE_URI" \
  python measure.py --skip-operator
org=$(python3 -c "import json;print(json.load(open('last-run-arm-c-reconcile.json'))['org'])")
gid=$(python3 -c "import json;print(json.load(open('last-run-arm-c-reconcile.json'))['graph_id'])")
python -m tortoise record-reconcile --db "$LANE_URI" \
  --record-dsn "$TEST_RECORD_DB_URI" --org "$org" --graph "$gid" \
  --cache-graph "org_${org}_${gid}" --json
```

exit 0, on a tenant created and written inside the run. `graph_only` is the
expected population (cache-held properties the record cannot place:
`content_hash`, `embedding`, `ep_dirty`, `ep_dirty_at`), reported as its own
bucket and **not** folded into divergence; `form_normalized: 2` is the float32
JSONB round-trip on the two Point embeddings, likewise not a mismatch.

⚠️ **Add an operator Point and it reads `divergent: 1`.** That residual is
**#12949 and it is not a lost write**: the record's operator row carries an EMPTY
`point_kind` and a structured `props.operator` echo (`{inputs, op_type}`), while
the graph represents the same operator NATIVELY — `is_operator: true`,
`op_type`, `direction`, plus the typed `IMPL`/`INPUT` edges. The information is in
both stores, in the shape each store uses; the RECONCILER compares the record's
echo to the graph's property names and reports the pair every time. Captured:
`reconcile-arm-b-with-operator-12949.json`. Fixing it means deciding what the
comparison surface should be for a record column that is a *spelling* of the
graph's representation — its own root, its own decision, and deliberately not
folded into a change about store SELECTION.
