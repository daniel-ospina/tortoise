# #3689 — Operator `annotator_*` props survive `rebuild_all` (journal durability)

**Level:** task · **complexity:** standard · **Gate 0:** category A (silent destruction of work — a
mutation that is never replayed, so wipe+rebuild erases committed state with no error).
**Domain:** (not adversarial) — this is durability/journal coverage, not a gate an attacker can
defeat. Verifier gates are unbounded by threat surface; standard review cycles apply.

## Problem (confirmed by reproduction)

`annotate_operator` writes four epistemic dims (`annotator_bias`, `annotator_precision`,
`annotator_consistency`, `annotator_directness`) onto an Operator `Point`. The live write lands,
but `rebuild_all()` erases the props **silently** (no warning, no error).

Reproduced (`/tmp/repro3689.py`, docker lane):
```
LIVE: [0.4, 0.3, 0.2, 0.1]
journal: PointAdded, PointAdded, OperatorAdded, PointRevised{annotator_* present}
         (no OperatorAnnotated line at all)
AFTER-REBUILD: [None, None, None, None]
```

Two independent defects, both on the write/replay seam:

1. **`OperatorAnnotated` is never journaled.** `annotate_operator` (sdk.py ~6149; emit ~6172) calls
   `_emit_event("OperatorAnnotated", {…})` positionally — no `id=`, no `point=`. The record reaches
   the graph-event store (type is in `_GRAPH_EVENT_TYPES`) but hits `_emit_event`'s JSONL
   early-return `if point is None and id is None: return` and **never writes the JSONL line**
   (sdk.py ~2345).
2. **The journal record that *does* carry the data is not folded.** `annotate_operator` delegates to
   `update_point(..., annotator_bias=…)`, whose `PointRevised` emit carries the dims as extras — so
   the durable record exists. But `_revise_point` (projection/__init__.py ~3801) folds only
   `content` / `embedding`, and `_apply_one` (the pure fold, ~1142) folds only content/context. The
   dims are therefore dropped on every replay path.

This is the **#3299 class** (live mutation, no effective journal entry → destroyed by wipe-and-replay)
on the operator-annotate surface.

## Why both halves must be fixed (not just the emit)

Fixing only the emit (defect 1) does **not** make existing data durable: journals already on disk from
the `#3299` era carry `PointRevised{annotator_*}` with **no** `OperatorAnnotated` line. A rebuild of
such a journal would still erase the props. Conversely, fixing only `_revise_point` (defect 2) leaves
the explicit annotation event out of the JSONL, so the journal is not self-describing and a future
change to `update_point`'s emit shape would silently regress durability again.

**Decision:** fix the write path (emit an explicit `OperatorAnnotated` record with the data) **and**
fold it on all three replay paths, **and** fold the annotator keys already present on `PointRevised`
(legacy-journal compatibility + covers direct `update_point(annotator_*=…)` writers). All folds are
idempotent property SETs, so the redundancy is benign.

### Relationship to #2946 — explicit scope boundary, not a silent narrowing

