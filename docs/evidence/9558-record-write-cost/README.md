---
title: "Record write path — #9558 (R10) per-record cost, measured before/after"
type: evidence
domain: platform
doc_status: final
created: 2026-11-07
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#9558, epic #7879, Postgres record, tortoise/record_writer.py, tortoise/record_projection.py"
---

# #9558 (R10) — the record write path's per-record cost, measured before/after

Child **#9558** of epic **#7879** (`08-hosted-prerequisites.md` §B R10, gate
`FOLLOW`). The deliverable this artifact satisfies is the issue's own target:
*"a recorded before/after measurement of records/second"*, plus indicators
(a) *"the writer uses a pooled connection … instead of `_default_connect`'s
per-record connect"* and (b) *"`table_columns()` is cached rather than re-read
per record"*.

Run: **2026-11-07**, one instrument against two trees, four runs each.
Probe: [`probe_record_write_cost.py`](./probe_record_write_cost.py).

## The measurement

```text
tree   BEFORE  /home/cmux/tortoise                          @ 1730570510  (origin/main)
tree   AFTER   …/.worktrees/child-9558                      @ the PR's head
dsn    postgresql://record:record@127.0.0.1:<ephemeral>/record
pg     mirror.gcr.io/postgres:16, log_connections=on, log_disconnections=on
door   record_writer.write_live_record   (the production door: one record per call)
n      60 timed records after 5 warmup, per run
```

| | BEFORE (4 runs) | AFTER (4 runs) | speed-up |
|---|---|---|---|
| **records/second** | 51.72 – 53.09 | **243.63 – 260.84** | **4.6× – 5.0×** |
| per record, mean | 18.82 – 19.33 ms | **3.83 – 4.10 ms** | 4.6× – 5.0× |
| per record, median | 18.61 – 19.14 ms | 3.72 – 4.07 ms | |
| per record, p95 | 20.35 – 23.61 ms | 4.82 – 5.27 ms | |
| `psycopg2.connect()` calls / record | **1.00** (every run) | **0.00** (every run) | indicator (a) |
| `table_columns()` calls / record | 1.00 | 1.00 | see note (b) |
| `refused_props_names()` calls / record | 1.00 | 1.00 | see note (b) |
| `table_columns()` alone, mean | 1.65 – 1.73 ms | **0.46 – 0.48 ms** | 3.6× |
| **server-logged `connection authorized`** | **66** (every run) | **2** (every run) | per 65 writes |

Every row is a RANGE across runs, not a best-of: the per-run values are below, and
the AFTER spread (243.6 – 260.8 rec/s) is host noise on a box shared with the
fleet, not a different behaviour.

The `connection authorized` row is the one no client-side change can move: a
`grep -c` delta on the Postgres server's own log across one probe run. 65 writes
cost 66 authorizations before (1 admin probe connection + 5 warmup + 60 timed)
and **2** after (the admin probe + the one pooled handle) — in every run.

Raw per-run values (`--json`; the median and `table_columns`-isolated fields quoted above are in the same JSON and are not repeated here):

| run | records/s | mean ms | p95 ms | connect/rec | `connection authorized` |
|---|---|---|---|---|---|
| BEFORE 1 | 52.76 | 18.93 | 21.55 | 1.00 | 66 |
| BEFORE 2 | 52.40 | 19.05 | 23.61 | 1.00 | 66 |
| BEFORE 3 | 53.09 | 18.82 | 20.35 | 1.00 | 66 |
| BEFORE 4 | 51.72 | 19.33 | 21.71 | 1.00 | 66 |
| AFTER 1 | 256.78 | 3.89 | 4.82 | 0.00 | 2 |
| AFTER 2 | 244.51 | 4.09 | 5.27 | 0.00 | 2 |
| AFTER 3 | 243.63 | 4.10 | 5.01 | 0.00 | 2 |
| AFTER 4 | 260.84 | 3.83 | 4.98 | 0.00 | 2 |

(AFTER 1 was taken on an earlier revision of this branch — the pooling design
changed afterwards, from the live door owning the connection to the pool living
behind `_default_connect`; the numbers are quoted because they land inside the
same spread. AFTER 2–4 are all the shipped design.)

## Reading the numbers honestly

**(a) The connection half is the whole of the win, and it is now a REUSE, not a
claim.** `_default_connect` returns an idle handle when it has one, so the live
door's per-record "open, commit, close" became "borrow, commit, return". The
remaining 3.8–4.1 ms/record is one transaction boundary (prologue + plan + COMMIT)
plus plan materialisation — **plus, and this was under-stated in the first
revision, ≈0.9–1.0 ms of pure stat overhead**: `replay_plan` drives BOTH cached
readers, and each recomputes the fifteen-file fingerprint, so the cache is
consulted twice per record. That is a quantified residual with a known fix
(compute the fingerprint once per plan build) and it is filed, not hidden in
"the work the record is for".

