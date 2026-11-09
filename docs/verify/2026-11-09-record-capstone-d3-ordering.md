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
  property that no cache-ahead write is *silent* — **leg 3 covers every cache statement in an ahead
  fold's window, raw-handle ones included, and requires a declared lane** — plus a
  FLOOR on the second half — no mutating cache statement precedes the capture's first journal append
  and first APPLIED record write (§6). It does **not** establish a per-write attribution for
  raw-handle cache writes outside an ahead fold's window; see §4.

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
| `test_D3_order_no_cache_write_runs_ahead_of_its_append_SILENTLY` | every cache statement in an ahead fold's window (raw-handle ones **included**) declares a lane, and the `(event_type, lane)` set of ahead folds declaring a **journal-first** lane equals the tree's recorded deviation set | **green** — the `_create_entity` registration folds run ahead under `"create_entity"`, which IS a journal-first lane, and that is exactly the pinned set |
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
- **It does not attribute an arbitrary raw-handle cache write to an append.** Leg 4 asserts a floor —
  nothing mutating reaches the cache before the capture's first journal append and first APPLIED record
  write. A cache write injected MID-capture (after some append and its record have landed) is green in
  every leg, **measured** (§6.2 F6), because the trace has no descriptor tying a raw `_GuardedGraph.query`
  statement to the append it skipped. D3's falsifier is therefore satisfied for the early shape only.

  ⛔ **A CHEAPER STRENGTHENING EXISTS AND IS DELIBERATELY DECLINED, stated so no one thinks the floor is
  the ceiling.** A membership rule is checkable from this trace: *every mutating cache statement is
  fold-attached (`in_fold is not None`) OR carries a declared lane*, with the lane-less raw population
  pinned by statement shape. It would red on §6.2 F6. It is declined because the pin it needs is ~30 raw
  CYPHER strings (the six lane-less writers plus the `apply-first:` / `create_*` / `event-store-*`
  statements), so it would red on any unrelated change to those statements — a signal that would be
  silenced by editing the pin rather than by fixing a write path. Per-fold attribution is separately
  available and already used: leg 3 pins the two `_create_entity` arms that legitimately fold first, and
  leg 2 requires every statement in a sanctioned door's window to follow that door's own record write.
- **It does not cover every mutating verb on the handle.** Only `_GuardedGraph.query` is wrapped.
  `ro_query` and `execute_command` are write-CAPABLE by the module's own comments
  (`projection/__init__.py:5739-5743`, `:5796-5803`), so a write through either is invisible here.

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
| 3 | **P2** — the record door counted **calls**, not **writes**: the wrapper appended `Door("record")` after forwarding and discarded `out`, so a declined seam kept `len(records) == len(journal)` green. Leg 2's probe runs **inside** the test body, where the autouse `_no_ambient_record_arming` (`tests/conftest.py:664`) has cleared the DSN — so it was ordering a record write that wrote nothing. | `record_writer.py:2340` (`return None` when unarmed), `:2343` (`return _refused(...)`), `tests/conftest.py:664-706`; leg 2 body | carry the seam's `outcome` on `Door`; leg 1 asserts APPLIED count == journal count; leg 2 re-arms the way `test_D1` does and asserts the door APPLIED |

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

### 6.2 The replacement falsifiers, executed and observed RED

Each mutation was applied to this worktree's committed tree, the leg run, the red captured, then the
file restored from a byte-exact copy and verified with `git diff --exit-code` (`git status
--porcelain` empty afterwards). Every red below is reproducible **at this head**.

#### F1 — drop the record write in `_emit_event` → leg 1 (count half)

```console
E AssertionError: the capture appended 14 journal record(s) and made 0 record write(s):
  a write path bypassed the record (or invented one)
```

#### F2 — swap the fold BEFORE the append inside `_apply_and_journal` → leg 2

```console
E AssertionError: `_apply_and_journal` did not write journal → record → cache: observed
  ['fold', 'cache', 'journal', 'record'] (journal at 2, record at 3, fold at 0)
```

#### F3 — re-declare a CAPTURE door's lane while it still folds first → leg 3

`_live_write_scope("create_entity")` → `"create_entity_v2"`:

```console
E AssertionError: the journal-first-ahead fold population changed: observed
  [('EventRecorded', 'create_entity_v2'), ('ObjectRegistered', 'create_entity_v2')],
  pinned [('EventRecorded', 'create_entity'), ('ObjectRegistered', 'create_entity')].
E   • NOT RECORDED anywhere: [('EventRecorded', 'create_entity_v2'), …
    This is a fresh violation of D3's order — a door claiming journal-first while folding first.
```

⛔ **SUPERSEDED:** an earlier revision of this section showed a red containing
`('ObjectRegistered', 'chokepoint')` from reordering `_apply_and_journal`. That red **is not
reproducible at this head** and its inclusion was itself an artifact of the leak §6.1 F7 removed:
leg 2's probe used to append into the module-scoped trace, so leg 3 saw the reordered probe fold. With
the probe trimmed, `_apply_and_journal` is not on the capture path and a reorder there is **leg 2's**
falsifier (F2), not leg 3's.

#### F4 — make the record seam DECLINE (force the unarmed branch) → leg 1 (APPLIED half)

```console
E AssertionError: the capture appended 14 journal record(s) and only 0 reached the record as APPLIED
  (outcomes: {'None': 14}) — the door was CALLED and did not WRITE, which is the
  `derived = replay(journal)` gap D3 measures (an unarmed/refused call is not a record write)
```

This is the round-1 finding-3 red: the count-based assertion alone stays green here.