The mechanism (`_revise_point` drops `PointRevised`-carried extras) is **generic**: the same one-liner
loses `mitigation_strength`, arbitrary scalars, and (per #2946's own subject) `tags`. **#2946 is the
ownership of the general class**, and its Research Needed item 2 says *"do not fix only `tags` if the
same one-line mechanism covers the class"* — i.e. do not leave a narrow special case in place of the
general mechanism.

This fix is therefore **deliberately scoped to the annotator dims**, and says so in code and in an
issue comment on #2946. It is not a general `PointRevised`-extras fold, because the general fold has
unresolved design questions **owned by #2946**, not by this lane:

- `_POINT_HANDLED` intentionally skips real mutable props (`confidence`, `status`, `direction`,
  `label`, `validFrom/To`, …) whose replay semantics on revision are exactly what #2946 must decide;
  blindly replaying them from a revision event would change lifecycle behaviour on the main rebuild
  path (`test_entity_delete_rebuild.py`'s survivor rules, `status` terminality, promote ordering).
- `tags` is list/edge-carried: `_persist_extra_props` denies undeclared lists (#2795) and the
  creation-snapshot edge wiring in pass 2 is the ordering hazard #2946's Research Needed item 1 names.
- A generic fold would need a shared skip-set for the pure `_apply_one` fold (which has no access to
  the projection's `_POINT_HANDLED`/`_POINT_DENY`), another general-design decision.

So: **annotator dims are closed here; the #2946 residue is explicitly left open and surfaced.** A
comment lands on #2946 recording that #3689 closed the annotator subset only (posted during the
review cycle; see the [REVIEW] comment on #2946).

### Preserve the `:GraphEvent` payload contract (P1)

`OperatorAnnotated` is in `_GRAPH_EVENT_TYPES`, so its **graph-store payload is a shipped contract**:
`docs/event-catalog.md` line 18 declares `id, bias, precision, consistency, directness`, and that is
what `events_poll` consumers read. `_emit_event` takes the graph payload from the positional `payload`
only when it is present. The fix therefore **keeps the positional payload as-is and adds `id=id` plus
the `annotator_*` extras for the JSONL branch only** — the established `PointRetracted` pattern
(`sdk.py` `_emit_event("PointRetracted", {"id": id}, id=id)`). It does **not** replace the payload with
`id=`+`point=` (issue #3689's shorthand wording would silently rename the polled fields), and does not
use `point=` (which would reduce the graph payload to `{id}`). A test pins the polled payload shape so
this cannot silently drift; no `docs/event-catalog.md` change is needed.

## Alternatives considered

| Option | Verdict |
|---|---|
| A. Emit only (fix defect 1) | Rejected — legacy/in-flight journals still lose the props. |
| B. Special-case the read (`_revise_point` only) | Rejected as the *whole* fix — the journal would still lack the annotation record; the read would depend on `update_point`'s incidental extra-field shape. Kept as one half (legacy compatibility). |
| C. Snapshot annotator props into the pass-1a `#548` synthetic-event snapshot | Rejected — treats the symptom (survives a rebuild only when the point already exists in the graph at rebuild time) and does not fix the journal; a rebuild after a wipe into a fresh graph would still lose them. |
| **D. Explicit `OperatorAnnotated` journal record + fold in all replay paths + annotator-key fold on `PointRevised`** | **Chosen** — makes the journal carry the data (the lane's stated requirement) and restores both new and existing journals. |

**`last_recreate_seq` survivor gate — REQUIRED.** `rebuild_all` pass-1a hoists EVERY creation
before pass-1b runs, so an annotation that predates the id's last creation would fold onto a
re-created same-id incarnation whose dims died with the deleted node live (`apply()`/`fold()` are
chronological and do not). Both pass-1b folds — the `OperatorAnnotated` branch and the annotator half
of `_revise_point` (via `skip_annotator_dims`) — are therefore gated on the #2488
`last_recreate_seq` anchor (`seq <= anchor` → skip), the same anchor the `EntityMutated` delete
branch uses. Regression: `test_rebuild_does_not_leak_deleted_incarnation_dims` (differential vs
`fold()`). The content/embedding half of the same divergence is pre-existing, out of scope, and
filed separately (#4042).

**`OperatorAnnotated` must be added to the fold vocabulary, not to the no-fold sets.** It is currently
absent from both `_NO_PROJECTION_FOLD` and `_NO_POINT_FOLD` — so once the emit is fixed and the record
lands in the JSONL, `apply()` / `rebuild_all()` / `_apply_one()` would each log *"unrecognized event
type 'OperatorAnnotated' — skipped"*. The fix adds a real fold branch on all three (so it must **not**
be added to `_NO_PROJECTION_FOLD`). This is the same #3299 vocabulary lesson.

## Integration surfaces / replay paths that must change

Line references are symbol-anchored approximations against the worktree base `766e4c3e2` (an
ancestor of the `origin/main` tip `3be34cdd1` at review time).

`sdk.py`
- `annotate_operator` (~6149, emit at ~6172): keep the positional graph payload; add `id=id` + the
  four `annotator_*` extras to `_emit_event`.
- `_emit_event` itself is unchanged (its `point is None and id is None` contract is correct).

`projection/__init__.py`
- Module constant `_ANNOTATOR_PROPS` (single source of truth for the four keys).
- `_apply_one` (~1142): new `OperatorAnnotated` branch; fold `_ANNOTATOR_PROPS` on the `PointRevised`
  branch.
- `apply()` (~1885, dispatch table): new `OperatorAnnotated` branch → `_apply_annotator(ev)`.
- `_apply_annotator(ev)` helper: `MATCH (n:Point {id:$id}) SET n.<key> = $<key>`; str-id guarded,
  no-op when no dim present (parity with the other guarded handlers).
- `rebuild_all` pass-1b (~2375 loop; `PointRevised` branch ~2494): new `OperatorAnnotated` branch →
  `_apply_annotator(ev)`, inline (matches the `PointRevised` branch directly above it), **gated on
  `last_recreate_seq`** (skip a pre-recreation fold) with a fold-miss warning.
- `_revise_point` (~3801): append `n.<key> = $<key>` clauses for any `_ANNOTATOR_PROPS` key present
  on the event; new `skip_annotator_dims` flag suppresses ONLY that half when pass-1b sees a
  pre-recreation revision (content/embedding replay unaffected).
- `_annotator_dims` filters non-persistable values via the shared `_is_persistable_prop_value`
  (#2894/#2795) so a malformed record cannot abort the rebuild after the wipe.
- `_NO_PROJECTION_FOLD` (~1111) / `_NO_POINT_FOLD` (~1131): **unchanged** — the new folds are real
  branches, so `OperatorAnnotated` must NOT enter these sets.

`config/ci-surfaces.yml`
- Register `test_operator_annotator_rebuild.py` under the `core` surface — the same surface as
  `test_entity_delete_rebuild.py`, the other #3299-family rebuild-durability suite.

**Not changed (deliberately):** `docs/event-catalog.md` (the graph payload is preserved verbatim);
`POINT_PROPS` — there is **no such constant in-tree** (the `_upsert_point_props` drift warning names a
future `contract.py`); `annotator_*` are not in `_POINT_DECLARED_PROPS`, so a *graph-only* annotated
operator replayed through the #548 synthetic snapshot would still trip the `#2795` drift warning.
That is the pre-existing open-set gap #2795 owns, not this fix (the annotator props are not in any
`PointAdded` snapshot, and this fix writes them from the journal instead).

## Test plan (red → green, mutation-sensitive)

New file `tests/test_operator_annotator_rebuild.py`:
- **core**: `annotate_operator` → snapshot → `rebuild_all` → all four dims equal their live values.
- **journal write path**: the JSONL contains an `OperatorAnnotated` record carrying the dims (reds
  pre-fix).
- **legacy/direct path**: `update_point(op, annotator_bias=…)` → `rebuild_all` → dims survive (reds
  pre-fix; pins that no `OperatorAnnotated` line is required for survival).
- **idempotency**: rebuild twice → dims stable.
- **`_apply_one` fold**: `fold([PointAdded, OperatorAnnotated])` and
  `fold([PointAdded, PointRevised{annotator_*}])` both restore the dims.
- **`:GraphEvent` payload contract**: `annotate_operator` → poll/read the `OperatorAnnotated`
  `:GraphEvent` and assert its payload still carries `id, bias, precision, consistency, directness`
  (guards against the `id=`+extras change silently renaming the polled fields).
- **`apply()` path**: `proj.apply({type: OperatorAnnotated, id, dims…})` restores the dims on an
  existing operator.

**Mutation that reds it:** remove the `_revise_point` / `_apply_annotator` fold → the surviving-dims
assertions return `None` (the exact pre-fix evidence reproduced above). The RED run of this file
before any fix is recorded as a comment on issue #3689 — as shipped (32 tests): `RED: 25 failed / 7
passed`, `GREEN: 32 passed` (the original 11-test revision measured `10 failed / 1 passed`).

**Review-cycle findings folded in:** (a) the short-name `:GraphEvent` aliases are admitted ONLY for
`OperatorAnnotated` records — applying them to `PointRevised` renamed a legitimate `precision`/`bias`
node prop into `annotator_*` and clobbered the real dim (regression test
`test_point_revised_short_named_props_are_not_aliased`); (b) `_apply_annotator` now returns the
matched count and the rebuild warns on a fold-miss, so an unfoldable annotation is audible (the
defect was silent loss); (c) the pass-1b folds are gated on the **#2488 creation-seq survivor
anchor** — `rebuild_all` hoists every creation in pass-1a, so a pre-recreation annotation would
otherwise leak the deleted incarnation's dims onto a re-created same-id operator, diverging from
`apply()`/`fold()` (#330 parity; regression test `test_rebuild_does_not_leak_deleted_incarnation_dims`).
The earlier "no survivor rule" decision was superseded by that finding. (d) **value gate**: the folds
compose the shared #2894/#2795 persistability predicate with a parameter-parse safety filter
(`_annotator_value_ok`) — maps/bytes/sets, non-finite floats (NaN/±Inf; JSON `1e400` → `inf`), and
strings carrying NUL or a lone surrogate are DROPPED, never passed to `SET`; the `id` gets the same
`_writable_id` gate (it rides as a Cypher parameter too); a malformed journal line
must not abort the recovery path after the wipe (regressions
`test_rebuild_drops_non_persistable_*`, `test_rebuild_drops_non_finite_*`,
`test_rebuild_drops_nul_and_surrogate_annotator_strings`). The same gate now also covers
``_revise_point``'s ``new_content`` parameter (a corrupt content value used to abort the rebuild after
the wipe; the content EDIT is dropped, the dims still fold) and the `id` on both folds.

### Known residuals (out of scope)

- **Over-deep arrays (depth ≥ 33).** `_is_persistable_prop_value`'s `_PERSISTABLE_MAX_DEPTH = 32`
drops them; the engine accepts arbitrary depth, so a live `update_point(annotator_bias=<33-deep>)`
replays as absent. Inherited from the shared #2894/#2795 predicate — fixing it there is #2795's call,
not this lane's.
- **Duplicate same-id creation with no intervening delete.** A raw `PointAdded`/`OperatorAdded` for an
id that was never deleted advances `last_recreate_seq`, so a still-valid annotation is suppressed on
rebuild (while chronological `apply()` keeps it). Same class as the documented label-blind survivor
anchor in `rebuild_all`; reachable only from a raw/legacy producer.
- **The general `PointRevised`-extras class** — see #2946 above.
- **Content/embedding leak on delete→recreate** — #4042.

## Complexity rationale

Standard: two files in the durability seam, three replay paths plus one pure fold that must stay in
parity, one new test module requiring CI-manifest registration. No API/UX change, no schema change, no
migration. Blast radius is additive (new event fold + extra SET clauses only when dims are present).