**(b) The column-map half is real but smaller than the issue's "~4 ms" note
suggests.** At this head `table_columns()` costs ~1.7 ms uncached and ~0.47 ms
cached. ⚠️ The CALL COUNT is unchanged (1 per record): what changed is that the
call no longer reads and regex-parses fifteen migration files, it stats them.
That is the substance of the indicator (`a fresh migration read per record`), and
it is why the target's wording — *"called once per process/refresh, not once per
record"* — is satisfied at the level of WORK rather than of call sites. Forcing
the call count itself to 1 would require a caller-held map, which is the
batching/lifecycle change this issue explicitly defers.

⚠️ **`column_types()` is NOT cached** (a contract read a pin performs, not a
per-record read) and **`jsonb_columns()` keeps its 5 s TTL** (it runs once per
string PARAMETER, where even a stat pass would be a regression). TWO caches, two
call frequencies, two invalidation costs — plus `column_types()`, deliberately
uncached. The division is stated at `_JSONB_CACHE_TTL_SECONDS` and at
`column_types`.

⚠️ **Not what this measures.** Per-record cost of ONE door's write path on a
local loopback Postgres. It is not a capture-throughput claim (the door is one of
several things a capture does), and it is not a concurrency claim — the pool's
idle set is bounded at `RECORD_POOL_MAX_CONNECTIONS = 2`, and a burst opens new
connections rather than queueing.

## Review cycle 2 — four corrections that changed the artifact

**The key needed `ctime_ns`.** A first key on `(mtime_ns, size, inode)` cannot see
a *same-length* rewrite whose mtime was restored — and restoring it is what
`cp -p`, `rsync -a`/`-t`, `install -p`, `tar -xp` and Docker's `COPY` do by
default. The reviewer reproduced it: five migrations rewritten `content`→
`contenu`, every stat field unchanged, the pre-edit map served indefinitely.
`ctime` is the one field those tools cannot restore on a fine-grained filesystem
(the kernel bumps it on every content or metadata change), so it is in the key
now, and the pin performs a same-size rewrite with `os.utime` restoring the
mtime — dropping `ctime_ns` reds it (measured).

⚠️ **Re-measured, and the residual is narrower than the claim, not zero.** On
this box's `/tmp` (an ext3-style mount) a write plus `utime` inside one tick
leaves `ctime_ns` **identical** (delta 0 ns), so on a coarse-ctime filesystem a
`cp -p` landing inside a single tick is still invisible. A content digest would
close it and was measured and rejected: blake2b over the fifteen files (129 KB)
costs **0.554 ms** per pass against the stat pass's **0.135 ms**, i.e. it would
consume most of the ~0.5 ms the cache saves over a re-parse. The pin skips — with
that reason printed — where the box's ctime cannot move, rather than flaking or
claiming coverage it does not have.

**The pool needed a cross-DSN bound.** `RECORD_POOL_MAX_CONNECTIONS` is per
bucket, and `record_dsn()` re-reads the environment on every call, so a rotating
credential or a per-tenant DSN would have left two live sockets behind for every
value the process ever saw. `RECORD_POOL_MAX_IDLE_KEYS = 4` drains the oldest
buckets, the same eviction the schema cache does, and the pin parks
`MAX_IDLE_KEYS + 2` distinct keys to prove it.

**The read door pays one rollback per borrow, and that is now stated.** The
release comment claimed "no round trip" — true of the write door's happy path
(`commit()` leaves the connection IDLE), false of a read, which ends INTRANS and
therefore takes a `rollback()` on 100% of borrows. One round trip against the
~19 ms connect+auth it replaces; named here rather than averaged away.

**Two silent-failure shapes are closed on the wrapper.** `close()` is now a fence
(`cursor`/`commit`/`rollback` raise the driver's `InterfaceError` once returned,
instead of committing whatever transaction the next borrower holds), `cursor()`
forwards its arguments (`cursor_factory=` would have been a `TypeError` on a call
site that was correct before), and a handle parked in `autocommit` mode is
normalised first — a transaction-local `set_config` sets NOTHING there, so the
tenant GUC would have failed every later record with an error naming the DSN.

**Re-measured after these fixes** (4 runs, same instrument, a box now carrying
other work — so this is a *load-sensitivity* datum, not a better number):
`records/s` 178.78 / 277.76 / 246.30 / 200.17; mean 4.99 / 3.60 / 4.06 / 5.59 ms;
`connect` per record **0 in every run**. The mechanism claim — no handshake, no
re-parse — is what the change is; the wall-clock headline above is the quiet-box
measurement and stays labelled as such.

