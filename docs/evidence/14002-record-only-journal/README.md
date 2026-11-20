---
title: "#14002 — taking the JSONL off the hosted write path: one allocator, measured"
type: evidence
domain: platform
doc_status: final
created: 2026-11-20
subjects.team: epistemic-team
aboutSubjects: Tortoise record allocator, journal ordinal, hosted write path
aboutObjects: "#14002, #12894, #9190, #9318, #13050, tortoise/live_write.py, tortoise/record_writer.py, tools/record_wiring_check.py"
---

# #14002 — the JSONL comes off the hosted write path

`docs/evidence/12894-live-wiring/README.md` §4 recorded the blocker and said the
flag must stay **OFF** because of it: *"a flag that can wedge a healthy graph
under ordinary concurrency is not a flag to flip"*. The wedge was not a fault —
it needed no restore, no truncation, no seal. Two allocators were on the write
path at once, ordered by different events, so two in-flight writes could be
numbered in the opposite order to their lines.

This document is the fix, the measurement that the fix holds under load, and the
falsifier that fails if it is undone. It does not re-derive #12894.

---

## 1. What was wrong, in one sentence

**The journal was ordered by APPEND and the counter by ALLOCATION, so on a
cut-over graph the counter could be left exactly one behind the journal — and on
a cut-over graph that gap never closes**, because an enumerated replay is itself
refused there (`record_projection._refuse_an_enumerated_replay` keys on the ROW,
not the mode). Every later armed write then refuses, with the cause printed in
the door's own warning.

Production shape, 2026-11-20: `record.ok: false`, `status: degraded`, some
records landing (`record_writes_applied` climbing, `record_last_write_at`
current) and some refused — a partial, non-deterministic loss with 20+ agents
writing continuously. Where it started:
`docs/evidence`-adjacent incident trail in #14002 (a 812 MB truncation that
restarted the graph's numbering, then an emergency pad-and-reseed that held only
until the first concurrent pair).

## 2. The fix, and what it deliberately does not touch

**One allocator.** `tortoise.live_write.write` asks
`record_writer.record_owns_position(tenant=…)` BEFORE it appends:

* flag OFF (the default, and every embedded/self-host deployment) ⇒ **no socket,
  no read**, today's path byte for byte;
* flag ON and the graph's `record_cursor` row in mode `active` ⇒ **the file
  append does not happen**; the counter issues the number, and the decision is
  forwarded to the seam as `journal_skipped=True` so the two `seq is None` states
  stay distinguishable;
* flag ON and a `seeded`/`halted`/absent row ⇒ **append anyway**. That is the
  asymmetry the issue's own acceptance implies: on those modes the allocator
  REFUSES, so the journal line is the only durable copy of the write. Skipping it
  there would put the write in neither store.
* the read fails, or the breaker is open ⇒ **append** (fail-open) plus a
  rate-limited WARNING. A refusal is recoverable; a skipped append behind a
  refused record write is not.

⛔ **The divergence guard is untouched.** #14002 says so explicitly, and it is
the right call: the guard is the only thing that kept the record honest while
both allocators existed. `write_live_record` still refuses any ordinal a caller
holds that disagrees with the issued number (`tests/test_12894_record_cursor_live.py::test_an_out_of_order_pair_WEDGES_the_graph_and_the_wedge_is_measured`
still passes), and `tests/test_14002_record_only_journal.py::
test_the_divergence_guard_still_REFUSES_a_caller_held_ordinal` drives the door to
pin the behaviour (an ordinal of 9 against an allocation of 4 must REFUSE, roll
back once and never commit), with
`test_the_guard_still_COMPARES_on_the_source` as its reformat-catching supplement.
⚠️ The behavioural leg replaced a pair of source-substring checks: a mutant that
turned the guard's own test into `if False:` kept both substrings intact, so the
pin passed with the guard disabled (review cycle 2, P3).
What changed is that the PRODUCTION path no longer manufactures the
disagreement the guard refuses.

Also unchanged: `backfill_copy_turns`' allocating batch, the seed's `--journal`
path, `journal_event` retention, `:GraphEvent.seq`, and every door that is not
the live one.

## 3. The measurement

Environment: a real `pgvector/pgvector:pg16` carrying this tree's record
migrations (`TEST_RECORD_DB_URI`, the same provisioner the
`test-record-postgres` job uses), one fresh tenant per test, seeded and armed.

**The window**: 12 concurrent writer threads × 10 writes = **120 writes**, all
released together by a `threading.Barrier` on one cut-over graph with
`TORTOISE_RECORD_ALLOCATE=1`. That is the production shape (20+ agents writing
continuously), scaled down to a deterministic count.

