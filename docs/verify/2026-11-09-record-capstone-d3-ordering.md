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
| Head measured | §9/§9.1: `30068eabb0` and the commit after it, each row stating its own head where they differ. §8/§8.1: `e15a9ead22` (the head that carries the predicate they describe). §6.1: `629283eef`. §6/§7: `a32edfb17f` and earlier, each stated where it differs. §0/§1: the ORIGINAL pre-fix run at `8d3e7f13f0`, retained and banner-marked as superseded |
| Record | `postgres:16`, throwaway container `t9567-cap-pg` on `127.0.0.1:33209`, repo record migrations applied by `_ensure_record_schema`; writer connected as `tortoise_record_app` (the RLS-scoped app role) |
| Graph | the SDK's **embedded** backend (`TortoiseSDK(<tmp>/graph.db)`) — the same shape the armed CI job (`test-record-postgres`) runs, so this measurement is reproducible without a graph server |
| Instrument | **committed**: `tests/test_9567_record_capstone.py` §"D3's ORDERING half" — `Door` / `Traced` / `_instrument` / the `traced` fixture and the four legs |
| Door wrapped | journal `tortoise.log.EventLog.append` · record `record_writer.write_live_record` · cache `FalkorProjection.apply` + every MUTATING `_GuardedGraph.query`, classified by the §17.1 guard's OWN classifier (`_statement_writes`) |
| "Declared lane" | read from the ContextVar the guard itself reads (`_CACHE_WRITE_AUTHORIZED`), so "declared" here means exactly what it means at the door |

## 0. The command

⛔ **EVERY NUMBER IN §0–§1 IS FROM THE ORIGINAL PRE-FIX RUN AT `8d3e7f13f0` — the commit that first adds
the ordering legs.** (`cbb9df838`, cited here previously, has **zero** `test_D3_order` legs; it carries
only the static `test_D3_no_write_path_bypasses_the_record`.) That head had three legs; this one has four,
so §0's `4 passed` is NOT reproducible here. §6.1 carries the
current head's outputs and they are the ones to quote; these are kept because deleting a measurement
does not make the record better.

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

## 2. The four legs, and what each one measures

