---
title: "#2517 / #2568 Source-session re-injection (C4 #2513) — implementation plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-09-15
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
relatedIssues: "#2517, #2568, #2519, #2513, #1509, #1540, #1745"
---

<!-- issue: https://github.com/daniel-ospina/tortoise/issues/2517 -->
<!-- duplicate: https://github.com/daniel-ospina/tortoise/issues/2568 -->

# Implementation plan — source-session re-injection on seeded hits (C4)

**Issue (canonical):** #2517 · **Duplicate superseded:** #2568 (its O/I/T is the
implementation spec — it is more specific) · **Branch:**
`fix/2513-retrieval-evidence` · **Epic:** #2513 (C4) → #1509 layer-3 RECALL

**Spec used:** #2568's O/I/T (batched Cypher by `session_id` mirroring
`evidence_sessions`, per-session budget + MMR/dedup guard, C5 chunk cap, flag +
per-session injection census + arm markers) — with the **corrected trigger, seed
window, and placement** derived below.

**Research basis (durable refs — these docs are NOT on `origin/main`):**
- `docs/scoping/2026-09-07-2513-multisession-evidence-surface.md` — read at commit
  `9b822b9ac` (branch `origin/opt/2513-retrieval-scope`); §2(d), §4 C4, §6, §7.
- `docs/scoping/2026-09-08-2519-coverage-loop.md` — in-tree; §2 (tail slice
  `s[150:250]`), §3(b), §5, §6.

---

## 0. Reconciliation — one mechanism, one home

| | #2517 (09-07) | #2568 (09-08) |
|---|---|---|
| Home | #2513 C4 (epic-scope child) | #2519 C3-2 (filed by the loop's scoping pass) |
| Labels | `complexity:standard`, `team:epistemic-team` | none |
| O/I/T | objective + reader-surface indicators | batched `session_id` fetch, flood-guard, flag + census, C4 tie note |

#2568's own text defers to #2513's C4 — *"Ties: #2513 C4 overlap decision — if C4
lands separately, consume its seam instead of double-building"* — and the #2519
doc §6 states *"If #2513's C4 stays separate, land there and C3-1 consumes the
seam (no double-build)"*. **#2517 is canonical**; **#2568 is superseded/closed**
with a link. Its more specific O/I/T is adopted as the implementation spec.

**Overlap with #2519 (checked before building):** C3-1 — the
retrieve→check→expand completeness loop — **already landed** on `origin/main`
(`tortoise/coverage_loop.py`, PR for #2567). It is **entity-facet-scoped**. This
change is a **sibling driver over a shared contract**, not a slice.

### 0.1 Shared contract — exact homes, signatures, and dependency direction

**Dependency direction (pinned, one-way at module level):** `retrieval → coverage_loop`.
`coverage_loop.py` stays a **stdlib-only leaf** at import time. `retrieval.py` may
`import tortoise.coverage_loop` (it needs `session_diverse_order` and the two
window constants). The one reverse need — `coverage_loop._session_of` delegating to
`retrieval.session_key_of` — is a **function-local import inside `_session_of`**, so
no import-time cycle exists under any import order. *(Verified: both modules are
stdlib-only today; `tortoise/__init__.py` imports neither.)*

| Contract | Home | Signature | Notes |
|---|---|---|---|
| `session_key_of(hit)` | **`tortoise/retrieval.py`** (public) | `(hit: dict) -> str` → `hit.get("session_id") or f"idx:{hit.get('lme_session_index', -1)}"` | The **authority**. `dedup_pool(session_key=None)` and `guard_and_recap_pool(session_key=None)` **default to it**. `coverage_loop._session_of` **delegates** via a function-local `from tortoise.retrieval import session_key_of` (keeps its private name for compatibility). |
| `DEFAULT_POOL_GUARD_WINDOW` / `DEFAULT_POOL_SESSION_CAP` | **aliases in `tortoise/retrieval.py`**, defined in **`tortoise/coverage_loop.py`** as `DEFAULT_LOOP_GUARD_WINDOW = 5` / `DEFAULT_LOOP_SESSION_CAP = 2` | — | The guard-window discipline's own defaults stay in `coverage_loop` (its module); `retrieval` imports them as `DEFAULT_POOL_*`. Same direction as `guard_and_recap_pool` needs, so **no new edge**. |
| `guard_and_recap_pool(items, *, guard=True, session_key=None, window=DEFAULT_POOL_GUARD_WINDOW, per_session_cap=DEFAULT_POOL_SESSION_CAP, max_chunks_per_session)` | **`tortoise/retrieval.py`** | — | `guard=True` → `coverage_loop.session_diverse_order(...)` then `dedup_pool(...)`; `guard=False` → **`dedup_pool(...)` only** (the ablation stays *inside* the contract, so the guard→re-cap ordering is never duplicated in a driver). C3-1 always passes `guard=True`; C4 passes the resolved `session_reinjection_guard`. |
| `annotate_pool_additions(hits, props, dates, *, match_source)` | **`tools/longmem_eval/retrieve.py`** (harness-local) | delegates to `_annotate_hits(hits, props, dates)` then sets `match_source` on every hit | Extraction of the C3-1 annotation half. **`dates` is required** — `_annotate_hits` derives `session_date = dates[si]` (`retrieve.py:658,667`) from the **question's** `haystack_dates` (`retrieve.py:1178`), which is not derivable from `proj` or from point props (props carry only `lme_session_index`). **Harness-local by direction**: it needs eval-lane readers, so a product home would force a product→harness import (forbidden). `match_source` is a **parameter** (see §2.3-A). |
| `SESSION_TRANSCRIPT_KIND` | **`tortoise/retrieval.py`** | `= "session-transcript"` | The authority (moved from `tools/longmem_eval/ingest.py:54`, now a re-export). |
| `CHUNK_KIND_FILTER` | **`tools/longmem_eval/retrieve.py`** (module level, next to `D5_POINTKIND_FILTER`) | `f"coalesce(p.pointKind, '') = {SESSION_TRANSCRIPT_KIND!r}"` — with `SESSION_TRANSCRIPT_KIND` added to an existing `from tortoise.retrieval import (…)` block (`retrieve.py:100-131`); **no module-name binding** (`retrieve.py` imports names, it does not `import tortoise.retrieval`) | The **addressable seam** for consumer #4 (the chunk-count Cypher at `retrieve.py:1647`), which is otherwise an inline string inside a ~700-line function and cannot be asserted by a unit test. `D5_POINTKIND_FILTER` (the `<>` exclusion twin) also derives from the constant. |

**Distinct drivers (keep separate):** C3-1's expansion source is a targeted FTS
re-query for a missing entity facet (`loop_expansion_pass`); C4's is a batched
`session_id` chunk fetch. Different sources, different merges.

**Pool-order ownership (A1):** the **C4 block is inserted immediately after the
C3-1 block and before the C2 evidence boost** in `retrieve_for_question`; the arm
is defined and measured with C3-1 OFF; and **`run.py` refuses the run when both
`coverage_loop` and `session_reinjection` are ON — at arm resolution, before the
question loop** (see §0.2). Two owners of the same pool order are refused rather
than left order-dependent. Three post-stages move pool-based metrics, in stage
order: **C3-1 guard → C4 guard → C2 boost** (this is the corrected wording of the
stale `retrieve.py:1598` comment — Task 2).

#2519 is **not** expanded or claimed.

### 0.2 The both-arms-ON refusal must ABORT, not fail-per-question

`run.py`'s per-question handler catches bare `Exception` and prints *"question
FAILED (non-fatal, continuing)"* (`run.py:4485`), re-raising only
`WatchdogAbortError` / `CheckpointPersistError`. A `ValueError` raised inside
`retrieve_for_question` would degrade a fail-closed refusal into N per-question
failure entries over a multi-hour run — and inside C4's fail-open `try/except` it
would be swallowed entirely.

**Mechanism (pinned, with the presentation pinned too):**
1. A dedicated `ArmConflictError(Exception)` is raised by **`run.py` at arm
   resolution, before the question loop** — the block at `run.py:3488-3512` that
   resolves `coverage_loop` (`:3499-3501`) and `aggregative_flag` (`:3508`). There
   is **no `try:` between `run_evaluation`'s def (`:3292`) and that block**, so it
   never enters a fail-open region.
2. The resolved arm **and** guard are threaded into `retrieve_for_question` exactly
   as `coverage_loop=coverage_loop` is today (`run.py:4073`) — so env-only arming
   cannot diverge between the check and the driver.
3. `ArmConflictError` is added to the handler's **re-raise set** (`run.py:4490`),
   so even if one were raised later it could not be recorded as a per-question
   failure. (This *narrows* handling for one type — it cannot mask other errors.)
4. **Presentation:** `main`/`_run_main` (`run.py:6213-6225`) currently catches only
   `FatalProviderError` / `ModelEncodeFailedError`, so an `ArmConflictError` would
   surface as a bare traceback. Add an `except ArmConflictError` clause printing
   `[longmem_eval] RUN ABORTED — arm conflict: <detail>` and `raise SystemExit(1)`.
   *(No run-level `degraded_aborted`/`checkpoint_abort` marker is written on this
   path — the watchdog/checkpoint writers are the only marker writers — so the test
   asserts the **message + exit code**, which is what is actually achievable.)*
5. Test: both arms ON **aborts** — asserts the abort message and `returncode != 0`,
   not "records failures and continues".

*(The two secondary `run_evaluation` callers — `run.py:5797::_run_spot_check` and
`tools/longmem_eval/sweep_granularity.py:95` — would surface an uncaught
`ArmConflictError` traceback rather than the clean message. Both are
non-measurement paths and still exit non-zero, so the refusal still aborts; noted,
not a defect.)*

The check is: `if coverage_loop_on and session_reinjection_on: raise ArmConflictError(...)`.

---

## 1. Confirmed problem (re-derived — the issue body's fix is a hypothesis)

**Confirmed problem definition:** On a multi-session question there are **two**
distinct losses, on two different graded surfaces:

1. **Pool rank-cut loss (graded: `recall_all@5`).** Points are never session-capped
   (`tortoise/retrieval.py::dedup_pool` counts only `is_raw_chunk(h)` against
   `max_chunks_per_session`; every non-chunk point is appended unconditionally), so
   the most-similar session's points occupy `hits[:5]`. A contributing session whose
   evidence **is in the pool at rank ≥ 6** never enters the window.
   `session_recall@k` counts **distinct** `session_id`s in `hits[:k]`, and
   `recall_all@5 == (session_recall@5 == 1.0)`
   (`tools/longmem_eval/retrieve.py::_recall_metrics`;
   `tools/longmem_eval/w7_publish.py`), so this scores **0** on the all-or-nothing
   metric even though the evidence was retrieved.
2. **Reader-surface loss (graded: `reader_surface@k` / `chunk_evidence_recall@k`).**
   A session's raw `session-transcript` chunks exist in the pool (union == full
   session, `tools/longmem_eval/ingest_v2.py`), but nothing expands a *seeded* hit
   back to the rest of its own source session, so question-relevant verbatim content
   stays outside the reader window. This is the "evidence never reached the reader"
   bucket.

