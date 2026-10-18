---
title: "#7951 increment 4a — the outbox on `delete_point`: record the destruction, then delete"
type: engineering
domain: platform
doc_status: draft
created: 2026-10-18
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: "#7951, #7950, STORAGE-ARCHITECTURE.md §17.1, tortoise/sdk.py delete_point, FalkorDB cache"
---

# #7951 increment 4a — the outbox ordering on `delete_point`

**Parent epic:** `#7879` · **Issue:** `#7951` (P0-blocker) · **Capstone:** `#7950`
**Architecture of record:** `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner ruling, 2026-10-08),
§17.1 (the write-path rule)
**Adopted decision (do not re-open):** issue comment `6170585712` — mechanism (1), the atomic write **in
the OUTBOX form**, plus mechanism (2), the per-projection watermark `last_applied_seq`.
**Predecessors:** increment 1 (`create_point`, `#8697`), increment 2a (`update_point`, `#8830`) —
`docs/plans/2026-10-14-7951-atomic-write-outbox.md`, `docs/plans/2026-10-17-7951-update-point-outbox.md`.
**OVERRIDES (carried from `#7951`):** the convergent single-write-authority default — the raw handle
(`proj.g.query` / `proj.g._g.query` / `proj.db.select_graph`) remains an ALLOWED direct cache write until
the atomic write lands, because that handle is the product's own live write path and a blanket refusal
breaks it (`#8389`). Deliberate and temporary; closure route is mechanism (1).

**Status measured at:** `origin/main` `a03c5d1ac` (2026-10-18). Branch `feat/7951-tm`.
**Review:** the parent lane runs the review gate (`[VGATE]`); this lane had no `task` tool, so no
verifier dispatch was possible here and the plan carries **no** clean verdict of its own.

---

## 1. Why this increment

Increments 1 and 2a routed the CREATE and the REVISION doors. The plan of record's §7 names the delete
door among increment 4's remaining raw-handle/apply-first sites. It is not equivalent to the other two,
and that is the whole argument for taking it first among the deletes:

> **On a create or a revision, the apply-first window LOSES a write. On a delete it UNDOES one.**

`delete_point` `DETACH DELETE`d the `:Point` and appended its `EntityMutated` op=delete record on the
next line. `_emit_event` is deliberately best-effort (`#4240`) and never raises, so a failed append left
the node **gone from the cache and absent from the truth**: `rebuild_all` re-folds the `PointAdded`
line and the deleted Point returns as a live claim. A user's deletion silently coming back is a
correctness defect in the direction §17.1 exists to forbid, and the record is the ONLY durable carrier
of the destruction (`PointRetracted` is a `:GraphEvent`-only subscriber row — `#3300`).

## 2. Scope of THIS increment (one deliverable)

**`delete_point` appends its `EntityMutated` op=delete record BEFORE the `DETACH DELETE`, and a failed
append refuses the delete instead of performing an unrecorded destruction.**

### What changes, precisely

1. `_journal_entity_mutation` returns `_emit_event`'s **per-call outcome** (`bool | None`) instead of
   `None`. Backwards compatible: every existing caller ignores the return; the payload, the envelope
   and the best-effort failure handling are byte-identical.
