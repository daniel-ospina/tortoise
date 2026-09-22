<!-- research-path: /tmp/scopes/2490-scope.md (archived on issue #2490) -->

# #2490 Terminal posterior freeze — Implementation Plan

**Goal:** Terminalized claims (superseded/retracted/invalidated/outdated-by-assessment/capture-lane-retracted) decay to vacuity (confidence=0.5, posterior (1,1)) at EVERY terminalizing write + rebuild fold, and every contested-metric reader excludes terminals via one shared predicate — so include-terminal surfaces stop showing frozen pre-terminal confidence and false "contested" signals.

**Team:** team:epistemic-team
**Role:** implementer

**Architecture:** Terminal claims never re-enter EP → their posterior pins at the pre-terminal value (0.904 repro). Fix = write-side vacuity decay appended to the 4 live terminalizing SETs (retract/supersede/invalidate/assess_source) AND the rebuild-fold SETs (_retract, _fold_point_superseded — closing the capture-lane retraction + rebuild replay), plus a shared terminal predicate (status vocab OR outdated=true — reuse live.py `_terminal_excluded` composition) applied to every contested computation (annotate, GraphRanker, StateRanker/GapsRanker, get_contested_claims, Q2-crit queries + Python assembly, sdk `_review_prune`).

### Pattern Research
Skipped — zero third-party deps. Prior: #2422 terminal exclusion machinery (live.py:42-55 `_terminal_excluded`, TERMINAL_EXCLUDED_STATUSES).

### Integration Surface Map
| Surface | Layer | Notes |
|---|---|---|
| 4 live terminalizing SETs (sdk.py:4589-4596 retract CAS, 4511-4515 supersede, 4160-4163 invalidate, 18140-18144 assess_source) | unit | decay fragment appended, crash-atomic with status write. **assess_source rebuild non-durable: emits NO event (no fold) — assess-outdated claims resurrect flagless + frozen-posterior on rebuild_all. #2488 fixes this class for invalidate only; assess_source scoped out + follow-up (2nd-model P1)** |
| fold SETs (_retract entities.py:265, _fold_point_superseded :308) | integration (rebuild) | capture-lane retraction + rebuild replay decay |
| annotate_ep_batch (search_engine.py:1216/1265) | unit | SELECT adds status/outdated; terminal → has_ep=False, contested=False |
| GraphRanker/StateRanker/GapsRanker (ranking.py:411-429, 678-708, 991-1002) | unit | same SELECT + predicate |
| get_contested_claims (ep.py:1156-1167) | unit | terminal-predicate exclusion ONLY (NO has_ep gate — pin) |
| Q2-crit op_crit/op_support (search_engine.py:1488-99/:1543) + assembly (:1590/:1613) | integration | has_ep gated in queries + status tuple extended |
| sdk _review_prune (sdk.py:8815-8825) | unit | contested scan gains terminal predicate |
| why.py:311-315 `_assemble_ep_rows` + `_EP_CYPHER` :425-431 | unit | NOT transitive — why computes contested DIRECTLY from its own existence-anchor read with NO terminal exclusion; gate + RETURN status/outdated (Task 3 step 8 — projection-side override, not a WHERE insertion) |
| analyze.py most_uncertain :81-92, trends :117-119 | unit | `ORDER BY variance DESC` with no status/outdated predicate — decayed terminals (var 1/12 ≈ 0.083 > 0.04) rank "most uncertain" on include-terminal surfaces; add `_terminal_excluded('c')` (2nd-model P1, Task 3 step 8) |
| why.py:569 / topic_summarization.py:293/351 | transitive | topic_summarization safe once annotate gates; why is direct (previous row) |
| volunteer.py:391/674 | transitive | consumes annotate ep dict — safe once annotate gates (map row for sweep completeness) |
| ask-lane fixture | regression | no EP numbers rendered — no impact |

**Tech Stack:** Python 3.12, FalkorDB/Cypher.

---
## Tasks

### Task 1: Shared vacuity decay fragment + 4 live SETs

**Intent:** Terminalizing writers neutralize the frozen posterior atomically with the status write.
**Acceptance:** After retract/supersede/invalidate/assess_source terminalization, claim reads confidence=0.5, posterior (1,1); stable across dream; live behavior otherwise unchanged.
**Files:**
- Modify: `tortoise/live.py` (add VACUITY_DECAY fragment constant + shared terminal predicate — sdk.py already imports live.py:26; entities.py imports only datetime, so the fragment must NOT live in sdk.py — entities.py importing sdk would cycle)
- Modify: `tortoise/sdk.py` (retract CAS :4589-4596 — the SET is at :4592-4593, DO NOT append at 4603-4608 which is the trailing #2422 comment block; supersede block :4511-4515; invalidate :4160-4163; assess_source older-set :18140-18144)

**Steps:**
1. Add the vacuity-decay fragment + alias-parameterized SET-clause builder ONCE in live.py: `VACUITY = (confidence=0.5, posterior_alpha=1.0, posterior_beta=1.0)` + `decay_clause(alias)` → `f"{a}.confidence=0.5, {a}.posterior_alpha=1.0, {a}.posterior_beta=1.0"`. Aliases: retract/supersede/invalidate use `n`; assess_source uses `p`. **Defined once in live.py — NOT 4 inline copies, NOT in sdk.py** (Task 2's projection/entities.py folds import from live.py without a cycle; a helper in sdk.py would force entities.py→sdk.py import = cycle via sdk.py:31 `from .projection import`).
2. Append `decay_clause(alias)` to all four terminalizing SET clauses (crash-atomic with the status/flag write).
3. Confirm ep_alpha/ep_beta remain untouched (prior history preserved; every coalesce reader prefers posterior_alpha first — verified). Re-scope the caveat precisely: "the only ep_alpha-ONLY reads are live-gated" refers to coalesce/posterior readers; the ep_alpha-only has_ep reader (GraphRanker :405-429) is NOT live-gated and is the bug surface — remediated in Task 3 step 6/9.
4. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_ep_terminal_ghost.py -q` — expect PASS (13/13; none read terminal's own columns).
5. Commit: `git add -A && git commit -m "feat(ep): vacuity-decay terminal posteriors at live terminalizing writes"`

### Task 2: Decay in rebuild-fold SETs

**Intent:** Capture-lane retractions (api.py:103-106 → projection.apply → _retract) + rebuild replay also decay. Closes the fifth writer AND answers rebuild parity (post-rebuild terminal reads 0.5, not ep_alpha-coalesced 0.909).
**Acceptance:** A superseded-then-rebuilt claim reads 0.5; capture-lane retraction decays.
**Files:**
- Modify: `tortoise/projection/entities.py:265` (_retract), `:308` (_fold_point_superseded)

**Steps:**
1. Append `decay_clause('n')` (live.py constant from Task 1 — import, don't re-inline) to both fold SETs (both use alias `n`, single SET — appends cleanly, idempotent on replay; pre-#2490 journals replay fine since decay constants need no journal fields).
2. Run: `tests/test_pointsuperseded_rebuild.py tests/test_ep_terminal_ghost.py` — expect PASS.
3. Commit: `git add -A && git commit -m "feat(projection): decay terminal posteriors in rebuild folds"`
4. **Merge-time step (rides #2488) — merge-BLOCKER, not async obligation:** when #2488's `_fold_point_invalidated` lands on main (post-rebase), append `decay_clause('n')` to ITS SET too — otherwise INVALIDATED claims resurrect their frozen posterior post-rebuild while superseded/retracted decay (the exact ghost class this issue eliminates). **assess-outdated claims are NOT covered by this fold** (assess_source emits no event — scoped out per Task 5 step 3 / map row; #2500 covers existing rows, follow-up tracks the assess fold). **#2490's PR must NOT merge until #2488 lands and this step is applied** — the executor note is insufficient as an unattached async obligation (executor may be disengaged post-#2488-merge; #2500 backfill is data-only, not fold logic). Add a rebuild-parity test: invalidate → rebuild → 0.5.

### Task 3: Shared terminal predicate in contested readers

**Intent:** Decayed (1,1) has var 1/12 > 0.04 — WITHOUT this, every terminal flips to "contested" in include-terminal surfaces. One predicate everywhere (status IN TERMINAL_EXCLUDED_STATUSES OR coalesce(outdated,false)) — reuse live.py:42-55 `_terminal_excluded` composition as the single source of truth (search_engine.py:23's `_exclude_status_clause` must delegate, not duplicate).
**Acceptance:** No contested computation lists a terminal claim (carve-out: `_review_prune`'s NAND-challenged branch :8833-37 stays status='live'-only per pre-existing #913 — acceptance scoped to the main contested scan); live unmeasured claims stay contested (pin intact); no circular imports; search_engine's terminal vocabulary DELEGATES to live.py (single source of truth).
**Files:**
- Modify: `tortoise/live.py` (terminal predicate usable in SELECT projections + expose TERMINAL_EXCLUDED_STATUSES for delegation)
- Modify: `tortoise/search_engine.py` (annotate :1216/1265, `_exclude_status_clause` :20-22 DELEGATES to live.py, Q2-crit op_crit :1488-99 / op_support :1543, assembly :1590/:1613)
- Modify: `tortoise/ranking.py` (GraphRanker :411-429, StateRanker :678-708, GapsRanker :991-1002)
- Modify: `tortoise/ep.py` (get_contested_claims :1156-1167 — terminal-predicate exclusion ONLY)
- Modify: `tortoise/sdk.py` (_review_prune :8815-8825 contested scan)

**Steps:**
0. **Operationalize the single-source-of-truth delegation** (P2): search_engine.py:20-22 carries its own parallel TERMINAL_EXCLUDED_STATUSES tuple + WHERE composition used at 8+ sites — replace its vocab with live.py's frozenset import and make `_exclude_status_clause` delegate to live.py's fragment (acceptance check: grep search_engine.py for a second literal terminal vocabulary — must be none).
1. Add `n.status, n.outdated` to the relevant SELECTs (GraphRanker/StateRanker/GapsRanker/_fetch_point_signals ranking.py:411-429 + siblings; annotate_ep_batch search_engine.py:1216/1265). Terminal rows → has_ep=False, contested=False.
2. Gate has_ep by the terminal predicate in BOTH Q2-crit queries (op_crit :1486-1500, op_support :1523-1537 — the :1543 cite is a blank/comment line; has_ep projects at :1533). **Add `other.status, other.outdated` to BOTH query RETURN projections** (tuples unpack positionally at :1503-1505/:1539-1541 — classification of outdated/archived peers is unreachable until the columns are fetched) AND handle op_crit's fetch WHERE :1486 3-status literal via the delegated frozenset (else step 0's no-second-vocabulary grep fails there). **The Python-side assembly (:1590/:1613) filter must extend to the outdated FLAG/COLUMN, not just the status tuple** — a flag-outdated live-status peer is in no status-set member; only the column expresses it (and :1613's tuple must be the delegated live.py frozenset, per step 0's no-second-vocabulary grep). The aligned has_ep boolean is explicit: `(posterior_alpha IS NOT NULL OR ep_alpha IS NOT NULL) AND NOT terminal_predicate` — the status/outdated gate lives INSIDE the has_ep expression, same SELECT edit, because decay-written (1,1) is column-indistinguishable from measured (1,1). `_terminal_excluded` emits a WHERE fragment — add a negated-expression/WHERE-in-CALL adapter for the has_ep projection booleans (small adapter, not drop-in).
3. get_contested_claims (ep.py:1156): terminal-predicate exclusion ONLY. **Do NOT add a has_ep gate** — test_agent_ops_supersede:169 pins that an unmeasured LIVE claim (Beta(1,1) fallback) MUST list as contested.
4. sdk `_review_prune` (8815-8825): add the terminal predicate to the contested scan. **NAND-challenged branch (:8833-37, status='live'-only) gets the flag gate at implementation** — original carve said "status='live'-only stays UNTOUCHED" but that carve rested on the false premise that status='live' implies live: the legacy invalidate class keeps status='live' while setting outdated=true, so the branch double-flagged the terminal as contested AND stale (caught at the 2nd-model code-review gate on PR #2532, repro-verified). Fixed by adding `coalesce(n.outdated, false) = false` to the WHERE — same flag exclusion the variance scan's `_terminal_excluded` applies; live claims are unaffected (outdated null/false).
5. Cross-comment the deliberate semantic asymmetries: (a) `_review_prune` unmeasured=not-contested vs `get_contested_claims` unmeasured=contested — so future edits don't "harmonize" them; (b) has_ep overload: gating terminals to has_ep=False falsifies the documented "EP actually ran" meaning for measured-then-decayed claims — update the annotate_ep_batch/topic docstrings (or add an explicit terminal marker) so consumers (topic disputed-pair gate, volunteer, mcp) don't misread terminal=unmeasured.
6. **GraphRanker has_ep expression asymmetry (:405-429):** its has_ep is `ep_alpha IS NOT NULL` only; StateRanker/GapsRanker use `posterior_alpha OR ep_alpha` — align all three fetchers to the same expression in the same SELECT edit.
7. Verify no circular import (live.py is a leaf; ranking imports search_engine only; search_engine module-level stdlib only — from .live import is acyclic).
8. **why.py direct-read gate + analyze.py variance readers (2nd-model P1s):** (a) why's canonical ep block is built by `_assemble_ep_rows` (why.py:311-315) from its OWN existence-anchor read (`_EP_CYPHER` :425-431, NO terminal exclusion) and computes `contested = has_ep and variance > 0.04` directly — it does NOT consume annotate_ep_batch. Post-decay a terminalized claim requested by id (why explicitly serves terminal ids in the supersession block :487-489) reads (1,1): has_ep=true, var 1/12 → contested on an include-terminal surface. **The gate is a projection/Python-side has_ep OVERRIDE, NOT a WHERE insertion** — filtering terminal rows out of _EP_CYPHER's WHERE would drop the very terminal ids why must serve in the supersession block. Add `n.status, n.outdated` to the RETURN and override has_ep/contested to False for terminal rows in `_assemble_ep_rows`. (b) analyze.py `most_uncertain` (:81-92) and `trends` (:117-119) ORDER BY variance DESC with no status/outdated predicate — add `_terminal_excluded('c')` to both queries so decayed terminals (var 1/12) don't dominate "most uncertain".
9. Run: `tests/test_ep_terminal_ghost.py tests/test_agent_ops_supersede.py` + ranking/search/why/analyze suites — expect PASS incl. the :169 pin.
10. Commit: `git add -A && git commit -m "feat(ep): exclude terminal claims from all contested computations"`

### Task 4: tests/test_ep_terminal_ghost.py decay cases

**Intent:** Pin decay across every surface + reader.
**Acceptance:** listed cases green.
**Files:**
- Modify: `tests/test_ep_terminal_ghost.py`

**Steps:**
1. Per-surface decay: retract/supersede/invalidate/assess_source → confidence 0.5 stable post-dream.
2. Capture-lane retraction decay (EventAPI re-ingest path).
3. Rebuild decay parity: supersede → rebuild → 0.5 (not 0.909); retract → rebuild → 0.5 (retract fold-decay shipped in Task 2 must not go untested).
4. Reader assertions: annotate/GraphRanker/get_contested_claims/_review_prune (variance scan + the NAND-challenged leg — the latter gated for the legacy outdated-flag class at the 2nd-model gate, see Task 3 step 4) all return 0.5 / not-contested / not-flagged for a terminal; **why() direct-read assertion** (id-lookup of a superseded claim → not contested, has_ep=false); W4 relevance-boost never fires on terminal.
5. Live unmeasured claim still contested (pin mirror at test_agent_ops_supersede:169 semantics).
6. Run: `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_ep_terminal_ghost.py -q` — expect PASS.
7. Commit: `git add -A && git commit -m "test(ep): terminal posterior decay across surfaces and readers"`

### Task 5: Docs + #2500 adjacency

**Intent:** Document decay semantics; confirm the one-shot backfill.
**Acceptance:** docs updated; #2500 scope note.
**Files:**
- Modify: `docs/ONTOLOGY.md` (EP one-liner: terminal claims read vacuous 0.5)

**Steps:**
1. ONTOLOGY EP section: one line — terminalized claims decay to vacuity (0.5, (1,1)); ep_alpha/beta retained as prior history. **Decay is UNIFORM across #2421 Case-1 restatement and Case-2 correction** (sound for the old either way — terminal; successor recomputes independently) — but no unsupersede path recovers the old's posterior; ep_alpha/beta retention is the SOLE recovery vector. State both in the one-liner (2nd-model P2).
2. **#2500 backfill predicate MUST include outdated=true + legacy status='outdated' rows (not status-only)** — pre-existing flagged assessments are status='live'. Confirmed still needed for rows frozen pre-deploy (folds fire only on rebuild/event-replay). #2500's body ALREADY carries the correct predicate (status ∈ {retracted,superseded,outdated,archived,deprecated} OR outdated=true) — no edit needed; verify at implementation time.
3. **Rebuild-parity scope + assess_source follow-up (2nd-model P1):** decay rides supersede/retract/invalidate folds only. assess_source emits NO event → its rebuild decay waits on a future assess-outdated fold event (parallel to #2488's PointInvalidated); the one-shot #2500 backfill covers EXISTING assess-outdated rows, but new assess-outdated claims between this merge and that fold still resurrect frozen on rebuild — accepted + tracked as a follow-up issue.
4. Commit: `git add -A && git commit -m "docs: terminal posterior vacuity decay semantics"`

---
**Notes for executor:** env `TORTOISE_TEST_CARVE_OUT=1 PYTHONPATH=$PWD .venv/bin/python -m pytest ...`. **Merge order is a HARD dependency: #2488 MUST merge before #2490** — #2490's Task 2 step 4 (decay on `_fold_point_invalidated`) is a merge-BLOCKER that only executes once #2488 lands and the fold exists; do not open #2490's PR until #2488 is merged and main is rebased into this worktree. Both touch projection folds — expect rebase churn. Ask-lane golden fixtures do not embed EP numbers — no fixture regeneration expected.

---

## Merge-time addendum: eval-why_suite gold rotation (Option A via Option C — approved)

**When:** CI on PR #2532 (this issue) surfaced the standing-bar regression
`conflict_surfacing_rate 0.833 (25/30) < 0.95` on the sealed eval why_suite
(A11 gate).  Root cause: the eval gold's superseded-family expectations
(`conflict_surfacing: true`, `support_chain_sufficient: true` over the 30
conflicted rows) predate this issue's terminal-freeze semantics — the W4-a
E2E-1 twin was rotated IN this PR (denominator 25, superseded not
contested), the eval gold was the MISSED lockstep update, not a genuine
product disagreement.

**Decision (user-approved):** rotate the eval suite to the #2490 product
semantic THROUGH THE SUITE'S OWN RITUAL — never a silent gold edit, never
forcing the product back to fit the ruler:

* Superseded-family gold flips to the RESOLVED contract: `conflict_surfacing:
  false`, `resolved: true`, `support_chain_sufficient: false` (denominator
  30 → 25; the 5 superseded rows grade the new resolved-presentation arm
  instead — `supersession.status == "superseded"` served + never read as a
  live dispute, anti-ghost pin).
* Judge protocol bumped v1 → v2 (`judge_why_suite_v2.txt` rename + re-pin
  of `PINNED_PROTOCOL_SHA256` via the `--bless-protocol` ritual) — the
  resolved arm lives in grading.py/schema.py, which are inside the judge
  digest.
* Gold + `_manifest.json` + pending baselines regenerated via
  `generate_corpus.py` (fixtures_hash changes are the documented
  consequence of a blessed gold rotation; baselines were first-run-pending,
  never published).
* Bar semantics unchanged for the live rows: conflict-surfacing ≥ 0.95 over
  the 25 live conflicted; navigation ≥ 0.95; 0 clean false positives;
  resolved arm = the anti-ghost gate for the 5 superseded.

Dig-deeper navigation coverage is UNCHANGED for the superseded family —
the nand/counter + superseded/successor pointers are still gold-expected
and still graded (only the contested/support flags flipped).

**Files:** `tests/eval/why_suite/{schema,grading,generate_corpus,judge,runner}.py`,
`gold/why_suite.gold.json`, `_manifest.json`, `baselines/*.json`,
`judge_why_suite_v2.txt`, `test_why_suite_{schema,benchmark}.py`, README,
__init__.

**Verification:** full why_suite (schema/grading/ab/benchmark incl. the
`test_run_determinism_and_canfail_standing_bars` CI gate) green on the
amended head.

<!-- plan-review: cycles=5+second-model, status=clean, version=2.3.0 -->
