---
title: "#7951 increment 2a — the outbox on `update_point`: append the revision, then write the cache"
type: engineering
domain: platform
doc_status: draft
created: 2026-10-17
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7951, #7950, STORAGE-ARCHITECTURE.md §17.1, tortoise/sdk.py update_point, FalkorDB cache"
---

# #7951 increment 2a — the outbox ordering on `update_point`

**Parent epic:** `#7879` · **Issue:** `#7951` (P0-blocker) · **Capstone:** `#7950`
**Architecture of record:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner ruling, 2026-10-08),
§17.1 (the write-path rule)
**Adopted decision (do not re-open):** issue comment `6170585712` — mechanism (1), the atomic write **in
the OUTBOX form**, plus mechanism (2), the per-projection watermark `last_applied_seq`.
**Predecessor:** `docs/plans/2026-10-14-7951-atomic-write-outbox.md` (increment 1, `create_point`, `#8697`).
**OVERRIDES (carried from `#7951`):** the convergent single-write-authority default — the raw handle
(`proj.g.query` / `proj.g._g.query` / `proj.db.select_graph`) remains an ALLOWED direct cache write until
the atomic write lands, because that handle is the product's own live write path and a blanket refusal
breaks it (`#8389`). Deliberate and temporary; closure route is mechanism (1).

**Status measured at:** `origin/main` `fa15f420b` (2026-10-17). Branch `fix/7951-write-path-rule`.

---

## 1. Why this increment

The adopted decision names the shape of §17.1's remaining work: **one atomic write, in the OUTBOX form —
the journal append is the transaction, and the cache is projected from the record.** Increment 1 routed
`create_point` through it. The plan of record's §7 names the next unit as *"increment 2 — the record
projection on the live path + `last_applied_seq`; the dedup-hit/`update_point` door"*.

**This increment takes the REVISION-DOOR half of that unit:** `update_point` is the SDK's most common
mutation, and it was wholly apply-first — it wrote the node and emitted `PointRevised` on its last line.
`_emit_event` is deliberately best-effort (`#4240`: a journal failure must never crash a write that DID
persist), so a failed append left the **cache ahead of the journal with no repair path**: `rebuild_all`
replays the OLD value and the revision is gone. That is window (a) of the ordering contract — the one a
replay cannot undo.

## 2. Scope of THIS increment (one deliverable)

**`update_point`'s UNGUARDED revision arms append the `PointRevised` record BEFORE the cache write, and
the journal payload is built from the write intent (`props`), not read back from the cache.**

### What changes, precisely

1. the `PointRevised` payload is built **before** the writes (it already came from `props`, so no value
   synthesis moved — unlike increment 1's `createdAt` finding);
2. the append happens before the `SET`s; **a failed append raises `WriteNotLandedError`** (per-call
   `_emit_event` outcome, not the shared counter delta — `#5612`) and the cache is **NOT** written;
3. the cache writes and the tag sync are otherwise **unchanged** — same statements, same order among
   themselves;
4. `WriteNotLandedError` gained `operation=` so the message names the door that raised it
   (`"create_point"` default keeps every existing message byte-identical).

## 3. Ordering contract

Already recorded at the module top of `tortoise/sdk.py` and in §17.1: **journal first, then project** —
die between append and cache ⇒ the cache lags, replay heals it; die between cache and append ⇒ the cache
is ahead of truth with no repair path.

**⛔ The PROMOTE arm (`status='live'`) is EXEMPT and stays apply-first.** Its `SET` is guarded by
`WHERE (n.status IS NULL OR n.status = 'draft')` and the door **raises** when the guard matches nothing
(an illegal transition, or a vanished node). Journalling first would record a promotion the graph
**refused**, and a replay would apply it — truth claiming a state the live write never reached, which is
strictly worse than the window this increment closes. This is the contract's exception 2 shape (the record
can only be stated AFTER the write) and it is pinned in both directions.

## 4. Adversarial Threat Surface

| # | threat class | the test that covers it | fails pre-change? |
|---|---|---|---|
| T1 | the revision cache write happens before the journal append | `test_update_point_appends_the_revision_before_the_cache_write` (positional spy on `_emit_event` vs the `SET n += $props` statement) | **YES** |
| T2 | a failed append still writes the cache (derived ahead of truth) | `test_a_failed_append_does_not_write_the_cache_on_update` (append raises ⇒ `WriteNotLandedError`, `operation="update_point"`, property absent) | **YES** |
| T3 | a committed revision does not survive a failed cache write | `test_a_failed_cache_write_leaves_the_revision_replayable` (force the `SET` to fail, then `rebuild_all` materialises `new_content`) | **YES** |
| T4 | the promote arm is silently converted to journal-first | `test_the_promote_arm_stays_apply_first_a_recorded_deviation` (deviation PIN — passes pre-change) | no (pin) |
| T5 | a REFUSED promote is journalled (a replay resurrects a terminal point) | `test_a_refused_promote_journals_no_revision` (guard — passes pre-change; reddens if the emit is hoisted) | no (guard) |
| T6 | a double append (two `PointRevised` per update) | `test_update_point_appends_exactly_one_revision` (mutation guard) | no (guard) |

