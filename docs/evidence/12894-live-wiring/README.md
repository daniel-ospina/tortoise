---
title: "#12894 — wiring the allocator onto the live path: the falsifiers, and whether the JSONL can stop being the commit point"
type: evidence
domain: platform
doc_status: final
created: 2026-11-16
subjects.team: epistemic-team
aboutSubjects: Tortoise record allocator, journal ordinal, hosted write path
aboutObjects: "#12894, #12986, #9190, #9318, #9025, tortoise/record_writer.py, tortoise/record_projection.py, tools/record_allocator_cli.py, docs/evidence/12894-landed-verify/README.md"
---

# #12894 — what the wiring changed, what it proved, and what it did not decide

`docs/evidence/12894-landed-verify/README.md` (V1–V6) proved that the allocator
chain which landed was scaffolding: the mechanism was complete and **nothing in
production could reach it**. This document is the next step — the mechanism is
now reachable behind a flag, six falsifiers pin it, and one question is answered
by measurement rather than assumption: **can the JSONL append stop being the
commit point?**

It does not re-derive V1–V6. Where this document leans on a fact, the citation is
to that report's section or to `file:line`.

---

## 1. Two readings of the ruling, and the one falsifier that separates them

The owner ruling (`#9190`, comment 6902871297) authorises: *the record allocates
the ordinal and the journal record CARRIES it; replay READS the number instead of
computing it.*

The brief for this lane adds a falsifier that the report above did not settle:
**(d) a journal line and its row carry the same number.** Two readings satisfy the
ruling's text and disagree about (d):

| Reading | (d) | What the landed tree does |
|---|---|---|
| the record is the **sole** authority; the journal's file index is a different coordinate | FALSE by construction (line 1, row 6) | `test_the_live_door_takes_the_allocated_number_over_the_journals` **pinned** exactly this: journal ordinal 9, row 4, APPLIED |
| the record **issues** the number and the journal must AGREE while both exist | TRUE, and it is a cutover invariant | not implemented; this lane's choice |

The second reading was taken, for one reason that is not a preference: **it is the
only one in which a live write can be verified today.** Under the first, the door
has no second reading to check the issued number against, so an armed deployment
that mis-measured its journal writes rows no replay of the JSONL can reproduce,
silently. Under the second, that state is a refusal.

This is also the standard dual-write cutover shape: an **agreement window** in
which both sources must produce the same answer, so that retiring one of them
later is a measurement rather than an assumption. That is the whole justification
for the window — not that agreement is elegant, but that disagreement becomes
observable while it is still harmless.

### The mechanism

`write_live_record` (the live door, one per journal append) gains:

* `TORTOISE_RECORD_ALLOCATE` (`record_writer.RECORD_ALLOCATE_ENV`) — default OFF,
  read **per call** through `env_flag`, so a typo leaves it OFF and an unarmed or
  refused write is byte-identical whatever the flag says;
* the **cutover guard**: on the allocated arm with the journal's ordinal in hand,
  `allocated != journal_position` ⇒ `connection.rollback()` ⇒
  `RecordWrite(REFUSED, "write_failed")` plus a WARNING naming both numbers and
  the `status --journal` command that shows them side by side;
* `seq is None` (the `#9307` "append succeeded, position not proven" states) on
  the armed arm is **refused** rather than allocated: there is nothing to check an
  issued number against.

The rollback is what makes the guard free. Detecting the mismatch *after* the
insert and *before* the commit means the counter increment rolls back with the
row, so the position is neither burned (no gap) nor reused (nobody saw it).

`replay_plan` gains `_carried_position`: a record that carries `seq` is asserting
its own position, and a disagreement with `start_seq + index` is refused, never
renumbered. No producer emits one **as of this change, because the three journal
intakes now strip or refuse the name** (`sdk._RESERVED_ENVELOPE_PROPS`,
`api._ENVELOPE_KEYS`, `backfill_copy_turns._SESSION_ENVELOPE`) — that premise was
MEASURED FALSE before this change (`update_point(pid, seq=…)`, `EventAPI._emit`,
and the session copy each put one on a line).

