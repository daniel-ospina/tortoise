---
title: "#12894 — what actually shipped: the landed allocator chain, verified against the ideal shape"
type: evidence
domain: platform
doc_status: final
created: 2026-11-16
subjects.team: epistemic-team
aboutSubjects: Tortoise record allocator, journal ordinal, hosted write path
aboutObjects: "#12894, #12959, #12904, #12897, #9190, #9318, #9025, #9307, #9252, tortoise/record_allocator.py, tortoise/record_writer.py, tortoise/record_projection.py, tortoise/log.py, docs/plans/2026-11-15-issue-12894-storage-shape.md"
---

# #12894 — what actually shipped

This is a **verification** document. It measures the three landed PRs (`#12959` the allocator,
`#12904` the collision guard, `#12897` the plan) against the owner ruling that grounds them and
against the plan's own sequence (`§A7.7`). Every claim carries `file:line` or a command. Where the
landed code contradicts the plan, the contradiction is stated as a contradiction — not softened, and
not fixed here.

Measured at `origin/main` `e61694255a` (worktree `.worktrees/12894-landed-verify`, branch
`verify/12894-landed`).

---

## 0. Question and method

The owner ruling (`#9190`, comment 6902871297) is the ground:

> the record allocates the ordinal and the journal record CARRIES it; replay READS the number instead
> of computing it

(quoted at `tortoise/record_projection.py:7504-7507` and `tortoise/record_allocator.py:3-6`).

So the question is not whether that shape is right. It is: **is that what now runs, and where does the
landed code part from the plan or from what the review rounds named?**

Method, in the order the brief prescribes: `record_allocator.py` end to end, then
`record_writer.py`'s live door, then `record_projection.py`'s `_journal_row` / `replay_plan` / guard /
`ALLOCATION_SQL`, then `tortoise/log.py::EventLog.append` — and then **outwards along the call graph**
(call sites, not PR bodies), because the decisive question turned out to be a call site, not a module.

What this lane did **not** do: it did not run a Postgres-backed suite (the one DB-free gate below was
run, `tools/record_wiring_check.py`, which is DB-free by design), it did not run the docs lint (the
invocation exited **2** on its own missing required inputs — `--markdownlint-output`,
`--markdownlint-expected-files`, `--lychee-output` — so it *never ran*; recorded in
`WORKTREE-REPORT.md` as suite 2, exit 2, and **not** a clean result), and it did not modify product code. It also
did not re-derive the discussion on `#12894`/`#12959` line by line — where a PR-body claim is quoted
below it is marked as a claim and the code is what settles it.

---

## V1 — the write path, in order, as it runs today

One hosted `create_point` (`tortoise/sdk.py:14859`), reached from `hosted_api.py:13489` → the
`_data_sdk` seam, and from `selfhost_api.py:291` on the self-host lane. Both funnel into the same SDK
method, so the ordering below is one ordering, not two.

Durable writes, in execution order:

| # | Write | Where | Precedes the commit point? |
|---|---|---|---|
| 1 | `:EpMeta.ep_version` **bump** (`MERGE (m:EpMeta) SET m.ep_version = coalesce(...) + 1`) | `sdk.py:16062` → `sdk.py:35986-35989` | yes — declared residual |
| 2 | `:Source` **adopt-on-touch** (`SET s.canonicalUrl`, `s.urlAliases`), only for a `:Source` whose `canonicalUrl IS NULL` | `sdk.py:16100-16101` → `source_identity.py:272-280` | yes — declared residual |
| 3 | `:GraphEventMeta.last_seq` **increment** + `:GraphEvent` **row** | `sdk.py:13975-13976` → `event_store.py:427` (`next_seq`), `event_store.py:777` (`append_event`) | yes — declared residual |
| 4 | **JSONL journal append** — `seq = log.append(event)` | `sdk.py:14151`, `log.py:1393` | ⛔ **THIS IS THE COMMIT POINT** |
| 5 | record tenant scope + role gate (`set_config('app.current_org_id', …)`) | `record_projection.py:7021-7024` | no (follows) |
| 6 | enumerated-replay refusal (**no-op** without a `record_cursor` row) | `record_projection.py:7654-7655`, `:7340` | no |
| 7 | the four ontology **layer rows** (record mutations) | `record_projection.py` `RECORD_MUTATIONS`, executed in `replay`'s loop `:7660-7671` | no |
| 8 | `journal_event` + the `#9318` collision guard | `record_projection.py:6377` (`_journal_row`) executing `:6343-6350`; guard at `:7703` | no |
| 9 | **commit** of the record's own transaction | `record_writer.py:2719` (`connection.commit()`) | no |
| 10 | byte meter (`_measure_written_bytes`) + the written-bytes charge | `record_writer.py:1732-1742`, `:2734` | no |
| 11 | the **cache write**: `CREATE (:Point …)` under `_live_write_scope("create_point")`, then `:Tag`/`:Source` edges | `sdk.py:16287-16304` | no — gated ON the commit point |
| 12 | counter allocation (`record_cursor.seq` increment) | `record_projection.py:6926-6945` — **not reachable on any production lane** (V2) | n/a |

### Which one is the commit point now

