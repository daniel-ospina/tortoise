# The write ORDER, observed at runtime — #9567 D3 (run 2026-11-09)

**Verdict: `MEASURED / PARTIAL` — the criterion's ordering is now measured instead of argued, and it
holds on the sanctioned door and on the record leg, while the criterion's LETTER does not hold on the
capture path: two folds reach the cache before their own journal append, and six mutating cache
statements carry no declared lane.**

Read that precisely. Two different claims are separated below and must not be merged:

- **`journal → record` holds for every record the capture appends** (14 of 14, measured pairwise on the
  observed call order, and counted as WRITES — the door carries the seam's own `APPLIED` verdict, so a
  decline cannot pass as a write). No write path bypassed the record in this run.
- **`record → cache` holds on `_apply_and_journal`, the sanctioned live-write door** (journal append,
  then record write, then the fold and every statement the fold issues), with the seam re-armed for the
  probe so "record" means a row that landed.
- **No cache-only path** — D3's *second* half — is measured over the WHOLE mutating-cache population
  (all 32 statements, folded and raw-handle): every one is owned by a journal append whose record write
  landed. The criterion's own falsifier (*"inject a cache write that skips the record"*) is also
  FIRED at that detector in the same run, and the detector reds.
- **`journal → record → cache` does NOT hold for every write of a capture.** Two folds
  (`ObjectRegistered`, `EventRecorded`, both emitted by `_create_entity`) run ahead of their own
  append — the KNOWN DEVIATIONS block at the top of `tortoise/sdk.py` records them — and six mutating
  cache statements in this capture carried **no** declared lane. D3's own sentence is *"assert the
  capture writes journal → record → cache in that order, with no cache-only path"*, so **D3 is not met
  as written by this measurement**; what is met is the cache-only half, the part of the ordering a door
  can be held to, and the property that no cache-ahead write is *silent* — each one declares a lane,
  and the journal-first ones are a PINNED, named set (the recorded deviation), not a free pass.

| | |
|---|---|
| Issue | #9567 **D3** (carrier: #11477) |
| Head measured | `629283eef` + this PR's change to `tests/test_9567_record_capstone.py` (the earlier run at `cbb9df838` measured the same census) |
| Record | `postgres:16`, throwaway container `t9567-cap-pg` on `127.0.0.1:33209`, repo record migrations applied by `_ensure_record_schema`; writer connected as `tortoise_record_app` (the RLS-scoped app role) |
| Graph | the SDK's **embedded** backend (`TortoiseSDK(<tmp>/graph.db)`) — the same shape the armed CI job (`test-record-postgres`) runs, so this measurement is reproducible without a graph server |
| Instrument | **committed**: `tests/test_9567_record_capstone.py` §"D3's ORDERING half" — `Door` / `Traced` / `_instrument` / the `traced` fixture, the legs, and the two pure detectors (`_cache_only_writes`, the pinned ahead-set classifier) |
| Door wrapped | journal `tortoise.log.EventLog.append` · record `record_writer.write_live_record` · cache `FalkorProjection.apply` + every MUTATING `_GuardedGraph.query`, classified by the §17.1 guard's OWN classifier (`_statement_writes`) |
| "Declared lane" | read from the ContextVar the guard itself reads (`_CACHE_WRITE_AUTHORIZED`), so "declared" here means exactly what it means at the door |

## 0. The command

```console
$ TEST_RECORD_DB_URI='postgresql://record:record@127.0.0.1:33209/record' \
  TORTOISE_TEST_CARVE_OUT=1 \
  .venv/bin/python -m pytest tests/test_9567_record_capstone.py -q -p no:cacheprovider -k D3 -s
#9567 D3 write-order census: journal=14 record=14 fold=3 cache=32 cache-with-no-declared-lane=6
7 passed, 14 deselected in 140.50s (0:02:20)
```

The whole file, same run environment (so the D3 legs are not green in isolation while their neighbours
are broken):

```console
$ TEST_RECORD_DB_URI=… TORTOISE_TEST_CARVE_OUT=1 \
  .venv/bin/python -m pytest tests/test_9567_record_capstone.py -q -p no:cacheprovider -r fEs
20 passed, 1 skipped in 146.47s (0:02:26)
SKIPPED [1] …:1472: TEST_RECORD_SCRATCH_URI not set — the drill restores into a target that is NOT the
record, and `refuse_same_database` correctly refuses a drill pointed at the record itself.
```

The two detector tests need no Postgres and no graph (`_cache_only_writes` / the ahead classifier are
pure functions over `Door` records), so they also run on any box:

```console
$ TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_9567_record_capstone.py \
    -q -p no:cacheprovider -k "cache_only_detector"   → 1 passed in 3.60s
```

## 1. The census — counted in the run, never a constant