| leg | assertion | observed |
|---|---|---|
| `test_D3_order_the_record_write_follows_the_journal_append` | every journal append gets a record write carrying the same `event_id`, **at a LATER position in the trace** (pairwise position, §10), and no record write precedes the first append | **green** — 14/14 paired, positions rising |
| `test_D3_order_a_journal_first_door_writes_journal_record_then_cache` | `_apply_and_journal` (the sanctioned chokepoint, `tools/record_wiring_check.py`'s production call site) writes journal → record → fold, and every cache statement in that window follows the record write | **green** — `['journal', 'record', 'fold', 'cache']` (four doors: the probe's `ObjectRegistered` carries no extra props, so `_persist_extra_props` emits no second statement, and `clear_embedding` is a no-op without an embedding — see §1's note) |
| `test_D3_order_no_cache_write_runs_ahead_of_its_append_SILENTLY` | every cache statement **still ahead of its own append** in an ahead fold's window declares a lane — fold-attached ones unconditionally, raw-handle ones only while nothing has journalled between the fold and the statement (§8.1) — and the `(event_type, lane)` set of ahead folds declaring a **journal-first** lane equals the tree's recorded deviation set | **green** — the `_create_entity` registration folds run ahead under `"create_entity"`, which IS a journal-first lane, and that is exactly the pinned set |
| `test_D3_order_no_cache_write_precedes_the_captures_first_journal_append_and_record` | a **FLOOR** on the criterion's second half: no mutating cache statement precedes the capture's FIRST journal append and FIRST **APPLIED** record write. It is **not** a per-write attribution — a mid-capture cache-only write passes, measured (§4, §6.2 F6) | **green** — no cache statement precedes the first append or the first APPLIED record |

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
  statement to the append it skipped — and a FOLD-ATTACHED mid-capture statement has an `in_fold`
  descriptor and is still unexamined, because leg 3 walks only ahead folds and leg 4 only the floor.
  D3's falsifier is therefore satisfied for the early shape only.

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

(`cache=29`, not the §1 run's 32 — and the difference is the ENVIRONMENT, not the head: `8d3e7f13f0`
ran on a box with the `embeddings` extra, where each of the three folds emits one extra
`MATCH … REMOVE n.embedding` statement (`tortoise/vector_write.py:192` — `clear_embedding` returns early
when the vector is `None`, which is this venv's state: `EmbeddingModel.get() -> None`). 3 folds × 1
statement = the 3-statement gap exactly. §2's leg-2 sequence differs for the same reason: four doors
here, five on an embeddings-armed box. Both counts are counted in the run that printed them, never
asserted against a constant.)

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
E   • LANE MOVED on a recorded event type: [('EventRecorded', 'create_entity_v2'),
    ('ObjectRegistered', 'create_entity_v2')]. The KNOWN DEVIATIONS block at
    `tortoise/sdk.py:4835` records this arm as apply-first under `create_entity`, and the
    pin no longer carries that lane. The deviation's lane was RENAMED: update the KNOWN
    DEVIATIONS block at `sdk.py:4835` and extend the pin.
```

⛔ **This output is the round-4 `LANE MOVED` message.** An earlier revision of this block showed
`NOT RECORDED anywhere`, which was the pre-round-4 advice for the same mutation — the artifact asserted
reproducibility of an output the head no longer produced. Recorded here so a reproducer who sees
`LANE MOVED` knows they ran the right mutation.

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

#### F9 — pre-write ONE event kind's record ahead of its own append → leg 1 (position half)

`_emit_event`, for `TurnsRecorded` only: `self._write_record_projection(event, seq=None)` before
`log.append(event)`, and the post-append write skipped. Identity still aligns (the k-th record still
answers the k-th append) and the global floor still holds (pair #1 is intact), which is exactly why
identity + a floor could not see it: **before §10's fix all four `D3_order` legs passed this mutation**
(`4 passed`). After it:

```console
E AssertionError: a record write preceded its own journal append — the record was written from
  something other than the journal's append (D3's cache-only shape, per event): journal positions
  [0, 4, 6, 11, 16, 22, 27, 33, 39, 41, 45, 50, 55, 57], record positions
  [1, 3, 7, 12, 17, 23, 28, 34, 40, 42, 46, 51, 56, 58]
1 failed, 3 passed, 15 deselected
```

The pair at journal position `4` and record position `3` is the inversion (a record write one door
EARLIER than the append it answers) — the direction D3 forbids, and the only one of the four legs'
falsifiers a `seq=None` pre-write can produce.

### 6.3 What the fixes do not change

D3 is still **not discharged**, and this PR still reorders no production write path. What changed is that
the instrument's four ordering assertions can now fail for the failure each one names. §3's F3 is retained above
with a `⛔ SUPERSEDED AND WRONG` marker rather than deleted: it recorded a real run whose *conclusion* was
mistaken, and a reader who trusted it would re-apply the inert comparison.

## 7. Review cycle 2 — six verified findings fixed, and ONE FALSE FINDING CORRECTED

A second fresh-context review, on the head this section's predecessor produced, raised seven findings.
Six reproduced and were fixed; the seventh did **not** reproduce and is recorded below as falsified —
recording it as a verified fix is exactly the defect `AGENTS.md` → *Finding Verification* names, because
a false correction changes nothing yet reads as an improvement. The two substantive reds are F7 and F8
in §6.2.

| # | finding | verified against | fix |
|---|---|---|---|
| 1 | **P1** — leg 3's statement window was filtered to `e.in_fold == fold.event_type`, so a **lane-less raw** cache write inside an ahead fold's window was never examined, and the "no cache-ahead write is silent" claim was measured only for fold-attached statements | reproduced: `CREATE (:SilentAhead …)` after `_create_entity`'s fold, before its append → `cache-with-no-declared-lane=9`, **5 passed** | window widened to every cache statement (it is already bounded by the fold index and its own append). Clean run still green; the injection now reds (§6.2 F8) |
| 2 | **P2** — `_ahead_population_advice` partitioned on event TYPE only, so a brand-new **lane** on a recorded event type was reported as "RECORDED, extend the pin" instead of a fresh violation | reproduced with the lane-change mutation | partitions on the `(event_type, lane)` **pair**; `create_entity` re-exercise is the only excused shape. Verified at the time: the lane change printed `NOT RECORDED anywhere`; from round 4 it prints `LANE MOVED on a recorded event type` instead (§6.2 F3), which is the more precise advice for a rename |
| 3 | **P2** — `_KNOWN_AHEAD_DEVIATIONS`'s comment named an `_apply_and_journal` reorder as a producible new pair, which this head cannot produce (that door is not on the capture path, and leg 2's probe is trimmed) | measured: reordering `_apply_and_journal` reds leg 2 only; leg 3 green | comment now names a CAPTURE door re-declaring its lane, matching leg 3's own docstring |
| 4 | **P2** — §6.2 F3 showed the `('ObjectRegistered','chokepoint')` red, which existed only at the superseded head (it was a product of the probe leak) | re-ran the same mutation at the current head | F3 replaced with the lane-change red, and the old output retained under a `⛔ SUPERSEDED` marker naming WHY it was unreproducible |
| 5 | **P2** — the PR body still carried the round-1 inert falsifier and pre-fix counts | artifact §3 marks it `⛔ SUPERSEDED AND WRONG` | corrected on the PR (body while no record exists, comment once one does) |
| 6 | **P2** — leg 4's narrowing is honest about the floor, but its implied "nothing stronger is available" is broader than the measurement: a membership rule is checkable from this trace | reasoned from the `Door` fields; the reviewer reproduced F6 independently | §4 now states the membership rule, that it WOULD red on F6, and why it is declined (a ~30-shape raw-CYPHER pin would red on unrelated edits and be silenced by editing the pin) |
| 7 | ⛔ **FALSIFIED — NOT APPLICABLE.** The claim was that an exception from `_apply_and_journal` would surface as `UnboundLocalError` because `observed` was bound inside the `try`. It does not: `observed = list(traced.entries[mark:])` is the LAST statement inside the `try`, and `doors = [e.door for e in observed]` sits AFTER the `try/finally`, so a raise propagates from the `finally` and the statement after it is never executed. Reproduced with a standalone copy of the shape: `raised: RuntimeError door raised`, no `UnboundLocalError`. | the shape, executed outside the suite | the `observed: list[Door] = []` initialisation is KEPT as defensive only — it fixes nothing, and this row is retained (rather than deleted) so the next reader cannot re-derive the finding and "fix" it again |

Gates re-verified green at this head: `18 passed, 1 skipped` (whole file), `5 passed` (`-k D3`),
`ruff check .` clean, markdownlint 0 issues.

## 8. Review cycle 3 — a latent false accusation, a contradictory message, and a self-corrected record

A third fresh-context review raised four. All four reproduced or were falsifiable by inspection, and all
four are fixed.

| # | finding | verified against | fix |
|---|---|---|---|
| 1 | **P2 (latent)** — leg 3's round-3 window widening took *every* cache entry in `[fold, its append)`, so a statement preceded by a **nested** journal-first door's append would be attributed to the outer ahead fold (`(EventRecorded, "chokepoint")`) and accused as a fresh violation — on code that is correct. Not reachable on today's tree (the three windows are `[8,11)`, `[19,22)`, `[36,39)`, each holding only that fold's own statements), but the round-3 widening removed the filter that made it unreachable | reproduced with a nested `_emit_event` + `_live_write_scope("chokepoint")` probe inside the window: leg 3 went red naming `("chokepoint", …)` as a violation | the window predicate is now *fold-attached* **or** *(raw-handle with nothing journalled between the fold and the statement)*. Measured: the nested probe leaves leg 3 GREEN (`1 passed`) and F8 still reds |
| 2 | **P2** — `_ahead_population_advice` printed two contradictory remedies for one change: F3's lane rename produced BOTH "NOT RECORDED … fix the door" and "the deviation was closed … re-grade D3" | the exact output of F3's mutation | a third branch: `LANE MOVED on a recorded event type` (rename → update the KNOWN DEVIATIONS block and extend the pin; or genuinely re-declared apply-first → re-grade D3), and the moved type is excluded from the `lost` line. Measured: F3 now prints LANE MOVED and neither of the contradictory lines |
| 3 | **P2** — §7 recorded the `UnboundLocalError` finding as *reproduced and fixed*; it is unreachable | ran the shape standalone: `raised: RuntimeError door raised`, no `UnboundLocalError` | §7 row 7 marked `⛔ FALSIFIED — NOT APPLICABLE`, with the reasoning and the standalone run; the initialisation is kept as defensive only, and §7's heading no longer claims seven verified fixes |
| 4 | **P2 (low)** — the artifact's provenance still pinned `Head measured cbb9df838` with §0/§1's superseded `4 passed` / `cache=32`, and the PR title said "three doors, three falsifiers" | re-ran the §0 command at this head: `5 passed`, `cache=29`; whole file `18 passed, 1 skipped` | the metadata row now names this head and says which sections it governs; §0/§1 carry an explicit `⛔ PRE-FIX RUN` banner; §6.1 is the current output of record. The PR title is retitled |

### 8.1 What cycle 3 changed about the instrument's coverage

Leg 3's population is now: every cache statement in an ahead fold's window that is **still ahead of its
own append** — fold-attached statements unconditionally, raw-handle ones only while nothing has journalled
between the fold and the statement. That is the precise predicate "the cache is ahead of the journal",
rather than "the cache is between two indices", and it is what makes F8 a real red without making a
nested door's correct journal-first write a false one.

Gates at this head: `5 passed (-k D3, cache=29)`, `18 passed, 1 skipped` (whole file), `ruff check .`
clean, markdownlint 0 issues.

## 9. Review cycles 4–5 — the record behind its own code, and two carried findings closed

Round 4 arrived as **two independent reviews** of the same head (`e15a9ead22`): one posted on the PR
(`IC_kwDOToroq88AAAABkng8HA`), one dispatched. Neither found a defect in the instrument's logic — both
re-verified leg 3's predicate against the live trace (ahead folds at `8, 19, 36`, windows `[8,11)`,
`[19,22)`, `[36,39)` holding only their own fold-attached statements), F8 red, and the nested-door case
green. Every finding was either the **record** drifting behind the code or a **carried claim** left open.
Full disposition, including the items that were NOT defects:

| # | finding | source | disposition |
|---|---|---|---|
| 1 | §6.2 F3 still showed `NOT RECORDED anywhere` while §6.2 claimed every red was reproducible "at this head" — the advice change made that mutation print `LANE MOVED` | dispatched | F3 re-captured with the `LANE MOVED` output + a ⛔ note naming the older wording, so a reproducer is not misled. §7 row 2 carries the same correction |
| 2 | the provenance row pinned §8/§8.1 to `a32edfb17f`, where their central claim (the new predicate) is false — §8 landed one commit later | dispatched | each section's head is now named individually |
| 3 | three places said "three legs" over a table of four; §2's leg-4 cell claimed per-write attribution that §4 and §6.2 F6 exist to deny | both | corrected to four; the cell now says FLOOR and points at §4/F6 |
| 4 | **leg 4's NAME still stated the per-write property** while its body asserts a floor | PR comment | **renamed** to `test_D3_order_no_cache_write_precedes_the_captures_first_journal_append_and_record` — the id is what CI output and any downstream quote carry, so fixing only the cell left the contradiction in the most-quoted place |
| 5 | **the door wrappers stayed installed through module teardown** (`mp.undo()` in the fixture's `finally`), so D4–D12 executed with `EventLog.append` / `write_live_record` / `FalkorProjection.apply` / `_GuardedGraph.query` wrapped | PR comment | `mp.undo()` moved **before** the `yield`; leg 2 installs its own instrumentation for its own probe window through the same `_instrument` helper. Re-verified: F2 and F7 still red with unchanged output |
| 6 | the PR body headline said "every falsifier observed RED" and named "the two" residues, while its own last row says **does NOT red** and §6.2 has exactly one | dispatched | "Seven of the eight observed RED; the eighth is the one disclosed non-red" |
| 7 | §6.1's wall-clock duration was read as a defect | PR comment | **NOT A DEFECT**: `19.60s` reproduces at `19.16s`; the cited `142.10s` was box load. Recorded rather than "fixed" |
| 8 | §6.1 explained the `32 → 29` census delta as "different heads and different captures" | dispatched (round 5) | **WRONG EXPLANATION, corrected**: the delta is the `embeddings` extra. Each of the three folds emits one extra `MATCH … REMOVE n.embedding` (`vector_write.py:192` returns early when the vector is `None`, this venv's state) — 3 folds × 1 = the 3-statement gap exactly. §2's leg-2 sequence likewise measured `['journal','record','fold','cache']` here, not the five doors recorded in §2 previously |
| 9 | §0/§1's provenance named `cbb9df838`, which has **zero** `test_D3_order` legs | dispatched (round 5) | repointed to `8d3e7f13f0`, the commit that introduces them |

### 9.1 Follow-ups fixed rather than deferred

- **`_ahead_population_advice`'s GONE branch was dead code.** It subtracted `pin_recorded_types` from
  `lost` unconditionally, which for this pin emptied `lost` on every call, so a genuinely-closed deviation
  would have printed no advice. It now subtracts only the types that actually MOVED, and the unreachable
  "or it really was re-declared apply-first" clause is gone from the `moved` text (an apply-first
  re-declaration leaves the observed population entirely, so it arrives in `lost`). All four branches
  executed: rename → LANE MOVED only; both pinned gone → GONE; a Subject arm → RECORDED-newly-exercised;
  an unrecorded new arm → NOT RECORDED.
- **A docstring indentation slip** in leg 3 made one paragraph start at column 0.

Gates at this head: `5 passed (-k D3, cache=29)`, `18 passed, 1 skipped`, `ruff` clean, markdownlint 0.

## 10. Review cycle 6 — leg 1 asserted IDENTITY, never ORDER

A fresh-context review at `2ca845f4437f` (PR comment `IC_kwDOToroq88AAAABkoP5uQ` / `6753089977`,
recorded `not-clean`, 1 finding) accepted the instrument — the census reproduces, the door choice and the
non-vacuity guards hold, the pinned deviation set is accurate rather than aspirational — and raised one
**P1**: leg 1's assertion was **identity alignment plus a global first-position floor**, which is not
the pairwise order its name and docstring claim.

| # | finding | verified against | fix |
|---|---|---|---|
| 1 | **P1** — `test_D3_order_the_record_write_follows_the_journal_append` asserts the k-th APPLIED record's `event_id` equals the k-th append's, then that the FIRST append precedes the FIRST record. A door that pre-writes **one event kind's** record ahead of its own append keeps both: identity pairing survives, and `first_journal < first_record` is satisfied by pair #1. The docstring claims it fails on *"a record write with no journal append behind it"*, and it did not | reproduced: `_emit_event`, `TurnsRecorded` only, `self._write_record_projection(event, seq=None)` before `log.append(event)` and the post-append write skipped → **`4 passed`** (`-k D3_order`), every leg green while a record write precedes the append it answers. Tree restored from a byte-exact copy (`git diff --exit-code` clean) | assert pairwise **position**: `journal_pos`/`applied_pos` from `traced.entries`, then `all(j < r for j, r in zip(journal_pos, applied_pos, strict=True))`. Re-measured with the mutation still applied: leg 1 **reds** (F9, §6.2); mutation reverted, clean run **`5 passed`** with the census unchanged |

**Why the three prior clean verdicts at this head did not contradict it:** they measured a **total**
inversion (the record write moved ahead of the journal for *every* event), which legs 1 and 2 both
detect. The gap is the **per-event** shape, which only a positional assertion sees. A `clean` verdict
and a `P1` on the same head are compatible when they measure different mutation classes — which is
why the mutation, not the verdict, is the evidence.

**This is an assertion added, never a weakened one.** Every existing assertion is kept (receipt `ok`,
counts equal, APPLIED count equal, `event_id` pairing, the global floor); the positional check is
strictly stronger and the clean-run census is byte-identical (`journal=14 record=14 fold=3 cache=29
cache-with-no-declared-lane=6`), so the leg it strengthens is not perturbed by it.

Gates at this head: `5 passed (-k D3, census unchanged)`, whole file `18 passed, 1 skipped` (the skip
is `TEST_RECORD_SCRATCH_URI`, which only the CI job's second database arms — unrelated to this
change), `ruff check` clean, worktree restored (no production file in the diff).