| Assertion | Result |
|---|---|
| `position_divergence` refusals over the window | **0** (no record-write failure of any token) |
| writes APPLIED | **120 / 120** |
| record positions | **1..120**, no gap, no repeat |
| `record_writes_applied` delta | **+120** (1:1 with the writes) |
| JSONL size / mtime over the window | **unchanged** (0 bytes → 0 bytes, same `st_mtime_ns`) |
| `LiveWrite.appended` | `False` for all 120 |
| `JournalCoverage.journal_is_export` (a real scoped read) | `True`, and `unprojected == 0` — not the contradiction arm |

Run: `TEST_RECORD_DB_URI=… TORTOISE_TEST_CARVE_OUT=1 uv run pytest
tests/test_14002_record_only_journal.py -q` →
**36 tests** (31 DB-free + 5 live). Without `TEST_RECORD_DB_URI` the same file
reports **31 passed, 5 skipped** — measured at this head, and the split the `core`
matrix leg sees; `--collect-only -q` prints `36 tests collected`, which is the
number to diff if this counts claim ever stops reproducing. ⚠️ It has drifted
three times already (`28` → `34` → `35` → `36`: every fix round that ADDS a leg
moves it), so re-derive it with those two commands rather than trusting the figure
— the same rule §6 states for the mutant population.