⛔ For a JOURNAL ALREADY ON DISK this read is NON-SILENT BUT NOT RECOVERABLE.
The journal is append-only (R6), so a line written before these intakes existed
keeps its top-level `seq` for ever; the read refuses rather than renumbering
(which is the right direction, and it names the ambiguity), but `replay_plan`
raises while BUILDING the plan — no partial repair — and the producer-side strip
only prevents FUTURE lines. **No journal-line migration ships**, so such a
graph's record cannot be rebuilt from that journal until one does. The
population is UNMEASURED: the evidence that the premise was false is a synthetic
`update_point(pid, seq=…)` and the `:Session` copy, not a census of journals in
the field.

`tools/record_allocator_cli.py` is the operator door: `status`, `seed`, `arm`,
`halt`, `reopen`. Before it, `record_allocator.seed()` had **no caller outside
tests** (V3) and the migration inserts no row, so every graph was uncut and the
cutover had no door.

⚠️ One consequence worth stating rather than discovering: **arming the flag on a
graph with no counter row refuses every record** (`cursor_not_active`, with the
`arm` remedy in the WARNING). That is the fail-closed direction and it is
deliberate — a flag that silently fell back to enumeration would hide exactly the
mis-armed deployment an operator needs to see.

⚠️ And a second, which the same reasoning covers but which costs more than one
record, so it is spelled out. **The journal's position advances on every append,
including an append whose record the door then declines, while the counter
advances only inside a committed write** — so the two directions a disagreement
can take are NOT symmetric, and they must not be described with one sentence:

