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
tree   AFTER   …/.worktrees/child-9558   @ 6ab6848b8b  (the sha each JSON records)
dsn    postgresql://record:record@127.0.0.1:<ephemeral>/record
pg     mirror.gcr.io/postgres:16, -c log_connections=on -c log_disconnections=on
door   record_writer.write_live_record   (the production door: one record per call)
n      60 timed records after 5 warmup, per run
```

⛔ **Every number here is arithmetic on a committed file.** The eight
`runs/{before,after}-1..4.json` carry `records_per_second`, `per_record_ms`,
`per_timed_record` and `table_columns_isolated_ms` — the probe's own fields — and
`fingerprint-cost.json` carries the two costs quoted without a probe field: one
`_migrations_fingerprint` pass and the rejected blake2b digest
(`fingerprint_cost.py` re-takes both). The rest is re-runnable by a named command:
the BEFORE-tree paragraph's counts are `git` (`rev-list --count`,
`--shortstat`), and §"Falsifiers" censuses twelve mutations and twenty-four
`FALSIFIER:` blocks over the file the tests name. Re-derive instead of trusting
the prose.

**The SEVEN items that are neither**, each named so a reader knows what to
re-derive and what to read as a local observation:

1. the server-logged `connection authorized` counts — a `grep -c` delta on the
   container's log, taken beside each run and recorded in the table below;
2. the `ctime_ns` "delta 0 ns" observation — a reading of THIS box's ext4 inode
   clock, not a probe field. What is reproducible is the pin that reds without
   `ctime_ns`; the 0 ns figure is not;
3. "4 skips in 10 runs" — the historical skip rate of the pre-fix pin, quoted to
   explain why the pin now burns a clock tick explicitly. It is not re-derivable
   now that the skip is gone;
4. the SUPERSEDED run set quoted in the note under the table — **51.7 – 53.1 →
   243.6 – 260.8 rec/s** — whose JSON was not kept either. That is why it appears
   only as a retracted figure, never as a current one;
5. the re-measurement paragraph under "Review cycle 2" — its four runs' JSON was
   not kept, so it carries no measured VALUES; only the run count and the
   `connect` count it observed, and that count is corroborated by all four
   committed AFTER runs (`per_timed_record.connect_calls = 0` in each);
6. the RESOLUTION half of the residual — the fifteen `Path.exists()` calls
   `_migrations_dir()` -> `_complete()` pays BEFORE the key is built —
   **0.169 ms**. This is the narrowest honest statement, because bullet (a)'s
   warm total and fingerprint pass DO have committed sources (its 0.79× RATIO
   does not — that is item 7): the warm total is
   `runs/*.json`'s `table_columns_isolated_ms.median` (0.332 / 0.339 / 0.342 /
   0.353 ms) and the fingerprint pass is `fingerprint-cost.json`'s
   `fingerprint_pass` (0.160–0.169 ms), so 2 x the warm median (≈0.66–0.71 ms) is
   arithmetic on a committed file and only the SPLIT between the two halves needs
   the resolution figure. That one was taken at `d51c7897d6` — this PR's head at
   the time, a descendant of the AFTER tree `6ab6848b8b` (verified) and not either
   compared tree, because the code it describes only exists after the change.
   Re-take it on the AFTER-side tree with
   `python docs/evidence/9558-record-write-cost/fingerprint_cost.py --column-consult`
   — it prints and commits nothing, deliberately, because a wall-clock file is
   only honest when it names the tree it was taken on. Re-taken at `860e85c91c` by
   two independent runs on this box (mine at `--reps 300`, a reviewer's at
   `--reps 100`): resolution **0.162–0.166 ms**, fingerprint **0.161 ms**, warm
   `table_columns()` **0.345–0.349 ms** — the ABSOLUTE values move with the box,
   which is exactly why they are a declared local observation; what carries is the
   sum property (warm ≈ resolution + one fingerprint pass), and that holds in both;
7. the SECOND cached reader's price relative to the first — **0.79×**, measured by a
   reviewer of this artifact on one box at `860e85c91c` and quoted here only as a
   SENSITIVITY. It is neither committed nor re-takeable: the instrument records
   `refused_props_names()`'s CALL COUNT (`probe_record_write_cost.py`, and
   `per_timed_record.refused_props_calls` in every `runs/*.json`) and never its
   TIME, and `fingerprint_cost.py --column-consult` measures `table_columns()`
   only. Nothing in the code bounds one reader's regex parse below the other's, so
   bullet (a) charges both consults at the one price the committed probe actually
   priced and reports the 0.79× case as a band, not a bound.

Earlier revisions quoted a run set whose JSON was not kept,
which made its numbers unfalsifiable — that is what `runs/` and
`fingerprint-cost.json` fix, and `fingerprint_cost.py` exists because the first
fix covered only the probe.

⚠️ **The BEFORE tree is 164 commits behind the base this measurement was taken
against, `23d227c534`**
(`1730570510` is 2026-11-03; the base is 2026-11-06), and **7 of those commits touch
the two files under measurement** (+883/−25: per-statement `param_columns`,
`Statement.__post_init__`, a per-write `_count_write_applied()`). ⛔ The denominator is the
**base**, not the AFTER tree's own parent: the AFTER tree `6ab6848b8b` is **5** commits into
the branch from that base (`e0e5484921`, `6c3b952066`, `440d1f95c2`, `a4d1a358e2`,
`6ab6848b8b`), so measured against its parent `a4d1a358e2` the same command reads **168**
commits / **11** touching / **+1465−48**. Those four extra commits are this PR's OWN, and a
drift figure that counted them would subtract the change under measurement.
So the delta is the pool plus the cache *plus* that drift. The direction is
conservative — the drift ADDS per-record work to the AFTER tree, so the measured
improvement is if anything understated — but it is a two-tree comparison whose
base must be named, not a controlled experiment.

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

Per-run values (**all eight are committed** as `runs/<run>.json`; the table is their transcription):

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

**(a) The connection half is ~83–87 % of the win, and it is now a REUSE, not a
claim.** The saving is 18.889–20.102 → 3.819–3.908 ms (means), i.e. 14.98–16.28 ms.
The column-map half is **~2.1–2.5 ms** of that — **TWO consults, not one**: the
per-record path drives BOTH cached readers (`table_columns()` AND
`refused_props_names()`, 1.00 call/record each in the table above, and at the
BEFORE tree `refused_props_names` called `_read_migrations` — the same full READ
of the same bytes `table_columns` paid; ⚠️ the READ is shared, the PARSE is not:
the two readers run different regexes over it, and the second's uncached-minus-warm
delta measured **0.79×** the first's on this box, so the column half is
**2 × 1.193–1.256 = 2.386–2.512 ms** under the artifact's convention (both consults
charged at the one price the committed probe priced) and **≈2.14–2.25 ms** if the
second reader is 0.79× as dear — ⚠️ that ratio is item 7 of the list at the top, a
SENSITIVITY, not a committed figure or a bound: a *costlier* second parse moves the
column half the other way, and nothing in the code or the committed runs fixes the
ratio). Both readers are charged on the MEDIAN, because the warm
cost is what the per-record path pays: the probe's 20-sample loop runs before the
first write, so its single cache-miss sample sits in the mean (AFTER mean
0.460–0.583 vs median 0.332–0.353; the BEFORE pair is 1.582–1.651 vs 1.546–1.588,
a **1.5–4.7 % gap** per run — 1.54 / 3.36 / 4.69 / 3.72 % — which shows the miss is
an AFTER-tree artifact: the AFTER means sit 35–76 % above their medians, the
BEFORE means a few per cent). 1.546–1.588 →
0.332–0.353 ms = **1.193–1.256 ms** for the `table_columns` consult, charged twice
for the two readers (the artifact's convention; see above) = **2.386–2.512 ms**. The
connection half is therefore the rest — **12.5–13.9 ms**, pairing the win's lower
end with the column half's UPPER end and its upper end with the column half's
lower end — i.e. **83.2–85.3 %**; if the second reader is 0.79× as dear the column
half is 2.14–2.25 ms, the connection half **12.7–14.1 ms** and its share
**85.0–86.9 %**. So the headline is **~83–87 %**, ~84–85 % of the win, the second
band being a SENSITIVITY on item 7 rather than a second measurement. ⚠️ This lands near a
retired "~80-85 % of the ~15 ms saved" (first stated at `6ab6848b8b`), and that is a
coincidence of BAND, not arithmetic: that revision did charge the consult twice, but at
0.27–0.33 ms of stat (a 0.135–0.166 ms band that no committed MEASUREMENT carried —
`fingerprint-cost.json` first appears at `873aec1812`; the band lived only in
prose, including this PR's own docstrings at `1120d0ef7` — with `_migrations_dir()`'s resolution
never priced at all), which against a ~15 ms win puts the connection half at
~98 % — so its own inputs never produced 80–85 % either. What the two-consult model fixes is
the SHAPE (both cached readers charged, at committed prices); the magnitude here
follows from the committed medians, and the intervening one-consult ~92 % is the
anomaly in this history.
`_default_connect` returns an idle handle when it has one, so the live
door's per-record "open, commit, close" became "borrow, commit, return". The
remaining 3.82–3.91 ms/record is one transaction boundary (prologue + plan + COMMIT)
plus plan materialisation — **plus, and this was under-stated in the first
revision, ≈0.68 ms of cache-consult overhead per record, in two halves this
bullet used to conflate**: `replay_plan` drives BOTH cached readers, and each
consult costs (i) `_migrations_dir()` → `_complete()`, fifteen `Path.exists()`
calls resolving the base **before** the key is built (`_schema_entry`'s
`base = migrations_dir or _migrations_dir()`, with `_complete` at `:1497` and
`_migrations_dir` at `:1502`; the seam is cited by SYMBOL on purpose — a bare line
number re-stales under exactly the edits this PR makes, and this one has re-staled
repeatedly inside it (most recently `1659 → 1665 → 1667 → 1669 → 1670`, and one of
those moves landed *after* a revision of this very sentence had counted the earlier
ones — which is why no total is quoted here), and (ii) one
`_migrations_fingerprint` pass over the same
fifteen files (0.160–0.169 ms, `fingerprint-cost.json`). The RESOLUTION half is
item 6 of the list at the top — taken on one box at `d51c7897d6`, re-takeable by
the command there: median **0.169 ms**; its siblings are committed (the fingerprint
pass in `fingerprint-cost.json`, the warm total in `runs/*.json`'s
`table_columns_isolated_ms.median`, 0.332–0.353 ms) — i.e. the committed warm medians
(0.332–0.353 ms) ARE those two halves summed PLUS the caller's own `dict` copy
(the copy `table_columns()` returns), not the fingerprint alone. So the
residual is ≈**0.68 ms/record** (2 × 0.34), ≈0.33 ms resolution + ≈0.33 ms
fingerprint. The known fix — compute the fingerprint once per plan build — saves
ONE pass, **0.160–0.169 ms**, and **leaves the resolution half in place**, so #11388
must not be read as removing the whole residual; it is filed rather than hidden in
"the work the record is for".

⛔ **Earlier revisions of this bullet, and why each was wrong.** (Deliberately no COUNT
of them: a count over an open history is stale by construction — the same hazard the
seam sentence above names, and this one has already been wrong twice.) `6ab6848b8b`
said "~80-85 % of the ~15 ms saved". Its own inputs do not yield it
(two 0.135–0.166 ms passes against a ~15 ms win is ~98 %), and it charged the
consult twice at a band no committed MEASUREMENT carried while leaving resolution
unpriced (the band itself was in committed prose — `git show
1120d0ef7:tortoise/record_projection.py` quotes it, until `873aec181` replaced it
with the committed 0.160–0.169; at this head it survives in no other file in this
repo — the one live carrier outside it is #11388's own body, named below);
the band this bullet now reports agrees with it by coincidence, not
arithmetic. ⛔ **CORRECTED:** an earlier revision of THIS erratum read that
80-85 % as "the connection half as a share of the BEFORE TOTAL, not of the win" —
falsified by its own text, `git show
6ab6848b8b:docs/evidence/9558-record-write-cost/README.md` reading "~80-85 % of the
~15 ms saved" (a share OF the win) and charging two fingerprints in the same
bullet. `1120d0ef7` said "~92-94 %" (94 % only falls out of ITS mean-based
~1.0–1.1 ms column half, not out of the ~0.9–1.0 ms figure — that one was a STAT figure (and 2 × the whole
cached call rather than 2 × the stat pass, as `6ab6848b8b`'s own erratum says); it
was never a column half). `860e85c91c`
said "~91.6–92.7 %" — the revision before `72df8168b2` — which charged ONE
column consult while the per-record path pays TWO (both cached readers, above), so
the column half is 2.386–2.512 ms under the artifact's convention (2.14–2.25 ms if
the second reader is 0.79× as dear, item 7) and the connection share 83.2–86.9 %,
not 1.193–1.256 ms and 92 %. `6c3b95206` said "≈0.9–1.0 ms of pure
stat" for the residual — which is 2 x the WHOLE cached call (stat + the per-call
dict copy), not 2 x the stat pass. `873aec1812` then said "0.32–0.34 ms of stat
overhead" (via `6ab6848b8b`'s 0.27–0.33 ms), which is the whole warm call attributed to the
fingerprint: **half of it is `_migrations_dir()`'s resolution**, so the residual
is ≈0.68 ms and only ≈0.34 ms of it is stat. Against the two denominators the
reader can pick, the retired ~0.9–1.0 ms figure was overstated **1.3–1.5× against
the residual (0.9–1.0 vs 0.68 ms)** and **5.3–6.3× against the one pass #11388
actually saves (0.9–1.0 vs 0.160–0.169 ms)**. Whichever denominator the reader
picks, it was wrong, and the denominator is named here now.
Those corrections were carried here and into the three places in this repo that
had copied the retired figure (⛔ #11388's own body still quotes the retired
`0.27–0.33` / `0.135–0.166` bands as current; that artifact is outside this repo,
so it is corrected on the issue itself rather than here).

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
blake2b over the fifteen files (129,355 bytes) costs **0.543–0.592 ms** per pass
against the stat pass's **0.160–0.169 ms** (`fingerprint-cost.json`), so it ADDS
0.374–0.432 ms net of the pass it replaces, against a warm-column-map win of
**1.193–1.256 ms** — i.e. **~30–36 % of the win**, about a third, cross-paired the
way this bullet pairs its other bands (0.374/1.256 = 29.8 %, 0.432/1.193 = 36.2 %).
⛔ An earlier revision said "most of the ~0.5 ms the cache saves", which
compared the digest against one stat pass instead of against the win and got the
verdict's character wrong; the remaining figures here are the same measurement,
re-derived. The decision (no digest) is unchanged: ~a third of the win for a residual that
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

**The read door's rollback is charged to the BORROW, not the release.** A read
ends INTRANS, so something must clear that transaction before the next borrower
inherits it. An earlier revision did it on the release path, which is a server
round trip taken **after the rows are fetched and therefore before the answer is
returned** — libpq has no per-statement timeout, so a backend that wedged in that
window blocked the release forever and threw away a read that had already
succeeded, contradicting `open_record`'s own "a served read must not be able to
hang a request on a wedged database". The reset now happens at REUSE
(`record_writer._reset_parked_transaction`): one round trip against the
**12.5–13.9 ms** of connect+auth it replaces (bullet (a)'s connection half — not
the whole 18.9–20.1 ms BEFORE record, which also contains work the read still
does), charged where the caller is about to issue statements anyway. It doubles
as the pool's liveness probe: both fields `_connection_is_reusable` reads are
LOCAL and stale once the peer is gone, so a handle parked INTRANS whose server
died while idle is caught by the borrow-time rollback, discarded, and replaced
by a fresh connect — instead of reaching the read door as a spurious
`RecordUnavailable` for a request a reconnect would have served. A handle the
WRITE door parked IDLE has no such probe; there the death is found at use and
costs one `write_failed`, healed by replay.

**Two silent-failure shapes are closed on the wrapper.** `close()` is now a fence
(`cursor`/`commit`/`rollback` raise the driver's `InterfaceError` once returned,
instead of committing whatever transaction the next borrower holds), `cursor()`
forwards its arguments (`cursor_factory=` would have been a `TypeError` on a call
site that was correct before), and a handle parked in `autocommit` mode is
normalised first — a transaction-local `set_config` sets NOTHING there, so the
tenant GUC would have failed every later record with an error naming the DSN.

**Re-measured after these fixes — an observation, deliberately without wall-clock
values.**
Four runs on the same instrument, on a box now carrying other work, showed the
per-record cost varying materially run-to-run with load while `connect` per
record stayed **0 in every run**. ⛔ Those four runs' JSON was not kept, so their
numbers are not quoted here: an unkept run set is exactly what the provenance
note at the top of this document refuses, and this paragraph is worth less than
that rule — the `connect` count stands as an OBSERVATION from unkept runs
(named as such in that note), never as a quoted figure. The mechanism claim — no
handshake, no re-parse — is what the change is; the wall-clock headline above is
the quiet-box measurement and stays labelled as such.

## Reproducing

```bash
# 1. a throwaway Postgres, on an ephemeral port, logging connections
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("",0));print(s.getsockname()[1])')
docker run -d --rm --name recpool-9558 -e POSTGRES_PASSWORD=record \
  -e POSTGRES_USER=record -e POSTGRES_DB=record -p 127.0.0.1:$PORT:5432 \
  mirror.gcr.io/postgres:16 -c log_connections=on -c log_disconnections=on

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

The mechanism half is pinned in `tests/test_9558_record_write_cost.py` (28
tests: 7 for the schema cache, 16 for the pool, and 5 for the wrapper/park/
read-door contracts the review cycles added). 24 of the 28 carry a recorded
mutation: **23** under a literal `FALSIFIER:` block and one
(`test_a_parked_transaction_is_reset_at_reuse_not_at_release`) stated as
`FALSIFIER, both halves:`. The four with no block at all
(`test_the_refused_props_map_also_reacts_to_an_edit`,
`test_a_removed_migration_is_seen_and_restores_the_error`,
`test_two_different_trees_never_share_a_parse`,
`test_the_returned_map_is_the_callers_own`) simply have none written for
them. ⛔ They are not mutation-proof by nature — dropping `str(base)` from
`_schema_entry`'s key reds the two-trees pin, and sharing the refused-props parse
across fingerprints reds the refused-props pin — so the honest statement is "no
falsifier was recorded for these four", not "no mutation exists". **Twelve were
OBSERVED RED** by breaking the source, running only the named test, and restoring
the file byte-identically (`/tmp/prove_falsifiers.py`, one pass; the ninth — the
parked-handle bound — is from review cycle 4, and the three that follow it are
from cycle 5, each re-measured the same way with the source restored
byte-identically — sha256 re-checked after restore):

| Mutation applied | Test that must notice | Observed |
|---|---|---|
| `_default_connect` never reuses (`reused = None`) | `test_the_record_path_opens_one_connection_for_many_records` | **RED** |
| drop the `table_columns` parse short-circuit | `test_the_column_map_is_parsed_once_for_an_unchanged_schema` | **RED** |
| identify a migration by inode only (mtime+size ignored) | `test_an_edited_migration_is_seen_immediately` | **RED** |
| accept a handle whose server went away | `test_a_broken_handle_is_discarded_not_reused` | **RED** |
| fix the transaction at RELEASE (the previous behaviour) | `test_a_parked_transaction_is_reset_at_reuse_not_at_release` | **RED** |
| park unconditionally (no idle bound) | `test_the_idle_set_is_bounded_and_overflow_is_closed` | **RED** |
| drop the double-close guard | `test_a_double_close_returns_one_handle_once` | **RED** |
| commit inside the hook on the reuse branch | `test_a_borrowed_handle_reports_the_same_outcome_to_its_caller` | **RED** |
| drop the `options` parked-handle bound from `_default_connect` | `test_every_connection_carries_the_parked_handle_bound` | **RED** |
| decide the bound by a substring of the whole `dsn` (the shape cycle 4 shipped) | `test_a_password_that_mentions_the_bound_does_not_disable_it` | **RED** |
| drop the `operator_options` half of `already_bounded` | `test_an_operator_idle_timeout_in_their_options_is_not_replaced` | **RED** |
| pass the bound without prefixing the operator's own `options` | `test_an_operators_other_startup_options_survive_the_bound` | **RED** |

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
  `mtime_ns` ALONE is caught only when the mtime-restore step lands in a later
  inode-clock tick than the force-forward step — i.e. sometimes, not always, which
  is why it is not a row the census can count. The mutation that reds is the
  INODE-ONLY one, `(name, 0, 0, inode, …)` (mtime and size both ignored), which is
  the row in the table above. Keeping the old sentence would have been a falsifier
  claim no test can fail, which is worse than having none.
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
28 passed          # [postgres] extra installed

$ python -m pytest tests/test_9558_record_write_cost.py -q
15 passed, 13 skipped   # the same file WITHOUT the extra
```

Both readings are of ONE tree — `594c4ae570`, the commit that added the
wrapper's cursor fence — differing only in whether the `[postgres]` extra is
installed; every skip
is a `_fake_driver` `importorskip` arm, which is why `test-record-postgres`
installs the extra and fails on any skip. The wider record-suite reading is
`docs/evidence/9558-record-write-cost/runs/`, NOT this console block — those
numbers move with which DB legs a given box can arm, and pinning one of them
here is what made this transcript stale in the first place.

`TEST_RECORD_DB_URI` armed (the throwaway container above) **and `psycopg2`
installed (the `[postgres]` extra)**, so the record's Postgres legs ran rather
than skipping. ⛔ Without the extra the SAME tree reports driver-gated skips —
the ones above plus the ones in the sibling record suites — which is exactly why
the `test-record-postgres` job installs the extra and fails on any skip.
