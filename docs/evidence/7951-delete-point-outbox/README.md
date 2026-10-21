---
title: "#7951 increment 4a — the outbox ordering on `delete_point` (evidence)"
type: evidence
domain: platform
doc_status: final
created: 2026-10-18
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7951, #9207, #7950, STORAGE-ARCHITECTURE.md §17.1, tortoise/sdk.py delete_point, FalkorDB cache"
---

# #7951 increment 4a — the outbox ordering on `delete_point`

**Measured at:** the #9207 head off `origin/main` `b77d24084`, 2026-10-19. Embedded lane
(`TORTOISE_DB_URI` unset, `TORTOISE_TEST_CARVE_OUT=1`) — no server needed, so the demonstration is
reproducible by any lane.

⚠️ **#9207 REVERSED this door's ordering**, so the numbers below are #9207's, not increment 4a's.
\#7951 moved the record ABOVE the `DETACH DELETE` to close the resurrection window; #9207 measured the
mirror window that opened in exchange — a `DETACH DELETE` that does not land (a generic
engine/transport failure, the node still present) raised with the destruction record ALREADY
committed, so `get_point` still served the Point while the journal claimed it was gone, and only a
destructive `rebuild_all` could resolve it. ⛔ It is NOT a systematic pre-write refusal, and the reason
is the guard's ARMING STATE, not a gap in its coverage: §17.1's write surface has three doors
(#9124 part 1), and this statement is issued at the third one — the projection's write handle,
`proj.g.query` — which DETECTS a mutating statement with no declared lane but does not yet REFUSE it
(`_HANDLE_GUARD_ENFORCES` is `False`; part 2 arms it). Section 4 of the probe below (the residuals)
prints `RAW HANDLE LANE (proj.g.query): SUCCEEDED` with that door measuring, which is why the failure
in the test is INJECTED. ⚠️ Declaration state: `delete_point` holds NO journal-first lane (`#9207`'s
apply-first order makes one false) — #9206 has since landed the "apply-first, by decision" lane
(`@_declares_apply_first_write_lane`), which this door now carries, so an armed handle COMPLETES it
and it is no longer inventoried in `_APPLY_FIRST_DOORS`.
The destroy is now issued FIRST (`MATCH … DETACH DELETE n`, with NO `RETURN` clause and no row-count
gate) and the record follows only once that statement has RETURNED. Because a raise does not prove
"nothing was destroyed" — the transport family may have applied the write and lost the reply — the
door READS THE CACHE BACK after a failure: gone ⇒ the destroy landed and the destruction is recorded;
present ⇒ the clean refusal, nothing recorded; unreadable ⇒ a third arm that records nothing and
states only that the destroy is UNVERIFIED (`destroy_verified=False`).

**What this proves, and what it does not.** It proves the §17.1 **ordering** on the hard-delete door:
no record can exist for a destroy the cache did not perform — the record follows the VERIFIED cache
state, so a cache write that raises appends nothing UNLESS the read-back proves the destroy landed (a
lost reply over an applied write IS recorded: the cache is what the record describes), and a zero-row
write IS recorded, because the node is absent either way. Every residual window in which CODE RUNS is
reported rather than silent: a landed
destroy whose append failed is `record_committed=False`, a lost reply over an APPLIED destroy is
recorded and logged, and an unreadable after-state raises an arm that asserts neither store. ⚠️ The
one window no code can report is a PROCESS DEATH between the destroy returning and the append
(`kill -9`, OOM, power loss): nothing raises and nothing is logged, and the next `rebuild_all`
re-creates the Point from its `PointAdded` line. That is the crash window #9207 accepts in exchange
for closing one that was systematic — removing it entirely is §17.1's atomic write (increment 2b). A
completed delete stays durable across replay. It does
**not** claim §17.1 is closed — the raw handle is still open, and `delete_entity` (the generic entity
delete) is still apply-first; both are shown in the probe's own last section. Plans of record:
`docs/plans/2026-10-18-7951-delete-point-outbox.md` (increment 4a) and #9207 (its ordering reversal).

## The demonstration

```console
$ env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
    .venv/bin/python docs/evidence/7951-delete-point-outbox/probes/delete_order_probe.py
journal_backed (guard armed): True
delete_point order:              ['cache-DETACH-DELETE', 'journal-EntityMutated-delete']
ORDERING VERDICT: CACHE-FIRST (#9207)
delete records committed:        1
Point live after the delete:     0
Point live after rebuild_all:    0  (0 = the deletion is durable)
failed cache delete raised:      RuntimeError (operation='?', record_committed='?')
Point live after refused delete: 1  (1 = the destroy did not land)
delete records committed:        0  (0 = the journal claims nothing the cache did not do)
REFUSAL VERDICT: NO RECORD FOR A DESTROY THAT DID NOT HAPPEN
failed append after the destroy: WriteNotLandedError (operation='delete_point', record_committed=False, record='EntityMutated op=delete')
record committed despite failure: 0  (0 = the append never landed)
caller told UNCONFIRMED (not silent): True
Point live before rebuild:       0  (0 = the destroy landed)
Point live after rebuild:        1  (1 = the TRUTH still held it and the replay restored it)
COMMIT VERDICT: REPORTED, REPAIRED ONLY BY A DESTRUCTIVE REBUILD
RAW HANDLE LANE (proj.g.query):  SUCCEEDED, rows = [['via-raw-handle']]
delete_entity order:             ['cache-DETACH-DELETE', 'journal-EntityMutated-delete', 'cache-DETACH-DELETE', …]
RESIDUAL: the raw handle is NOT closed, and `_delete_entity` is still apply-first — both recorded on #7951; increment 4a routes ONE delete door (`delete_point`).
```

**Raw output:** `delete-order-probe.log` (verbatim — **re-measured** at this head, after the §17.1
reconciliation that withdrew this door's live-lane declaration; the shipped live engine also logs, and
the log carries every one of those lines: the pack-registry / ontology-relation advisories
(`#7852`/`#7848`), the **`§17.1 … NO declared lane` measure-mode advisories** (`#9124` part 1 — this
door's `DETACH DELETE`, `_mark_dirty`'s `UNWIND … SET n.ep_dirty` and `invalidate_messages`'s
`REMOVE r.msg_*` are now counted as UNDECLARED, which is the visible consequence of `delete_point`
being apply-first and holding no lane), the
undeclared `ep_dirty`/`ep_dirty_at` Point props (`#2795`), the `#2943` snapshot-dir note (twice — once
per temp dir), and the probe-induced `failed to append EntityMutated event` line (`#4240`, section 3's
deliberate journal failure)).
Exit code `0`. Those advisory lines carry the paths of the worktree the log was measured in
(`pack registry /Users/danielospina/Documents/GitHub/tortoise/.worktrees/fix/p0-delcommit/packs`,
plus the per-run `mkdtemp` dirs) — they are the engine's own report, not probe output, and are left
unfiltered so the log stays verbatim; the WORKTREE path is normalized to that canonical
`fix/p0-delcommit` form (both captures ran in a worktree of this branch), and the cold-cache
HuggingFace download warning/progress line is omitted as environment noise rather than output.
Re-running the probe therefore differs from the log in those paths only (the worktree form and the
`mkdtemp` names are per-run); every measured line is stable.

## What each line means

| line | it is the failing criterion of… | how it would fail |
|---|---|---|
| `order: ['cache-DETACH-DELETE', 'journal-EntityMutated-delete']` | #9207's invariant — the record may only claim what the cache DID | with the record first (increment 4a's order, still on `origin/main` at `b77d24084`), a cache write that cannot be issued leaves the record committed over a live node |
| `REFUSAL VERDICT` (the destroy fails ⇒ no record, node survives) | #9207's divergence | pre-#9207 the `EntityMutated` delete record was already on disk when the write was refused, so the journal said *destroyed* while `get_point` returned the Point — the state §17.1's definition forbids (the cache must never be AHEAD of the truth) and only a destructive `rebuild_all` resolves |
| `COMMIT VERDICT` (the destroy lands ⇒ the append fails) | the ONE window this order still has, and its `record_committed=False` arm | the caller must be told the record is UNCONFIRMED (never "the cache still holds it" — the destroy landed). Nothing repairs that by itself (`recover_from_log` rebuilds only an empty graph; `reconcile` only counts): the repair is a DESTRUCTIVE `rebuild_all`, which RE-CREATES the Point from the journal — undoing the delete — after which it is re-issued. It is reported, not silent |
| `Point live after rebuild_all: 0` (happy path) | `derived = replay(journal)` | a delete whose record is missing is undone by the replay; the line shows the deletion is durable |

## Tests (the gates those lines are drawn from)

```console
$ TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/test_7951_delete_point_outbox.py -q      # every test green
```

⛔ The COUNT is deliberately not written here. It grew on every commit of this PR
that added a row, and each hard-coded total went stale within one push — the same
"stale flag that records a past state" failure the fleet rules warn about. What
is stable is the ROW SET and the fact that the file is green; re-run the command
rather than trusting a number. For the same reason the pre-change measurement
states WHICH rows are red, not how many tests there are.

`tests/test_7951_delete_point_outbox.py` carries the threat rows this door declares: T1 the ordering
(cache before record), T3e a `graph was deleted or replaced` abort whose KEY is gone is never read
back as a landed destroy (the read would auto-create the empty graph and answer `count(n)=0`, so the
door gates the read on `EXISTS` and surfaces the engine's own error instead), T2 no record for a
destroy the cache did not perform, T3/T8 a landed destroy
whose append failed (reported, and repaired only by a destructive rebuild), T3b a raise that does not
prove "nothing was destroyed" (the door reads the cache back, so a lost reply over an APPLIED write is
still recorded), T3d the landed-destroy arm's OWN reason (a lost reply over an APPLIED destroy whose append then
fails must not be told "the `DETACH DELETE` did not report a failure of its own", which that path
falsifies), T3f the presence gate paid on the TRANSPORT family too (a dropped connection over a gone
key: no read, no record, and the arm that claims nothing), T3g the STATE-INDEPENDENT arm (write
slot / MISCONF), which cannot have applied, so it pays NEITHER a probe nor a read, T3i a read-back that
answers NO ROWS (nothing was established about the cache, so it takes the "claim nothing" arm rather
than reporting a landed destroy), T3c the unreadable state (`destroy_verified=False`: no record, and the message is
asserted NOT to claim a store state it did not establish — it neither says the `DETACH DELETE` "did
not report a failure of its own" (that raise is how the arm is reached), nor that "the cache no longer
holds this Point", nor that a present node is "behind the TRUTH"; its PRESENT branch must send the
caller to a re-issue and must NOT send them to `rebuild_all`, which is the ABSENT branch's repair —
all three clauses were false in the first cut of the shared prose), T4 no record for a no-op delete, T5 the
record's declared shape, T6 the `:GraphEvent` subscriber row, T7 the journal-less (`None`) arm, T9 the
evidence probe's own instrumentation, T10 the delete door's own record in the refusal and the graph on
the cache clause. **T1/T2/T3/T3b/T3c/T3d/T3e/T3f/T3g/T3i/T8/T10 were measured RED against #9207's predecessor** (the
record-first order: at `origin/main` this test file is RED on every row below), and the reason
differs by row because the old order got each case wrong in its own direction — T10 on its
`update_point` record-noun assertion (answered `PointAdded` there), T3e/T3f on a presence gate with no
analogue at that head, T3g on the committed arm it raises instead of the engine's own error, T3i on the
empty-result default (the predecessor has no read-back at all, so the row is red on the raised type and the
record it leaves behind), and the rest as follows: T1 read `['journal', 'cache']` (the record first); T2/T3c/T8
read a committed `EntityMutated` op=delete over a cache that never performed the destroy; **T3** is
different in kind — it reddens on its PREMISE, not on a lie: at `b77d24084` the door appended first,
so a failed append skipped the `DETACH DELETE`, the node stayed live, and the message ("… skipped the
`DETACH DELETE` and the cache still holds this Point … Nothing was destroyed in the cache") was TRUE;
the test fails because the ordering it asserts (destroy landed) cannot happen at that head. **T3b**
reddens on the mirror error: the spy HAD deleted the node and the old committed-arm message announced
"the Point … was DESTROYED in the record and not in the cache" over a cache that no longer held it,
pointing the caller at a divergence that was not there. **T3d** reddens because the predecessor has no
`destroy_verified` distinction at all — it fails on the attribute (`AttributeError`), not on message
text: at that head the append ran FIRST, so on this path the append raised before the statement was
issued, no destroy applied, and the predecessor's own "the ordering skipped the `DETACH DELETE`"
message was true.
T10's delete-door message assertions are unchanged by the reversal (the headline and the record noun
are the door's own) — its `update_point` noun is a separate, additive assertion, and it is RED at
`b77d24084` for that reason; the docstrings name the mutation that reddens each row. T4-T7 and
T9 are declared guards: T4-T7 pass on both sides by design, and T9 reddens if the probe restores a
BOUND method onto the guarded handle's CLASS, which would silently misroute every later section's
reads and writes to section 1's graph.

## The residuals, shown rather than hidden

1. **The raw handle (`proj.g.query`) is DETECTED but not yet REFUSING** — the recorded `OVERRIDES:`
   departure on `#7951`; a blanket refusal would break the product's own live write path (`#8389`).
   #9124 part 1 counts and logs an undeclared write here; part 2 arms the refusal. `delete_point`
   itself is NOT one of them any more: #9206 declares its apply-first lane, so it completes under an
   armed handle (`_APPLY_FIRST_DECLARED_DOORS`).
2. **`delete_entity` (the generic entity delete) is still apply-first** — it `DETACH DELETE`s per
   matched canonical label and journals afterwards, so it still carries the resurrection window.
   Closing it needs a pre-read per label (to journal only the label that matched). Named in
   `tortoise/sdk.py`'s module-top KNOWN DEVIATIONS and in plan §7 increment 4.
3. **`PointRetracted` stays after the delete** — it is the `:GraphEvent` store's statement of what the
   call DID (the unconditional `deleted: true` marker), so stating it before the write would publish a
   destruction that may not happen. §17.1's scope is truth-vs-derived; `:GraphEvent` is truth-vs-truth
   (`docs/architecture/STORAGE-ARCHITECTURE.md` §3), unchanged here.
4. **The ordering is still a two-store sequence, not one transaction** — §17.1's mechanism (1) in its
   atomic form (the record and the projection in one Postgres transaction) is increment 2b, not this.
   #9207 chose WHICH window that sequence has: a landed destroy whose append failed, instead of a
   committed record the cache could not honour. Two things about that window, stated rather than
   glossed: it is reported only when the door can TELL (a raise from the statement is read back, so a
   lost reply over an applied write is recorded, and a destroy whose state cannot be re-read raises
   with no record written), and its only repair is a DESTRUCTIVE `rebuild_all` — `recover_from_log`
   rebuilds only an empty graph and `reconcile` only counts — which RE-CREATES the Point, undoing the
   delete; the caller re-issues afterwards.
