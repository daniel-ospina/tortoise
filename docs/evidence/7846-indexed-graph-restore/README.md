---
title: "Measurement — a graph-key RESTORE of a vector-indexed graph (#7846)"
type: engineering
domain: platform
doc_status: draft
created: 2026-10-12
subjects.team: epistemic-team
issue: "#7846"
aboutSubjects: Tortoise memory graph
aboutObjects: STORAGE-ARCHITECTURE.md, FalkorDB, vector index, key-level DUMP/RESTORE, graph eviction
---

# Measurement — a graph-key RESTORE of a vector-indexed graph (#7846)

Measured at: feat/7846-tm@ab90bda9460624bafd7844f06128c744742d639d on 2026-10-12

> The measured artifact is the **FalkorDB engine image**, not this repository's tree: the repo
> hosts the harness and (now) the regression test. The tree line above names the commit the
> harness was run from.

**Verdict — two findings, and they point the same way:**

1. The **permanent wedge** the issue reports (a `RESTORE` that never returns, `PING` dead, 0 % CPU,
   no log line) **does NOT reproduce** on any image pullable today, at the issue's own scale, on
   any of four falsification attempts. What does reproduce is the reason the report read as a
   wedge: a graph-key `RESTORE` of a vector-indexed graph **rebuilds the index on the serving
   thread**, so it blocks the **entire instance** for its duration, burns CPU in proportion, and
   returns **no progress, no log line and no error**. Acceptance criteria 2 and 5 are therefore
   still unmet, and they are upstream (FalkorDB).
2. A **hard SIGSEGV of the whole instance** was found while proving criterion 4, and it **does**
   reproduce — on `falkordb/falkordb-server:v4.20.6`, **the engine this repo's CI pins**. The
   trigger is one condition narrower than the issue's claim: an **indexed** payload restored under
   a key that **differs from the payload's own graph name**, **while the source graph still
   exists**. It is filed separately as **#8270**; it is the same family — a graph-key restore of an
   index-bearing graph killing the instance, silently, from the caller's side.

## 1. What was claimed, and what was re-measured

The issue reports that `DUMP` → `DEL` → `RESTORE` of a graph carrying a vector index leaves the
server alive but **permanently unresponsive** (no `PING`, 0 % CPU, no further log line), while the
same graph without an index restores in 0.38 s. It was filed `P0-blocker` because a RAM-bound
graph can be evicted only by dumping it, which would make graph-level eviction unsafe.

Two traps the issue names are reproduced faithfully in the harness below: `DUMP` returns binary,
so every client here is `decode_responses=False`; and `RESTORE` without `REPLACE` fails `BUSYKEY`
because the engine keeps a deleted graph in its module registry.

### The harness's own correction — an unpopulated index measures nothing

The issue's lane could not settle whether the index survived a restore because its vector probe
returned `ResponseError`/nothing both before and after. Measured here: a node built with a plain
float-listed `embedding` is **not indexed at all** — `CALL db.idx.vector.queryNodes` returns **0
rows** with the index `OPERATIONAL`. Written through `vecf32(r.emb)` (the form production uses,
`tortoise/projection/__init__.py::_ensure_vector_index_api`), the same probe returns all K
requested neighbours. Every run below therefore **asserts the index is populated before the
`RESTORE`**; a run that cannot return neighbours is not evidence about an index-bearing restore,
and the first 30 000-node attempt in this lane was discarded for exactly that reason.

## 2. Results — four falsification attempts, three engines, three sizes

Engine, persistence off (`save ""`, `appendonly no`), a dedicated throwaway container per engine,
a binary-safe client, and a bystander connection polled on its own socket throughout.

| engine (graph module) | image | nodes | DUMP | RESTORE | instance after | index after |
|---|---|---|---|---|---|---|
| 60001 | `falkordb/falkordb:6.0.1` | 3 000 | 1.44 MB | 0.42 s | `PING` 1.8–3.1 ms | `OPERATIONAL`, KNN 3 |
| 60001 | `falkordb/falkordb:6.0.1` | 30 000 | 14.42 MB | 2.62 s | `PING` 4–6 ms | `OPERATIONAL`, KNN 3 |
| 60001 | `falkordb/falkordb:6.0.1` | 90 000 | 43.19 MB | **not returned within the client's 10 s read bound**; the call surfaced at 53.3 s. The server **did** complete it — 90 000 nodes and the index `OPERATIONAL` after | `PING` 3.2–16.1 ms | `OPERATIONAL`, KNN 3 |
| 60002 | `falkordb/falkordb:latest` | 30 000 | 14.42 MB | 2.61 s | `PING` 2.2–3.7 ms | `OPERATIONAL`, KNN 3 |
| 42006 | `falkordb/falkordb-server:v4.20.6` (the CI pin) | 5 000 | 2.44 MB | 2.25 s | `PING` 1.3–2.1 ms | `OPERATIONAL`, KNN 3 |
| 60001 | `falkordb/falkordb:6.0.1`, index **`UNDER CONSTRUCTION`** at dump time (`[Indexing] 0/90000`) | 90 000 | 43.20 MB | 9.69 s | `PING` ok | `OPERATIONAL`, KNN 3 |