**Red-first, the same window with the fix disabled** (`journal_skipped = False`
in the chokepoint — the pre-#14002 tree, everything else identical):

```text
E  AssertionError: 117 of 120 writes were REFUSED on a cut-over graph —
   first: refusal='write_failed' record=RecordWrite(outcome=REFUSED,
   events=0, statements=0, first_seq=4, last_seq=3, …)
```

**115–120 of 120, and the exact count is scheduling-dependent — the DIRECTION is
not.** Repeated runs on two hosts produced 117 and 115; what is stable is that the
pre-fix path refuses almost every write in the window and each refusal logs the
door's `position_divergence` warning naming both numbers (`the journal holds the
record at position 95 and the ORDINAL ALLOCATOR issued 3`) — the hosted lane's
symptom, reproduced on demand.

**And the falsifier the issue asks for**:
`test_the_falsifier_an_appending_cut_over_graph_wedges_and_grows_the_file`
restores the append (skip forced off) and drives the SAME production path with a
chosen interleaving: write A appends+allocates in order, the journal advances by
one without the record, write B appends+allocates one behind. It asserts three
things — the file GREW (so the acceptance's size/mtime check has discriminating
power), write B was REFUSED, and the counter was rolled back to 1 with the record
holding exactly `[1]`. With the fix in place the same helper, driven without
forcing the decision off, appends nothing.

## 4. What this costs

One scoped `SELECT` (`record_allocator.read_cursor`, which sets and verifies the
tenant GUC) on the pooled handle the record write is about to use, **per write,
and only when the flag is ON**. It replaces a file append (open/write/lock) on
that same path.

⚠️ **NO LATENCY MEASUREMENT IS CLAIMED.** The 120-write window finished inside the
acceptance leg's own timeout with no observed contention, and that is all it
shows: no p50/p99 was taken against the append this call replaces. Nor is
"serialises better" an argument for this change — both designs concentrate a
tenant-graph's writes on one lock (a file lock, or the counter's `UPDATE … WHERE
seq = seq + n` row lock, held to commit). The case for the record owning the
ordinal is the property, not the throughput: ONE number space, so `divergence` is
not expressible, and nothing numbers off a truncatable file.

⛔ **Deliberately not cached.** A cached POSITIVE keeps skipping the append for a
graph that has since been halted or whose counter row was deleted — every write
in that window would be refused AND unlined. A cached NEGATIVE delays the
cutover by the same window. The measured cost above is the alternative.

## 5. Residuals, named rather than implied

1. **`tortoise rebuild` is now REFUSED on an armed deployment, and that guard is
   part of this change.** `tortoise rebuild` is a wipe + journal-only replay, and
   `docs/architecture/STORAGE-ARCHITECTURE.md` §3 makes journal completeness a
   PRECONDITION of `derived = replay(journal)` (the gap is tracked as `#5089`).
   Once the file stops growing, a rebuild from it would produce a graph as of the
   arming point and **silently drop every later record** — `rebuild_all` prints
   `Done:` either way and its counts are the journal's own. So the operator door
   refuses when a RECORD DSN resolves (`__main__._journal_rebuild_refusal`) —
   keyed on the record's EXISTENCE, never on the writer's
   `TORTOISE_RECORD_ALLOCATE`, because an operator shell, a cron job and a
   maintenance one-off do not carry a writer flag: keying on it left the
   destructive door open exactly where the people who run it live (review cycle 3,
   P2). The escape is `--journal-is-complete` — the operator asserting that no
   graph on this record is cut over, so the file IS the full log.
   ⚠️ It is deliberately BROADER than per-graph: a `record_cursor` row is keyed by
   `(org_id, graph_id)` and the rebuild door has neither, so it can refuse a
   rebuild on a configured record where no graph is cut over yet. Too broad
   refuses a rebuild that would have been fine (one flag); too narrow destroys
   work with no error. ⛔ A MALFORMED DSN IN THE SHELL IS NOT AN ANSWER: it means
   the shell could not say, so the deployment's `.env` is asked too, and only
   "no record in either" allows a rebuild (the earlier rule — "nothing can
   allocate there, so the journal IS the source" — was true only while the gate
   was process-env-only).
   ⚠️ **Reachability, stated so the gate is not read as covering more than it
   does:** on a HOSTED lane `tortoise rebuild` is the only wipe+replay door. Two
   more call `rebuild_all(..., confirm_destructive=True)` — the
   `tortoise migrate-db` route and `consistency.recover_from_log`'s auto-recovery
   — and neither is gated, because both are embedded/self-host paths
   (`recover_from_log` is gated on `self._is_embedded and not is_prod`) and an
   armed hosted box does not run them. If that ever stops being true, this gate
   belongs in `FalkorProjection.rebuild_all`, where all three would inherit it.
2. **`present > enumerated` is the HEALTHY cut-over shape, and every reader of
   coverage had to be told so.** `journal_coverage` compares the journal's
   enumeration with `journal_event`; once the file stops growing the record holds
   positions its journal never issued, which `JournalCoverage.
   unprojected_unknown_reason` used to refuse as a CONTRADICTION ("that file
   cannot be the journal that fed this record"). That arm was right while both
   allocators advanced together and it is wrong now: read as impossible it
   (i) disarmed the dense leg for ever — `record_vector._measure_readiness` reads
   the `None` as a contradiction and keeps the GRAPH branch, with a WARNING
   claiming the journal could not have fed the record — and (ii) made
   `record-reconcile --journal` unable to report clean on a healthy pair, which is
   the arming step `journal_ready` tells an operator to run. So the reading now
   carries `journal_is_export` — the ROW ALONE, in mode `active`, decides — and
   that shape answers `unprojected == 0` instead. (The mode is what separates a
   CUT-OVER graph from a journal that is still an input; the flag is a separate
   fact, which is why the door can still append on a row-`active` graph whose
   flag is not rolled yet.) ⚠️ `record_owns_position` additionally
   requires the deployment to be ARMED; that difference is deliberate (#14002
   cycle 3 P2): a reader does not carry a writer's flag, and a reading keyed on
   it reported a healthy cut-over pair as an impossible journal. ⚠️ **The reading asks the RECORD, never the reading process's
   `TORTOISE_RECORD_ALLOCATE`** (review cycle 3, P2): the row is the fact
   `ALLOCATION_SQL` keys on, while the flag is a WRITER switch an operator's
   shell does not carry — keying on it made `record-reconcile --journal` (the
   arming step a runbook tells an operator to execute) report a contradiction on
   a healthy pair. With the row `active` and the writers unarmed, the enumerated
   arm is refused, so the record cannot gain positions while the journal does: a
   row-active + `present > enumerated` reading can only be one the allocator fed.
   **Found by review cycle 2, not by the lane** — the module text it
   contradicts said "every healthy graph satisfies it … so no correct deployment
   is refused by this", which was true before this change. `LiveWrite.unprojected`
   `0` on the skip arm is the same fact one layer down, and the loss case — if the
   record refuses a skip-arm write — is the pair `appended=False, applied=False`.
   ⚠️ **The classification costs ONE statement plus its SAVEPOINT bookkeeping, and
   `want_gap=False` does not switch it off** (review cycle 4, P3-3):
   `JournalCoverage.unprojected` — the ONE thing the dense leg's readiness gate
   reads — consults `journal_is_export`, so gating the classification on `want_gap`
   would re-arm cycle 2's P1 (the dense leg disarmed for ever on every cut-over
   graph). And the read runs UNDER a savepoint (review cycle 4, P2) because a
   failed statement leaves the caller's transaction ABORTED (`25P02`) in
   PostgreSQL: the answer is still the fail-closed `False`, but the handle the
   caller keeps using stays usable and keeps its tenant scope — the same
   mechanism, for the same measured reason, as `postgres_bytes.measure_written_rows`
   (`#12781`). Both facts are asserted DB-free in
   `tests/test_14002_record_only_journal.py::test_the_export_read_fails_CLOSED_and_REPAIRS_the_callers_transaction`
   and `::test_the_export_predicate_answers_False_on_every_unmeasured_arm`.
3. **A skip-arm record refusal is not replay-healable.** There is no line. The
   door counts it (`record_write_failures`) and WARNs that the write is in
   NEITHER store, naming the remedy (the allocator's own status/repair), not a
   replay. ⚠️ **And it is reachable by an ordinary operator action, not only by a
   fault:** the decision is read on its own connection and the mode is only
   re-checked inside `ALLOCATION_SQL`, so a `halt`/`seed`/row-delete landing
   between the decision and the write turns a would-be recoverable refusal into
   that pair (`appended=False`, `applied=False`). The window is one write wide
   and the two ways to close it are to re-assert the mode on the write's own
   transaction (which the allocation statement ALREADY does — it is why the
   write refuses rather than mis-numbering) or to drain writers before halting,
   which no path does today. Reported, not prevented; a `halt` during heavy
   traffic should be followed by a re-emit of anything it landed on.

   ⛔ **WHICH FAILURES REACH THAT LOSS ARM**, because the answer is what makes the
   trade acceptable or not. The arm is reachable only when the DECISION READ
   SUCCEEDED and the write then failed on its own — an allocation refusal, a
   `journal_collision`, a statement error, a database dying between the two calls.
   It is NOT reachable for a general record OUTAGE: `record_owns_position` fails
   open, so an unreachable database means the append happens and the line survives
   the failed record write exactly as it did before #14002. That asymmetry is the
   justification for the direction, and it has a cost that must be said plainly:
   **on a cut-over graph the journal line is no longer a fallback.** One decision
   read before a failing write and the record is gone from both stores;
   pre-change, the same failure left a line a replay could heal. The door counts
   it (`record_write_failures`) and WARNs that it is in neither store — a
   notification, not a recovery.
4. **The JSONL is now an EXPORT artifact** and no export job writes it yet. The
   file's content is not reproducible from the record alone (`event_id`,
   `initiated_by`, `projection_version` are stored by neither column nor
   payload — measured in `docs/evidence/12894-live-wiring/README.md` §3.1), so an
   export that must be replayable needs those keys first (§A7.8 item 6). This
   change does not claim the export exists; it claims the write path no longer
   depends on the file.
5. **A sealed layout (#13050) is still a procedural gate**: `read_cursor` answers
   the file-local/segment-spanning question the same way it did, so *do not arm
   across a seal* continues to hold. Segment-aware numbering is #13050's.
6. **The 812 MB reclaim is still spent.** Reclaiming safely needs this change
   deployed first; after that, nothing numbers off the file, so a truncated or
   absent journal cannot restart the numbering.

## 6. How to check this is still true

```bash
# the DB-free legs (a deletion reds these without a database)
# ⛔ TORTOISE_TEST_CARVE_OUT=1 is REQUIRED for a URI-less run (epic #1647 P4):
# without it the session gate refuses once per collected test.
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_14002_record_only_journal.py -q

# the wiring gate: the append is behind the decision, and every door forwards it
uv run python tools/record_wiring_check.py

# the live legs (a real Postgres carrying the record migrations)
TEST_RECORD_DB_URI=… TORTOISE_TEST_CARVE_OUT=1 \
  uv run pytest tests/test_14002_record_only_journal.py -q
```

**TWELVE mutants across THREE legs**: FIVE on the chokepoint
(`test_the_wiring_gate_reds_when_the_cutover_skip_is_deleted`, one parametrized
case per `_MUTATIONS` key — the decision replaced by a constant, the append moved
out of the guard, the helper answering a constant, the helper CALLING the record
and ignoring the answer, and the decision not forwarded to the seam), SIX door
shapes
(`test_a_door_that_drops_the_flag_is_refused_by_the_gate` — dropped, constant
`False`, constant `True`, `None`, a name bound NOWHERE forwarded by name
(`_local_constant` — it reds on the undefined name, not on provenance), and
**the parameter REBOUND to a constant and forwarded under its own name**, review
cycle 8 F1: the parameter arm of the provenance rule was taken on the signature
alone, so that mutant shipped with the gate printing `WIRED` and the whole
DB-free suite green),
and ONE ARMING mutant (`test_the_cutover_rule_is_ARMED_inside_check` — the
`+ live_call_sites_forward_the_journal_skip()` term deleted from `check()`'s
`position_problems` expression, which disarmed the whole gate with every other leg
green; review cycle 2/4's carried P2, closed here).
Every one leaves the tree compiling and every other claim of the gate green: the
shape this repo has already shipped once (#12894's door refused every write while
the gate printed `WIRED`). ⚠️ Stated ONCE here and cited elsewhere — three
artifacts quoting three populations was itself a finding (review cycle 4), and the
first unification still said NINE while the code held TEN (five `_MUTATIONS` keys
plus five door shapes, re-counted at `4cf3e8ce0c` rather than carried from prose);
the ARMING mutant is the eleventh, added because the rule was pinned by nothing,
and the parameter-REBIND door shape is the twelfth (review cycle 8 F1).
A reader diffing this figure should re-count the three populations, not trust it.