2. `delete_point`: after the existence probe and the pre-delete reads (neighbour capture, tag-edge
   probe — all reads), the `EntityMutated` op=delete record is appended. `False` (a configured
   journal's append RAISED) ⇒ `WriteNotLandedError(..., operation="delete_point")` and **no**
   `DETACH DELETE`.
   **The refusal's TEXT is the door's own** (review round 3): on a delete, `False` means the append
   did not CONFIRM, not that the record is absent — a buffered `EventLog.append` can land the record
   and then raise — so the delete door gets its own `_headline`/`_cause`/`_contract` in
   `WriteNotLandedError` instead of sharing `create_point`'s, which said "was not committed" and "no
   rebuild record was committed for it" about a record that may be durably on disk. That string is
   the MCP wire contract (`mcp_server.tortoise_delete_point` → `_safe` → `{"error": …}`).
3. `PointRetracted` (`:GraphEvent` store) stays **after** the delete — it states what the call DID, and
   it is truth-vs-truth, not truth-vs-derived (§3).
4. The module-top KNOWN DEVIATIONS paragraph was **corrected**: it claimed `delete_point` conformed
   (it did not) and that "every other live door reaches the journal FIRST" — measurably false. It now
   names the four apply-first doors verified line by line (`_delete_entity`, `_session_event_write`,
   `_update_entity`, `create_operator`) and states that a full inventory is owed rather than implying
   one exists. A contract comment that overstates its coverage is how the next lane inherits a false
   premise.

## 3. Ordering contract (unchanged, applied)

**Journal first, then project** — module top of `tortoise/sdk.py`, argued from the two crash windows.
The divergence class each window produces DEPENDS ON THE DOOR, and this plan's earlier draft
generalized the delete's class to all of them (review round 2):

| door | die between append and cache | replay | die between cache and append |
|---|---|---|---|
| `create_point` / `update_point` | `missing_in_cache` — the record exists, the node does not | materialises the write | ahead of truth, no repair path |
| `delete_point` | **`extra_in_cache`** — the cache holds a node the record no longer has (§17.1's second-writer signature, *not* a lag) | **removes** the node, destructively | resurrection on the next rebuild |

On a delete the second window's harm is resurrection, which is strictly worse than loss and is why
the delete door is worth a unit of its own.

**Why journal-first is sound here even though the delete is conditional-looking.** The existing
existence probe runs first, so a no-op delete journals nothing (T4). A concurrent delete between the
probe and the `DETACH DELETE` makes the record a truthful *"this id is deleted"* whose fold is a 0-row
`DETACH DELETE` — the record's end state, not a fold miss (the same shape `SessionDeleted`'s fold
documents). This is **not** contract exception 2 (post-apply snapshot): the payload is fully formed
before the write.

**The contrast case is explicit and stays.** `hosted_api._sweep_orphaned_writes` /
`_delete_session_sync` journal **after** their guarded deletes, gated on the guarded delete's own row
count, and that is correct: those `DETACH DELETE`s are CONDITIONAL (`WHERE present = 0`), so the record
cannot be stated before the write without claiming a destruction the guard may refuse. `delete_point`'s
delete is unconditional once the probe passes, so the record can and must precede it.

## 4. Adversarial Threat Surface (declared)

| # | threat class | the test that covers it | fails pre-change? |
|---|---|---|---|
| T1 | the `DETACH DELETE` happens before the record is appended | `test_delete_point_appends_the_record_before_the_cache_delete` (positional spy on `_journal_entity_mutation` vs the delete statement) | **YES** — `['cache', 'journal']` |
| T2 | a failed append still destroys the node | `test_a_failed_append_does_not_delete_the_point` (append raises ⇒ `WriteNotLandedError(operation="delete_point")`, node still live, no record) | **YES** — `DID NOT RAISE`, node destroyed |
| T2 (text) | the refusal states the opposite of its own state (`False` reported as "not committed" / "no rebuild record was committed") | same test: the NEGATIVE assertions `"not committed" not in msg`, `"no rebuild record was committed" not in msg`, `"was not applied" not in msg` — a positive substring check cannot catch an overclaim, which is why the old `"did not confirm"` assertion passed while the test's premise was contradicted | **YES** — red on the shared create-shaped text (review round 3) |
| T3 | a committed record does not survive a failed cache delete (so the window is a resurrect) | `test_a_committed_delete_record_is_healed_by_replay` (force the delete to fail ⇒ record stands ⇒ `rebuild_all` removes the node) | **YES** — `[]` instead of one delete record |
| T4 | journal-first journals a destruction that never happened | `test_a_missing_point_journals_nothing` (unknown id ⇒ `False`, zero records) | no (guard) |
| T5 | the record's declared shape drifts in with an ordering fix | `test_the_delete_record_keeps_its_declared_shape` (one record, `op=delete`, `label=Point`, no `state`, no JSONL `PointRetracted`) | no (guard) |
| T6 | the `:GraphEvent` subscriber surface silently changes | `test_the_graph_event_row_is_still_written_after_the_delete` (`poll_events` still reports `PointRetracted` with `deleted: true`) | no (guard) |
| T7 | the `None` outcome (no journal / nothing to log — the DEFAULT embedded shape) is read as a refusal, so every delete on every journal-less SDK fails closed | `test_a_journal_less_sdk_still_deletes` (journal-less SDK ⇒ `delete_point` returns `True` and the node is gone) | no (guard) |
| T8 | an append that LANDS and then raises is reported as committed | `test_an_append_that_lands_then_raises_is_reported_as_unconfirmed` (append writes then raises ⇒ refusal with `record_committed=False`, `DETACH DELETE` not issued, record on disk, replay removes the Point) | **YES** — the pre-change door ignored `_emit_event`'s outcome, so no refusal is raised at all |
| T9 | the evidence probe restores a BOUND method onto the guarded handle's class, so sections 2-4 silently address section 1's graph | `test_the_evidence_probe_rebinds_the_guarded_handle_per_instance` (patch → restore ⇒ the class attribute is the class FUNCTION again, and a second SDK's write is visible only on its OWN underlying handle) | **YES** (against the pre-fix PROBE, not the door) — its restore assigned `proj.g.query` (bound) to the class, so SDK #2's write lands in SDK #1's graph |
| T10 | the refusal names a record this door never writes (`PointAdded`) and attaches the graph name to the RECORD clause, sending a caller to the store that does not hold the destruction | `test_the_message_names_the_doors_own_record_and_graph` (the committed and unconfirmed arms both say `EntityMutated op=delete`, never `PointAdded`, and the graph sits in "in the record and not in the cache on graph 'cache_graph'"-style text only on the cache clause) | **YES** — red on the pre-change hardcoded `PointAdded` wording and misplaced `_where` |

**Measured** (`TORTOISE_TEST_CARVE_OUT=1`, embedded lane): `5 failed, 5 passed` on the pre-change
source (`origin/main`'s `tortoise/sdk.py` and `tortoise/exceptions.py` — T1/T2/T3/T8/T10 red,
T4-T7/T9 green), `10 passed` after. The
guards (T4-T7) pass on both sides by design and say so in their docstrings rather than implying
detection power. T9 is a guard on the EVIDENCE ARTIFACT, not on the door: it is red against the
pre-fix probe (whose restore put a BOUND method on the guarded handle's class) and green against the
fixed one.

## 5. Wiring Check

| touch point | type | covered by | status |
|---|---|---|---|
| `delete_point` append-before-delete | code | `tests/test_7951_delete_point_outbox.py` (T1, T3) | done |
| failed append ⇒ no delete ⇒ `WriteNotLandedError` | code | same (T2) | done |
| the unconfirmed arm's caller-facing text is the DELETE door's own (never "not committed") | code | same (T2, negative assertions); `tortoise/exceptions.py` `WriteNotLandedError` | done |
| `_journal_entity_mutation` returns the per-call outcome | code | same (T2); `tests/test_unjournaled_mutation_class.py` (45 tests, unchanged shape) | done |
| the no-op delete still journals nothing | code | same (T4) | done |
| the journal-less (`None`) outcome is not a refusal | code | same (T7) | done |
| an append that LANDS then raises is reported as unconfirmed | code | same (T8) | done |
| the evidence probe measures each SDK's OWN graph | evidence | same (T9); `docs/evidence/7951-delete-point-outbox/probes/delete_order_probe.py` | done |
| module-top KNOWN DEVIATIONS corrected | docs | `tortoise/sdk.py` module top | done |
| the message names this door's own record, and the graph attaches to the cache clause | code | same (T10) | done |
| test-file classification | config | `config/ci-surfaces.yml` — six-surface dual-registration + a `durations` row; `python tools/ci_selection.py --integrity` green | done |
| executable demonstration | evidence | `docs/evidence/7951-delete-point-outbox/` (probe + verbatim log, exit 0; re-measured at this head — section 3 now prints the `record_committed=True` arm) | done |
| MCP / public SDK surface | none | `tools/surface_manifest.py check` OK (82 rows); no tool or public method added/renamed | verified |
| raw guarded handle `proj.g.query` | **open** | — | **NOT enforced** (the §17.1 residual; `#7951` stays OPEN) |

## 6. Residuals — declared, not absorbed

1. **The raw guarded handle stays open** for the other ~287 sites; this routes one door's ORDER, not the
   handle. The `OVERRIDES:` line above remains in force.
2. **`delete_entity` (the generic entity delete) is still apply-first.** It `DETACH DELETE`s per matched
   canonical label and journals each match afterwards; it therefore still carries the resurrection
   window this increment closes on `delete_point`. Closing it needs a **pre-read per label** (journal
   only the label that matched) — a design choice this increment does not take, and the probe shows the
   current order so the next lane starts from a measurement rather than a grep.
3. **`_session_event_write`, `_update_entity`, `create_operator` are still apply-first** — named in the
   module-top KNOWN DEVIATIONS. A full inventory of the apply-first doors is owed; the paragraph no
   longer claims otherwise.
4. **`PointRetracted` remains a post-delete `:GraphEvent` write** (see §2.3). Its move to the record is
   out of §17.1's scope and is not claimed.
5. **The record projection on the live path and `last_applied_seq` (mechanism 2) are NOT here** — still
   zero production call sites (`docs/evidence/7950-capstone/s2_wiring_census.log`). Increment 2b.
6. **This is still a two-store sequence, not one transaction.** §17.1's mechanism (1) in its atomic form
   is what removes the residual gap between the committed record and the delete; this increment makes
   that gap replay-healable in the safe direction.

## 7. Next increments (recorded so the next lane does not re-derive)

- **2b** — the record projection on the live path + `last_applied_seq` (journal + record in ONE Postgres
  transaction).
- **3** — the cache driven from the *record*, and the fold made the live Point writer (requires `#8699`
  first: `_POINT_DENY` drops the `#2199` baseline fields on replay).
- **4b** — the remaining apply-first doors, starting with `delete_entity` (needs the per-label pre-read),
  then `_session_event_write`, `_update_entity`, `create_operator`.
- **5** — wire `reconcile` as the S2 backstop (observed, not merely checkable).

## 8. Rejected alternatives

1. **Journal the delete AFTER the `DETACH DELETE`, gated on the delete's row count** (the shape
   `_sweep_orphaned_writes` uses). REJECTED here: that shape exists because those deletes are
   CONDITIONAL and the record must not claim a destruction the guard refused. `delete_point`'s delete is
   unconditional once the probe passes, so gating on its result would preserve exactly the window this
   increment closes — the unrecorded destroy.
2. **A `_live_write_scope` around the delete's raw-handle writes.** REJECTED — a scope authorizes
   nothing on the raw handle (the guard covers `FalkorProjection.apply`/`.query`), so it would imply an
   enforcement that is not there. Increment 3 is where the door declares itself.
3. **A blanket refusal on the guarded handle.** REJECTED — `#8389` verbatim; carried as the recorded
   `OVERRIDES:` departure.
4. **Widening the record to a new event type for a Point hard delete.** REJECTED — `EntityMutated`
   op=delete already carries it (`#3299`/`#3300`), and a new type would be a journal-vocabulary change
   smuggled in with an ordering fix.
