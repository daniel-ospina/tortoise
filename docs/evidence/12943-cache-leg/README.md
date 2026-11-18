---
title: "#12943 — the cache leg, measured"
type: evidence
domain: platform
doc_status: final
created: 2026-11-16
subjects.team: epistemic-team
aboutSubjects: Tortoise cache leg (FalkorDB, derived store)
aboutObjects: "#12943, #12949, #12941, STORAGE-ARCHITECTURE.md §17.1/§17.2, Postgres record, FalkorDB cache"
---

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
  transcribed wrongly. ⚠️ It is a validity gate for **arm A only**: arm B's
  `write_path_uri` is `null` by construction (there is no write target to
  match), so `false` is arm B's CORRECT value and seeing it there is not the
  failure this warning describes.
* **`OWNER_RECORD_DSN`** — the RLS-bypassing owner, for seeding and counting
  only. It is required and is not optional politeness: the app role below cannot
  see across tenants, so counting through it would answer 0 for the wrong reason.
* **`EVENTS_BASE`** — where the per-run JSONL journal is written. ⚠️ The committed
  captures record it as an ABSOLUTE path, so the exact string embeds the box that
  produced it; reproduce the DIRECTORY, not the string. (Default
  `/tmp/12943-events-<run>/`). **Scratch, not evidence**: each run appends a
  run-unique `org_<hex>/events.jsonl`, and the record rows were projected from
  it. `SUMMARY_OUT` — where the JSON capture goes (default `last-run.json`), and
  how the two committed captures below were produced.

Both rows, copy-pasteable **from the repo root** (fill the four DSNs/URIs from your
own instances). ⛔ `uv run python` and not a bare `python`: the driver imports
(`psycopg2`, `falkordb`) are LAZY — a bare interpreter fails mid-run with
`ModuleNotFoundError`, not at startup. ⛔ And run it from the repo ROOT: `$PWD` is
what the capture records as `events_base`, so from this directory you would get a
path the captures do not carry.

```bash
export TEST_RECORD_DB_URI='postgresql://tortoise_record_app:…@127.0.0.1:33943/record'
export OWNER_RECORD_DSN='postgresql://record:…@127.0.0.1:33943/record'
export CACHE_COUNT_URI="$LANE_URI"          # e.g. docker://:pw@127.0.0.1:16419/db
export EVENTS_BASE="$PWD/.evidence-events"  # repo root — the `events_base` in the captures

# ARM A — shared cache: count and write on the SAME server
SUMMARY_OUT=docs/evidence/12943-cache-leg/last-run-arm-a-shared-cache.json \
  TORTOISE_DB_URI="$LANE_URI" uv run python docs/evidence/12943-cache-leg/measure.py

# ARM B — the defect: record armed, write path URI-less, cache still COUNTED
SUMMARY_OUT=docs/evidence/12943-cache-leg/last-run-arm-b-local-cache.json \
  TORTOISE_DB_URI='' uv run python docs/evidence/12943-cache-leg/measure.py
```

```text
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
reconcile-arm-c-with-operator-12949.json
                                    the same command on a graph that ALSO has an
                                    operator Point: divergent=1 — #12949, and
                                    the section below says why that is not a
                                    lost write
both-arms.json                      EVERY column the table below reads (all
                                    three arms), plus the same-server check arm A
                                    depends on and each arm's record-DSN
                                    fingerprint — so a capture can show WHICH
                                    record it hit, not merely that one was set
```

## The measurement

Same writes, same record schema, **one throwaway tenant per arm** (`measure.py`
mints a run-unique `org_<hex>` / `g_<hex>` pair per run, and
`public.journal_event` is append-only, so a pair cannot be reused). The only
*deliberate* difference between the arms is `TORTOISE_DB_URI`.