> **[unverified] assumption (load-bearing):** the 13% "evidence never reached the
> reader" bucket is attributed here to reader-window truncation of pool-present
> chunks. The parent scope doc instead attributes it to *"extractor/write or
> rank-depth (write-path side)"* (#2513 §1). **Falsifier — a Task-4 deliverable:** a
> forensic census over that bucket's questions asking "was the answer session
> represented in the pool, and were its `session-transcript` chunks pool-present but
> context-absent?" If falsified, part 2 shrinks and this arm's reader-surface claim
> narrows to the `chunk_evidence_recall` sub-case; that is recorded, not absorbed.

### 1.1 What the issue body got wrong (documented re-derivation)

Both the problem-diverge agent and the Phase-1.5 external research independently
falsified two specifics of the issue text. They are **not** adopted:

| Issue-text claim | Verdict | Evidence |
|---|---|---|
| Trigger = "point/turn with **stored or read-time marks**" | **Rejected — gold leakage** | `has_answer` is written from dataset gold turns (`ingest_v2.py`); `evidence.mark_for_question` derives from `evidence_sessions` + the gold answer. The product has no such mark. C3-1 deliberately triggers on **query entities**, not marks. → **Trigger is rank-based: a session represented in the pool head.** Label-free, product-real. |
| "Re-inject … **before the rank cut**" *by displacement* (head placement) | **Rejected as head placement** | `session_recall@k` is a distinct-session set; injecting more items of an already-counted session adds no distinct id. Placed *competitively at the head* it can only displace/evict other sessions → negative EV. → Placement is **anchored and additive**: injected chunks splice **after that session's last base hit in the pool**. |
| "53% partial evidence" as the headline target | **Corrected / narrowed** | 53% partial includes *starved other* sessions that are not in the pool at all; expansion from seeded sessions cannot summon them (the #2519 doc concedes this). This arm targets (a) pool-present starved sessions (loss 1) and (b) the reader-surface bucket (loss 2). |
| External precedent for the trigger | **Negative finding** | Phase 1.5 found **no source** evaluating a rank-seed trigger; canonical parent-document / sentence-window / auto-merging retrieval expand **always-on on-hit**. Recorded as a deliberate divergence, not established practice. |

**Falsification check** — 1–3 falsify; 4 is the instrumentation/inertness guard:

1. **Flip census:** on questions whose answer sessions are pool-present but outside
   `hits[:5]`, the **arm (merge + guard)** does not flip that session into `hits[:5]`.
   (With the guard ablated off, a flip is attributed to the guard, not the chunks.)
2. **Surface census:** `reader_surface@k`, `chunk_evidence_recall@k` **and
   `reader_evidence@5`** do not rise (any may regress at the item/token cap when
   injection evicts marked tail items).
3. **Cost guard:** aggregate `evidence_recall@5` regresses with no `recall_all@5`
   gain, or `context_tokens` regresses on the head cohort.
4. **Inertness / instrumentation guard** (not an independent falsifier): the census
   shows zero injected-and-retained ids (`injected_merged == 0`) — the arm is inert;
   the honest result is a null, not a win.

**Cohorts — operationally defined (not by type label):**
- **tail** = the literal `s[150:250]` question set — **100 questions**. Materialized
  at receipt time by the committed **cohort builder** (Task 4), not committed as data.
- **head** = the **50 single-session-user** questions (selected by the same builder).
- Reported side by side, per arm; per-question-type breakdowns **inside** the tail.
  *(The issue's "MSR/KU/preference" label is NOT used as a filter: a KU filter over
  this set selects zero questions, which would make falsifier 2 vacuous.)*

(No numeric exit thresholds are pre-registered: the owner's beta posture is
"iterate with feedback", not exit-criteria numbers.)

---

## 2. Converged solution

**One new product operator, one arm, OFF by default.**

`tortoise/session_reinjection.py` (product rules, pure + one bounded graph pass):

1. **SEED** — `seeded_sessions(pool, *, window, limit, session_key)`: the distinct
   sessions represented in `pool[:window]`, in **first-seen rank order**, bounded to
   `limit`. Seeds are restricted to **real `session_id` values**: the synthetic
   `idx:N` bucket key is **dropped** (it can never equal a graph `p.session_id`, so
   seeding it would be a phantom session), as are `""` / `idx:-1`. The window is the
   **reader-reachable pool head** and is **derived at the call site from the resolved
   effective reader item cap** (`eff_item_cap`, `retrieve.py:1164-1175`), with
   `DEFAULT_REINJECTION_SEED_WINDOW = 40` as the product fallback — so a non-default
   `TORTOISE_LME_CONTEXT_ITEMS` cannot silently desynchronise it. Pure; **label-free**
   (rank trigger). `DEFAULT_REINJECTION_SEED_SESSIONS = 5`.
2. **EXPAND** — `source_session_chunk_pass(proj, seed_point_ids, *, pool_ids,
   chunk_kind, per_session_cap, total_cap)`: **ONE batched Cypher fetch** per
   **fired question**. **AS BUILT (#2517 retarget — the fetch was re-pointed at the
   product's own verbatim material; the pre-retarget form fetched
   `session-transcript` by `p.session_id IN $sids AND p.lme_question_id = $q`, which
   is unreachable in a product graph because no product writer emits that kind, that
   property, or a `session_id` on a turn Point):**
   ```cypher
   MATCH (seed:Point) WHERE seed.id IN $seed_ids
   MATCH (s:Session)-[:CONTAINS]->(seed)
   WITH DISTINCT s                      -- plan barrier: seed scan, not Point label scan
   MATCH (s)-[:CONTAINS]->(p:Point)
   WHERE coalesce(p.pointKind, '') = $chunk_kind
     AND coalesce(p.is_episodic, false) = true      -- turn shape, turn kind only
     AND coalesce(p.content, '') STARTS WITH '['    -- turn shape, turn kind only
     AND NOT p.id IN $pool_ids
   RETURN p.id, coalesce(p.session_id, s.id), coalesce(p.lme_chunk_index, -1)
   ORDER BY coalesce(p.session_id, s.id), coalesce(p.lme_chunk_index, -1), p.id
   ```
   The default `chunk_kind` is `TURN_POINT_KIND` (`'event'` — the product's episodic
   turn points written by `TortoiseSDK.capture_session` / hosted `POST /v1/sessions`);
   `SESSION_TRANSCRIPT_KIND` (the eval ingest's raw chunk windows) remains available
   as the non-default arm. The pool-membership filter is **in the query**, so the
   per-session/total budgets are spent on genuinely-new items. **`pool_ids` is coerced
   with `list(pool_ids)`** before binding (the call site holds a Python set; every repo
   precedent passes a list — `tortoise/sdk.py:17226`, `tortoise/hosted_api.py:5060`).
   Deterministic `(session key, lme_chunk_index, id)` order; budgets
   `DEFAULT_REINJECTION_PER_SESSION = 3`, `DEFAULT_REINJECTION_TOTAL_ITEMS = **10**`
   (retuned from 20: at turn grain the per-session candidate list is the session's
   whole turn list, and 20 was above the structural fan-out `5 × 3 = 15`, so
   `total_cap_hit` could never be True). Fail-open, and the `try/except` wraps **the
   whole block including the merge stage**.

   **Scope (as built):** the Session is named by the `Session-[:CONTAINS]->Point`
   edge the product writes, not by a point property. Question scope comes from the
   per-question graph namespace (the eval ingests one question per graph, with its own
   wipe) **and** from the anchor (the seeded hit is this question's pool hit). The
   removed `lme_question_id` predicate was redundant under that isolation: it was
   never what made the fetch question-safe, and the corpus-level `session_id`
   collisions (212 shared dataset ids across the 100-question tail cohort) never
   co-exist in one graph. **Residual:** a content-addressed seed point shared across
   Sessions expands every containing Session (`WITH DISTINCT s` dedups, does not
   constrain). **SEED limitation (unchanged):** a product turn Point carries no
   `session_id`, so a turn HIT cannot seed (`session_key_of` → `idx:-1`); in the
   product the seed is a non-turn point carrying `session_id` and the fetch expands
   that session's turns.
3. **MERGE** — `reinjection_merge_order(pool, added_by_session, *, ...)`:
   - **anchor** = the seeded session's **last base rank in the pool** (not merely in
     the seed window), guaranteeing the injected group lands **after every base hit
     of that session**; the C5 re-cap then keeps base chunks first and an injected
     chunk can never evict a base chunk (asserted by test);
   - groups spliced in **seed-rank order**, each immediately after its anchor
     (splices computed against the base index map, applied left-to-right so repeated
     insertions accumulate deterministically);
   - **already-present ids are dropped** (the fetch already filtered them; this is
     defence in depth). The merge is purely additive w.r.t. base hits;
   - then **`retrieval.guard_and_recap_pool(..., guard=<resolved
     session_reinjection_guard>)`** — **one call**, so guard-OFF still re-caps
     through the same function (never a second recap entry point);
   - **the whole merge+guard+re-cap runs iff `new_ids` is non-empty** (the C3-1
     condition): with zero new ids the base pool is returned **unchanged**.

**Harness composition** (`retrieve_for_question`), **inserted immediately after the
C3-1 block and before the C2 evidence boost**: the resolved arm is threaded from
`run.py` (as `coverage_loop` is), skip TR, annotate injected hits on the same
surface as the base pool via the **harness-local
`annotate_pool_additions(hits, props, dates, *, match_source="session")`**, record
the arm marker + census. **Fingerprint convention (pinned): both keys are the
resolved bools, ALWAYS present** — matching every sibling boolean arm
(`evidence_boost` `run.py:3613`, `entity_key_expansion` `:3619`, `coverage_loop`
`:3623`, `aggregative_flag` `:3627`), NOT the value-conditional `tr_top_k` pattern
(`:3648`, the only `if != DEFAULT` site). Concretely: `session_reinjection` = the
resolved arm bool; `session_reinjection_guard` = the resolved guard bool (present
whenever the arm key is present, i.e. governed by the **arm's** OFF, not the
guard's). `tests/test_eval_resume_retry_failed.py::_resume_fingerprint()` gains
`session_reinjection=False` + `session_reinjection_guard=True` (the #2649 heal
shape — the guard is the RESOLVED value: `run_evaluation` maps unset/None to
True, and the hand-written fixture must match the run path or the resume is
refused as stale), so a checkpoint written by one arm refuses under another (the
`CheckpointStaleError` contract). **Consequence, stated honestly:** because the keys
are always present on a new run, a **pre-feature checkpoint refuses on resume**
(`CheckpointStaleError`) — the same safe direction as every arm added since #1745.
The fingerprint is deliberately **not** byte-identical to a pre-feature run's; that
is the point of the gate.

### 2.1 Metrics — what each mechanism can and cannot move

> ⚠️ **Written for the pre-retarget CHUNK grain.** The shipped default is the
> product's TURN grain (§2 as built), at which `dedup_pool` does not re-cap turns
> and injected turns are NOT raw chunks — so the three rows marked ⌁ below read
> differently at the shipped grain.

| Metric | Effect | Why |
|---|---|---|
| official `recall_all@5` | **↑ or flat** | only the guard can admit a pool-present starved session into `hits[:5]`; the guard runs only when injection produced new ids, so a delta is attributable to the arm. Flat = legitimate null. |
| `chunk_evidence_recall@5` | **↑ where reachable** | injected marked chunks enter the pool's top-k. **Reach-bounded:** a session already at the C5 ceiling (3 base chunks in the pool) drops injected chunks at the re-cap. ⌁ At the shipped turn grain this metric is a raw-chunk surface and the injected material is turns, so it is NOT the arm's own surface (and is `null` under `--retrieval-only --mock`). |
| `reader_surface@k` | **↑ or flat; may regress at the item/token cap** | injected items enter the **full** reader context; a marked tail item can be evicted at the 40-item cap. ⌁ Measured at the shipped grain, this did NOT rise: 1.0 → 0.975 → 0.95 pooled (receipt falsifier 2). |
| `reader_evidence@5` | **bounded / indirect** | its numerator **excludes raw chunks** (`retrieve.py:1802-1803`), so injected chunks cannot raise it; only the guard's reorder can move it, and the guard can also lower it. ⌁ At the shipped turn grain the injected items are turns, which the numerator does NOT exclude — so injected turns CAN raise it. |
| `evidence_recall@5` | **guardrail — may regress structurally** | the guard caps a session at 2 inside the window, so a top-5 holding ≥3 marked points from one session loses marked points **by construction**. Reported; a regression with no `recall_all@5` gain falsifies. |
| `context_tokens` (head cohort) | **no regression** | injected items are bounded by the total budget + item caps (at the turn grain the C5 re-cap does not apply). |
| retrieval latency | **+1 batched query per FIRED question** | the fetch is one batched Cypher per question that fires (0 when it does not), not one per seed. Reported per arm; at turn grain the ISOLATED C4 block cost is 6.4–9.0 ms mean, worst question 26.0 ms (receipt falsifier 3 — process-level latency was load-dominated and is not a cost proxy). |

**Known reach boundaries (readable, not hidden):** (a) a seeded session contributing
**no new ids** skips the guard entirely (its material is already pool-present);
(b) at the **chunk** grain a session at the **C5 chunk ceiling** cannot receive
injected chunks (the re-cap drops them) — **at the shipped turn grain the C5 re-cap
does not apply at all** (`dedup_pool` counts only `is_raw_chunk`), so the **total
budget (10) is the volume guard** and the C5 boundary is a chunk-arm property only.
Both nulls are readable in the census (`injected_total`, `injected_merged`,
`guard: false`, `total_cap_hit`).

**C5 posture (documented choice, #2517 indicator 3):** the plan **respects C5** and
does **not** override it. The alternative (a separate injected-chunk budget beyond
C5) would extend reach at the cost of a second budget dimension the reader-window
guard must police; it is recorded as a follow-up (§6), not silently taken.

**Measurement preconditions (stated):** **R6 rerank, A6 evidence assembly, C3-1,
#2518, `evidence_boost` (C2) and `aggregative_flag` (C5) are ALL pinned OFF** — C2
and C5 are pool-order movers, so leaving either unpinned would confound the delta
with a second re-order. C3-1×C4 both-ON additionally refuses (§0.2). The arm is
defined at the one-shot defaults (also the C1 baseline). Each run gets its **own
checkpoint per arm *per cohort*** (`--checkpoint <path>`, `run.py:5547`) — 2 cohorts
× 3 arms = 6 runs. `run_key` does not include the knobs, so a shared checkpoint file
would refuse arms 2/3; the fingerprint also carries `dataset_fingerprint`, so the two
cohorts could not share one anyway.

### 2.2 Adversarial threat surface

**(not adversarial)** — retrieval-recall logic. Fail-open by design; it neither gates
nor enforces anything and has no attacker-controlled input whose failure could fail
a gate open.

### 2.3 Duplication dispositions (Reviewers #2/#5, plan-verify cycles 1–3)

| Finding | Verdict | Disposition |
|---|---|---|
| ~50-line merge/guard/re-cap **driver** cloned | `unify-contract-keep-drivers` | extract `retrieval.guard_and_recap_pool` (with `guard:` inside the contract) + harness-local `annotate_pool_additions`; both drivers call them |
| Guard-ON/OFF not expressible → a second recap entry point | `unify-contract-keep-drivers` | `guard: bool = True` **inside** `guard_and_recap_pool`; the guard→re-cap ordering lives once; §4 row (g) becomes "guard OFF still re-caps **through the same function**" |
| Session-key vocabulary (5 sites) | `keep separate`, reason stated | One **public** `retrieval.session_key_of` is the *pool* key (`session_id` or `idx:{lme_session_index}`); `dedup_pool` + `guard_and_recap_pool` default to it; `coverage_loop._session_of` delegates. The remaining keys are **deliberately not unified** — the ask-lane key is *semantically distinct*: `sdk.py:13919-13921::_ask_session_key` and `ask_recall_bench.py:184-186::_ask_session_key` fall back to **`session_date`** (`session_id` or `session_date` or `idx:{…}`), which is a different identity, not a drifted copy; `retrieval.py:941::_pkg_session` is a packaging key. **Recorded note (not filed — category B, "nothing but time"):** the two `_ask_session_key` copies are byte-identical twins; a shared helper is a tidy-up with no failure mode today. |
| `match_source` leg vocabulary — 4 live definitions | `unify` | `search_engine.py:296`'s `Literal` is authoritative and gains `"session"`; the **two closed-set readers** — `tests/test_longmem_rerank.py:313-314` and `:569` — are updated to include it; the `_leg_mix` docstring enumeration (`retrieve.py:801`) is corrected. The readers are **hoisted to one module constant** `_MATCH_SOURCE_LEGS = ("fts","vector","structural","rrf","tfidf","session")` used at both sites (they are currently inline tuples inside test bodies, so a consistency test cannot reference them otherwise), and the consistency test asserts `set(_MATCH_SOURCE_LEGS) == set(get_args(get_type_hints(SearchResult)["match_source"]))` — **`get_type_hints`, not `field.type`**: `search_engine.py:6` has `from __future__ import annotations`, so the dataclass field's `.type` is the *string* `"Literal['fts', 'vector', 'structural', 'rrf', 'tfidf']"` and `get_args()` on it returns `()` (a permanently-red assertion). `SearchResult` must be imported into the test module (it is not bound there today). *(Without this, a rerank×C4 co-run false-fails — the plan's own "single-source the leg" goal was one home short.)* |
| `annotate_pool_additions` — the extraction could not reproduce `_annotate_hits` | `unify-contract-keep-drivers` | `dates` is a **required** parameter, and C4's leg is a parameter (`"session"`); the no-regression proof is **whole-dict equality of all 17 annotated keys**, not a 4-field subset (a subset passes while `session_date` silently empties) |
| Chunk-kind literal (4 sites) | `unify` | one constant in `tortoise/retrieval.py`; `is_raw_chunk` imports it; **all three eval-lane consumers** derive from it — `ingest.py` re-export, `D5_POINTKIND_FILTER`, and the new module-level `CHUNK_KIND_FILTER` (the addressable seam for the chunk-count Cypher); parity test enumerates all four |
| Pool-order ownership (A1) | `unify-contract-keep-drivers` | insertion point pinned; **`run.py` aborts** on C3-1×C4 both-ON (§0.2); order owner declared |
| `retrieve.py:1598` comment: *"the only pool-metric mover"* (false) | `keep separate` (stale-comment defect) | Task 2 corrects it to name all three movers **in stage order: C3-1 guard → C4 guard → C2 boost** |
| `hosted_api.get_session_detail` session reader | `keep separate` | genuinely distinct (dashboard API keyed by `Session` node id, no chunk filter, no caps vs retrieval read keyed by `session_id` property with filters+caps); reason recorded in the module docstring |

**Deliberately not filed (latent, no consequence today):** `hosted_api.get_session_detail`'s
`extracted_count` filter (`pointKind IS NULL OR <> 'event'`) would *include* raw chunks
were they ever written to a product graph — verified no product writer emits
`session-transcript` (only `tools/longmem_eval/`), so it is latent;
`volunteer.EXCLUDED_POOL_KINDS` likewise; the two identical `_ask_session_key`
copies. All category B — recorded here, not filed.

---

## 3. Task breakdown (TDD)

### Task 1 — product primitives + shared contract + hermetic unit tests

**Intent:** land the pure rules, the bounded graph pass, and the product-side shared
contract (constant, key authority, guard/re-cap helper).
**Acceptance:** `tortoise/session_reinjection.py` exports the constants,
`SeededSession`, `seeded_sessions`, `source_session_chunk_pass`,
`reinjection_merge_order`; `tortoise/retrieval.py` gains `SESSION_TRANSCRIPT_KIND`
(consumed by `is_raw_chunk`), `session_key_of` (with `dedup_pool` defaulting to it),
`DEFAULT_POOL_*` aliases, and `guard_and_recap_pool` (including the `guard: bool`
ablation path); `coverage_loop._session_of` delegates via a function-local import and
the module stays a stdlib-only leaf. Unit tests prove the bounds, the label-free
seeding, `idx:N` seeds dropped, the deterministic placement (anchor = last base rank
in the pool; accumulation; already-present dropped), **that a base chunk ranked
beyond the seed window is never evicted**, additivity, **that `guard=False` still
applies `dedup_pool`**, `session_key_of` parity with the historical bucket key,
fail-open (`list(pool_ids)` coercion included), and **no import cycle under either
import order**. *(No lint rule forbids the function-local import in
`coverage_loop._session_of`: ruff selects `E,F,B,UP,I,SIM,RUF`
(`pyproject.toml:177`) — no `PLC0415` — and the idiom has ~762 in-tree precedents,
including `coverage_loop.py` itself at `:179,:209,:220,:355,:395,:416`. This is
asserted by the import-order test, not by a lint gate.)*
**Files:**
- Create: `tortoise/session_reinjection.py`
- Modify: `tortoise/retrieval.py` (constant + `session_key_of` + `DEFAULT_POOL_*` +
  `guard_and_recap_pool`), `tortoise/coverage_loop.py` (delegate)
- Create + Register: `tests/test_session_reinjection_rules.py` (added to
  `config/ci-surfaces.yml` in **this** task — a file that exists unregistered reds
  `test_integrity_covers_all_test_files`)

### Task 2 — harness arm wiring + shared-helper refactor

**Intent:** arm the operator; make C3-1 and C4 share one guard/re-cap/annotation
contract; single-source the chunk kind and the `match_source` leg across the eval lane.
**Acceptance:**
- CLI `--session-reinjection` / `--no-session-reinjection`,
  `--session-reinjection-guard` / `--no-session-reinjection-guard`; env
  `TORTOISE_LME_SESSION_REINJECTION`; **`ArmConflictError` raised at arm resolution
  (run.py:3488-3512), threaded into `retrieve_for_question` like `coverage_loop`,
  added to the handler re-raise set (run.py:4490), caught in `_run_main`
  (run.py:6213-6225) with the abort message + `SystemExit(1)`** (§0.2).
- The arm block is inserted after C3-1 and before the C2 boost; the C3-1 block is
  refactored onto `guard_and_recap_pool` + `annotate_pool_additions` with
  **byte-identical behaviour**, **proved by whole-dict equality of all 17 annotated
  keys** for the injected hits (the existing `test_coverage_loop*` assert only
  ids/membership/stats, so they cannot prove this).
- `annotate_pool_additions(hits, props, dates, *, match_source)` is **harness-local**;
  C3-1 passes `"fts"`, C4 passes `"session"`; `"session"` is added to the `Literal`
  (`tortoise/search_engine.py:296`); the two closed-set readers
  (`tests/test_longmem_rerank.py:313-314`, `:569`) and the `_leg_mix` docstring
  (`retrieve.py:801`) are updated; a consistency test asserts reader sets == the
  `Literal`, and a unit test asserts each driver's `leg_mix` label.
- The chunk kind is single-sourced: `tools/longmem_eval/ingest.py` re-exports the
  product constant (keeping `ingest_v2.py`'s `from .ingest import` working);
  `D5_POINTKIND_FILTER` and the new **module-level `CHUNK_KIND_FILTER`** derive from
  it, and the chunk-count Cypher (`retrieve.py:1647`) uses `CHUNK_KIND_FILTER`;
  **the parity test asserting all four consumers lives here**, where all four exist.
- The stale `retrieve.py:1598` comment is corrected to name the three movers **in
  stage order: C3-1 guard → C4 guard → C2 boost**.
- Outcome carries `session_reinjection` + `session_reinjection_stats` with the
  enumerated keys `{on, seed_window, seed_limit, seed_sessions: [str], seeded: int,
  injected_per_session: {sid: int}, injected_total: int,
  injected_per_session_merged: {sid: int}, injected_merged: int, dropped_by_cap: int,
  fetch_ok: bool, total_cap_hit: bool, guard: bool, latency_ms, tr_excluded}`
  (per-session dicts + `injected_merged` are the non-derivable signals; `seeded`/
  `injected_total` are documented reader conveniences, never a second source of
  truth); `r1_knobs` + **always-present resolved-bool fingerprint** entries for both
  keys (§2); the Layer-1 projection entry in `outcomes_to_report`
  (`run.py:4923` precedent) **together with the `tests/test_longmem_runner.py:365/433`
  golden update in this same task** (the golden pins the exact outcome dict, so a
  projection entry without its golden reds that test at this commit); env/flag
  tri-state test; checkpoint-refusal tests (ARM mismatch, GUARD mismatch); the
  consistency test for `_MATCH_SOURCE_LEGS`; **the both-arms-ON abort test**
  (message + exit).
**Files:**
- Modify: `tools/longmem_eval/retrieve.py`, `tools/longmem_eval/run.py`,
  `tools/longmem_eval/ingest.py`, `tortoise/search_engine.py`,
  `tests/test_longmem_rerank.py`, `tests/test_longmem_runner.py`,
  `tests/test_eval_resume_retry_failed.py`, `config/ci-surfaces.yml`
- Create + Register: `tests/test_session_reinjection.py` (arm/unit tests — created
  and registered in **this** task)
- Test: extend `tests/test_coverage_loop*.py` (refactor no-regression + annotation
  golden)

### Task 3 — docker-lane composition E2E

**Intent:** prove the end-to-end behaviour.
**Acceptance:** on a dedicated per-test graph: (a) `session_recall@5` improves on a
monopolised pool with a pool-present starved session; (b) OFF == byte-identical;
(c) fetch failure, zero-new-ids, AND a forced merge-stage exception each return the
base pool unchanged; (d) caps hold (C5 + injected budget + reader item/token);
(e) TR excluded; (f) the guard is a no-op on a single-session pool; (g) guard OFF
still re-caps **through the same function**. Registry mirrors:
`tests/test_eval_resume_retry_failed.py` (hand-built resume fingerprint — add
`session_reinjection=False` + `session_reinjection_guard=True`, the resolved value),
`tests/test_uri_env_mutations_declared.py` (**required**, not optional: this file
necessarily mutates `TORTOISE_DB_URI` via the `_probe` suffix, exactly as
`test_coverage_loop.py` does at `:71`). *(`tests/test_longmem_runner.py`'s golden
update is owned by **Task 2**, where the projection entry lands — see Task 2.)*
**Files:**
- Test: **extend** `tests/test_session_reinjection.py`
- Modify: `tests/test_uri_env_mutations_declared.py`

### Task 4 — measurement + forensic census (retrieval-only, 3 arms)

**Intent:** the honest delta, or an honest report of why it could not be measured.
**Acceptance:**
- A **`--retrieval-only`** run on a **named revision**, with a **named receipt path**
  (`docs/scoping/receipts/2026-09-15-2517-reinjection-<sha>.json`, recorded in the PR
  body).
- **Cohorts are built, not committed.** A committed **cohort builder** (small script)
  materializes both cohorts at receipt time and pins the **selector** in the receipt:
  tail = questions at index range `s[150:250]`; head = the first 50
  **single-session-user**-type questions (a real field — `retrieve.py:1160`).
  (`--data <built file>` is the runtime mechanism — `--limit` is "first N" only,
  `run.py:5284`.) **The built files are written OUTSIDE the repo** — to
  `~/.cache/tortoise-longmemeval/` (`dataset.py:65`, the existing cache root) — because
  `.gitignore:45` is `data/longmemeval*` (no underscore), so a path spelled
  `data/longmem_eval*` would **not** be ignored. If a builder ever writes under
  `data/`, the filename must match the real pattern. A 49 MB slice is not committed.
- **Embedder + graph health stated** (`EmbeddingModel.get()` non-None,
  `BAAI/bge-small-en-v1.5` 384-dim; `falkordb` healthy on 127.0.0.1:6379 — both
  already verified).
- Arms = OFF / `--no-session-reinjection-guard` (injection-only) / ON, each on its
  **own checkpoint per arm per cohort** (`--checkpoint <path>`, 6 runs), with
  **C3-1, #2518, A6, `rerank`,
  `evidence_boost`, `aggregative_flag` all pinned OFF**.
- Per-arm **`recall_all@5`, `evidence_recall@5`, `reader_evidence@5`,
  `chunk_evidence_recall@5`, `reader_surface@k`, `context_tokens`**, per-arm
  retrieval latency (P50/P95) + the injected-query count (expected 1 when fired, 0
  otherwise); **head vs tail reported separately, with per-type breakdowns inside the
  tail**; plus the **forensic census** of the 13% bucket (assumption falsifier).
- The per-session-cap sweep is a measurement-only follow-up (knobs are exposed), not
  a deliverable.
**Files:**
- Create: the cohort builder (e.g. `tools/longmem_eval/build_cohorts.py`) + the
  receipt path (no product code).

---

## 4. Verification checklist

| Surface | Test layer | Expected verification |
|---|---|---|
| seeded-session detection + bounds | unit (hermetic) | only pool-head **real** sessions seed; bounded by window+limit; label-free; `idx:N`/sentinels dropped |
| batched fetch anchored on the seeded hit | unit (fake proj) + integration (docker) | one query per fired question; pool-membership filter in-query; `pool_ids` bound as a list; scope = `Session-[:CONTAINS]->hit` (no `lme_*`, no `p.session_id`); `WITH DISTINCT s` plan barrier; per-session + total caps in `ORDER BY` order |
| additive merge + placement | unit (hermetic) | anchor = last base rank in pool; accumulation deterministic; already-present dropped; a base chunk beyond the seed window survives |
| `guard_and_recap_pool` (shared, `guard:` inside) | unit (hermetic) | ≤ per-session cap in the window; additive; no-op on a single-session pool; C5 re-cap via the pinned key; **`guard=False` still re-caps through the same function** |
| `session_key_of` unify + import direction | unit | `session_key_of` == the historical `dedup_pool` bucket key; `coverage_loop._session_of` delegates; **no import cycle under either import order** |
| chunk-kind single source (4 consumers) | unit | constant == `is_raw_chunk`, `D5_POINTKIND_FILTER`, `CHUNK_KIND_FILTER`, `ingest` re-export; parity test fails closed on drift |
| `match_source` leg vocabulary (4 sites) | unit | C3-1 `leg_mix` == `fts`; C4 `leg_mix` == `session`; both reader closed sets == the `Literal` |
| arm env/flag tri-state + fingerprint refusal | unit | unset/garbage → OFF; only 1/true/yes/on arms; explicit flags beat env; both keys are always-present resolved bools; ARM/GUARD checkpoint mismatch refused |
| both-arms-ON refusal **aborts** | unit + integration | `ArmConflictError` at arm resolution; run aborts with the message + non-zero exit (not per-question failures) |
| C3-1 refactor no-regression | unit + integration | existing `test_coverage_loop*` stay green; **whole-dict (17-key) equality of annotated injected hits** |
| end-to-end backlink | integration (docker) | seeded hit → source-session chunks in the reader window |
| zero-new-ids / fetch-fail / merge-exception | integration (docker) | base pool byte-identical; guard not run |
| TR exclusion | integration (docker) | TR questions untouched; stats `tr_excluded` |
| reader item/token caps | integration (docker) | invariants hold on re-injected pools |
| test registration | CI/unit | each new test file registered in the task that creates it (no dead entry, no unregistered file); the Layer-1 projection golden updated in the same task as the projection entry |
| measurement + forensic census | eval (`--retrieval-only`) | delta or honest substrate report; cohorts built + selector pinned; assumption falsifier answered |

---

## 5. `### Axis Research`

> Phase 1.5, fresh external queries (6 successful) + code-grounding. Prov: `[canonical]` / `[pitfalls]` / `[competitor-precedent]` / `[internal]`.

- **Canonical mechanism names:** **parent-document retrieval** (child→parent id map;
  LangChain `ParentDocumentRetriever`) and **sentence-window retrieval** (matched
  sentence ± N; LlamaIndex `SentenceWindowNodeParser` +
  `MetadataReplacementPostProcessor`); **auto-merging retrieval** is the merge-style
  variant firing on a coverage ratio (`simple_ratio_thresh`). `[canonical]` LangChain
  + LlamaIndex docs. Version pins unverified (JS-rendered) — moot, no dependency.
- **Competitor precedent:** Zep/Graphiti make raw **episodes first-class**, so a
  fact hit can return the episode that supplies context. `[competitor-precedent]`
  Zep v3 docs.
- **Pitfalls that shaped this design:** duplicate flooding of k slots, rank
  displacement, context dilution, precision-for-recall trade. `[pitfalls]`.
- **Metric semantics (decisive, independently confirmed):** adding items from an
  **already-retrieved** session cannot raise a **distinct-session** set metric;
  placed competitively it can only displace/evict. `[internal]` `_recall_metrics`
  (adversarially re-derived).
- **Trigger:** canonical systems expand **always-on on-hit**; **no source** was found
  naming or evaluating a rank-seed trigger. `[pitfalls]`/`[no-source-found]` — the
  deviation is recorded in §1.1.

### Integration Docs

- **In-repo seams consumed (no new dependency):** `tortoise.retrieval.session_key_of`
  / `dedup_pool` / `is_raw_chunk` / `guard_and_recap_pool` / `SESSION_TRANSCRIPT_KIND`
  (new); `coverage_loop.session_diverse_order` / `DEFAULT_LOOP_*` (landed C3-1, PR
  #2567); `tools/longmem_eval/retrieve.py` `point_props_for_hits` / `_annotate_hits` /
  `_speaker_for_turns` / `annotate_pool_additions` / `CHUNK_KIND_FILTER` (new
  harness-local); `ingest.SESSION_TRANSCRIPT_KIND` (re-export).
- **No third-party dependency is added.**

---

## 6. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| **Mark-triggered seeding** | gold leakage in a product path; C3-1 chose a product-safe trigger |
| **Competitive head placement** | cannot raise a distinct-session set metric; only evicts — negative EV |
| **Tail-append placement** | inert: appended chunks fall outside both `hits[:5]` and the reader window |
| **Seeding from the graded window only** | pool-present starved sessions are unreachable; seed window widened to the reader-reachable head |
| **Unconditional session-diverse ranking** | scope creep into C3-1's mechanism (d) and un-attributable; the guard runs only on non-empty `new_ids`, and `guard=False` isolates it **inside** the shared contract |
| **Reader-context-only placement (A6-style)** | cannot move the pool-graded binary; A6 covers the 1-ref-per-point case |
| **Standalone fetch + a new MMR** | duplicates C3-1's guard; the C4 tie mandates one shared contract |
| **Fetching the session's extracted points too** | flood surface without a label-free selector; chunks are the specified material |
| **Re-positioning already-present injected ids** | breaks the additive contract; only genuinely-new ids are merged |
| **Anchoring inside the seed window** | lets an injected chunk evict a base chunk ranked beyond it |
| **Adding `"session"` vs reusing `"fts"`** | reusing `fts` silently mis-attributes a new retrieval leg in `leg_mix`; adding `"session"` is the honest, cheap fix |
| **A second recap entry point for the guard-OFF ablation** | duplicates the guard→re-cap ordering; the toggle lives inside the one contract instead |
| **A separate injected-chunk budget beyond C5** | extends reach at the cost of a second budget dimension the reader guard must police; recorded as a follow-up, not taken silently |
| **A `ValueError` inside the pipeline for the both-arms refusal** | the per-question handler swallows it into N "non-fatal" failures; the refusal is raised at arm resolution and re-raised (§0.2) |
| **Committing a 49 MB cohort slice** | repo convention keeps `data/longmem_eval*` out of git; a committed builder + a pinned selector is reproducible without the blob |
| **Unifying the ask-lane session key with the pool key** | the ask key has a `session_date` fallback — a different identity, not drift |

---

## 7. Wiring check

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `tortoise/session_reinjection.py` (new product module) | module | Task 1 | ✅ |
| `tortoise/retrieval.py` `SESSION_TRANSCRIPT_KIND` + `session_key_of` + `DEFAULT_POOL_*` + `guard_and_recap_pool` | product seam | Task 1 | ✅ |
| `tortoise/coverage_loop.py` `_session_of` delegate | product seam | Task 1 | ✅ |
| `tools/longmem_eval/ingest.py` re-export | eval seam | Task 2 | ✅ |
| `tortoise/search_engine.py` `match_source` `Literal` `+ "session"` | product vocabulary | Task 2 | ✅ |
| `tools/longmem_eval/retrieve.py` `CHUNK_KIND_FILTER` + `D5_POINTKIND_FILTER` + `_leg_mix` docstring + stale comment | eval seam | Task 2 | ✅ |
| `annotate_pool_additions` (harness-local, `dates` + `match_source`) | harness seam | Task 2 | ✅ |
| C3-1 block refactored onto the shared helpers | product/harness seam | Task 2 | ✅ |
| `tools/longmem_eval/retrieve.py` arm block (insertion point pinned) | eval seam | Task 2 | ✅ |
| `tools/longmem_eval/run.py` CLI/env/fingerprint/knobs/projection + `ArmConflictError` + abort presentation | eval seam | Task 2 | ✅ |
| `config/ci-surfaces.yml` (`core:` + `sdk:` + `eval:`), registered in the creating task | CI registry | Task 1/2 | ✅ |
| `tests/test_longmem_rerank.py` closed sets (hoisted `_MATCH_SOURCE_LEGS`) | test registry | Task 2 | ✅ |
| `tests/test_longmem_runner.py` golden projection keys | test registry | Task 2 (with the projection entry) | ✅ |
| `tests/test_eval_resume_retry_failed.py` resume fingerprint | test registry | Task 2 (with the fingerprint change) | ✅ |
| `tests/test_uri_env_mutations_declared.py` docker-lane URI probe | test registry | Task 3 | ✅ |
| `tests/test_session_reinjection_rules.py` (create+register) / `tests/test_session_reinjection.py` (create+register) | tests | Task 1 / Task 2 | ✅ |
| cohort builder + receipt + forensic census | evidence | Task 4 | ✅ |
| Graph schema change | — | none (as built: reads `Point.pointKind` / `is_episodic` / `content` and the `Session-[:CONTAINS]->Point` edge; no `lme_*` or `Point.session_id` read on the turn path) | n/a |

---

## 8. Review cycle log

### scope-verify — cycle 1 (2 reviewers, fresh context)
- problem-verify: **P1 ×1, P2 ×4, P3 ×1**; solution-verify: **P2 ×4, P3 ×4, P4 ×1**.
- **Fixed:** seed window widened + `new_ids` gate + ablation; 13% re-attribution
  `[unverified]` + falsifier; threshold-free independent falsifiers; anchor/placement
  specified; metric rows split; test mirrors; covariate preconditions; chunk-kind
  parity; census schema; `### Axis Research`; research docs cited by SHA.
- **Ignored:** numeric delta thresholds (owner forbade exit-criteria numbers).

### scope-verify — cycle 2 (2 fresh reviewers)
- problem-verify: **P2 ×3, P3 ×3, P4 ×1**; solution-verify: **P2 ×2, P3 ×3, P4 ×1**.
- **Fixed:** seed window = reader item cap + seed-limit constant; anchor → last base
  rank **in the pool**; `injected_merged`/`dropped_by_cap` census; falsifier 1 reworded;
  merge-stage fail-open + forced-exception test; `config/ci-surfaces.yml`; ablation
  boundary pinned; `reader_surface@k` restated; canonical chunk-kind constant in
  `tortoise/retrieval.py`; re-cap `session_key` pinned. **Gate passed** (no P0/P1).

### plan-verify — cycle 1 (Reviewers #1, #2, #5)
- #1: **P1 ×4** (reader_evidence@5 missing; Task 2↔3 circular; 13% census unowned;
  fetch budget wasted on pool-present chunks), **P2 ×6**.
- #2: **P1 ×1** (ci-surfaces is `core:` + **`sdk:`** + `eval:`), **P2 ×6**.
- #5 (advisory): **P0 ×1** (pool-order owner/insertion/refusal undeclared),
  **P1 ×3**, **P2 ×1**.

### plan-verify — cycle 2 (Reviewers #1, #2, #5)
- #1: **P1 ×1** (both-arms-ON swallowed by `run.py:4485`), **P2 ×7**.
- #2: **P2 ×4** (guard toggle inexpressible; chunk-kind under-assigned; Task 4 not
  actionable; Task 2↔3 file ownership).
- #5 (advisory): **P1 ×1** (A6 stale comment), **P2 ×3** (`match_source` leg
  parameterization; guard toggle; session-key unify).

### plan-verify — cycle 3 (Reviewers #1, #2, #5) — cap reached
- #1: **P1 ×1** (`annotate_pool_additions` omits `dates` → `session_date` silently
  empties; the 4-field golden cannot see it), **P2 ×3** (`DEFAULT_POOL_*` aliasing
  direction risks a two-way edge; abort has no run-level marker to assert; committed
  tail slice is repo bloat + head cohort unnamed).
- #2: **P1 ×1** (same `dates` omission), **P2 ×6** (17-key golden must be whole-dict;
  consumer #4 has no addressable seam; committed 49 MB slice; head cohort unnamed;
  test files created before registration; `test_longmem_rerank.py` closed sets
  unlisted; abort marker unassertable).
- #5 (advisory): **P1 ×2** (same `dates`; `match_source` reader closed sets unlisted),
  **P2 ×3** (session-key row verdict wrong; stage-order parenthetical inverted;
  dependency arrow inverted).

### Deep-fix attempt (orchestrator, per plan-review cap-exit path)
- **`annotate_pool_additions(hits, props, dates, *, match_source)`** — `dates` is now
  required (§0.1, §2, Task 2); the no-regression proof is **whole-dict (17-key)
  equality** (§2.3, §4).
- **`match_source` vocabulary unified across all 4 sites** — the `Literal` gains
  `"session"`; both reader closed sets (`tests/test_longmem_rerank.py:313-314`,
  `:569`) and the `_leg_mix` docstring updated; a consistency test added (§2.3, Task 2).
- **Dependency direction pinned one-way** (`retrieval → coverage_loop`), with
  `coverage_loop._session_of` delegating via a **function-local** import; `DEFAULT_LOOP_*`
  **stay in `coverage_loop`** and `retrieval` imports them as `DEFAULT_POOL_*`
  (§0.1); the inverted arrow is corrected.
- **Consumer #4 gets an addressable seam** — module-level `CHUNK_KIND_FILTER`
  (§0.1, §2.3, Task 2).
- **Abort presentation pinned** — `except ArmConflictError` in `_run_main` + message +
  `SystemExit(1)`; the test asserts the message/exit, not a marker that does not
  exist (§0.2, Task 2).
- **Test registration moved into the creating task** (§3 Task 1/2, §4, §7) so no
  commit leaves a file unregistered.
- **Task 4** — a committed **cohort builder** + pinned selector replaces the 49 MB
  slice; the **head** cohort is now named (50 single-session-user); each arm on its
  own `--checkpoint`.
- **Stage-order parenthetical corrected** to C3-1 guard → C4 guard → C2 boost.
- **Session-key row verdict corrected** to `keep separate` with the reason
  (`session_date` fallback), and the two identical `_ask_session_key` copies recorded
  as a category-B note (not filed).
- **`run.py` threads the resolved arm + guard** into `retrieve_for_question`
  (Task 2 acceptance).

### plan-verify — cycle 4 (post-deep-fix re-verify)
- **#1 (proportional): 0 P0/P1** — 2 P2 (`.gitignore:45` pattern misquoted; the
  function-local-import justification cited two tests that do not check it). All four
  cycle-3 findings genuinely resolved (AST-verified 17 keys incl. `session_date`;
  import direction one-way with `coverage_loop` stdlib-only at module level; abort
  reachable and assertable; cohort builder resolves the bloat intent).
- **#2 (proportional): 1 P1** — the Layer-1 projection entry in Task 2 reds
  `tests/test_longmem_runner.py:365/433`'s exact-dict golden, whose update was
  scheduled in Task 3 (the same created-before-registered class as cycle-3 #5, now
  for an *existing* golden). 2 P2 (the rerank closed sets are inline tuples → a
  consistency test cannot reference them; the fingerprint convention contradicted
  the repo's always-present sibling arms) + 1 nit (`CHUNK_KIND_FILTER` referenced a
  module name `retrieve.py` does not bind).
- **#5 (advisory): WEDGED** — no output (1187 s, bound 1200 s). Recorded as
  `NO ISSUES FOUND — DEGRADED (reviewer #5 wedged)`. Advisory: does not affect
  convergence, does not trigger re-dispatch.
- **Controller action (fixed, this revision):** the `tests/test_longmem_runner.py`
  golden update moved into Task 2 (with the projection entry); the `.gitignore`
  citation corrected to `data/longmemeval*` and the builder output moved outside the
  repo to `~/.cache/tortoise-longmemeval/`; the function-local-import justification
  replaced with the true one (ruff `E,F,B,UP,I,SIM,RUF` — no `PLC0415`; ~762 in-tree
  precedents); the rerank legs hoisted to `_MATCH_SOURCE_LEGS` with a `get_args`
  consistency assertion; the fingerprint convention pinned to **always-present
  resolved bools** (matching C2/C3-1/C5), with the guard key governed by the arm's
  OFF and `_resume_fingerprint()` gaining both keys; the `CHUNK_KIND_FILTER`
  expression pinned to the imported name; Task-4 checkpoints made per-arm-*per-cohort*
  (6 runs); URI-mutation declaration marked required; the secondary-caller traceback
  noted as an accepted abort.
- **Gate:** one final confirmation cycle (cycle 5) — a clean verdict from both
  proportional reviewers is a clean completion; any residual P0/P1 → `Requires Human
  Input`.

### plan-verify — cycle 5 (final confirmation)
- **#1 (proportional): 1 P1, 1 P2** — the `tests/test_eval_resume_retry_failed.py`
  fixture is hand-written and consumed by a real resume (`:792/:818`), so Task 2's
  always-present fingerprint keys red it — its update was in Task 3 (the same
  ownership class, for the second golden); and
  `get_args(SearchResult.__dataclass_fields__["match_source"].type)` is
  **unsatisfiable** (`from __future__ import annotations` → `.type` is a string →
  `get_args() == ()`).
- **#2 (proportional): 2 P1, 1 P2** — same two P1s (independently rediscovered);
  plus the P2 that the §2 fingerprint clause set was self-contradictory
  (always-present vs byte-identical-to-today). Both reviewers: **0 P0**.
- **#5 (advisory): not re-dispatched** on this final cycle (wedged in cycle 4;
  advisory findings never gate convergence).