`falkordb/falkordb:6.0.1` is the image the issue's own comment thread names — graph module 60001,
built 2026-10-01, i.e. **before** the issue was filed (2026-10-09). So the wedge is not explained
by "the fix landed after the report": the version the thread cites does not wedge here either.

**None of the six runs wedged.** The instance stayed alive in all of them, every node came back,
and the index was `OPERATIONAL` and answerable after every restore.

## 3. What the report was actually looking at — the cost, measured

Same graph, same node count, same client, one engine (60001), one run
(`evict-cost-60001-30000.log`):

| arm | DUMP | RESTORE | CPU burned by the restore | worst bystander `PING` | bystander failed pings |
|---|---|---|---|---|---|
| A — no vector index | 14.42 MB | **0.17 s** | 0.2 s | **53.3 ms** | 0 of 66 |
| B — vector index, `OPERATIONAL` | 14.37 MB | **2.62 s** | 2.6 s | **2 497.7 ms** | 0 of 68 |

Two readings follow directly:

1. **The index is what the restore costs.** 15× slower (0.17 s → 2.62 s), and the CPU burned
   equals the wall clock — the restore is a CPU-bound, single-threaded index rebuild, not I/O and
   not a decode the engine amortises.
2. **The restore blocks the whole instance.** The bystander's worst single `PING` (2 497.7 ms)
   *is* the restore duration: no other graph is served while it runs. At 90 000 nodes the same
   work exceeded the caller's 10 s read bound, so a caller watching from outside sees the exact
   signature the issue describes — an instance that answers nothing, with no log line and no
   error — for as long as the rebuild takes, and cannot tell it from a wedged one.

That is the "we cannot tell load from death" half of the report, and it survives: the operation
has **no progress signal, no timeout and no distinguishing log line**. The permanent wedge does
not.

## 4. The crash (#8270) — found while proving criterion 4, and reproducible on the CI pin

Criterion 4 asks whether the index survives a restore or is silently dropped. A **same-name**
restore cannot answer it: a name-keyed module registry would also answer the probe afterwards. So
the payload was restored a second time **under a different key name** — which is where the engine
died.

```console
$ python crash_isolate.py 16420 kept
{"variant": "kept", "index_status": "OPERATIONAL", "dump_mb": 1.46,
 "source_present_at_restore": true,
 "restore_error": "ConnectionError: Error 61 connecting to 127.0.0.1:16420. Connection refused.",
 "instance_alive_after": "ConnectionError: ... Connection refused.", "VERDICT": "INSTANCE DEAD"}
```

The engine's own report (`crash-report-isolated-kept-42006.log`):

```text
Redis 8.6.3 crashed by signal: 11, si_code: 1
Accessing address: 0x18
falkordb.so(Index_SetLanguage+0x14)
CURRENT CLIENT ... cmd=restore
argv[1]: '"iso_target"'
```

### The trigger, isolated

| variant | source key at `RESTORE` | vector index | result |
|---|---|---|---|
| `kept` | present | yes | **SIGSEGV — the instance dies** (`Exited (139)`) |
| `deleted` | deleted first | yes | SURVIVED — restore returned, index `OPERATIONAL` |
| `noindex` | present | no | SURVIVED |

Both halves are load-bearing: **remove either the in-graph vector index or the coexisting source
graph and the crash does not happen.** The caller sees no error reply — it sees the connection
drop, i.e. a `ConnectionError` — and every other graph in the instance dies with it.

### Engine scope

| engine (graph module) | image | `kept` variant |
|---|---|---|
| **42006** | `falkordb/falkordb-server:v4.20.6` — **the CI pin** | **SIGSEGV** |
| 60001 | `falkordb/falkordb:6.0.1` | survives |
| 60002 | `falkordb/falkordb:latest` | survives |