| | record `object` / `point` / `journal_event` | cache `Object` / `Point` | `record_writes_applied` | `cache_leg_failures` |
|---|---|---|---|---|
| `TORTOISE_DB_URI` **set** | 2 / 3 / 6 | **2 / 3** | `0 → 6`, no failure | **`{}`** |
| `TORTOISE_DB_URI` **empty** | 2 / 3 / 6 | **0 / 0** | `0 → 6`, no failure | **`{"cache_not_shared": 2}`** |

Every capture also carries `record_dsn_fingerprint`
(`scheme://host:port/db#8hex`), `record_writes_applied_delta` and
`record_write_failures_published`. ⛔ **`write_path_uri` and `cache_count_uri` are
fingerprinted the same way, never raw** — they are `docker://:<password>@…` values
and a capture is a committed file, so a password-protected cache lane would
otherwise write its credential into the PR; what the arm claims needs
host/port/graph identity, never the credential. The harness **assigns**
`TORTOISE_RECORD_DSN` / `TORTOISE_EVENT_LOG_BASE_DIR` rather than defaulting them,
and **refuses to run** when an ambient value disagrees — because `setdefault`
would let an operator's exported `TORTOISE_RECORD_DSN` win, so the SDK would
write to the ambient record while the counts read the throwaway one, and the
capture would look perfectly normal. That is the same variable-vs-server defect
this whole change is about, one level down.

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

## `record-reconcile` on a freshly-written tenant — the operator-inclusive arm

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
this measurement rather than folded into the cache-leg change.

⚠️ **This is ONE ARM, not the verdict.** A write set that includes an operator
reads `divergent`; the same command on a write set that does not is `clean`, and
that is the section immediately below. Both captures are committed so neither
number is quotable without the other.

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
# the capture, reproduced — from the repo ROOT, for the two reasons arms A/B give
# above (`uv run`: the driver imports are lazy; `$PWD`: it is the captured `events_base`).
export EVENTS_BASE="$PWD/.evidence-events"
SUMMARY_OUT=docs/evidence/12943-cache-leg/last-run-arm-c-reconcile.json \
  TORTOISE_DB_URI="$LANE_URI" \
  uv run python docs/evidence/12943-cache-leg/measure.py --skip-operator
org=$(python3 -c "import json;print(json.load(open('docs/evidence/12943-cache-leg/last-run-arm-c-reconcile.json'))['org'])")
gid=$(python3 -c "import json;print(json.load(open('docs/evidence/12943-cache-leg/last-run-arm-c-reconcile.json'))['graph_id'])")
uv run python -m tortoise record-reconcile --db "$LANE_URI" \
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
`reconcile-arm-c-with-operator-12949.json`. Fixing it means deciding what the
comparison surface should be for a record column that is a *spelling* of the
graph's representation — its own root, its own decision, and deliberately not
folded into a change about store SELECTION.

---

## The reopening, measured PER DOOR (2026-11-17)

\#12950 fixed a DOOR. The reopening's evidence was door-specific (REST reaches the
cache, the in-process writer does not), so the question was re-asked per door with
`door_matrix.py`, and answered in every graph the write could have landed in —
because a "0 in `org_<org>_<gid>`" is only evidence of a lost write if the write
is not in some OTHER graph of the same server. That is how the first two readings
of this issue went wrong.