#### F5 — inject `CREATE (:X {id:'cache-only'})` at the top of the measured region → leg 4

```console
E AssertionError: 1 mutating cache statement(s) ran with no journal append and no APPLIED record write
  behind them — the cache-only path D3 forbids: [0] "CREATE (:X {id:'cache-only'})" (lane=None, in_fold=None)
```

#### F6 — ⛔ THE SAME INJECTION PLACED MID-CAPTURE DOES **NOT** RED, and that is the disclosed residue

The same cache-only write injected after `_emit_event`'s record branch — i.e. after some append and its
record have already landed — leaves **all five** D3 legs green (`cache=43 … 5 passed`, measured). Leg 4
asserts a FLOOR (nothing before the first append / first APPLIED record), not a per-write attribution,
because the trace carries no descriptor tying an arbitrary raw-handle statement to the append it
skipped. Its name, its docstring and the verdict line say exactly that. See §4.

#### F7 — raw-handle cache write on the sanctioned door BEFORE its append → leg 2

A `proj.g.query(...)` issued on `_apply_and_journal` before its append used to be unexamined: leg 2's
ordering loop filtered to `e.in_fold is not None`, and a raw statement has `in_fold is None`. Measured
green before the fix, red after:

```console
E AssertionError: a cache statement ran before the door's record write (0 <= 2:
  "MERGE (x:CacheAhead {id:'cache-ahead'})", in_fold=None)
  — the cache is ahead of the record on a door that claims journal-first
```

#### F8 — lane-less raw cache write INSIDE an ahead fold's window → leg 3

A `proj.g.query("CREATE (:SilentAhead …)")` issued after `_create_entity`'s fold and before that
event's append is exactly the silent cache-ahead shape. Leg 3's statement window used to be filtered to
`e.in_fold == fold.event_type`, so the raw statement (`in_fold=None`) was never examined: measured
`cache-with-no-declared-lane=9` and **5 passed**. Widened to every cache statement in the window
(already bounded by the fold index and its own append), it reds in the clean-run-preserving direction:

```console
E AssertionError: a cache write ahead of its journal append ran with NO declared lane
  ("CREATE (:SilentAhead {id:'silent'})", fold EventRecorded, in_fold=None)
  — that is the §17.1 silent cache-ahead write D3 forbids
```

### 6.3 What the fixes do not change

D3 is still **not discharged**, and this PR still reorders no production write path. What changed is that
the instrument's three assertions can now fail for the failure each one names. §3's F3 is retained above
with a `⛔ SUPERSEDED AND WRONG` marker rather than deleted: it recorded a real run whose *conclusion* was
mistaken, and a reader who trusted it would re-apply the inert comparison.

## 7. Review cycle 2 — seven more, all verified and fixed

A second fresh-context review, on the head this section's predecessor produced, found seven more. Each
was reproduced before it was fixed; the two substantive ones are now falsifiers F7 and F8 in §6.2.

| # | finding | verified against | fix |
|---|---|---|---|
| 1 | **P1** — leg 3's statement window was filtered to `e.in_fold == fold.event_type`, so a **lane-less raw** cache write inside an ahead fold's window was never examined, and the "no cache-ahead write is silent" claim was measured only for fold-attached statements | reproduced: `CREATE (:SilentAhead …)` after `_create_entity`'s fold, before its append → `cache-with-no-declared-lane=9`, **5 passed** | window widened to every cache statement (it is already bounded by the fold index and its own append). Clean run still green; the injection now reds (§6.2 F8) |
| 2 | **P2** — `_ahead_population_advice` partitioned on event TYPE only, so a brand-new **lane** on a recorded event type was reported as "RECORDED, extend the pin" instead of a fresh violation | reproduced with the lane-change mutation | partitions on the `(event_type, lane)` **pair**; `create_entity` re-exercise is the only excused shape. Verified: the lane change now prints `NOT RECORDED anywhere` |
| 3 | **P2** — `_KNOWN_AHEAD_DEVIATIONS`'s comment named an `_apply_and_journal` reorder as a producible new pair, which this head cannot produce (that door is not on the capture path, and leg 2's probe is trimmed) | measured: reordering `_apply_and_journal` reds leg 2 only; leg 3 green | comment now names a CAPTURE door re-declaring its lane, matching leg 3's own docstring |
| 4 | **P2** — §6.2 F3 showed the `('ObjectRegistered','chokepoint')` red, which existed only at the superseded head (it was a product of the probe leak) | re-ran the same mutation at the current head | F3 replaced with the lane-change red, and the old output retained under a `⛔ SUPERSEDED` marker naming WHY it was unreproducible |
| 5 | **P2** — the PR body still carried the round-1 inert falsifier and pre-fix counts | artifact §3 marks it `⛔ SUPERSEDED AND WRONG` | corrected on the PR (body while no record exists, comment once one does) |
| 6 | **P2** — leg 4's narrowing is honest about the floor, but its implied "nothing stronger is available" is broader than the measurement: a membership rule is checkable from this trace | reasoned from the `Door` fields; the reviewer reproduced F6 independently | §4 now states the membership rule, that it WOULD red on F6, and why it is declined (a ~30-shape raw-CYPHER pin would red on unrelated edits and be silenced by editing the pin) |
| 7 | **P2** — an exception from `_apply_and_journal` would surface as `UnboundLocalError` because `observed` was bound inside the `try` | code read | `observed: list[Door] = []` before the `try` |

Gates re-verified green at this head: `18 passed, 1 skipped` (whole file), `5 passed` (`-k D3`),
`ruff check .` clean, markdownlint 0 issues.
