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
tree   BEFORE  /tmp/before-9558          @ 1730570510  (origin/main, detached worktree)
tree   AFTER   …/.worktrees/child-9558   @ the PR's head
dsn    postgresql://record:record@127.0.0.1:<ephemeral>/record
pg     mirror.gcr.io/postgres:16, -c log_connections=on -c log_disconnections=on
door   record_writer.write_live_record   (the production door: one record per call)
n      60 timed records after 5 warmup, per run
```

⛔ **Every number in this document is arithmetic on the eight JSON files committed
beside it** (`runs/before-1..4.json`, `runs/after-1..4.json`). Re-derive any of
them — `records_per_second`, `per_record_ms`, `per_timed_record`,
`table_columns_isolated_ms` are the probe's own fields — instead of trusting the
prose. Earlier revisions of this artifact quoted a run set whose JSON was not
retained, which made its numbers unfalsifiable; that is what the `runs/` directory
fixes.

| | BEFORE (4 runs) | AFTER (4 runs) | speed-up |
|---|---|---|---|
| **records/second** | 49.72 – 52.86 | **255.68 – 261.61** | **4.8× – 5.3×** |
| per record, mean | 18.89 – 20.10 ms | **3.82 – 3.91 ms** | 4.8× – 5.3× |
| per record, median | 18.49 – 18.58 ms | 3.75 – 3.84 ms | |
| per record, p95 | 21.47 – 26.39 ms | 4.31 – 4.89 ms | |
| `psycopg2.connect()` calls / record | **1.00** (every run) | **0.00** (every run) | indicator (a) |
| `table_columns()` calls / record | 1.00 | 1.00 | see note (b) |
| `refused_props_names()` calls / record | 1.00 | 1.00 | see note (b) |
| `table_columns()` alone, mean | 1.582 – 1.651 ms | **0.460 – 0.583 ms** | 2.7× – 3.6× |
| **server-logged `connection authorized`** | **66** (every run) | **2** (every run) | per 65 writes |

Every row is a RANGE across runs, not a best-of: the per-run values are below, and
the AFTER spread (255.7 – 261.6 rec/s) is host noise on a box shared with the
fleet, not a different behaviour. ⚠️ An earlier revision of this table quoted a
prior run set (51.7 – 53.1 → 243.6 – 260.8 rec/s) whose JSON was not kept; the
mechanism and `connect`/`connection authorized` counts were identical, and this set
supersedes it **because it is the one a reader can check**.

The `connection authorized` row is the one no client-side change can move: a
`grep -c` delta on the Postgres server's own log across one probe run. 65 writes
cost 66 authorizations before (1 admin probe connection + 5 warmup + 60 timed)
and **2** after (the admin probe + the one pooled handle) — in every run.

Per-run values, each a link to its committed JSON:

| run | records/s | mean ms | median ms | p95 ms | connect/rec | `table_columns` alone | `connection authorized` |
|---|---|---|---|---|---|---|---|
| BEFORE 1 | 49.72 | 20.10 | 18.49 | 26.39 | 1.00 | 1.582 ms | 66 |
| BEFORE 2 | 52.86 | 18.89 | 18.52 | 21.75 | 1.00 | 1.598 ms | 66 |
| BEFORE 3 | 52.32 | 19.11 | 18.52 | 21.47 | 1.00 | 1.651 ms | 66 |
| BEFORE 4 | 50.73 | 19.69 | 18.58 | 23.40 | 1.00 | 1.647 ms | 66 |
| AFTER 1 | 255.68 | 3.91 | 3.84 | 4.89 | 0.00 | 0.583 ms | 2 |
| AFTER 2 | 257.66 | 3.88 | 3.79 | 4.31 | 0.00 | 0.460 ms | 2 |
| AFTER 3 | 261.61 | 3.82 | 3.81 | 4.44 | 0.00 | 0.466 ms | 2 |
| AFTER 4 | 261.41 | 3.82 | 3.75 | 4.62 | 0.00 | 0.477 ms | 2 |

`connection authorized` is not a probe field: it is a `grep -c` delta on the
server's own log across one run, recorded beside the JSON.

## Reading the numbers honestly

**(a) The connection half is ~92-94 % of the win, and it is now a REUSE, not a
claim.** The saving is 18.89–20.10 → 3.82–3.91 ms, i.e. ~15–16 ms; the column-map
half is ~1.0–1.1 ms of that (1.582–1.651 → 0.460–0.583), leaving the connection
half ~13.9–14.9 ms. ⛔ An earlier revision said "~80-85 %": that is the connection
half as a share of the BEFORE total, not of the WIN — the two are different
denominators and only the second is the claim. `_default_connect` returns an idle handle when it has one, so the live
door's per-record "open, commit, close" became "borrow, commit, return". The
remaining 3.8–4.1 ms/record is one transaction boundary (prologue + plan + COMMIT)
plus plan materialisation — **plus, and this was under-stated in the first
revision, 0.27–0.33 ms of stat overhead**: `replay_plan` drives BOTH cached
readers, and each recomputes the fifteen-file fingerprint (0.135–0.166 ms per
pass), so the cache is consulted twice per record. The known fix — compute the
fingerprint once per plan build — therefore saves ONE pass, **0.135–0.166 ms**,
and is filed as #11388 rather than hidden in "the work the record is for".
⛔ An earlier revision of this bullet said "≈0.9–1.0 ms of pure stat", which is
2 x the WHOLE cached call (stat + the per-call dict copy), not 2 x the stat pass;
the fix was consequently overstated **2.7–3.7× against the residual it described
(0.9–1.0 vs 0.27–0.33 ms), or 5.4–7.4× against the one pass #11388 actually saves
(0.9–1.0 vs 0.135–0.166 ms)** — whichever denominator the reader picks, it was
wrong, and the denominator is named here now. Corrected here and in the three
places that had copied it.

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

## Review cycle 2 — five corrections that changed the artifact

**The key needed `ctime_ns`.** A first key on `(mtime_ns, size, inode)` cannot see
a *same-length* rewrite whose mtime was restored — and restoring it is what
`cp -p`, `rsync -a`/`-t`, `install -p`, `tar -xp` and Docker's `COPY` do by
default. The reviewer reproduced it: five migrations rewritten `content`→
`contenu`, every stat field unchanged, the pre-edit map served indefinitely.
`ctime` is the one field those tools cannot restore on a fine-grained filesystem
(the kernel bumps it on every content or metadata change), so it is in the key
now, and the pin performs a same-size rewrite with `os.utime` restoring the
mtime — dropping `ctime_ns` reds it (measured).

⚠️ **Re-measured, and the residual is narrower than the claim, not zero — and it
applies on the DEFAULT filesystem.** On this box (**ext4**, `/dev/root`, in both
`/tmp` and the repo worktree) a write plus `utime` microseconds apart leaves
`ctime_ns` **identical** (delta 0 ns), because inode-timestamp granularity is
coarse on ordinary Linux filesystems. So a `cp -p` landing inside a single tick is
still invisible. A content digest would close it and was measured and rejected:
blake2b over the fifteen files (129 KB) costs **0.42–0.55 ms** per pass against
the stat pass's **0.135–0.166 ms**, so it ADDS 0.25–0.42 ms (net of the pass it
replaces) to a win of **~1.0–1.1 ms** (1.582–1.651 → 0.460–0.583) — i.e.
**~25–40 % of the win**. ⛔ An earlier revision said "most of the ~0.5 ms the cache saves", which
compared the digest against one stat pass instead of against the win and got the
verdict's character wrong; the remaining figures here are the same measurement,
re-derived. The decision (no digest) is unchanged: 20–35 % for a residual that
needs an archive extraction inside one inode-clock tick is not a trade worth
taking, but it is a smaller cost than that revision claimed. The pin therefore **burns an inode-clock tick explicitly**
before its same-size write, so the ctime arm always executes — the earlier version
of this test skipped when a tick boundary happened not to fall between two calls,
which measured as 4 skips in 10 runs and would have intermittently reddened a
required CI job whose leg gate fails on ANY skip.

**The pool needed a cross-DSN bound.** `RECORD_POOL_MAX_CONNECTIONS` is per
bucket, and `record_dsn()` re-reads the environment on every call, so a rotating
credential or a per-tenant DSN would have left two live sockets behind for every
value the process ever saw. `RECORD_POOL_MAX_IDLE_KEYS = 4` drains the oldest
buckets one at a time — ⛔ the schema cache does the OPPOSITE on overflow (it
clears wholesale, a tiny population of cheap-to-rebuild entries, so the two
bounds are deliberately not the same shape) — and the pin parks
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
`records/s` 178.78 / 277.76 / 246.30 / 200.17; mean **5.59 / 3.60 / 4.06 / 4.99**
ms; (`1000/rate` reconciles each pair — 5.59, 3.60, 4.06, 5.00 — which is how the
transposition in the previous revision of this line was caught);
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

The mechanism half is pinned in `tests/test_9558_record_write_cost.py` (20
tests: 7 for the schema cache, 8 for the pool, and 5 for the wrapper/park/
read-door contracts the first two review cycles added). 16 of the 20 carry an
explicit `FALSIFIER:` block naming the mutation that reds them; the four that do
not (`test_the_refused_props_map_also_reacts_to_an_edit`,
`test_a_removed_migration_is_seen_and_restores_the_error`,
`test_two_different_trees_never_share_a_parse`,
`test_the_returned_map_is_the_callers_own`) simply have no block written for
them. ⛔ They are not mutation-proof by nature — dropping `str(base)` from
`_schema_entry`'s key reds the two-trees pin, and sharing the refused-props parse
across fingerprints reds the refused-props pin — so the honest statement is "no
falsifier was recorded for these four", not "no mutation exists". **Eight were OBSERVED RED** by breaking the
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

⚠️ Two mutations were tried first and reported **STILL-GREEN**. ONE was a
defect in the mutant; the other was reporting a REAL gap in the pin (per bullet) —
recorded because a green mutant is the one result that can quietly pass as
coverage, in EITHER direction:

* zeroing only `mtime_ns` left `size` in the fingerprint, and the edit changes the
  file's size, so the cache correctly invalidated. ⛔ Re-mutated to inode-only, it
  reds — but the second review round showed the FIRST mutation was reporting a
  real GAP IN THE PIN, not just a bad mutant: every edit in
  `test_an_edited_migration_is_seen_immediately` changed the file's size, so a
  `(name, size, inode)` key passed the whole file. The pin now performs a
  SAME-SIZE rewrite, first with the mtime forced forward and then with the mtime
  RESTORED (the `cp -p` shape). ⛔ **Corrected in review cycle 3:** dropping
  `mtime_ns` ALONE no longer reds it — once `ctime_ns` joined the key, ctime
  changes on the same step and catches it — so the surviving falsifier for the
  mtime component is the inode-only mutation (`(name, 0, size, inode, …)`), which
  reds. Keeping the old sentence would have been a falsifier claim no test can
  fail, which is worse than having none.
* committing inside the hook's FRESH-connection branch was never reached by a
  single-write test. The pin now performs two writes (fresh, then reused), which
  is the only shape that exercises the branch; re-mutated to the reuse branch, it
  reds.

The S-surface properties are asserted as TESTS rather than as table rows above: reuse keeps the
prologue and the plan in order (§S2), the same journal position stays one
`journal_event` row (§S1), and an unarmed write opens nothing (§2.0). A revert
there means deleting the assertion, which review would catch — stated so the
difference is visible rather than implied.

## Verification run

```console
$ python -m pytest tests/test_9558_record_write_cost.py -q
20 passed

$ python -m pytest tests/test_9078_record_written_by_production_path.py \
    tests/test_9548_record_arming_health.py tests/test_9558_record_write_cost.py \
    tests/test_record_writer_seam.py tests/test_record_projection.py -q
327 passed
```

`TEST_RECORD_DB_URI` armed (the throwaway container above), so the record's
Postgres legs ran rather than skipping.