**The JSONL append still is** — `sdk.py:16216` states it as an invariant (`#7951 §17.1`, *"THE APPEND IS
THE COMMIT POINT"*), the cache write at `sdk.py:16287` is wrapped in `_live_write_scope`, and the §17.1
guard makes that declaration load-bearing rather than documentary (`sdk.py:16281-16288`). The record
write at step 5–10 runs **after** it and is deliberately fail-soft: `write_live_record` never raises
(`record_writer.py:2481-2483`), counts `record_write_failures`, and relies on `replay` to heal.

So the ruling moved the *ordinal's owner* in the design, but on the running path the ordering anchor is
still the file. Row 4 is what the cache write is gated on; row 8 is what *would* be if the allocator
were armed.

⚠️ Note what step 3 means for the "one ordinal" idea: the `:GraphEvent` store has its **own** allocator
(`GraphEventMeta.last_seq`, `event_store.py:427-433`), and it is bumped **before** the journal append.
That coordinate is `:GraphEvent.seq`, not `journal_event.seq`, and the ruling does not touch it
(`record_allocator.py:17-21`, plan `§A7.7` step 4 which *deletes* D-07 from the sequence). It is listed
here because it is a durable write that precedes the commit point, not because it shares a coordinate.

### Per lane

| Lane | Journal | Record armed? | Ordering |
|---|---|---|---|
| **hosted** | file on the volume (`TORTOISE_EVENT_LOG_BASE_DIR`) | **yes** — `TORTOISE_RECORD_DSN` staged as a Fly secret (`fly.toml:322-329`) | steps 1–11 as above; **no step 12** |
| **self-host** | its own data dir | only if the operator sets `TORTOISE_RECORD_DSN` | same code path (`selfhost_api.py:291`); unarmed ⇒ steps 5–10 do not run at all |
| **embedded / local SDK** | local dir or in-memory | no DSN by default ⇒ **NOT_ARMED** | `write_live_record` returns `None` before opening a connection (`record_writer.py:2682-2684`); only steps 1–4, 11 |
| **MCP stdio** | none (transport has no journal) | no | declared out of scope by the plan (`§A3.1`), unchanged |
| **M2 session capture** | `_InMemoryEventLog` | declined on purpose (`durable=False`) | step 4 is a scratch append; steps 5–10 **do not run** (`record_writer.py:2454-2484` region) |
| **CI** | — | unarmed unless a test arms it | `MEMORY.md:87` records the trap: setting `TEST_RECORD_DB_URI` without `TORTOISE_RECORD_DSN` measures an unarmed record |

`record_dsn` is presence-based (`record_writer.py:654-678`): unset/blank ⇒ `None` ⇒ NOT_ARMED ⇒ *zero
statements, nothing red* (`fly.toml:328-329` says the same in the deployment's own words).

---

## V2 — is the ordinal actually the record's?

**No. The live path does not allocate through `record_allocator`, and no production call site asks it
to.**

The proof is a call-site census, not a reading of the PR body.

1. `write_live_record` accepts `allocate: bool = False` (`record_writer.py:2446-2452`) and passes it
   through to `write_records(... allocate=allocate)` (`:2713-2716`).
2. `RecordWriter.write` forwards it into `record_projection.replay(..., allocate=allocate)`
   (`record_writer.py:1646-1650`), and `replay` builds the allocation statement only on that arm
   (`record_projection.py:7630-7640`, `:7760-7785`).
3. **Every production call site passes `seq=`, and none passes `allocate=`**:
   - `tortoise/sdk.py:14264-14265` — `return record_writer.write_live_record(record, durable=durable, seq=seq, tenant=self._record_tenant)`
   - `tortoise/api.py:2172-2173` — `record_writer.write_live_record(event, durable=…, seq=seq)`
   - `tortoise/mining.py:1006-1007` — `record_writer.write_live_record(record, durable=…, seq=seq)`
4. Consequently `allocate` is `False` on every live lane, and the live path takes the **enumerated**
   arm: the position row is written from the number `EventLog.append` returned (`sdk.py:14151` →
   `:14203/:14205`), i.e. *the journal* still allocates the ordinal on the running path.
5. `allocate=True` is exercised by **tests only** — `tests/test_12894_record_cursor_live.py` (23
   occurrences), `tests/test_12894_record_cursor.py` (10), `tests/test_9078_record_written_by_production_path.py` (3).
6. There is no environment flag that arms it: `rg -n 'allocate' tortoise/sdk.py tortoise/hosted_api.py
   tortoise/api.py tortoise/mining.py tortoise/selfhost_api.py` returns **no** `allocate` plumbing, and
   no env read names the allocator.
7. Nothing in production ever calls `record_allocator.seed/arm/halt/reopen`: outside `tests/`, the only
   **imports** of the module are `record_projection.py:7630` (the lazy import inside `replay`) and
   `record_writer.py:2402` (`_allocator_refusal_cause` reading `record_allocator.metrics()`) — every
   other mention is prose in a docstring or a comment (`record_projection.py:6362,6401,6874,6876,6883,6889,6900`;
   `record_writer.py:1586,2395,2665,2787`), and no call to any of the four entry points exists.
   `provision_graph_record` — the creation hook of `§A7.8` item 8 — **does not exist in the tree**
   (`rg -n 'provision_graph_record' --glob '!tests/**'` returns plan-document hits only).

### Is the byte-meter P0 fixed?

**Yes — mechanically, on the arm that nothing calls.** The review's P0 was that the watermark and the
byte meter used the caller's `first_seq`, so an allocated number never reached them and the meter
matched zero rows and charged 0. The landed code reads the *report* on the allocated arm:

- `record_writer.py:1722-1724` — `watermark_first = report.first_seq if allocate else first_seq`,
  `watermark_last = report.last_seq if allocate else first_seq + len(records) - 1`;
- `record_writer.py:1732-1742` — `_measure_written_bytes(..., first_seq=watermark_first, ...)`;
- `record_projection.py:6984-6991` — `ReplayReport.first_seq`/`last_seq` are documented as *"the
  positions this call ALLOCATED, and are 0 for a replay that did not allocate"*;
- `record_writer.py:1620-1626` refuses `allocate=True` with `position=False` before opening a
  connection, and `record_writer.py:2691-2716` refuses to forward a caller `first_seq` on the
  allocated arm (the plan rejects `allocate=True` with `start_seq != 1`).

So the fix is real and pinned by
`tests/test_12894_record_cursor_live.py::test_the_allocated_number_reaches_the_watermark_and_the_byte_meter`
(`record_writer.py:1717-1721` cites it). **It is inert in production for the same reason as item 3
above: no caller enters the arm.**

---

## V3 — the seed / cutover for EXISTING graphs

**There is a seed. It is unreachable.** There is no verb, no RPC, no migration that seeds, and no
runbook step. `record_allocator.seed()` (`:361-463`) is called from tests only (item 7 of V2). The
migration `supabase/migrations/20261116000001_record_cursor_allocator.sql` creates the table
(`:56-72`) and its grants/policies, and **inserts no row**. So on `main` today every graph is
*uncut*: no `record_cursor` row ⇒ `_refuse_an_enumerated_replay` returns immediately
(`record_projection.py:7367-7368`, *"the refusal fires ONLY when the graph has a `record_cursor` ROW"*)
and the live path enumerates as it always did.

### The zero-row case (the round-2 P0, plan case 2a)

A graph with a large journal and **zero** `journal_event` rows is the population the seed exists for
(plan `§A7.5.5`, *"B1's MOTIVATING POPULATION"*). The landed statement handles it: `row_max = 0`,
`floor = N`, and

```sql
 WHERE observed.row_max <= observed.enumerated_floor
...
SELECT %s, %s, GREATEST(observed.row_max, observed.enumerated_floor)
```

(`record_allocator.py:221-237`) writes `seq = GREATEST(0, N) = N`, and the next allocation takes
`N + 1`. `SeedResult.healed_tail` reports `floor > row_max` (`:455-460`) — *"a `None`-position tail …
REPORTED, never repaired"* (`:161-179`). That is plan case 2a, honoured, and the plan's `seq >= 0`
requirement is honoured by the migration (`CHECK (seq >= 0)`, `:61` — see V6-3).

### ⛔ Divergence: the landed seed LIFTS plan case 2b, which the plan REFUSES

The plan splits the population (`§A7.5.5`, the `(1b)` block) and only admits two of the three
lift-shaped cases:

> `$record_floor =  0` (and `$3 > 0`) **CASE 2a — NOTHING IS CARRIED** … → **LIFT to $3.**
> `0 < floor <  $3` **CASE 2b — NUMBERS ARE CARRIED AND THE TWO READINGS ARE INDISTINGUISHABLE** — a
> D-25 tail, or a journal replaced by a LONGER one. … **DEFERRED ⟹ REFUSE.**

The landed guard is `row_max <= enumerated_floor`, which **admits 2b** and lifts it to the enumerated
floor. It reports `healed_tail = True` and does not refuse. So the exact population the plan deferred —
*numbers carried, readings indistinguishable, journal identity unavailable* — is the one the landed
seed accepts.

Two things make this worth stating precisely rather than as a nit:

- the module's own label is `reason="divergence"` for `row_max > floor` (`:418-429`), which is plan
  **case 1**, not plan case 2 — so *"round-2 case 2"* in `:127-130` and `:375-380` names a different
  case than the plan's case 2 does;
- lifting 2b is not obviously wrong *once the `#9318` guard exists* (a longer replacement journal's old
  rows collide loudly at replay rather than being absorbed). But that is a **new argument the landed
  code makes implicitly**, and the plan's refusal was written before the guard. Nothing in the tree
  records that the decision changed — `record_allocator.py:3-21` re-states the ruling and says nothing
  about 2b.

**This is the honest status: the zero-row P0 is closed correctly; the 2b deferral is silently
overturned, and the overturn is not recorded where a lane reading the sequence would find it.**

---

## V4 — what still silently absorbs or loses, and whether it is loud

### 4.1 `ON CONFLICT … DO NOTHING` on `journal_event`

It is still there, **once**, on the replay insert:

```sql
INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, payload, recorded_at)
VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now()))
ON CONFLICT (org_id, graph_id, seq) DO NOTHING
```

(`record_projection.py:6343-6350`). It is not silent any more, but it is not free either:

- **the allocated insert has none** (`ALLOCATION_SQL`, `:6926-6945`, with the reason stated at
  `:6912-6917`): a duplicate there is `23505`, classified as `PositionCollision` and raised
  (`record_allocator.py:342-356`). Correct — and unreachable in production (V2).
- **the live path still uses the absorbing insert**, because `position=True` with `allocate=False` goes
  through `_journal_row`.
- **the guard that reads the absorption runs on one call site** — `replay`'s statement loop,
  `record_projection.py:7703` — so it covers the live door, a full replay, the reconciler and any
  backfill tool, and it skips the allocation statement by prefix (`:7659`, `:7703`, with the
  `42883 operator does not exist: text = text[]` incident that motivated the `startswith` recorded at
  `:7690-7700`).

**Two arms are still silent by design**, and the code is unusually honest about both:

| Arm | Loud? | Where |
|---|---|---|
| genuine replay (same type, same payload, same capture) | silent — that is idempotence | `:6718-6723` (the arm), `:6812-6813` (the silent return), `:6847` |
| same type **and** same capture, payload differs → read as *derivation drift* | **silent** — admitted as a *reading*, not evidence | `:6820-6846`, `:6847` |
| anything else → `JournalPositionCollision` | loud: named refusal `journal_collision`, counter, WARNING | `:6858-6872`, `record_writer.py:2747-2771` |
| `ts` is `NULL` and the payload differs | loud (`capture_state="unknown"`) | `:6849-6853` |

The drift arm admits a shape the class docstring itself calls a *"mirror residual"*: *"TWO DIFFERENT
RECORDS sharing an instant land here and are absorbed — with `_identity_evidence` never consulted, even
where it would have answered `disagrees`"* (`:6829-6846`), pinned as admitted by
`tests/test_9318_journal_collision.py::test_a_same_capture_instant_pair_is_ADMITTED_not_detected`.
`#12965` (the head of `main`) is the commit that closed the two P0s in this guard, so the pair
above is the post-`#12965` state.

### 4.2 `TORTOISE_JOURNAL_SEGMENTS` — the round-1 live trigger

Still **OFF**, and its writers are the only thing it gates (`journal_segments.py:22-30`, `:127`,
`:189-195`). `seal_active` (`journal_segments.py:1132`) renames the active segment aside and starts a
fresh `events.jsonl`, so the *file-local* index restarts at 1 while the segment-spanning reader keeps
counting.

Under the allocator this class **narrows but does not vanish** (plan `§A7.6`/D-28): the allocator never
recounts, so it survives a seal; the *seed* and the *replay floor* do not. On today's running path —
enumeration, because V2 — a seal after a cutover is exactly the D-28 live trigger: the next append
returns 1 again and collides with rows 1..N. That collision is now **loud** (guard, §4.1) unless the two
records share `event_type` and `recorded_at`, and the journal's segment manifest is *designed* to make
the reader count across segments (which is why the plan calls the trigger "one flip from live" rather
than "broken").

### 4.3 The journal MUTATORS

| Mutator | What it does | Under the allocator | Today (enumeration) |
|---|---|---|---|
| restore-replace, `backup.py:638` (`shutil.copy2(events_file, events_path)`) | replaces the journal file | allocator unaffected; **seed** and **replay floor** are not — a shorter restored journal makes `row_max > floor`, which now **refuses** (`SeedRefused(reason="divergence")`, `record_allocator.py:418-429`) — loud | replay enumerates the restored journal against surviving rows; collisions loud via the guard, silent on the same-capture arm |
| `seal_active`, `journal_segments.py:1132-1141` | renames aside, fresh empty file | as §4.2 | as §4.2 |
| truncate + regrow in place, `log.py:2215-2224` | same inode, larger size, different bytes below the watermark; `st_dev`/`st_ino`/size/`mtime_ns` cannot detect it | the allocator does not care; the **seed** cannot detect it either — the recount *is* the answer it uses | undetectable by any cheap identity check; the guard catches content differences at the same position, which is the only remaining net |

**Verdict for V4:** *nothing in the list is unreachable, and nothing in it is silent in the way it was
before `#12904`/`#12965`* — with one deliberate exception (the same-capture drift arm, §4.1) and one
class that no guard can see (in-place truncate + regrow, whose detection would need journal identity in
the key — `#9318`).

### 4.4 A write that happens on the seam and is not journaled at all: the byte meter's schema DDL

`write_live_record` calls `_record_written_bytes` after commit (`record_writer.py:2734`, after the commit at `:2719`),
which calls `metering.record_postgres_bytes_written` (`:1838-1840`) → `_metering_anchor`
(`metering.py:285`) → in **non-Supabase** control-plane mode `_reg_sdk()._get_registry()`
(`metering.py:303`). `_get_registry()`'s default is `create=True`, and its own docstring says the
mint is caused by **`_ensure_registry_indexes` issuing a `CREATE INDEX` per label** — *"the first DDL on
a name is what brings a FalkorDB graph into existence"* (`sdk.py:13411-13427`).

So on a graph-registry lane the byte meter can issue DDL from the write seam. That is a mutating write
with **no journal append and no record row behind it** — outside the ordinal entirely, and outside
§17.1's ordering. It is **not fixed on `main`**: `no_schema_ddl` appears **0 times** in
`tortoise/metering.py` and `0 times` in `tortoise/record_writer.py` (measured).

Provenance, stated because the fix exists only in an archived lane: the cap-archived ref
`refs/wip/issue-12894-allocator-spike` (`703613fa0c` — **not** its `feat-` sibling `ec0bc91f6d`, which
does not carry it: measured with `git merge-base --is-ancestor e2a9eadb87 <ref>`) carries commit
`e2a9eadb87`, whose message records this as a
measured P1 (the D3 legs of `tests/test_9567_record_capstone.py` measuring the meter's `_ensure_indexes()`
DDL as *"mutating cache statements with no journal append and no APPLIED record behind them"*), fixed
there by `_get_registry(no_schema_ddl=True)`.

Against that, a **comment** on `#12959` (id `6906584678`) records the opposite attribution for the same
red legs — *"reproduced identically on a pristine `origin/main` worktree"*, evidence on `#11477`. It is
a comment and **not the PR body**, which contains neither "pristine" nor "11477" (measured).
**Both statements are on the record and they are not obviously compatible.** What this lane verified is
the *code shape* on `main`: the unguarded call is there. The measurement that it fires belongs to
`#11477`.

### 4.5 The guard also refuses the LIVE write, not only a replay (`#12986`)

`_refuse_an_enumerated_replay` is called from `replay` whenever `position and not allocate`
(`record_projection.py:7654-7655`) — and the **live** door takes exactly that arm today (V2). So on a
graph whose cursor row is `seeded` or `halted`, every live write is refused as an *enumerated replay*
while `write_live_record` passes the carried `seq` with `position=True`. Filed during `#12959`'s review
as `#12986`; it is the live path's own consequence of the guard being keyed on the row rather than on
the caller, and it disappears once the live door allocates (V5 item 4).

---

## V5 — what remains for the ideal shape

Numbered smallest-safe-first, each with its blocking evidence and whether `#12897` already specifies
it. `§A7.7` step numbers are the plan's.

1. **The wiring gate must be re-derived, not widened** (`§A7.7` M12 — *specified*).
   `tools/record_wiring_check.py` accepts **either** the journal's ordinal **or** the allocator
   (`tools/record_wiring_check.py:20-37`, `:443-500`), so it exits 0 (`measured`) over a tree where
   nothing passes `allocate=`. M12 said the two checks *"assert exactly the rule the change inverts"*
   and must be re-derived *at 6.1*; instead the old rule was **kept as an accepted alternative**. This
   is the instrument that should have made V2 visible, and it is the cheapest remaining fix.
2. **Seed reachability** (`§A7.7` 6.2 — *specified, unbuilt*). `seed()` exists, is correct for the
   zero-row case, and nothing calls it. Blocking evidence: no verb/RPC/runbook; `rg` over non-test code
   returns nothing. Without it the cutover is a code path with no operator door.
3. **The creation hook `provision_graph_record`** (`§A7.8` item 8 / `§A7.7` 6.6 — *specified, absent*).
   Not in the tree. Without it, even a completed cutover is a snapshot: every graph created afterwards
   is born uncut and keeps the enumerating allocator *forever* — the plan's own words.
4. **The live writer must actually allocate** (`§A7.7` 6.3 — *specified, not done*). This is V2: the
   three live doors must pass `allocate=True` (and then `first_seq=1`/`position=True`), which requires
   items 2 and 3 first or every live write is refused (`CursorNotActive`).
5. **Reader-first window + journal identity in the key** (`§A7.7` 6.1 — *specified, partially done*).
   The refusal half exists (`_refuse_an_enumerated_replay`, `record_projection.py:7340`). Two things are
   missing: (a) the journal *export line* does not carry a `seq` field (no writer emits one — `rg` finds
   no `"seq"` key in the emitted envelope; `last_exported` exists **only in the plan document**, at
   `docs/plans/2026-11-15-issue-12894-storage-shape.md:1256` and the six lines that follow, and in no
   Python module); (b)
   `§A7.8` item 6's precondition — `event_id` in `journal_event` — is not done:
   `CREATE TABLE … journal_event (… PRIMARY KEY (org_id, graph_id, seq))` with no identity column
   (`supabase/migrations/20261012000001_record_event_stream.sql:81-90`). The plan says that widening
   needs **its own decision on its own issue** and is *"a PRECONDITION of this sub-step, not of 6.3"*.
6. **`#9318` — journal identity in the key** (*specified, open*). Until it lands, the guard must stay,
   and the guard's own docstring says leaving it past the key change *"converts legitimate old-journal
   replays into refusals"* (`record_projection.py:6752-6753`). `#9318` is open because its acceptance
   needs identity in the key; `#12904` only made the conflict loud.
7. **The JSONL becomes an EXPORT rather than the commit point** (`§A7.7` 6.4/6.5, `§A7.5.8` —
   *specified, unbuilt*). Today `sdk.py:16215-16217` still declares the append the commit point, and the
   cache write is gated on it (`_live_write_scope`, `sdk.py:16287`). The plan's export must be
   **reproducible from the record** and *"today it is not"* (`§A7.5.8`).
   **On the review's "the drainer's hole premise was impossible":** that finding was resolved **in the
   plan**, not in the landed design — `§A7.5.6` records the remedy explicitly: an earlier row of the
   drainer table claimed a fourth case, and *"that row was **unreachable**, because on a halted graph
   the WRITER appends the line **and advances `last_exported` with it** … A halted rolled-back write is
   a hole in the RECORD, healed by a later replay's enumeration (§A7.5.10 row 4), not a hole in the
   export"* (`docs/plans/2026-11-15-issue-12894-storage-shape.md:1336`). There is no drainer in the code
   to carry the defect (`last_exported` appears only in the plan, never in a Python module), so nothing
   merged contradicts the resolution — the step simply remains planned. `#12959`'s own body makes the same point under a heading it
   writes as *"Not built, and why (a measurement, not a drainer)"*, whose entry says the export
   drainer's premise *"is forbidden by the allocator's own lock"*.
8. **M2 session capture off `durable=False`** (`§A7.7` 6.4 / `§A7.8` — *specified*). The decline exists
   because the lane has no position to hand the record; the flip removes the reason. Still declined at
   `record_writer.py:2454-2484` (`tortoise/sdk.py:14262-14265` for the SDK half).
9. **`#9025`'s decision boundary** (*owner's, not ours*). `§A7.7` step 7 and `§B7` scope it: the
   decision covers the **file** and only the file. A bound on `journal_event` is a *different* decision
   (D-27: it is a primary status store — the only carrier of `Object.status`/`Subject.status`), and the
   ruling that grounds this lane forbids it. Nothing in the landed code bounds `journal_event`
   (no retention/delete on that table — `record_allocator.py:17-21` states the prohibition, and the
   app role holds `journal_event` **SELECT+INSERT only**: `GRANT SELECT, INSERT ON public.journal_event
   TO tortoise_record_app` (`supabase/migrations/20261025000001_record_tenancy_role_and_guc.sql:190`),
   which `20261116000001:42-44` cites as the reason `record_cursor` withholds DELETE).
10. **`§A7.7` step 1 — D-01's counts reconciled** (*specified, not done here*). This lane did not
    re-derive the 387/211/32 vs 442/38/288 vs 458/50 population.

---

## V6 — anything merged that should not have been

The chain had four review rounds. Each named defect, and where it stands now:

| # | Defect named in review | Status | Evidence |
|---|---|---|---|
| 1 | **positional `params[:5]` unpack** in `_refuse_a_journal_collision` — the guard unpacked five params for a six-bind insert, so `journal_id` would have been read as `seq` etc. | **FIXED** | the 6-bind unpack `org_id, graph_id, seq, event_type, payload, ts = statement.params` at `record_projection.py:6796`, guarded by an arity/identity **bail before any unpacking** at `:6773-6794` (`_JOURNAL_POSITION_PARAM_COUNT = 6`, `:6354`), and the shape test at `:6339-6350` |
| 2 | **`recorded_at` discriminator, both directions** — (a) an unguarded arm reported every replay of a `ts`-less record as a collision; (b) a content match at a *different* capture was silently absorbed | **FIXED** | (a) `WHERE … (%s::timestamptz IS NOT NULL AND recorded_at IS DISTINCT FROM %s::timestamptz)` at `:6455-6458` with the false-red explained at `:6445-6454`; (b) `same_capture` is computed by Postgres in `_JOURNAL_CONFLICT_CONTENT_SQL` (`:6437-6460`) and only `same_type and same_capture and ts is not None` returns silently (`:6847`); everything else raises `JournalPositionCollision` (`:6858-6872`) |
| 3 | **`23514` on an empty-graph seed** — a `CHECK (seq > 0)` on the cursor made the *emptiest* graph's own seed fail | **FIXED** | `seq bigint NOT NULL DEFAULT 0 CHECK (seq >= 0)` at `supabase/migrations/20261116000001_record_cursor_allocator.sql:61`, with the reasoning at `:24-32`; `journal_event` keeps `seq > 0` (`20261012000001:84`) because a *position* is always ≥ 1 |
| 4 | **`42501` from `FORCE ROW LEVEL SECURITY` without the GUC** | **FIXED / not applicable as written** | the landed migration uses `ENABLE ROW LEVEL SECURITY` (`20261116000001:78`), **not** `FORCE` — so an owner-role connection is not subjected to the policy by this table. The seed sets *and verifies* the tenant GUC itself (`record_allocator.py:396-397`, `:637-650`), and `record_projection.scope_connection` does the same plus the role gate (`record_projection.py:7021-7024`). The refusal-vs-divergence distinction is pinned by `tests/test_12894_record_cursor_live.py::test_a_seed_without_the_guc_is_a_refusal_not_a_divergence` (`record_allocator.py:383-394`). ⚠️ The plan text still says `FORCE` (`§A7.5.5`, *"FORCE ROW LEVEL SECURITY on `record_cursor`"*), so plan and migration disagree — the *migration* is what runs, and it is the more forgiving of the two |
| 5 | **the placeholder converter** — *"a statement mixing a literal `$1` with `%s` is refused by the converter"* | ⛔ **NOT FIXED — the claim is false, and the test that pins it passes for the wrong reason** | measured: `to_dollar_placeholders` (`record_projection.py:7806-7824`) refuses only on **arity** (`len(parts)-1 != len(params)`). `Statement("SELECT $1, %s", ("a",))` → `SELECT $1, $1`; `Statement("SELECT $1, %s, %s", ("a","b"))` → `SELECT $1, $1, $2`, both silently. The comment asserting the refusal is `record_allocator.py:199-202`; the test is `tests/test_12894_record_cursor.py:294-319`, and its case `Statement("SELECT $1, %s", ("a","b"))` raises **because there are two params for one placeholder**, not because a literal `$n` was detected — delete the literal and the same case still raises. No *landed* statement mixes the two (checked: the only `$<n>` occurrences in `record_projection.py` are inside comments/docstrings at `:266` and `:7570`), so this is a **false-passing gate**, not a live data defect — the shape it cannot see is one a future edit can introduce |
| 6 | **`record_cursor` ships `ON DELETE CASCADE`** — a *recorded plan decision*, not a review finding, and the plan forbids it by name | ⛔ **MERGED CONTRARY TO THE PLAN** (already tracked as `#12987`) | migration: `FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs (org_id, id) **ON DELETE CASCADE**` (`supabase/migrations/20261116000001_record_cursor_allocator.sql:70`). Plan `§A7.8` item 1 (`docs/plans/2026-11-15-issue-12894-storage-shape.md:1840`): *"⛔ **and NO `REFERENCES public.graphs … ON DELETE CASCADE`, deliberately, unlike every sibling record table** … `purge_org_record` deletes `public.organizations` and relies on that cascade, so a cascade FK here would **delete the counter with the purge** and let a re-armed graph restart at 1 — the exact outcome item 7 rules against"*. ⛔ **And the migration's own header (`:46-48`) ENDORSES the cascade** — *"DELETE stays withheld because the counter is never erased per-row; it goes with the graph via the FK's ON DELETE CASCADE, which is the tenant-erasure path"* — which is a direct contradiction of the plan's reasoning, not an oversight. So the finding is sharper than "unrecorded DDL": **two recorded reasonings disagree about the same constraint**, the plan's (a cascade lets a purge delete the counter, so a re-armed graph restarts at 1 — item 7 rules against it) and the migration's (the cascade *is* the tenant-erasure path, so withholding DELETE per-row is deliberate). `#12987`'s two options turn on exactly that question |

### One more thing that should not have been merged, in the sense that it reports success over a gap

`tools/record_wiring_check.py` passing (exit 0) is **correct by its own contract** and still misleading
about this chain: its contract admits two honest sources (`:20-37`), so the pre-`#12894` shape is a pass.
`§A7.7` M12 required it to be re-derived at 6.1. It was not. This is V5 item 1 and it is the reason V2
had to be established by a call-site census rather than by the repo's own instrument.

---

## 7. The verdict in one table

| Part of the ideal shape | State |
|---|---|
| the record allocates the ordinal (`record_cursor`, row-locked, transactional, allocation order = commit order) | **BUILT** — `record_allocator.py` (663 lines), `ALLOCATION_SQL` in `record_projection.py:6926`, two migrations |
| the journal record CARRIES the ordinal | **BUILT on the class, not on the path** — the allocated number reaches `RecordWrite.last_seq` and the byte meter (`record_writer.py:1722-1742`); **no live caller enters the arm** |
| replay READS the number instead of computing it | **BUILT for the refusal half** — an enumerated replay is REFUSED for a graph with a cursor row (`record_projection.py:7340-7510`); there is no carried-`seq` *read* yet, because nothing writes a `seq` onto the export line |
| the duplicate position is a defect, not an absorption | **BUILT — loud for a DIFFERENT record, and still silent for one admitted pair**: `23505` → `PositionCollision` on the allocated insert; replay's `DO NOTHING` is interrogated by the `#9318` guard, which returns silently when `event_type` **and** `recorded_at` agree (`record_projection.py:6847-6848`) — so two different records sharing a capture instant are still absorbed (§4.1) |
| the seed / cutover for existing graphs | **BUILT, UNREACHABLE** (no verb, no RPC, no runbook); zero-row case correct; **2b lifted contrary to plan** |
| the live writer allocates (§A7.7 6.3) | **NOT DONE** |
| the creation hook (§A7.7 6.6) | **ABSENT** |
| the JSONL becomes an export (§A7.7 6.4/6.5) | **NOT DONE** — the append is still the commit point |
| `#9318` journal identity in the key | **OPEN**; the guard is a stop-gap its own docstring says must be deleted with the key |
| `record_cursor`'s constraint shape | **CONTRADICTED** — ships `ON DELETE CASCADE`, which plan `§A7.8` item 1 forbids deliberately (`#12987`) |
| `#9025` bound | **unchanged, owner's** — and `journal_event` is correctly excluded |

**Plainly:** the *mechanism* landed and is well built — transactional counter, one statement for
allocate+insert, an isolation guard, an honest refusal vocabulary, and a byte meter that now reads the
allocated block. The *cutover* did not land, and nothing in production can reach the mechanism. The
three things that would make the ruling true of the running system are V5 items 2, 3 and 4 — and item 1
(the wiring gate) is what should have made that visible.

---

## 8. The WIP refs — what they carry that `main` does not

The dispatch brief asked for this claim to be checked rather than trusted, so:

`git for-each-ref refs/wip/ | grep 12894` returns two cap-archives, both labelled *"not for merge"*:

```
ec0bc91f6d  refs/wip/feat-12894-allocator-spike   (archived 2026-11-16T06:29:21Z)
703613fa0c  refs/wip/issue-12894-allocator-spike  (archived 2026-11-16T10:08:08Z)
```

Relative to the merged `#12959` head (`acc9cbc035`), **only the `issue-` ref** carries a substantive
commit — `e2a9eadb87` (`acc9cbc035..refs/wip/issue-12894-allocator-spike` = `703613fa0c`, `7d4e802fdf`,
`e2a9eadb87`, `5351f89298`); `acc9cbc035..refs/wip/feat-12894-allocator-spike` holds the `wip:` archive
commit only. The substantive commit's two halves are **not** in the same state:

- **half 1 (the `seeded` exemption in the enumerated-replay guard) was REVERSED on `main`** by
  `f9f3e7e8b4` (*"the `seeded` exemption is WRONG — `reopen` keeps `seq`, so the row is the fact and the
  mode is only the remedy"*), and the reasoning is quoted in the landed comment block at
  `record_projection.py:7428-7441`. So that half is **superseded, not missing** — the WIP's arm would
  re-open the second-position defect its own review measured.
- **half 2 (`_get_registry(no_schema_ddl=True)` on the byte-meter seam) is NOT on `main`** — measured:
  `no_schema_ddl` occurs 0 times in `tortoise/metering.py` and 0 times in `tortoise/record_writer.py`,
  and `metering.py:303` still calls `_get_registry()` unguarded. See §4.4 for what that means and for
  the unreconciled attribution of the `test_9567` D3 red.

⚠️ `git diff --stat origin/main <wip-ref>` is **not a usable reading here** (a three-dot diff re-inverts
the whole squash-merged PR; a two-dot diff shows reverts of every `main` commit since the branch base).
The honest comparison is `git log refs/pull/12959/head..<wip-ref>` plus a path-scoped two-dot diff.

---

## 9. Commands used (verbatim, with counts)

**Which invocations are registered, and which are not.** Three suite runs went through the lane's
report tool (`WORKTREE-REPORT.md`, "Suites run"); every other measurement in this document is a
read-only inspection (`rg`, `sed`, `grep`, `git log`/`git merge-base`/`git for-each-ref`,
`gh issue|pr view`, and two `uv run python -c` evaluations), quoted inline at the claim it supports and
**not** in the report tool's register. The two `python` evaluations are reproduced verbatim in §6 (V6-5)
and §9 below so the negative findings are re-runnable rather than asserted.

**One recorded suite did not run and is reported as red rather than omitted:** the docs-lint invocation exited
`2` because the harness needs three outputs this lane did not produce, so the document was never
linted by that tool — a gap in coverage, stated here rather than left to `WORKTREE-REPORT.md`:

```bash
uv run python tools/worktree_report.py init --issue 12894
uv run python tools/gh_write_preflight.py --repo .
uv run python tools/worktree_report.py run --cmd 'uv run python tools/record_wiring_check.py'   # exit 0
uv run python tools/worktree_report.py run --cmd 'uv run python tools/docs_lint_baseline.py check --baseline config/docs-lint-baseline.json'   # exit 2, NEVER RAN
uv run python tools/worktree_report.py note --not-done '<the docs-lint non-run, verbatim>'
uv run python tools/worktree_report.py check        # exit 3 — VALID EVIDENCE, 1 FAILING SUITE
```

The two evaluations the negative findings rest on, verbatim and re-runnable:

```bash
# §6 / V6-5 — the converter does not refuse a literal $n mixed with %s
uv run python -c "
import sys; sys.path.insert(0,'.')
from tortoise.record_projection import Statement, to_dollar_placeholders, RecordProjectionError
for s in [Statement('SELECT \$1, %s', ('a',)),
          Statement('SELECT \$1, %s, %s', ('a','b')),
          Statement('SELECT \$1, %s', ('a','b'))]:
    try: print(s.sql, '->', to_dollar_placeholders(s))
    except RecordProjectionError as e: print(s.sql, '-> REFUSED:', e)
"
# => 'SELECT $1, %s' with one param  -> ('SELECT $1, $1', ['a'])      NOT refused
# => 'SELECT $1, %s, %s' with two    -> ('SELECT $1, $1, $2', [...])  NOT refused
# => 'SELECT $1, %s' with TWO params -> REFUSED, on arity ('1 placeholders for 2 parameters')

# V6-5 support — no landed statement mixes a literal $n with %s
# ([$] rather than \$ so the shell does not eat the escape)
uv run python -c "
import re; src=open('tortoise/record_projection.py').read()
for m in re.finditer('[\$][0-9]+', src):
    print(src[:m.start()].count(chr(10))+1, m.group(0))
"
# => 266 $1 / 7570 $1 — both inside comments/docstrings, no live statement

# §8 — which WIP ref carries the byte-meter fix
git merge-base --is-ancestor e2a9eadb87 ec0bc91f6d || echo 'not in feat-ref'
git merge-base --is-ancestor e2a9eadb87 703613fa0c && echo 'in issue-ref'
git grep -c no_schema_ddl 703613fa0c -- tortoise/    # metering.py:3  sdk.py:3
```

The report tool's log, with pass/fail per suite and the note above, is `WORKTREE-REPORT.md` at this
worktree's root.

### Measured contradictions of a *documentation* claim (not of the product)

- `record_allocator.py:199-202` and `tests/test_12894_record_cursor.py:294-319` claim the converter
  refuses a mixed `$n`/`%s` statement. Measured: it does not (`SELECT $1, %s, %s` → `SELECT $1, $1, $2`).
  See V6-5.
- Plan `§A7.5.5` says `FORCE ROW LEVEL SECURITY` on `record_cursor`; the migration says `ENABLE`
  (`20261116000001:78`). See V6-4.
- Plan `§A7.5.5` refuses case 2b; the landed `SEED_SQL` lifts it. See V3.

None of these three is a data-loss finding. The first is a gate that cannot see what it claims to see;
the other two are plan/code divergences with the code being the more permissive side in both cases.