**Measured (2026-10-17, throwaway worktree at `fa15f420b`, embedded lane):** `3 failed, 3 passed` —
T1/T2/T3 red exactly on the pre-fix behaviour they name; T4/T5/T6 green by design, and that is stated in
each docstring rather than implied.

## 5. Wiring Check

| touch point | type | covered by | status |
|---|---|---|---|
| `update_point` append-before-cache | code | `tests/test_7951_update_point_outbox.py` (T1, T2, T6) | done |
| revision payload from the write intent | code | same (T3 asserts the committed record's `new_content`) | done |
| replay healability | code | same (T3) | done |
| promote arm stays apply-first | code | same (T4, T5) | done |
| `WriteNotLandedError.operation` | code | same (T2) | done |
| test-file classification | config | `config/ci-surfaces.yml` — six-surface dual-registration + a `durations` row (`--integrity` green) | done |
| MCP / public SDK surface | none | `tools/surface_manifest.py check` OK (82 rows); no tool or public method added/renamed | verified |
| raw guarded handle `proj.g.query` | **open** | — | **NOT enforced** (the §17.1 residual; `#7951` stays OPEN) |

## 6. Residuals — declared, not absorbed

1. **The raw guarded handle stays open** for the other ~287 sites; this routes one door's ORDER, not the
   handle. The `OVERRIDES:` line above remains in force.
2. **The record projection on the live path and `last_applied_seq` are NOT here.** The other half of
   increment 2 (`record_projection.replay` on the live path, journal + record in one Postgres
   transaction; mechanism (2)) has **zero production call sites** still
   (`docs/evidence/7950-capstone/probes/s2_wiring_census.py`: 0 for `record_projection.replay`,
   `cache_projection.read_record_view`, `reconciliation.reconcile`, `rebuild_from_record`).
3. **The promote arm remains apply-first**, with the reason above and a pin in both directions. Closing it
   needs a guarded write whose outcome is known before the record is stated (pre-read + TOCTOU, or a
   conditional append), which is a design decision this increment does not take.
4. **The door still writes through the raw handle, so it opens no `_live_write_scope`.** A scope around a
   raw handle authorizes nothing (the guard covers `FalkorProjection.apply`/`.query`) — the same note
   `create_point`'s scope makes about itself. Increment 3 (the cache driven from the record) is where the
   door declares itself.
5. **`:Object` version bump / `updatedAt` still are not replayable from the record** (live
   `version = coalesce(version,0)+1`, replay no clause; replay stamps its own `updatedAt`). Pre-existing
   live-vs-replay parity gaps, unchanged and not claimed closed here.
6. **A revision for a NON-EXISTENT id is still journalled** (the `MATCH … SET` matches nothing, the call
   returns `{}`). Pre-existing; this increment moves *when* the record is written, not *whether*.

## 7. Next increments (recorded so the next lane does not re-derive)

- **2b** — the record projection on the live path + `last_applied_seq` (journal + record in ONE Postgres
  transaction); then the promote arm.
- **3** — the cache driven from the *record*, and the fold made the live Point writer (requires `#8699`
  first: `_POINT_DENY` drops the `#2199` baseline fields on replay).
- **4** — the remaining raw-handle doors (`_sync_tags`, `_link_source`, deletes, `onboarding/state.py`,
  `onboarding/seed.py`, `hosted_api.py`, `__main__.py`, connectors), then gate the handle.
  **LANDED SO FAR: the `delete_point` DOOR (increment 4a)** —
  `docs/plans/2026-10-18-7951-delete-point-outbox.md`: the append is the commit point of the hard
  delete, so a delete the journal never recorded cannot be performed. **STILL OPEN:** `delete_entity`
  (needs a per-label pre-read) and the rest of this list.
- **5** — wire `reconcile` as the S2 backstop (observed, not merely checkable).

## 8. Rejected alternatives

1. **Journal-first on the promote arm too.** REJECTED — see §3: the guarded `SET` can refuse, so the
   record would claim a promotion the graph refused.
2. **A `_live_write_scope` around the raw-handle writes.** REJECTED for this increment — it authorizes
   nothing on the raw handle, so it would be a ~90-line reindent of statements it does not affect,
   implying an enforcement that is not there. Increment 3 adds it when the writes move to the fold.
3. **A blanket refusal on the guarded handle.** REJECTED — `#8389` verbatim; carried as the recorded
   `OVERRIDES:` departure.