| door | count | meaning |
|---|---|---|
| journal (`EventLog.append`) | **14** | records the capture appended |
| record (`write_live_record`) | **14** | record writes, **14 APPLIED** and **pairwise equal to the journal, per event id** — the door carries the seam's own outcome, so a call that stored nothing reds |
| fold (`FalkorProjection.apply`) | **3** | sanctioned folds the capture drove |
| mutating cache statements | **32** | every CREATE/MERGE/SET issued through `proj.g`; **32 of 32 owned by an append whose record write landed** (no cache-only path) |
| …of those, **no declared lane** | **6** | `aboutObject` edge, `CONTAINS` edge, `status='live'`, two `ep_dirty` sweeps, `lastDreamedAt`. This is the §17.1 census class (#9124 part 1 measures, does not yet refuse; the writers are owed a record/#9350). ⚠️ A different question from cache-only: all six sit in an episode whose append + record landed, so none of them is a cache-only write |

## 2. The legs, and what each one measures

| leg | assertion | observed |
|---|---|---|
| `test_D3_order_the_record_write_follows_the_journal_append` | every journal append gets an **APPLIED** record write, same `event_id`, and no record write precedes the first append | **green** — 14/14 paired, 14/14 APPLIED |
| `test_D3_order_a_journal_first_door_writes_journal_record_then_cache` | `_apply_and_journal` (the sanctioned chokepoint, `tools/record_wiring_check.py`'s production call site) writes journal → record → fold → fold statements, with the seam re-armed and the record door APPLIED | **green** — `['journal', 'record', 'fold', 'cache', 'cache']` |
| `test_D3_order_no_cache_write_runs_ahead_of_its_append_SILENTLY` | a fold that runs ahead of its append declares a lane, and the set of `(event_type, lane)` pairs that then claim the JOURNAL-FIRST order is exactly the recorded deviation set (pinned, so it reds when the set grows **or** shrinks); an empty "ahead" population is also a RED, because a leg that measured nothing is not a measurement | **green** — `{('EventRecorded', 'create_entity'), ('ObjectRegistered', 'create_entity')}` |
| `test_D3_order_no_cache_write_runs_without_a_journal_append_and_a_record_write` | every mutating cache statement (32, folded **and** raw-handle) is owned by a journal append whose record write landed — D3's *"no cache-only path"* half | **green** — 32/32 owned |
| `test_D3_a_cache_write_that_skips_the_record_is_detected` | D3's own falsifier, FIRED live: a declared lane that writes the cache and appends nothing must red the detector the leg above uses | **green** — the injected statement is reported as cache-only |
| `test_D3_the_cache_only_detector_reds_on_an_unowned_cache_write` | the same detector, DB-free, on synthetic traces: owned = green; no append / REFUSED record / unarmed record / fold never appended = red | **green** |

The static half is unchanged and still runs beside these: `test_D3_no_write_path_bypasses_the_record`
executes `tools/record_wiring_check.py` → `WIRED`, exit 0.

## 3. Every falsifier, observed RED (not asserted — run)

Each mutation was applied to a throwaway checkout of this worktree, the named leg was run, the red was
captured, and the tree restored with `git checkout -- tortoise/sdk.py tortoise/record_writer.py`
(`git status --porcelain` clean afterwards). None of these mutations is in the diff. **F3 and F4 were
applied TOGETHER in one run** (2.3 minutes each, and each red names its own leg); F1, F2 and F3′ were
recorded on the previous head and are unchanged by the fix.

**F1 — drop the record write (`_emit_event`'s `record_sink` branch) → leg 1**

```console
E AssertionError: the capture appended 14 journal record(s) and made 0 record write(s):
  a write path bypassed the record (or invented one)
    assert 0 == 14
1 failed, 17 deselected in 129.12s
```

**F2 — fold before the append inside `_apply_and_journal` → leg 2**

```console
E AssertionError: `_apply_and_journal` did not write journal → record → cache: observed
  ['fold', 'cache', 'cache', 'journal', 'record'] (journal at 3, record at 4, fold at 0)
    assert 4 < 0
1 failed, 17 deselected in 129.34s
```

**F3 — a fold that runs ahead under a JOURNAL-FIRST lane nobody recorded → leg 3**

`_create_entity`'s scope renamed to `_live_write_scope("chokepoint")` (the lane `_apply_and_journal`
uses, so this is what a reordered chokepoint looks like in the trace):

```console
E AssertionError: the journal-first-ahead fold population changed: observed
  [('EventRecorded', 'chokepoint'), ('ObjectRegistered', 'chokepoint')], pinned
  [('EventRecorded', 'create_entity'), ('ObjectRegistered', 'create_entity')].
  • A `(event_type, lane)` that is NEW here is a fresh violation of D3's order …
  • A pinned pair that is GONE means that deviation was closed or re-declared apply-first …
2 failed, 19 deselected in 128.94s
```

⛔ **The falsifier this REPLACES was not a falsifier.** The earlier record of this row ("make
`_create_entity` fold under the journal-first lane while still folding first → a door declaring the
journal-first lane `'live-door'` …") red because the mutation renamed the lane to the literal
`"live-door"` — `assert 'live-door' != 'live-door'` — a string no door emits
(`projection/__init__.py:4063` is only the DEFAULT argument of `_live_write_scope`, overridden by every
caller). It therefore proved the comparison, not the property, and the property it claimed was FALSE of
the run it reported green (#11647 review F1).

**F4 — make the seam decline every call (`write_live_record` forced to the unarmed return) → leg 1**

The door count is untouched (14 calls, 14 appends) and only the APPLIED count collapses, which is
exactly the shape that used to read as green:

```console
E AssertionError: the capture appended 14 journal record(s) and only 0 reached the record as APPLIED
  (outcomes: {'None': 14}) — the door was CALLED and did not WRITE, which is the
  `derived = replay(journal)` gap D3 measures (an unarmed/refused call is not a record write)
    assert 0 == 14
2 failed, 19 deselected in 128.94s
```

**F5 — inject a cache write that skips the record → the cache-only detector**

Fired by `test_D3_a_cache_write_that_skips_the_record_is_detected` on every run, not by a hand-applied
mutation: it opens `_live_write_scope("injected-cache-only-probe")`, writes one `CREATE (:CacheOnlyProbe)`,
and asserts the detector reports it. It runs green **because the detector reds**, which is what makes
leg 4 a measurement rather than an assertion nobody has broken on purpose. (The injection opens a
declared lane because that is the only reachable shape — see F3′.)

**F3′ — the same fold run with NO lane at all** (recorded because it measures a different door than
expected, and the difference matters for what #11477 still owes):

```console
E AssertionError: no fold ran ahead of its append — this leg measured nothing. …
WARNING tortoise.sdk: capture of session …: sessionCaptured Event/EventRecorded write failed
  (non-fatal): Refusing a DIRECT fold: `FalkorProjection.apply` was called with no sanctioned write
  scope in flight. … event_type='EventRecorded'
```

So a lane-less **fold** is already REFUSED by the fold door; a lane-less **raw handle** write is only
MEASURED. That is why the six undeclared statements in §1 are the residual, why they are a §17.1
*declaration* gap rather than a cache-only one, and why the injected falsifier above has to open a lane
to reach the write handle at all.

## 4. What this does NOT prove, stated so it cannot be over-read

- **It does not discharge D3.** The criterion's letter — *"journal → record → cache in that order"* —
  is false on the capture path today: two `_create_entity` registration folds precede their append (a
  recorded deviation, not a regression this run introduced), and six cache statements are undeclared.
  The residual is carried by **#11477**; this run narrows what that carrier owes.
- **It does not claim the cache-only detector is complete.** Ownership is attributed to the append
  EPISODE a statement falls in: a cache write injected *inside* an episode that appended **and whose
  record landed** is attributed to that episode. What this closes is the reachable class — a declared
  lane that writes the cache and appends nothing (the #9068 shape), a fold whose event was never
  appended, and any episode whose record write declined. A lane-less mutating statement never reaches
  the instrument at all: the §17.1 guard refuses it first (F3′), and that refusal — not this leg — is
  the control for that class.
- **It does not prove `_delete_entity` / `_session_event_write` / `_update_entity` / `create_operator`**
  (the four doors the module-top contract lists as still apply-first). This run exercised the
  **capture** path only; those doors are not on it.
- **It does not prove anything about a server-mode FalkorDB.** The graph here is the SDK's embedded
  backend, deliberately: that is the shape the armed CI job runs. A server-mode graph reaches the same
  `_GuardedGraph.query` / `FalkorProjection.apply` doors, so the instrument transfers, but this run did
  not measure it.
- **It is not a claim that `fold` count 3 means "3 cache writes".** The cache axis is 32 statements; the
  fold door is one *entry point* into them (the other statements are issued by declared live/apply-first
  lanes and by the six undeclared ones).

## 5. What #11477 inherits from this run

1. **D3 now has a runtime instrument that reds** for every direction D3 names: an append whose record
   write did not land (F4), a reordered chokepoint (F2), an ahead fold under an unrecorded journal-first
   lane (F3), and a cache write that skips the record (F5, fired on every run). Its status moves from
   `NOT MEASURED` to `MEASURED, letter unmet` — the unmet letter is ONLY the ahead-fold direction, and
   those two arms are named by `(event_type, lane)` in a pinned set.
2. **The remaining code work is named**: convert `_create_entity`'s Object/Event registration blocks to
   journal-first (the KNOWN DEVIATIONS block says this rides the #2194/#2295/#2309 hardening and its
   pinned tests — a reviewed change, not a lane's reorder), and give the six undeclared capture writers
   a lane or a record (#9124 part 1, #9350). When either lands, the pin in leg 3 moves with it — by
   design, including the direction where a deviation is CLOSED, because a population that shrank
   silently would leave D3 graded against a tree that no longer exists.
3. **The pooler and narrative halves of #11477 are untouched by this run** (D9's G1 leg, D7's narrative
   reader leg) — this artifact is D3 only.
