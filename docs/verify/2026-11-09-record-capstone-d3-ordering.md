# The write ORDER, observed at runtime — #9567 D3 (run 2026-11-09)

**Verdict: `MEASURED / PARTIAL` — the criterion's ordering is now measured instead of argued, and it
holds on the sanctioned door and on the record leg, while the criterion's LETTER does not hold on the
capture path: two folds reach the cache before their own journal append, and six mutating cache
statements carry no declared lane.**

Read that precisely. Two different claims are separated below and must not be merged:

- **`journal → record` holds for every record the capture appends** (14 of 14, measured pairwise on the
  observed call order). No write path bypassed the record in this run.
- **`record → cache` holds on `_apply_and_journal`, the sanctioned live-write door** (journal append,
  then record write, then the fold and every statement the fold issues).
- **`journal → record → cache` does NOT hold for every write of a capture.** Two folds
  (`ObjectRegistered`, `EventRecorded`, both emitted by `_create_entity`) run ahead of their own
  append — the KNOWN DEVIATIONS block at the top of `tortoise/sdk.py` records them — and six mutating
  cache statements in this capture carried **no** declared lane. D3's own sentence is *"assert the
  capture writes journal → record → cache in that order, with no cache-only path"*, so **D3 is not met
  as written by this measurement**; what is met is the part of it that a door can be held to, plus the
  property that no cache-ahead write is *silent* (each one declares the order it used), plus the
  second half of the criterion: **no cache-only path** — every mutating cache statement has a journal
  append and an APPLIED record write behind it (§6).

| | |
|---|---|
| Issue | #9567 **D3** (carrier: #11477) |
| Head measured | `cbb9df838` + this PR's change to `tests/test_9567_record_capstone.py` |
| Record | `postgres:16`, throwaway container `t9567-cap-pg` on `127.0.0.1:33209`, repo record migrations applied by `_ensure_record_schema`; writer connected as `tortoise_record_app` (the RLS-scoped app role) |
| Graph | the SDK's **embedded** backend (`TortoiseSDK(<tmp>/graph.db)`) — the same shape the armed CI job (`test-record-postgres`) runs, so this measurement is reproducible without a graph server |
| Instrument | **committed**: `tests/test_9567_record_capstone.py` §"D3's ORDERING half" — `Door` / `Traced` / `_instrument` / the `traced` fixture and the three legs |
| Door wrapped | journal `tortoise.log.EventLog.append` · record `record_writer.write_live_record` · cache `FalkorProjection.apply` + every MUTATING `_GuardedGraph.query`, classified by the §17.1 guard's OWN classifier (`_statement_writes`) |
| "Declared lane" | read from the ContextVar the guard itself reads (`_CACHE_WRITE_AUTHORIZED`), so "declared" here means exactly what it means at the door |

## 0. The command

```console
$ TEST_RECORD_DB_URI='postgresql://record:record@127.0.0.1:33209/record' \
  TORTOISE_TEST_CARVE_OUT=1 \
  .venv/bin/python -m pytest tests/test_9567_record_capstone.py -q -p no:cacheprovider -k D3 -s
#9567 D3 write-order census: journal=14 record=14 fold=3 cache=32 cache-with-no-declared-lane=6
4 passed, 14 deselected in 142.99s (0:02:22)
```

The whole file, same run environment (so the D3 legs are not green in isolation while their neighbours
are broken):

```console
$ TEST_RECORD_DB_URI=… TORTOISE_TEST_CARVE_OUT=1 \
  .venv/bin/python -m pytest tests/test_9567_record_capstone.py -q -p no:cacheprovider -r fEs
17 passed, 1 skipped in 144.21s (0:02:24)
SKIPPED [1] …:1086: TEST_RECORD_SCRATCH_URI not set — the drill restores into a target that is NOT the
record, and `refuse_same_database` correctly refuses a drill pointed at the record itself.
```

## 1. The census — counted in the run, never a constant