So the crash belongs to the OSS `falkordb-server` line — which is what `python-ci.yml` and
`post-merge-validation.yml` provision — and is absent from the newer all-in-one
`falkordb/falkordb` line.

### 4a. Index carriage — what criterion 4 actually is

With the source graph **deleted first** (the safe variant), the same payload restored under a new
name comes back with an `OPERATIONAL` index that answers the same vector query
(`payload-carriage-ci-42006-3000.log`):

```text
source: {status: OPERATIONAL, knn: 3}   dump_mb: 1.46
target: {status: OPERATIONAL, knn: 3, nodes: 3000}
VERDICT: CARRIED-BY-THE-GRAPH-KEY (the index answered under a DIFFERENT graph name)
```

⇒ **The vector index travels inside the graph key's serialized payload.** A graph owns no second
key for it — the only other key a graph owns is `telemetry{<graph>}`, a Redis stream, which is
telemetry and not an index. It is **PRESERVED, not dropped**; the issue's worry that a restored
shard would answer vector search with an empty result does not hold on any engine measured.

## 5. Acceptance criteria, mapped

| # | Criterion | Status |
|---|---|---|
| 1 | the restore completes in bounded time with an observable rebuild, or fails with an explicit error | **Partly — upstream for the rebuild, MET at our boundary for the bound and the error.** The rebuild is still not observable and still emits nothing. A caller using `tortoise/graph_key_restore.py` now owns the bound (`bound_s`) and gets a typed, measured error instead of a socket timeout |
| 2 | the instance continues to serve other graphs throughout the restore | **UNMET — upstream.** It blocks for the whole rebuild (2 497.7 ms at 30 000 nodes, measured). The driver now MEASURES that outage per restore (`blocked_ms`) instead of leaving it inferred |
| 3 | a regression test does dump → delete → restore on an index-bearing graph and asserts the server still answers `PING` | **MET — `tests/test_indexed_graph_key_restore_7846.py`** (docker lane; the populated index is a precondition, so it cannot pass vacuously) |
| 4 | the fate of the index across a restore is documented and asserted | **MET — PRESERVED, payload-carried** (§4). Asserted same-name in the regression test; the cross-name proof runs OUTSIDE CI because on the pinned engine it is the crash trigger |
| 5 | the failure mode is distinguishable from load | **MET ONLY FOR A CALLER THAT ADOPTS THE MODULE — and no production call site adopts it yet.** Upstream unchanged: the engine still emits no progress, no timeout and no log line, so an instance blocked by a rebuild and a genuinely dead one look identical from a bare client. `tortoise/graph_key_restore.py` watches from a second connection and separates them: a witness PING that never answers plus a port that still ACCEPTS a TCP connection is *blocked (not load, not a crash)*; a REFUSED port is *the process died* (#8270). Two different errors, two different recoveries. ⚠️ Nothing in the product calls `restore_graph_key` (`rg` finds the module and its tests, no caller), so the restore production actually performs — `tortoise/hosted_backup.py::_graph_copy_with_restore_bound`, a bounded `GRAPH.COPY` swap (delete live → copy temp → live) — is **UNWITNESSED**: it has an explicit read bound and still makes no second-connection measurement, so for the wired path this criterion stays **UNMET** |

Criteria 2, and the engine half of 5, are not fixable in this repository: they are properties
of the engine's restore path. This doc states them unmet rather than asserting around them —
the in-repo test asserts other graphs and `PING` **after** the restore, never *through* it. What
IS fixable here is the reading of the event, and the driver above fixes it **for a caller that
adopts it**: it turns "the instance answered nothing for as long as my socket would wait" into a
measurement with a cause, and the regression test asserts the outage it measures is the
restore's own (it ends when the restore does, and is no longer than it). That is a
capability, not a landing: **no production call site adopts the driver yet**, and the wired
restore path (`hosted_backup`'s `GRAPH.COPY`) is unwitnessed.

## 6. Two defects to route, and why they are not one

- **The silence** (§3) — the restore blocks the instance with no observable progress. Upstream,
  and the half that makes the issue's symptom unreadable from outside. The engine is still
  silent; a caller that adopts `tortoise/graph_key_restore.py` is no longer — but nothing in the
  product adopts it yet, so the wired path (`hosted_backup`'s `GRAPH.COPY`) is still silent
  (criterion 5 is met for adopters only).
- **The crash** (§4) — `falkordb-server:v4.20.6`, the CI pin, dies on an indexed new-name restore
  while the source exists. Filed as **#8270**, cross-referenced to this one: the trigger is
  narrower and the blast radius is *larger* (a hard exit, not an unresponsive process), and the
  issue's own comment thread warns against folding peer symptoms into one fix.

## 7. Why this matters to the storage architecture (input for #5090)

`docs/architecture/STORAGE-ARCHITECTURE.md` §2.1 concludes that the graph is the only eviction
unit, and that the vector index is the class that dominates residency. This measurement adds the
operation-level constraint on top of the RAM-level one:

- a graph-level eviction implemented as key-level `DUMP` → `DEL` → `RESTORE` is **sub-second on an
  index-free graph** (0.17 s at 30 000 nodes) and **index-dominated and instance-blocking on an
  index-bearing one** (2.62 s at the same size, ~15× the CPU);
- on the engine this repo currently pins, a *variant* of that same operation **kills the
  instance** — so on that engine the eviction design is not merely slow, it is unsafe, and
  re-admitting a graph under a name other than the one baked into its payload is a crash;
- the migration to a separate vector layer is therefore not only a cost question — the blocking
  restore is a **safety** question for any design that evicts and re-admits graphs under live
  traffic;
- the search for an alternative is a search for a restore path that neither rebuilds the index on
  the serving thread nor re-keys a payload — this doc does not pick one, it records both
  constraints.

## 8. Reproducing

```bash
# one throwaway engine per arm; persistence OFF so no BGSAVE fork-COW confounds the reading
docker run -d --name fdb-7846 -p 127.0.0.1:16413:6379 falkordb/falkordb:6.0.1
docker exec fdb-7846 redis-cli CONFIG SET save ""
docker exec fdb-7846 redis-cli CONFIG SET appendonly no

# verify.py <port> <nodes> <label>   — build, index, assert POPULATED, DUMP/DEL/RESTORE, probe
python docs/evidence/7846-indexed-graph-restore/verify.py 16413 30000 "60001 @ 30k"
# evict_cost.py <port> <nodes> <label> — the same, index-free vs index-bearing, with a
#                                       bystander polled 5 s before / through / 10 s after
python docs/evidence/7846-indexed-graph-restore/evict_cost.py 16413 30000 "index vs no index"
# inflight.py <port> <nodes>          — DUMP taken while the index is UNDER CONSTRUCTION
python docs/evidence/7846-indexed-graph-restore/inflight.py 16413 90000
# payload_carriage.py <port> <nodes>  — restore the same payload under a DIFFERENT graph name
#                                       (source deleted first: the SAFE variant)
python docs/evidence/7846-indexed-graph-restore/payload_carriage.py 16413 3000
# ⛔ crash_isolate.py <port> kept     — the crash trigger. Run it ONLY against a throwaway
#                                       container: it kills the instance (exit 139) by design.
python docs/evidence/7846-indexed-graph-restore/crash_isolate.py 16420 kept
```

Exit code 2 from `verify.py` means **INVALID HARNESS** — the index was not populated, so the run
says nothing about an index-bearing restore (see §1).

The criterion-5 driver is exercised by the regression test itself, against whatever engine
`TORTOISE_DB_URI` names (the index-free control is not needed — the witness and the port probe
are what is being asserted):

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \
    uv run pytest tests/test_indexed_graph_key_restore_7846.py -v
# the driver's own branches, no server needed:
uv run pytest tests/test_7846_restore_is_not_silent.py -v
```

## 9. Committed logs

Raw stdout / raw engine output of the scripts above, from the runs quoted in this doc:

| file | what it is |
|---|---|
| `verify-60001-3000.log` | 60001 @ 3 000 nodes |
| `evict-cost-60001-30000.log` | 60001 @ 30 000 nodes, both arms (the §3 table) |
| `verify-60001-90000.log` | 60001 @ 90 000 nodes (the client read bound expiring) |
| `inflight-index-60001-90000.log` | 60001 @ 90 000 nodes, index `UNDER CONSTRUCTION` at dump |
| `verify-60002-30000.log` | 60002 @ 30 000 nodes |
| `verify-ci-42006-5000.log` | the CI pin @ 5 000 nodes |
| `payload-carriage-ci-42006-3000.log` | the index answering under a different graph name |
| `crash-variant-kept-42006.log` | the crash (exit 139, `INSTANCE DEAD`) |
| `crash-variant-deleted-42006.log` | control: source deleted first → survived |
| `crash-variant-noindex-42006.log` | control: no vector index → survived |
| `crash-report-isolated-kept-42006.log` | the engine's own bug report for the crash |