- **Controller action (fixed, this revision):**
  `tests/test_eval_resume_retry_failed.py` moved from Task 3's Files to Task 2's
  (with the fingerprint change); the consistency assertion corrected to
  `get_type_hints`; the false "byte-identical to today's" clause replaced with the
  honest "pre-feature checkpoints refuse (the safe direction)"; the
  `test_coverage_loop.py` URI cite corrected `:154`→`:71`.
- **Gate: CAPPED — `Requires Human Input`.** Cycle 5 still returned P1s, so neither
  the clean-exit condition nor convergence is satisfied and the standard tier's
  Max Cycles (3) is exceeded. Per plan-review's Capped/Stalled exit, the
  orchestrator's deep-fix attempt is spent and the loop halts here. **Every**
  finding raised in five cycles has now been applied to the plan; the remaining
  risk is plan-doc precision, not implementation correctness (0 P0 in every
  cycle; the last two P1s were file-ownership bookkeeping and one test-assertion
  form). The human decision is whether to proceed to implementation.

---

## 9. Complexity

| Domain | Rating |
|---|---|
| Architecture | standard (one new operator over a shared, extracted seam) |
| Research | standard (metric semantics + SOTA mechanisms verified) |
| UX | low (no user-visible surface) |
| Ontology | low (no ontology change) |