One node per door, run-unique throwaway tenant, a real Postgres record (the
record's own migration list, the APPLICATION role) and a real shared FalkorDB.
`--reconcile` adds the product's own instrument on BOTH candidate cache graphs.
Capture: `door-matrix-shared-cache.json`.

| door | record `object` / `journal_event` | cache `org_<org>_<gid>` | cache `org_<org>` | `cache_leg_failures` | `record-reconcile` canonical / actual |
|---|---|---|---|---|---|
| `sdk` — `_make_sdk(graph_name=…)` + bind | 1 / 1 | **1 Object** | absent | `{}` | `clean` / `clean` |
| `_data_sdk` — GRAPH-BOUND credential | 1 / 1 | **1 Object** | absent | `{}` | `clean` / `clean` |
| `_data_sdk` — ORG-WIDE credential | 1 / 1 | **absent** | **1 Object** | `{}` | **`divergent` (missing_in_cache=1, exit 3)** / `clean` |
| `POST /v1/objects` (real app) | 1 / 1 | **1 Object** | absent | `{}` | `clean` / `clean` |
| MCP `tortoise_create_entity` (tool body) | 1 / 1 | **1 Object** | absent | `{}` | `clean` / `clean` |
| script: `_make_sdk(namespace=org)` + bind custom gid | 1 / 1 | **absent** | **1 Object** | `{}` | **`divergent`** / `clean` |
| misroute: SDK for `g_A`, tenant `g_B` | 1 / 1 | absent | absent (3rd graph) | **`{"tenant_graph_mismatch": 1}`** | **`divergent`** / `clean` |

`record-reconcile` on the `_data_sdk` row, verbatim:

```json
{"verdict": "clean", "checked": 1, "divergent": 0, "missing_in_cache": 0,
 "extra_in_cache": 0, "field_mismatches": 0, "exit": 0}
```

…and the same tenant written through the ORG-WIDE credential, on the graph the
record's tenant names:

```json
{"verdict": "divergent", "checked": 1, "divergent": 1, "missing_in_cache": 1,
 "extra_in_cache": 0, "field_mismatches": 0, "exit": 3}
```

…and on the graph the write ACTUALLY used (`org_<org>`, the one nothing told the
operator):

```json
{"verdict": "clean", "checked": 1, "divergent": 0, "missing_in_cache": 0,
 "extra_in_cache": 0, "field_mismatches": 0, "exit": 0}
```

## What that says

**The `_data_sdk` row MOVES.** On a coherent deployment (both stores configured,
graph-bound credential) the in-process door writes both stores and reconciles
clean, so the reopened symptom is NOT a property of the door. The class is:

> **A record TENANT and a cache GRAPH are two answers, chosen by two different
> things — and nothing compared them.** The tenant comes from the credential's
> `graph_id` / `default_graph_id`; the graph comes from the credential's
> `graph_namespace` (graph-bound) or the org-level open (org-wide). For a custom
> graph they are the same only on the graph-bound path.

Both misrouting rows are *successful* writes into a graph the tenant's own name
does not point at. Nothing fails, nothing raises, and `record-reconcile` reports
the whole tenant missing — while `record_writes_applied` and every counter read
healthy. #12950's `cache_not_shared` cannot see either: it grades a
construction-level configuration split, and its own docstring names the residual
("a truthy `cache_uri` is PRESENCE, not LOCALITY").

**The public doors are coherent.** REST, MCP and both `_data_sdk` branches of the
hosted app take the graph and the tenant from ONE credential, so they cannot
drift there — the second finding is what happens when they are taken from two
places (a maintenance script, a lane, any future door).

## External grounding (rung 3 — see the note below)

The canonical answer to "truth and derived copy must agree" is the **transactional
outbox**: write the message/derived state in the SAME transaction as the truth,
and let a separate **message relay** publish it (microservices.io:
`patterns/data/transactional-outbox.html`, `patterns/data/transaction-log-tailing.html`).
Two properties of that pattern map onto this measurement:

* the relay must have ONE target, derived from the truth it is replaying — our
  fold derives its target from the CREDENTIAL instead, which is the finding above;
* the relay's own lag/divergence is what you instrument, not the writer's success
  (`record_writes_applied` is identical on both arms of this table — that is why
  nothing downstream could tell them apart).

⛔ **Rung note, as the brief requires:** rung 1
(`mcp__seo-intelligence__perplexity_search`) is UNAVAILABLE on this box —
`Unknown MCP server 'seo-intelligence'`. Rung 2 (`web_search`, model `sonar`
passed explicitly) is ALSO unavailable: `PERPLEXITY_API_KEY not set` (and
`AGENT_INFRA_PATH` is empty, so there is no `.env` for the harness to find). The
external reading above is therefore rung 3 — `web_fetch` on primary sources
(Chris Richardson's pattern catalogue), quoted rather than paraphrased where it
matters.