* **journal AHEAD of the counter** (a refused write consumed a position the counter
did not). An **enumerated** replay cannot close the gap — it writes rows and never
the counter, and on a cut-over graph it is refused outright — so the guard refuses
every *later* record for the same reason, indefinitely. ⚠️ An **armed** replay of
the missing record does close it: `replay(allocate=True)` runs `ALLOCATION_SQL`,
which *issues* the position — `replay()` never commits (it is forbidden one), so
the caller's commit is what lands the gap closed, and a rolled-back armed replay
closes nothing. That is per-record surgery. The general remedy for a whole gap is
the operator chain: `halt` →
`seed --journal <path>` → `reopen` → `arm` (the seed is allowed
here, because the record's rows are below the journal).
* **journal BEHIND the counter** (a restored or truncated journal). This is
*worse*, not better: each refused append still advances the journal by one, so the
guard stops refusing after `counter - floor` records — and **each of those records
silently loses its row** with no operator action at all. It does not wedge, it
eats. `seed --journal` refuses in this state (`SEED_SQL` requires
`row_max <= enumerated_floor`) and a re-seed cannot lift the counter *down*
(`GREATEST(cursor_row.seq, EXCLUDED.seq)` is monotone), so `--enumerated-floor` is
not a remedy — it only lets the seed RUN. The remedy is to restore the journal, so
its enumeration reaches the counter again.
* **a SEALED journal** is a third case and is *strictly worse* than either, which is
why the seal is a gated operator transition. After a seal `EventLog.append` reports
the **file-local** index (it restarts at 1) while the counter is per-graph over the
whole layout, so the guard refuses for `local` 1..`counter` — and every one of those
appends already committed its journal line and writes no row, so it **eats `counter`
records**, more than the behind case's `counter - floor` — then *accepts* at
`local == counter + 1`, writing a row whose number is not the journal's position: a
silent and permanent split of the two coordinates the guard exists to keep. **No
counter value fixes that comparison**, because the two sides are different
*coordinates* — re-seeding corrects the seed, never the comparison. So the gate is
procedural: **do not arm across a seal; halt the graph before sealing it.**
Segment-aware numbering is #13050, not a re-seed.

This is the price of keeping one invariant rather than guessing which side of a
disagreement is right, and it is a real operational consequence of the design, not
a defect to be discovered in production. Both the door's WARNING and
`tools/record_allocator_cli.py status --journal` now say which side is ahead, and
`status` reports the door's own condition as `position_divergence` — distinct from
the seed's `divergence`, which is a different pair (`row_max > floor`) and can
read `false` while the door refuses. ⚠️ `position_divergence` is exact for a
SINGLE-FILE journal only: on a segmented layout the append's number is file-local
while every floor this tool can be given is segment-spanning, so the two sides of
that comparison are not the door's two sides. The residual — no automatic
recovery, and no repair path that does not require an operator — is recorded here
rather than designed away in this lane (#12894 review cycles 4–6, P1).

---

## 2. The six falsifiers, and which of them ran red first

Order and wording are the brief's. Every leg lives in
`tests/test_12894_record_cursor_live.py` (the "wire" section) and drives the
**real** door (`write_live_record`) or the **real** verb — not `RecordWriter`, not
the plan builder alone — because "a green suite that never exercises
`allocate=True` on the live door is not" evidence.

**The exception, stated so the green count is read as what it is** (an earlier cut
of this paragraph claimed the rule without it, #12894 review cycles 4–5, P2): three
falsifiers assert a fact that belongs to the allocator or the plan, not to the
door, and drive that layer directly — (3a)
`test_two_writers_allocate_in_commit_order` and (3b)
`test_a_rolled_back_allocation_is_reissued_exactly_once` call
`record_allocator.execute_allocation`; (3c) is TWO legs,
`test_replay_reads_a_carried_ordinal` and
`test_replay_refuses_a_carried_ordinal_that_disagrees`, and both drive
`record_projection.replay_plan` without touching the door. Every other leg —
including all six divergence, seal and unproven-position refusals — goes through
the door.

| # | Falsifier | Leg | Red before the code because |
|---|---|---|---|
| (3a) | two concurrent writers never share a position; allocation order = commit order | `test_two_writers_allocate_in_commit_order` | — (the mechanism existed; this is the proof) |
| (3b) | a rolled-back transaction burns no position and reuses none | `test_a_rolled_back_allocation_is_reissued_exactly_once` | — (proof) |
| (3c) | replay READS the carried ordinal and refuses on `carried != start_seq + i` | `test_replay_reads_a_carried_ordinal`, `test_replay_refuses_a_carried_ordinal_that_disagrees` | replay always enumerated |
| (3d) | a journal line and its row carry the same number | `test_the_armed_row_carries_the_journals_position`, `test_a_divergent_journal_position_is_refused_not_renumbered` | the door ignored `seq` on the allocated arm |
| (3e) | a truncated journal does not re-mint a position | `test_a_shrunken_journal_cannot_mint_a_position` | the door would have written at `counter + 1` |
| (3f) | `TORTOISE_JOURNAL_SEGMENTS=1` after a seal is gated or harmless | `test_a_sealed_journal_is_gated_on_the_armed_arm` | the door would have written at `counter + 1` |

(3a) is the property a bare `nextval()` cannot give — a sequence is not
transactional and its order is not the commit order (`record_allocator.py`'s
module docstring). The leg has writer A allocate and **hold**, asserts writer B is
still blocked after a second, then commits A and asserts B received `A.last_seq + 1`
and finished after A's commit. (3f) does not assert the seal from the code: it
builds one with a real `journal_segments.seal_active`, appends again, asserts the
append returns **1** again (the premise), and only then drives the door with that
number.

The measured red, on the flag-only tree before the guard and the carried-ordinal
read existed: **9 passed, 7 failed** (`WORKTREE-REPORT.md`, suite 2). Green after:
**39 passed** for the file.

⚠️ **Which legs that red actually covers, stated because the heading above used to
claim all six.** The table rows carry the answer in their own last column: (3a) and
(3b) are marked `— (proof)` because the mechanism they assert already existed and
their legs cannot be red on any tree — they prove, they do not falsify. (3c)'s
`test_replay_reads_a_carried_ordinal` also cannot be red: a plan that ignored `seq`
entirely would enumerate to the same `5, 6` (its own docstring says so); the
discriminating half is `test_replay_refuses_a_carried_ordinal_that_disagrees`.
So the **7 failures** are the falsifiers proper — the carried-ordinal REFUSAL, (3d)'s
two legs, (3e) and (3f) — against 4 of the 6 named properties; the other two, plus
(3c)'s read leg, are proofs. Red-first is the strong claim, and it belongs only to
the legs whose last column does not say `— (proof)`.

---

## 3. Can the append stop being the commit point? The measurement

**No — and not because of policy.** The question is whether the JSONL line can be
re-rendered from the record, so that what §17.1 calls the commit point could
become an export. Two halves, both measured.

### 3.1 Content — the record's own layers ARE reproducible; the export is NOT

`journal_payload` (`record_projection.py:6357`) is the ONE definition of what a
`journal_event` row contains, and it filters `ENVELOPE_KEYS`. The measurement
re-renders a record from its row — payload from the `payload` column, `type` from
`event_type`, `ts` from `recorded_at` — and compares the plans:

```text
$ uv run python /tmp/measure_12894.py      # the script is inlined below
PointAdded     statements= 2 plan_identical=True unrecoverable_keys=[]
PointRevised   statements= 1 plan_identical=True unrecoverable_keys=['event_id', 'initiated_by', 'projection_version']
EntityLinked   statements= 1 plan_identical=True unrecoverable_keys=[]

envelope keys the row stores as COLUMNS or not at all: ['event_id', 'initiated_by', 'projection_version', 'seq', 'ts', 'type']
```

Read it precisely, because the two halves point in opposite directions and only
one of them is convenient:

* **The record's four layers are reproducible from its own rows.** For all three
  representative shapes the plan built from a row-reconstructed record is
  byte-identical to the plan built from the journal line. So the record is a
  sufficient source *for its own derived layer* — which is what
  `STORAGE-ARCHITECTURE.md` §3 claims and what this change makes more nearly true.
* **The journal LINE is not.** `event_id`, `initiated_by` and `projection_version`
  are stored as neither column nor payload. The one place that decides this says
  so already (`journal_payload`'s docstring, round-1 finding M15), and `§A7.8`
  item 6 records that widening the key with `event_id` needs its own decision on
  its own issue. So an export produced from the record alone would be a DIFFERENT
  journal, and the JSONL's whole job — being the durable source a graph rebuild
  replays — is not reproducible from the record.

### 3.2 The measurement is re-runnable

Write this to `/tmp/measure_12894.py` (run from the repo root) and run
`uv run python /tmp/measure_12894.py`. The record shapes are taken from records the
tree's own suites use, so the three cases are not invented for the measurement.

```python
"""Step 4's measurement: is a rebuild from the RECORD alone exact?

The question the append's fate turns on. Two halves:
 (1) CONTENT — does a record re-rendered from its journal_event row produce the
     same plan as the original journal line? (payload + event_type + recorded_at)
 (2) POPULATION — is every journal line represented by a row at all?
"""
import json, sys
sys.path.insert(0, '.')
from tortoise import record_projection as rp

cases = {
  "PointAdded": {"type":"PointAdded","ts":"2026-11-16T00:00:00Z","id":"p1",
                 "point":{"id":"p1","content":"c","pointKind":"claim"}},
  "PointRevised": {"type":"PointRevised","ts":"2026-11-16T00:00:01Z","id":"p1",
                   "point":{"id":"p1","content":"c2","pointKind":"claim"},
                   "event_id":"ev-1","initiated_by":"agent-7","projection_version":3},
  "EntityLinked": {"type":"EntityLinked","ts":"2026-11-16T00:00:02Z",
                   "id":"pt-x","source_id":"pt-x","source_label":"Point",
                   "target_label":"Object","target_id":"o1",
                   "edge_type":"aboutObject"},
}

def rebuild(record):
    """What a row can reconstruct: columns back into a record."""
    out = json.loads(rp.journal_payload(record))   # the payload, as stored
    out["type"] = record.get("type")               # from the event_type COLUMN
    out["ts"] = record.get("ts")                   # from the recorded_at COLUMN
    return out

for name, rec in cases.items():
    before = rp.mutations_for(rec, org_id="o", graph_id="g",
                              columns=rp.table_columns(), refused=rp.refused_props_names())
    after = rp.mutations_for(rebuild(rec), org_id="o", graph_id="g",
                             columns=rp.table_columns(), refused=rp.refused_props_names())
    same = [(s.sql, s.params) for s in before] == [(s.sql, s.params) for s in after]
    lost = sorted(set(rec) - set(rebuild(rec)))
    print(f"{name:14s} statements={len(before):2d} plan_identical={same} unrecoverable_keys={lost}")

print()
print("envelope keys the row stores as COLUMNS or not at all:", sorted(rp.ENVELOPE_KEYS))
```

### 3.3 Population — the record's rows are a subset BY DESIGN

Independently of content: a refused live write appends its line and writes **no**
row, and that is deliberate on every refusal arm. The arms are countable in the
door: `unarmed`, `malformed_dsn`, `no_durable_journal`, `no_tenant`,
`no_tenant_carried`, `circuit_open` (`LIVE_WRITE_REFUSALS`), plus `write_failed`
(including the two this lane adds — divergence and an unproven position) and
`journal_collision`. The M2 session-capture lane appends to a scratch log by
design. So `journal_event` holds fewer records than the journal by construction,
and an export built from the record could not contain the lines the record
declined — including the ones a rebuild needs most.

### 3.4 Verdict

**The append stays the commit point.** It is not removed here, and no change in
this lane touches `sdk.py`'s §17.1 ordering or the cache write gated on it. The
step's own DoD allows exactly this outcome: *"if it is not decidable here, leave
the append in place and say why"* — and it is decidable, with an answer of "not
yet, and here is the missing half".

No code in this lane removes or reorders the append; `sdk.py`'s §17.1
declaration and the `_live_write_scope` gate on the cache write are untouched.

What would have to be true for the export to replace the append, stated so the
next lane does not have to rediscover it: (i) `journal_event` carries every
journal-carried key (`event_id`, `initiated_by`, `projection_version`) — `§A7.8`
item 6's decision; (ii) the record's population equals the journal's, i.e. a
refused record write does not lose a line the rebuild needs; and (iii) the
drainer `§A7.5.6` specifies exists (`last_exported` appears only in the plan
document, never in a Python module — V5 item 7).

---

## 4. The residual that decides the flag: DO NOT ARM THIS YET

> **⛔ STATUS UPDATE, 2026-11-20 (#14002): THE BLOCKER IS REMOVED.** Everything
> below describes the tree as it stood when this was written, and it is kept as
> the record of WHAT the wedge was and why the flag stayed off. The repair is not
> the per-graph lock §4 names as one of two options, and not `#9318`'s journal
> identity: it is the third option the sentence above did not consider — **remove
> the second allocator**. `tortoise.live_write.write` now asks
> `record_writer.record_owns_position` BEFORE it appends, and on a cut-over armed
> graph the JSONL append does not happen at all, so the append/allocation
> ordering wedge is not expressible. The divergence guard is untouched and still
> refuses every out-of-order ordinal a caller holds, which is why
> `test_an_out_of_order_pair_WEDGES_the_graph_and_the_wedge_is_measured` still
> passes — it pins the GUARD's behaviour, not the production path's, and the
> production path no longer reaches it. Measurements (120 concurrent writes, 0
> divergence refusals; the same window pre-fix: **115-120 of 120 refused**, the exact
> count being scheduling-dependent), the
> falsifier and the new residuals: `docs/evidence/14002-record-only-journal/`.
> ⚠️ §3's verdict (*"the append stays the commit point"*) is unchanged in the
> sense that matters there: the JSONL LINE is still not reproducible from the
> record, so it cannot yet be an export that a rebuild replays — the write path
> just stops feeding it on a cut-over graph.

The cutover invariant compares the counter's number with the journal's
**position**, and the two are ordered by different events: the journal by
**append** (the `sdk` site appends, then calls the door — no lock across the two),
the counter by **allocation**. Two in-flight writes can therefore be numbered in
the opposite order to their lines, and that needs **no fault at all** — not a
restore, not a truncation, not a seal. The one that allocated first refuses, the
other applies, and the counter is left **one behind** the journal. Every later
armed write then refuses for the same reason, because on a cut-over graph an
enumerated replay is itself refused (`_refuse_an_enumerated_replay`) — so the
state does not heal on its own. The operator path back is
`halt → seed --journal → reopen → arm`.

This is measured, not described:
`test_an_out_of_order_pair_WEDGES_the_graph_and_the_wedge_is_measured` drives the
interleaving deterministically (no threads) through the real door and asserts the
wedge — a counter at 1 while the journal holds two lines, and the next write
refused. It pins a KNOWN DEFECT on purpose: it exists so nobody arms
`TORTOISE_RECORD_ALLOCATE` believing only a fault can wedge a
graph, and so the fix has a leg that must change when it lands.

**Consequence for this lane's outcome.** The brief's DoD (a) asks for the live path
to allocate behind a flag that is *proven* by the six falsifiers — that holds, and
the byte meter charges real bytes. But a flag that can wedge a healthy graph under
ordinary concurrency is not a flag to flip, so the outcome is the DoD's **(b)**
half: a measured finding naming the blocker, with the wiring landed and the switch
OFF. The blocker is `#9318`'s journal identity in the key, or a per-graph lock
spanning append→allocate; either is a redesign of the invariant's R2 contract and
belongs to that change, not to this one.

⚠️ Named residual ON the residual: the interleaving above is driven by calling the
door twice in a chosen order, which is what two concurrent writers produce but is
not itself two threads. `test_two_writers_allocate_in_commit_order` covers the
allocator's own ordering; it cannot see the append/allocation split, because both
of its writers arrive with no journal at all.

---

## 5. The follow-up review (#13043 comment `6938339131`) — what it found, and what changed

A fresh-context review of the merged PR found **no P0** (commit point unmoved, the `seq` hole closed
on both doors, the flag OFF and typo-safe, the seed reachable), and **three P1s** that were real. All
three are closed on `fix/13043-cutover-blockers`, with the switch still **OFF**.

**P1-3 — the carried ordinal was inferred from key PRESENCE.** Reproduced by the review:
`replay_plan([{… "seq": 42}], start_seq=7)` → REFUSED. A pre-existing journal can hold such a line
(the pre-reservation SDK put one there for `update_point(pid, seq=…)`, and a `:Session` copy carries
one), so ONE legacy line made that record's live write **and** its only repair refuse, with no in-band
fix. `replay` / `replay_plan` / `RecordWriter.write` / `write_records` now take `carried_positions`
(default `False` = journal-sourced: enumerate, ignore the prop; `True` = record-sourced: read it, and
refuse a disagreement naming both numbers). Shown red then green **on a real Postgres**: a line
carrying `seq: 42` at journal position 3 refused `write_failed` before, applies at 3 after.

**P1-4 — the fourth position-row writer was not wired to the cutover.**
`tools/backfill_copy_turns.py` passed `position=True, first_seq=first` and no `allocate=`, so on a
graph this feature had cut over the enumerated arm refused EVERY session batch — and #13043 is what
made that state reachable (the seed was unreachable before it). It now passes `allocate`, with
`first_seq=1` on that arm, compares the allocated number against the journal's reported position
before committing (refusing + rolling back on a disagreement — the check the live door makes and this
lane cannot borrow), and pre-flights the counter row BEFORE the first append, so a mis-set environment
refuses while nothing has been written.

**P1-1 / P1-2 — the flag is global, the cutover is per graph, nothing creates the row.**
`provision_graph_record` (§A7.8 item 8) had **zero hits in this tree** when this lane ran, and it was
NOT built here: it is a migration plus two `hosted_api` call sites with a fail-hard/fail-soft split
that `#10025` F1 governs, i.e. its own change. ⛔ **`#14582` has since built it** —
`record_allocator.provision_graph_record`, called from `hosted_api._provision_graph` and
`supabase_control.ensure_default_graph_row` (and deliberately NOT from `sdk._graph_create`, whose
both arms are findings) — so the "absent hook" reading below is this lane's history, not the tree.
What this lane does instead is make the door *refuse to
pretend*:

* `arm` REFUSES without `--acknowledge-cutover`, and the refusal names the flag and its home (the
  writer's environment, not the operator's shell), the order of operations, the per-graph scope, the
  creation hook (absent then; built by `#14582`), and the backfill lane. The state that ate rows — counter armed, door not —
  is no longer reachable by following the documented steps.
* A read-only `sweep` finds the graphs the record has seen that have NO counter row, with the seed
  command and the warning that the floor is the JOURNAL's count, not `row_max`.
  ⛔ **Since #14624 that population is everything NOT ALLOCATING**, not just everything missing a row:
  a counter row in `seeded` or `halted` allocates nothing either, so a `WHERE c.graph_id IS NULL`
  population read **CLEAN** over the graphs `backfill` had just seeded (it seeds without arming), and
  an operator following the printed checklist rolled the global flag over them — silently, because the
  live door is fail-soft. Each entry carries `counter_mode`, because `seeded` is healed by `arm` and
  `halted` by `reopen`. The verb's own docstring is the canonical statement.
  ⚠️ Its first cut answered "no born-uncut graphs" on a real org: an unscoped connection returns zero
  rows under RLS, and a false CLEAN is the worst answer a pre-flight can give. The DB-backed leg
  caught it; `record_allocator.scope_tenant` is the fix.
  ⛔ **And it reports only what it read.** The per-graph number is `journal_max_seq` — `MAX(seq)`, named
  for the aggregate it is — and `floor_is_the_journals_count` is answered only where an enumeration
  exists (`--journal` counted here, or `--enumerated-floor`); otherwise it is `null` beside the reason.
  The first cut printed `true` as a literal and, worse, read the journal AFTER a `rollback()` had ended
  the transaction carrying the transaction-local tenant GUC, so every entry said `0`. A single
  `--enumerated-floor` on a sweep that finds more than one graph is REFUSED — the floor is per graph.
  ⛔ **What it cannot see, stated on the verb itself:** a graph that has never been written (no
  `journal_event` row) — the record role has no grant on `public.graphs`. Such a graph has lost
  nothing yet; the first append is the moment it becomes visible and actionable.
* `status` reports the RECORD's state (`allocates`, `allocates_scope`) separately from this box's
  environment, because `allocates: true` on an unarmed door reads as "the door is allocating".

**Residual, and it is the reason the switch stays OFF regardless:** the append/allocation ordering
wedge in §4 above is unchanged by this lane — it belongs to `#9318`'s journal identity, not to the
review's findings. Until a graph's creation provisions its counter, and the ordering is one lock, the
honest state is: wired, tested, and unarmed.
