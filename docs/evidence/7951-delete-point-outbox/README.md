---
title: "#7951 increment 4a — the outbox ordering on `delete_point` (evidence)"
type: evidence
domain: platform
doc_status: final
created: 2026-10-18
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7951, #7950, STORAGE-ARCHITECTURE.md §17.1, tortoise/sdk.py delete_point, FalkorDB cache"
---

# #7951 increment 4a — the outbox ordering on `delete_point`

**Measured at:** branch `feat/7951-tm` off `origin/main` `a03c5d1ac`, 2026-10-18. Embedded lane
(`TORTOISE_DB_URI` unset, `TORTOISE_TEST_CARVE_OUT=1`) — no server needed, so the demonstration is
reproducible by any lane.

**What this proves, and what it does not.** It proves the §17.1 **ordering** on the hard-delete door:
the `EntityMutated` op=delete record is appended before any `DETACH DELETE`, a failed append refuses
the delete, and a committed record whose cache delete did not land is healed by a replay. It does
**not** claim §17.1 is closed — the raw handle is still open, and `delete_entity` (the generic entity
delete) is still apply-first; both are shown in the probe's own last section. Plan of record:
`docs/plans/2026-10-18-7951-delete-point-outbox.md`.

## The demonstration

```console
$ env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
    .venv/bin/python docs/evidence/7951-delete-point-outbox/probes/delete_order_probe.py
journal_backed (guard armed): True
delete_point order:              ['journal-EntityMutated-delete', 'cache-DETACH-DELETE']
ORDERING VERDICT: JOURNAL-FIRST
delete records committed:        1
Point live after the delete:     0
Point live after rebuild_all:    0  (0 = the deletion is durable)
failed append raised:            WriteNotLandedError (operation='delete_point', record_committed=False)
Point live after refused delete: 1  (1 = nothing was destroyed)
delete records committed:        0
REFUSAL VERDICT: NOTHING DESTROYED, NOTHING SKIPPED FROM THE RECORD
failed cache delete raised:      WriteNotLandedError (operation='delete_point', record_committed=True, record='EntityMutated op=delete')
record committed despite failure: 1
caller told COMMITTED (no blind re-issue): True
Point live before rebuild:       1
Point live after rebuild:        0
COMMIT VERDICT: COMMITTED + REPLAY-HEALABLE
RAW HANDLE LANE (proj.g.query):  SUCCEEDED, rows = [['via-raw-handle']]
delete_entity order:             ['cache-DETACH-DELETE', 'journal-EntityMutated-delete', 'cache-DETACH-DELETE', …]
RESIDUAL: the raw handle is NOT closed, and `_delete_entity` is still apply-first — both recorded on #7951; increment 4a routes ONE delete door (`delete_point`).
```

**Raw output:** `delete-order-probe.log` (verbatim — **re-measured** after the review that found the
earlier copy still carried the pre-`60dc70e` `RuntimeError`; the shipped live engine also logs
`#4997`/pack-registry advisories and the `#2943` snapshot-dir note, which the headline block omits).
Exit code `0`. Those advisory lines carry the paths of the worktree the log was measured in
(`pack registry /tmp/cell-t3/.worktrees/repair-c2a/packs`, plus the per-run `mkdtemp` dirs) — they
are the engine's own report, not probe output, and are left unfiltered so the log stays verbatim.
Re-running the probe therefore differs from it in those paths only (the `mkdtemp` names are
per-run); every measured line is stable.

## What each line means

| line | it is the failing criterion of… | how it would fail |
|---|---|---|
| `order: ['journal-EntityMutated-delete', 'cache-DETACH-DELETE']` | §17.1's ordering | on `origin/main` the order is reversed (`cache` first) — the door deleted the node and then appended the record |
| `REFUSAL VERDICT` (append fails ⇒ node survives, no record) | §17.1's resurrection window | pre-fix, a failed append left the node **destroyed and unrecorded**, so `rebuild_all` re-folded the `PointAdded` line and the Point came back live |
| `COMMIT VERDICT` | §17.1's crash window, and its `record_committed=True` arm | with the `DETACH DELETE` forced to fail after the append, the record stands and a later `rebuild_all` removes the node — the cache is `extra_in_cache` (a node the record no longer has, `tortoise/reconciliation.py`'s second-writer class), NOT a lag (`missing_in_cache` is the lag direction). The caller is handed `WriteNotLandedError(record_committed=True, record='EntityMutated op=delete')`, not the bare engine error: a bare error reads as "the delete did not happen" and invites a blind re-issue that appends a SECOND delete record for one intent |
| `Point live after rebuild_all: 0` (happy path) | `derived = replay(journal)` | a delete whose record is missing is undone by the replay; the line shows the deletion is durable |

## Tests (the gates those lines are drawn from)

```console
$ TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
    tests/test_7951_delete_point_outbox.py -q
10 passed
```

`tests/test_7951_delete_point_outbox.py` carries the threat rows this increment declares: T1 ordering,
T2 failed-append refusal, T3 committed-record healability, T4 no record for a no-op delete, T5 the
record's declared shape, T6 the `:GraphEvent` subscriber row, T7 the journal-less (`None`) arm, T8 an
append that lands then raises, T9 the evidence probe's own instrumentation, T10 the delete door's own
record in the refusal and the graph on the cache clause. **T1/T2/T3/T8/T10 were measured RED on the
pre-change source** (5 failed, 5 passed: `['cache', 'journal']`; `DID NOT RAISE WriteNotLandedError`;
`[]` instead of `[('delete', 'Point')]`; no refusal at all for a landed-then-raised append; the
`PointAdded` wording this door never writes) — the docstrings name the mutation that reddens each.
T4-T7 and T9 are declared guards: T4-T7 pass on both sides by design, and T9 reddens if the probe
restores a BOUND method onto the guarded handle's CLASS, which would silently misroute every later
section's reads and writes to section 1's graph.

## The residuals, shown rather than hidden

1. **The raw handle (`proj.g.query`) is still open** — the recorded `OVERRIDES:` departure on
   `#7951`; a blanket refusal would break the product's own live write path (`#8389`).
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
