# Implementation plan — state, lifecycle and pipelines

**Design of record:** `docs/plans/2026-11-15-state-lifecycle-and-pipelines.md` · **Issue of record:** #11824 · **Tracker:** #11081

This is the *plan to build* the approved model. The design says what the model is; this says what changes, in what order, and what proves each change. The design's owner approval (2026-11-16, PR #12895) is the ruling that makes its reversals of earlier recorded positions stand — a lane landing S2–S5 depends on that ruling, not on its own reading of the code.

**Status: drafted, five review rounds on the text.** Both former conflicts are resolved (see §8), and resolving the **first** one **re-ordered the plan**: the write door is now S0, because every vocabulary change below is only safe once a word outside a kind's set can no longer land. Do not dispatch execution until the design text lands (#12954) and the two ungated surfaces in §4 are gated.

## 0. What is already true, so this plan does not rebuild it

| already shipped | where | consequence |
|---|---|---|
| The pipeline slot model — `PIPELINE_SLOTS`, `PIPELINE_OUTCOMES`, `STAGE_PROPS`, `isClosed` | `pipeline.py:55`, `:56`, `:65-68`, `:77` (#2792) | the work is making the reader *use* it |
| One shared currency predicate with a family-delta seam | `live.py:108-139` `_terminal_excluded(clause, excluded=…, include_outdated_flag=…)`, `:281` `is_terminal_status` | S1 adds **inputs**, not a new predicate |
| `status_folds.py` refuses a fold whose target is not a declared status | `:215-219`, `:294` | this is *why* a pack's words must move to stages (S3), not be deleted |
| `packs/venture` declares `written-off`, in no exclusion set | `packs/venture/manifest.yaml:107` | a written-off investment is current to every reader today |

**What is NOT true, and the plan must not assume:** there is **no Object status write vocabulary**. A shared `OBJECT_STATUS_VALUES` was explicitly **foreclosed** (`commit_ops.py:114-122`; `sdk.py:49101-49124` — *"ONE RULE, AND IT IS NOT A VOCABULARY"*, the guard asks the transition question only). Subject has `SUBJECT_STATUS_VALUES` (`commit_ops.py:123`); Point has `POINT_STATUS_VALUES` (`sdk.py:275`), which **omits `deprecated`** and has no `deprecated` entry in `_ALLOWED_TRANSITIONS` (`:4639-4653`).

## 1. Non-goals

- **No backfill, no migration, no compatibility shim.** A legacy word survives only as a **read delta** over stored rows — which is why the legacy `outdated` **flag** keeps being read (see §6).
- **No new public surface** except those named in §4, each gated there.
- **No `status` on `:Source`** until it has a writer and a reader (its own approval).
- **No general rule engine**; **no priority/importance design**.

## 2. Slices, in dependency order

### S0 — the write door validates the status

**Failure it prevents (live, and the reason every later slice is ordered behind it):** **any string can be written as a kind's `status`**. `status` is not a server-managed prop and `_update_entity` issues an unvalidated `SET n += $props`, so `tortoise_create_entity` / `tortoise_update_entity` accept `status='outdated'`, `'archived'`, or `'garbage'` on an Object today — and there is no `OBJECT_STATUS_VALUES` anywhere in `tortoise/`, only comments recording that it was considered and rejected (`sdk.py:49104`, `commit_ops.py:118`).

**Why it is first.** A filter set is correct when it covers every value the writes can produce. Today a write can produce `status='outdated'` on an Object, which is exactly why `assembly._RECALL_OBJECT_EXCLUDED_STATUSES` carries `outdated` (`assembly.py:4370-4381` says so in its own comment: *"Dropping the member would flip such a successor from recall-excluded … to a verified link"*). **Removing a value from a filter before removing it from the write path does not narrow the vocabulary — it moves the failure from the door to the reader.** So the door goes first, and every drop below is then a deletion of provably unreachable weight.

**Change:** validate `status` on the generic entity write against the kind's declared set where it has one, and the four-word base otherwise. Packs keep their words — as stages (S4), not as statuses.

⚠️ **Pinned test this slice deliberately breaks:** `tests/test_11926_derived_invalidation.py:310-313` writes `update_entity(..., status='outdated')` on an Object and reads it back — the pin `commit_ops.py:53-56` names as today's behaviour. It is updated in the same commit, with the reason, exactly as S2 handles its own pin.

**Acceptance:** `status='garbage'` is refused on every kind; each kind accepts exactly its declared set; nothing is dropped from any filter in this slice.

### S1 — the predicate reads the position axis, and every consumer uses it

**Failure it prevents (live):** a row ended by a `terminal` stage or by `isClosed` is served as current, because the predicate reads only `status` + the legacy `outdated` flag — and **five** consumers do not use it at all: `mcp_server.py:4931` (tag arm, `!= "retracted"`, #12502), `hosted_api.py:13696`/`:13719`/`:13872`, `topic_summarization.py:231`/`:253`/`:289-290`, `sdk.py:37138` (`_hydrate_evidence` — a superseded claim's ghost votes in EP), and `derived_invalidation.py:381-386`, which answers currency itself and never reads `isClosed` or the terminal slot (`grep isClosed tortoise/derived_invalidation.py` → 0 hits).

**Change:** the Cypher twin reads the durable `isClosed` marker **or** a resolved stage in the `terminal` slot; the Python mirror gains the same input at its 22 call sites; the five consumers are converted or declared as deltas.

**Acceptance:** an Object ended **by `isClosed` or a `terminal` slot while `status` is still `live`** is not served and its derived caches are treated as retired — that is the case no test covers today (the `status`-based case is already pinned by `tests/test_11926_derived_invalidation.py:238`). A slot reclassification cannot resurrect a closed Object (both `test_pipeline_2792` shapes). No converted module keeps a hand-written status comparison.

### S2 — a Point can end, and stops ending five ways

**Failure it prevents:** `deprecated` **cannot be written on a Point** — missing from `POINT_STATUS_VALUES` (`sdk.py:275`), so the vocabulary gate raises before `_ALLOWED_TRANSITIONS` (`:4639-4653`) is consulted; and a Point can still be `retracted` and `outdated` through verbs, so one ending has five spellings.

**Change:** add `deprecated` to the Point write set **and** its transition entry; **remove `retracted` from `POINT_STATUS_VALUES` (`sdk.py:275`) and from the `live` row of `_ALLOWED_TRANSITIONS` (`:4646`, `frozenset({"retracted", "superseded"})`), and remove `outdated` from `POINT_STATUS_VALUES` and from its own terminal entry (`:4653`, `"outdated": frozenset()`, the `#2498` shared-guard key)** — without that removal the slice's own acceptance is unreachable; `retract_point` (`sdk.py:24172`, takes no successor) targets `deprecated`. ⚠️ `invalidate_point` (`sdk.py:22416`) takes a **required** `corrected_by_id` and always writes `CORRECTS`, so by the design's defining rule it is a **supersede** — it targets `superseded` (§7).

⚠️ **Pinned invariant this slice deliberately breaks:** `tests/test_lifecycle_guards.py:87` asserts `"deprecated" not in POINT_STATUS_VALUES` (the pin for a deliberate omission recorded at `live.py:90-98`). That test is updated in the same commit, with the reason, not silently deleted.

**Acceptance:** a withdrawn Point reads `deprecated`; a corrected Point reads `superseded` with its successor; no verb produces `retracted`/`outdated`; `retract_point`, `invalidate_point` and the extractor's `restatus` land on one of the four.

### S3 — pack words become pipeline stages

**Failure it prevents:** nine pack `statuses:` declarations, thirty distinct words, one overlapping the base set — and a `venture:investment` at `written-off` (`packs/venture/manifest.yaml:107`) is current to every reader because nothing declares that stage terminal; while `status_folds.py:215-219`/`:294` refuses a fold whose target is not a declared status, so the words cannot simply be deleted.

**Change:** a pack's `statuses:` list becomes that kind's **pipeline stage declaration**, each stage carrying a slot (including a `terminal` one); `folds[].status` targets move with it; the built-in connector folds (`status_folds.py:82-88`, targets `in_progress`/`completed`, writer `projection/entities.py:5800` `_fold_object_status`) move onto stages of a **declared core default pipeline** — this slice names the pipeline key, the stage ids, and which writer advances the stage instead of setting a status (`advance_stage(..., reopen=True)` for the reopening fold).

**Acceptance:** `written-off` is terminal **by slot**; a kind declaring stages without slots is refused at install; each built-in closing fold lands on a terminal stage the reopen path clears; the core default pipeline is named in this repo, not deferred.

### S4 — the Object narrowing, and #12855

**Failure it prevents:** `commit_ops` still accepts `outdated`/`retracted` as Object words (#12855), and `archived` is writable on Objects while "reserved" for Points.

**Change (as the design states it):** after S3 the pack's words are stages, so a kind's **`status` set is the four-word base** — the design records this as a deliberate reversal of the foreclosure (`OVERRIDES` (2)) and states it plainly: *"a pack kind's own `status` is now constrained to the four-word base"*. `archived` leaves the write surface everywhere; `outdated`/`retracted` leave it for Objects.

⚠️ **This cannot be written as "resolve per-kind"**: after S3 there is no per-kind *status* declaration left to resolve from, and `sdk.py:49101-49124` records the transition-only guard, not a per-kind route (that route is recorded at `commit_ops.py:119-120`, and S3 supersedes it). See §8.

**Acceptance:** #12855's cases pass, including its `assembly` row — which is only reachable once S0 has closed the write door (§8).

### S5 — `goalState` loses `guideline`, and its endings obey the lifecycle

**Failure it prevents (measured):** `goalState='abandoned'` with `status='live'` is retired to `derived_invalidation` and current to every reader — nothing cross-checks the fields (`sdk.py:7742-7748` validates vocabulary only) while `derived_invalidation.py:385` retires caches on `goalState in RETIRED_GOAL_STATES`.

**Change:** vocabulary becomes six (`met · unmet · blocked · active · abandoned · superseded`); the write boundary refuses a write where `goalState ∈ {abandoned, superseded}` and `status` does not carry the matching ending, in either direction; the judgement cache keeps writing only `met`/`unmet`.

**Depends on S2**: a **Point goal** cannot be `abandoned` until `deprecated` is writable on a Point — landing S5 first would make it unwritable, a regression (only the vocabulary is checked today).

**Acceptance:** `goalState='guideline'` is refused; an ending on one field without the other is refused both ways; a Point goal and an Object goal reach `abandoned`/`superseded` through the same verbs.

### S6 — a scope parameter, and declared deltas that stay declared

**Failure it prevents:** a caller cannot ask for ended rows on most read paths — the scope knob exists only as `include_retracted` on a subset of the MCP read tools (`sdk.py:26793`, `:26856`; `mcp_server.py:4335`), so "show me what was retired" is a per-tool accident rather than a read-scope choice.

**Not a failure, and must not be "fixed":** the four sets the design's §1 lists by size are not four divergences. `live.TERMINAL_EXCLUDED_STATUSES` (`live.py:104`) is the shared currency filter; `commit_ops.OBJECT_TERMINAL_STATUSES` (`:69`) and `assembly._RESOLVER_UNRESOLVABLE_OBJECT_STATUSES` (`assembly.py:1668`) answer *other questions* — the resolver's exists so a superseded Object can still be shown, which is how a reference avoids dangling; `derived_invalidation.RETIRED_GOAL_STATES` (`:174`) asks about retirement, not currency. **Until the design text lands (#12954), the design still states this as "one base set plus declared deltas, never re-pointed at the base" (§6); the pending edit replaces that with two questions (should it show up in search? can it be shown by name?) and the membership test that follows from them (§11824, issuecomment-6907321613).** Either way a lane must not read this slice as licence to unify the sets: the reason they differ is that they answer different questions.

**Change:** one ended-scope parameter on the read tools, generalising `include_retracted`; every reader that hides something declares which delta it is. Nothing is added to the record.

**Acceptance:** two readers with different deltas return different rows **and each says which delta it applied**; the resolver's narrowing still resolves an ended Object; no reader hides by an undeclared literal.

### S7 — the events boundary and planned occurrences

**Failure it prevents:** an Event's progress is carried by **two** mutable words, not one — `eventStatus` (`scheduled` → `completed`, an ingest marker: `ONTOLOGY.md:553-554`; `sdk.py:50428`, `:33179`, `:40031`; `hosted_api.py:22331`) **and** a writer-supplied non-terminal `status` (`scheduled`/`draft`/`live`), which is accepted and stored verbatim (`sdk.py:31121-31123`; `live.py:797-816`, *"would refuse `status='scheduled'`, which main pins"*). So "will happen" and "did happen" still share a mutable field after `eventStatus` alone is retired.

**Change:** `eventStatus` keeps only its ingest meaning (or is renamed to say so); the **non-terminal `status` on an Event is retired too** — that is the second half, and it is what makes the conflation go away. A planned occurrence becomes an **Object** of its own kind with a pipeline (`scheduled → held | missed | cancelled`); the **Event** is created when it happens and linked to the plan. Naming is part of the slice: the plan kind must not share a noun with the event kind.

⚠️ **A live reader depends on the non-terminal status prop:** `_promote_incident_operators` suppresses operator promotion on an Event that looks draft, exactly as for a draft Point (`ONTOLOGY.md:543-546`; `sdk.py:24692`, gate `:24744-24747`). That suppression must be re-homed onto whatever replaces the prop — it is not droppable.

**Acceptance:** "planned but never happened" is answerable without reading a mutable word; a plan advances through a terminal stage with an outcome and an Event is created on occurrence; the operator-promotion suppression still holds, tested on the Event path.

## 3. Order and why

**S0 → S1 → S2 → S3 → S4 → S5 → S6 → S7.**

- **S0 first**, because every later drop is only safe once the write cannot produce the value being dropped (see §8, conflict 1).

- **S1 next**: correctness with no vocabulary change, so nothing downstream is built on a predicate that serves dead rows. (S0 is first; S1 is first among the slices that change no vocabulary.)
- **S2 before S3/S4**: the Point write set is the one place a word narrows without per-kind resolution.
- **S3 before S4**: the Object narrowing has no vocabulary to narrow; the design's route is that pack words leave the status axis, so the four-word base applies.
- **S5 after S2**: a Point goal cannot record an ending until Points can write `deprecated`.
- **S6 after S4**: the deltas it declares are the ones the earlier slices leave behind.
- **S7 independent**; last only because it is least urgent.

## 4. Gates before code

| gate | why | state |
|---|---|---|
| **The design ruling itself** (#12895 → #12954) | every slice reverses an owner-approved position; without the approval the recorded positions stand | **approved 2026-11-16**; text landing in #12954 |
| `ONTOLOGY.md` exact text | that file's CRITICAL RULE — owner approves content, lane applies the diff | **proposed**, #11824 |
| **`status` write vocabulary** (S2, S3, S4) | removing `retracted`/`outdated`/`archived` from sets validated at the write boundary (`sdk.py:15148`, `:20710`; `commit_ops.py:123`) is a surface change | **not gated yet** |
| `GOAL_STATE_VALUES` (S5) | SDK/MCP surface approval | approved in principle; the diff needs its own record |
| **Read-tool scope parameter** (S6) | generalising `include_retracted` touches the MCP/SDK read surface | **not gated yet** |
| `:Source` `status` | declares a field, a writer and a reader that do not exist | not proposed |
| §8 conflict 1 | resolved by deleting the rule it argued about; #12855's row is now S4's acceptance, gated behind S0 | **resolved** |
| §8 conflict 2 | resolved by recording the reversal on #2901 before moving the pin | **resolved** |

## 5. What proves each slice

The test that fails before and passes after; the **measured** baseline it moves in the PR body; `WORKTREE-REPORT.md` with suite commands and derived counts. A slice whose report cannot name a behaviour that changed is not done.

## 6. Residuals this plan deliberately leaves

- Stored `retracted`/`outdated`/`archived` rows stay readable as deltas; nothing rewrites them.
- **The legacy `outdated` flag keeps being read after S2.** `invalidate_point` still sets it (`sdk.py:22538`), §1 forecloses backfill, and the design requires the read *"until it is migrated onto a word"* — a migration no slice here performs. Ending that read is a later slice, not this plan.
- `:Source` has no lifecycle until it has a successor route, a writer and a reader.
- Priority/importance has no design; `guideline`'s intent is parked there.

## 7. The ending verbs, resolved (was: a design-doc correction)

The design's §3.1 maps **`invalidate` → `deprecated`**, but `invalidate_point` (`sdk.py:22416`) takes a **required** `corrected_by_id` and always writes `CORRECTS` — so by §3's own defining rule (*"`superseded` names its successor; if nothing replaced it, the word is `deprecated`"*) it is a supersede. Rather than patch one row, the verbs are now aligned on a single rule, and **`invalidate` is not a word-producing verb at all**: it names *our record* going out of date — the `expiredAt` bookkeeping `invalidate_point` and `supersede_point` write — which is exactly how W3C PROV splits it (`prov:Invalidation` ends the entity and says nothing about a replacement; `prov:wasRevisionOf` is a separate relation).

So: `retract_point` (no successor, `sdk.py:24172`) → `deprecated`; `invalidate_point` (required successor) → `superseded`; and **the rule, not the verb, picks the word**. The exact replacement text for §3.1 is recorded on #11824 (issuecomment-6906427754). Renaming `invalidate_point` is a surface change and is not proposed.

⚠️ **One gap this surfaced in today's code:** `invalidate_point` and `supersede_point` both stamp the dedicated expiry prop (`expiredAt`, `sdk.py:22541`, `:24130`), but **`retract_point` does not** — it writes `status`, the belief halves, `updatedAt = $now` and an optional `validTo` (`sdk.py:24303-24305`; the replay fold agrees, `projection/__init__.py:10256-10257`). So a retracted claim's ending instant survives **only in the generic `updatedAt`**, which any later write moves, rather than in the expiry prop the other two endings write (`sdk.py`'s own `:22464`: *"`expiredAt`/`updatedAt` record when the graph LEARNED of the end, not when the claim ended"*). S2 closes it: retraction stamps `expiredAt` like every other ending. Until then, "invalidation is the bookkeeping, not a word" describes the intended model rather than all of today's code.

## 8. The two former conflicts, and how each closed

**Conflict 1 — resolved: the rule it argued about is being deleted.** The question was whether #12855 may re-point `assembly._RECALL_OBJECT_EXCLUDED_STATUSES` at the base by dropping `outdated`. Researching it from first principles rather than from the recorded positions shows the row is **inverted**: `outdated` is only droppable *after* the write door validates the vocabulary (S0), because today any string can land as an Object's `status` and the member is the reader-side defence for that. So #12855's `assembly` row becomes **S4's acceptance test**, not an independent change — and the design's "never re-point a list at the base" rule is replaced by two questions (a filter is wrong when it omits a value a write can produce, or adds one its question does not cover), recorded on #11824. There is no delta to adjudicate.

**Conflict 2 — resolved by protocol.** `tests/test_lifecycle_guards.py:87` pins a deliberate omission from a recorded decision. Per the AGENTS.md rule that a recorded decision is reopened in its own home, the reversal was recorded on **#2901** (issuecomment-6906438450) — the issue that made the decision — before the test moves. S2 proceeds by adding the word and the transition entry, pointing `retract_point` at it, and updating the pin in the same commit **linking #2901**.