## Reproducing

```bash
# 1. a throwaway Postgres, on an ephemeral port, logging connections
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("",0));print(s.getsockname()[1])')
docker run -d --rm --name recpool-9558 -e POSTGRES_PASSWORD=record \
  -e POSTGRES_USER=record -e POSTGRES_DB=record -p 127.0.0.1:$PORT:5432 \
  mirror.gcr.io/postgres:16 -c log_connections=on

# 2. the SAME instrument, both trees (it refuses a `--tree` it did not import)
python docs/evidence/9558-record-write-cost/probe_record_write_cost.py \
  --tree /path/to/main-checkout      --dsn "postgresql://record:record@127.0.0.1:$PORT/record" --records 60 --json
python docs/evidence/9558-record-write-cost/probe_record_write_cost.py \
  --tree /path/to/9558-worktree      --dsn "postgresql://record:record@127.0.0.1:$PORT/record" --records 60 --json

# 3. the server's own count, across one run
docker logs recpool-9558 2>&1 | grep -c 'connection authorized'
```

The probe applies `RECORD_MIGRATIONS` and the record tests' own minimal
substrate preamble on first use, seeds a tenant, and asserts every write is
`APPLIED` — timing a refused write would report a fast door that stored nothing.

## Falsifiers — what must make each claim red

The mechanism half is pinned in `tests/test_9558_record_write_cost.py` (15
tests), each naming its own falsifier. **Eight were OBSERVED RED** by breaking the
source, running only the named test, and restoring the file byte-identically
(`/tmp/prove_falsifiers.py`, one pass):

| Mutation applied | Test that must notice | Observed |
|---|---|---|
| `_default_connect` never reuses (`reused = None`) | `test_the_record_path_opens_one_connection_for_many_records` | **RED** |
| drop the `table_columns` parse short-circuit | `test_the_column_map_is_parsed_once_for_an_unchanged_schema` | **RED** |
| identify a migration by inode only (mtime+size ignored) | `test_an_edited_migration_is_seen_immediately` | **RED** |
| accept a handle whose server went away | `test_a_broken_handle_is_discarded_not_reused` | **RED** |
| park without rolling back | `test_an_aborted_transaction_is_rolled_back_before_the_handle_is_parked` | **RED** |
| park unconditionally (no idle bound) | `test_the_idle_set_is_bounded_and_overflow_is_closed` | **RED** |
| drop the double-close guard | `test_a_double_close_returns_one_handle_once` | **RED** |
| commit inside the hook on the reuse branch | `test_a_borrowed_handle_reports_the_same_outcome_to_its_caller` | **RED** |

⚠️ Two mutations were tried first and reported **STILL-GREEN**, and both were
defects in the MUTANT rather than in the pin — recorded because a green mutant is
the one result that can quietly pass as coverage:

* zeroing only `mtime_ns` left `size` in the fingerprint, and the edit changes the
  file's size, so the cache correctly invalidated. ⛔ Re-mutated to inode-only, it
  reds — but the second review round showed the FIRST mutation was reporting a
  real GAP IN THE PIN, not just a bad mutant: every edit in
  `test_an_edited_migration_is_seen_immediately` changed the file's size, so a
  `(name, size, inode)` key passed the whole file. The pin now performs a
  SAME-SIZE rewrite (one added column renamed for another of equal length) with
  the mtime forced forward, and dropping `mtime_ns` from the fingerprint reds it
  (re-measured after the fix).
* committing inside the hook's FRESH-connection branch was never reached by a
  single-write test. The pin now performs two writes (fresh, then reused), which
  is the only shape that exercises the branch; re-mutated to the reuse branch, it
  reds.

The remaining three rows are S-surface assertions, not mutations: reuse keeps the
prologue and the plan in order (§S2), the same journal position stays one
`journal_event` row (§S1), and an unarmed write opens nothing (§2.0). A revert
there means deleting the assertion, which review would catch — stated so the
difference is visible rather than implied.

## Verification run

```console
$ python -m pytest tests/test_9558_record_write_cost.py -q
15 passed

$ python -m pytest tests/test_9078_record_written_by_production_path.py \
    tests/test_9548_record_arming_health.py tests/test_9558_record_write_cost.py \
    tests/test_record_writer_seam.py tests/test_record_projection.py -q
327 passed
```

`TEST_RECORD_DB_URI` armed (the throwaway container above), so the record's
Postgres legs ran rather than skipping.