| door | count | meaning |
|---|---|---|
| journal (`EventLog.append`) | **14** | records the capture appended |
| record (`write_live_record`) | **14** | record writes; **pairwise equal to the journal, per event id** |
| fold (`FalkorProjection.apply`) | **3** | sanctioned folds the capture drove |
| mutating cache statements | **32** | every CREATE/MERGE/SET issued through `proj.g` |
| …of those, **no declared lane** | **6** | `aboutObject` edge, `CONTAINS` edge, `status='live'`, two `ep_dirty` sweeps, `lastDreamedAt`. This is the §17.1 census class (#9124 part 1 measures, does not yet refuse; the writers are owed a record/#9350) |

## 2. The three legs, and what each one measures

| leg | assertion | observed |
|---|---|---|
| `test_D3_order_the_record_write_follows_the_journal_append` | every journal append gets a record write, same `event_id`, and no record write precedes the first append | **green** — 14/14 paired |
| `test_D3_order_a_journal_first_door_writes_journal_record_then_cache` | `_apply_and_journal` (the sanctioned chokepoint, `tools/record_wiring_check.py`'s production call site) writes journal → record → fold → fold statements | **green** — `['journal', 'record', 'fold', 'cache', 'cache']` |
| `test_D3_order_no_cache_write_runs_ahead_of_its_append_SILENTLY` | a fold that runs ahead of its append declares a lane, and the `(event_type, lane)` set of ahead folds declaring a **journal-first** lane equals the tree's recorded deviation set | **green** — the `_create_entity` registration folds run ahead under `"create_entity"`, which IS a journal-first lane, and that is exactly the pinned set |
| `test_D3_order_no_cache_write_runs_without_a_journal_append_and_a_record_write` | the criterion's second half — **no cache-only path**: every mutating cache statement (folded *and* raw-handle) has a journal append and an **APPLIED** record write behind it | **green** — no cache statement precedes the first append or the first APPLIED record |

The static half is unchanged and still runs beside these: `test_D3_no_write_path_bypasses_the_record`
executes `tools/record_wiring_check.py` → `WIRED`, exit 0.

## 3. Every falsifier, observed RED (not asserted — run)

Each mutation was applied to a throwaway checkout of this worktree, the named leg was run, the red was
captured, and the tree restored with `git checkout -- tortoise/sdk.py` (`git status --porcelain` clean
afterwards). None of these mutations is in the diff.

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

**F3 — ⛔ SUPERSEDED AND WRONG. Review cycle 1 found this falsifier proved a RENAME, not the property.**

It claimed to fire by *"making `_create_entity` fold under the JOURNAL-FIRST lane while it still folds
first"*. Read its own output: `assert 'live-door' != 'live-door'`. The mutation was to the **test file**
(a literal), and the tree under test was unchanged — `_create_entity` already folds first under
`_live_write_scope("create_entity")`, a journal-first lane, and the leg was **green** on it. The
assertion compared against `"live-door"`, which is only `_live_write_scope`'s *default argument* and a
lane no production caller emits, so it could not fail on any production change. See §6 for the
replacement, which reds on the real property.

**F3′ — the same fold run with NO lane at all** (recorded because it measures a different door than
expected, and the difference matters for what #11477 still owes):

```console
E AssertionError: no fold ran ahead of its append — this leg measured nothing. …
WARNING tortoise.sdk: capture of session …: sessionCaptured Event/EventRecorded write failed
  (non-fatal): Refusing a DIRECT fold: `FalkorProjection.apply` was called with no sanctioned write
  scope in flight. … event_type='EventRecorded'
```

So a lane-less **fold** is already REFUSED by the fold door; a lane-less **raw handle** write is only
MEASURED. That is why the six undeclared statements in §1 are the residual, and why leg 3's "silent"
falsifier had to be expressed against the lane the door *claims* (F3) rather than against an absent one.

## 4. What this does NOT prove, stated so it cannot be over-read

- **It does not discharge D3.** The criterion's letter — *"journal → record → cache in that order"* —
  is false on the capture path today: two `_create_entity` registration folds precede their append (a
  recorded deviation, not a regression this run introduced), and six cache statements are undeclared.
  The residual is carried by **#11477**; this run narrows what that carrier owes.
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

1. **D3's ordering assertion now exists and is falsifiable** — the item's status moves from
   `NOT MEASURED` to `MEASURED, letter unmet`, with the two deviating arms named by event type.
2. **The remaining code work is named**: convert `_create_entity`'s Object/Event registration blocks to
   journal-first (the KNOWN DEVIATIONS block says this rides the #2194/#2295/#2309 hardening and its
   pinned tests — a reviewed change, not a lane's reorder), and give the six undeclared capture writers
   a lane or a record (#9124 part 1, #9350).
3. **The pooler and narrative halves of #11477 are untouched by this run** (D9's G1 leg, D7's narrative
   reader leg) — this artifact is D3 only.

## 6. Review cycle 1 — three inert assertions, found and fixed

A **fresh-context** reviewer (PR #11647 comment `IC_kwDOToroq88AAAABkmL_Kg`) found that all three added
legs asserted something weaker than they claimed. Each finding was verified against primary source
before it was applied; each fix is in this PR, and each replacement falsifier was **executed and observed
RED** (§6.2).

| # | finding | verified against | fix |
|---|---|---|---|
| 1 | **P1** — leg 3's `stmt.lane != "live-door"` could never fail: `"live-door"` is only `_live_write_scope`'s **default argument** (`projection/__init__.py:4063`) and no production caller uses it (`git grep '"live-door"'` → 2 hits: that default, and the assertion). It was green on the exact contradiction it documented: the ahead folds run under `_live_write_scope("create_entity")`, a journal-first lane. | `git grep` both hits; `sdk.py:43309`; `projection/__init__.py:3535-3540`; `sdk.py:4835` KNOWN DEVIATIONS | classify with the tree's own discriminator `_APPLY_FIRST_LANE` (`projection/__init__.py:4969`, read back at the write handle by `test_9206`) and pin the `(event_type, lane)` SET `_KNOWN_AHEAD_DEVIATIONS` |
| 2 | **P1** — the criterion's *"with no cache-only path"* half was **unmeasured**: leg 3 inspects only statements of a fold that ran ahead (`e.in_fold == fold.event_type`), so a mutating statement with `in_fold=None` — the shape D3's own falsifier injects — was examined by no leg | `tests/test_9567_record_capstone.py` leg 3 population; issue #9567 D3 falsifier sentence | a **fourth leg** over the whole mutating-cache population requiring a journal append and an APPLIED record write behind every statement |
| 3 | **P2** — the record door counted **calls**, not **writes**: the wrapper appended `Door("record")` after forwarding and discarded `out`, so a declined seam kept `len(records) == len(journal)` green. Leg 2's probe runs **inside** the test body, where the autouse `_no_ambient_record_arming` (`tests/conftest.py:664`) has cleared the DSN — so it was ordering a record write that wrote nothing. | `record_writer.py:2338` (`return None` when unarmed), `:2178` (REFUSED), `tests/conftest.py:664-706`; leg 2 body | carry the seam's `outcome` on `Door`; leg 1 asserts APPLIED count == journal count; leg 2 re-arms the way `test_D1` does and asserts the door APPLIED |

### 6.1 Re-measured at this head (`629283eef`, after the fixes)

```console
$ TEST_RECORD_DB_URI='postgresql://record:record@127.0.0.1:33209/record' TORTOISE_TEST_CARVE_OUT=1 \
  .venv/bin/python -m pytest tests/test_9567_record_capstone.py -q -p no:cacheprovider -k D3 -s
#9567 D3 write-order census: journal=14 record=14 fold=3 cache=29 cache-with-no-declared-lane=6
5 passed, 14 deselected
```

The whole file, same environment:

```console
18 passed, 1 skipped in 19.60s
SKIPPED: TEST_RECORD_SCRATCH_URI not set — the drill refuses a scratch target that IS the record
```

(`cache=29`, not the §1 run's 32: the two runs are different heads and different captures. Both are
counted in the run that printed them, never asserted against a constant.)

### 6.2 The four replacement falsifiers, executed and observed RED

Each mutation was applied to this worktree's committed tree, the leg run, the red captured, then the
file restored with `git checkout -- <file>` (`git status --porcelain` empty afterwards).

**F1 — drop the record write in `_emit_event` → leg 1 (count half)**
```console
E AssertionError: the capture appended 14 journal record(s) and made 0 record write(s):
  a write path bypassed the record (or invented one)
```

**F2 — swap the fold BEFORE the append inside `_apply_and_journal` → leg 2**
```console
E AssertionError: `_apply_and_journal` did not write journal → record → cache: observed
  ['fold', 'cache', 'journal', 'record'] (journal at 2, record at 3, fold at 0)
```

**F3 — the same swap → leg 3 (the property, not a rename)**
```console
E AssertionError: the journal-first-ahead fold population changed: observed
  [('EventRecorded', 'create_entity'), ('ObjectRegistered', 'chokepoint'),
   ('ObjectRegistered', 'create_entity')],
  pinned [('EventRecorded', 'create_entity'), ('ObjectRegistered', 'create_entity')].
```
The `('ObjectRegistered', 'chokepoint')` pair is the reordered door naming ITSELF — this is the red the
old literal comparison could not produce.

**F4 — make the record seam DECLINE (force the unarmed branch) → leg 1 (APPLIED half)**
```console
E AssertionError: the capture appended 14 journal record(s) and only 0 reached the record as APPLIED
  (outcomes: {'None': 14}) — the door was CALLED and did not WRITE, which is the
  `derived = replay(journal)` gap D3 measures (an unarmed/refused call is not a record write)
```
This is the finding-3 red: the count-based assertion alone stays green here.

**F5 — inject `CREATE (:X {id:'cache-only'})` at the top of the measured region → leg 4**
```console
E AssertionError: 1 mutating cache statement(s) ran with no journal append and no APPLIED record write
  behind them — the cache-only path D3 forbids: [0] "CREATE (:X {id:'cache-only'})" (lane=None, in_fold=None)
```
This is D3's OWN stated falsifier (*"inject a cache write that skips the record → D3 must red"*), which
before this cycle left all three legs green.

### 6.3 What the fixes do not change

D3 is still **not discharged**, and this PR still reorders no production write path. What changed is that
the instrument's three assertions can now fail for the failure each one names. §3's F3 is retained above
with a `⛔ SUPERSEDED AND WRONG` marker rather than deleted: it recorded a real run whose *conclusion* was
mistaken, and a reader who trusted it would re-apply the inert comparison.
