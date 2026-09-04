<!-- research-path: in-repo (#2164 scoping + commit_ops.apply_supersessions = the reference) -->

# #2193 — Migrate hosted §6b supersession consumer onto shared `apply_supersessions`

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Migrate the hosted commit path's inline supersession consumer (§6b in `_execute_commit_writes`) onto the shared `apply_supersessions` helper — closing the last divergent consumer and the C2 journaling gap.

**Team:** epistemic-team
**Architecture:** Whole-§6b-loop swap (pt_ + entity lanes, hosted_api.py:6687-6741 @a0f5bc47) to ONE `apply_supersessions(proj, sdk, payload.supersessions, session_id=session_id, warn=_logger.warning)` call, mirroring §7's lazy-import + direct-call precedent. Hosted inherits the #2164 guard set (keep-first, never-guess, visible-successor, self-alias, id-style emit) with zero happy-path delta. A2 (client-visible warnings) rejected — breaks the payload-determinism contract. A3 (entity-lane-only) rejected — leaves two pt_ consumers.

### Pattern Research
> **Findings date:** 2026-09-04
> Gate skipped: plan touches zero third-party dependencies — pure in-repo refactor onto an existing, tested helper (#2193 issue: "Research: none needed — in-repo"). PRIOR_RESEARCH: #2164 full scoping + 6 fresh-reviewer code-review cycles of the helper's guard semantics; problem-verify/solution-verify/review-gate cycles for #2193 verified every anchor against origin/main code (one verifier executed Tasks 2-4 LIVE).

### Integration Surface Map
| Surface | Boundary | Test layer | Where |
|---|---|---|---|
| §6b loop → helper call | in-process seam | integration (direct drive) | wiring spy + end-state smoke, parity file |
| POST /v1/sessions/commit entity supersession | HTTP + graph | integration | Test6bEntitySupersessionGuards (a)-(h) |
| ObjectSuperseded GraphEvent payload (session_id) | event store | integration assert | guard (g) json.loads |
| pt_ lane (CORRECTS) | endpoint | integration regression | TestE5PointSupersessions (unchanged) |
| Helper byte-unchanged | regression | capture/eval/projection suites | T6.1 |
| Docs/comments stale refs | sweep | Task 5 (extended scope) | grep sweep |

**Tech Stack:** Python 3.12, pytest (docker lane), FalkorDB. No new deps.

---

### Task 1: Worktree + baseline verification

**Intent:** Edit base off origin/main @a0f5bc47 with a green start so RED flips are attributable to the migration.
**Acceptance:** Worktree on `feat/2193-supersession-migration` @a0f5bc47 (created via hub-worktree.sh — the dirty local checkout predates #2164 and is NEVER an edit base); parity + E5 green at base.
**Files:** none (environment)

**Step 1.1** — Worktree exists (done). Verify base: `git log --oneline -1` = a0f5bc47; `grep -c "def apply_supersessions" tortoise/commit_ops.py` = 1.
**Step 1.2** — `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/test_commit_supersession_parity.py tests/test_commit_endpoint.py -q` → 60 passed.
**Step 1.3** — Confirm §6b region: `sed -n '6687,6741p' tortoise/hosted_api.py` and §7 precedent `sed -n '6752,6766p'`. (Line numbers may drift if main moved — re-anchor the START via `grep -n "for sr in payload.supersessions"`, the TAIL boundary via `grep -n "for pr in reconcile.points"` (replace through the line BEFORE it — never swallow the reconcile.points loop), and §7 via `grep -n "apply_payload_operators"`.)

> **RED-state batching note:** Tasks 2, 3, and 4 must execute as ONE batch — dispatch any verification sub-agent only AFTER Task 4.1's green flip. The deliberate RED failures in Tasks 2.4/3.2 are evidence, not defects: never let a verifier "fix" them (that is Task-4 work), and carry the pre-4.1 red pytest output into the Task-4.6 commit message as RED proof.

### Task 2 (RED): Endpoint negative suite — Test6bEntitySupersessionGuards

**Intent:** Document §6b's CURRENT blind behavior as failing endpoint tests; mirror TestE5PointSupersessions harness. The substance of the migration is the guard set — it must be pinned through the hosted path.
**Acceptance:** (a) happy-path anchor green at base; (b)-(h) FAIL at base pinning blind behavior; all flip green after Task 4.
**Files:**
- Modify: `tests/test_commit_endpoint.py` (add `import json`; suite inserted between `TestE5PointSupersessions` (780-887) and `TestBudgetDE2E7` (line 888))

**Step 2.1** — Add `import json` to stdlib imports.

**Step 2.2** — Add module-level helpers `_seed_objects(client, session_id, names)` (prior-commit seeding of LIVE Objects by name + one net-new point), `_supersede_commit(client, session_id, ref, sby, *, evidence, entities)` (commit whose only supersession work is one entity record; successor rides payload.entities), `_object_row(name)` (graph read via `_team_sdk()._get_proj().g`).

**Step 2.3** — Add `class Test6bEntitySupersessionGuards` with tests (assert graph outcomes / GraphEvent journal — NEVER response["warnings"], which is L1-only):
- (a) `test_happy_path_entity_fold` — seed A+B; supersede A→B → A superseded/supersededBy=B, B live.
- (b) `test_divergent_successor_keep_first` — A→B folded, then A→C → supersededBy STAYS B, C live.
- (c) `test_same_successor_rededup_single_journal` — A→B twice with DIFFERENT evidence strings (E1 then E2 — evidence is part of the supersession canonical in commit_schema canonical_payload, so the ccid changes and the second commit is NOT an L1 replay; identical evidence would replay and make the test pass vacuously at base) → exactly ONE ObjectSuperseded GraphEvent.
- (d) `test_duplicate_name_never_guess` — second carrier raw-written via `_team_sdk()._get_proj().g.query("CREATE (o:Object {name:$n, status:'live'})", ...)` (NO obj_ id — the id-less legacy shape; create_entity/commits would MERGE by name and collapse the dup, making the test vacuous); ref by name → NEITHER folds.
- (e) `test_self_supersession_id_name_alias_stays_live` — superseded=canonical id, supersedes_by=own name → stays live.
- (f) `test_dangling_successor_skipped` — successor never written → ref stays live.
- (g) `test_entity_fold_journals_session_id_in_graph_event` — GraphEvent payload json = {id,name,supersedes_by,session_id,evidence}; session_id == committing session.
- (h) `test_existing_terminal_successor_skips_fold` — seed A+B+C; fold B→C; then record A→B → A STAYS LIVE (successor B is terminal/recall-excluded — visible-successor gate).

**Step 2.4** — Run at base: `uv run pytest tests/test_commit_endpoint.py::Test6bEntitySupersessionGuards -v` → (a) PASS; (b)(c)(d)(e)(f)(g)(h) FAIL (pin §6b's blind behavior; (g) fails on payload keyset — set-equality mismatch, 4 keys no session_id — NOT KeyError).

**Step 2.5** — No commit (red state — repo gate is green-only; RED evidence goes in the Task-4 commit message).

### Task 3 (RED): Wiring-spy test in the parity file

**Intent:** Pin the migration's seam shape — §6b = EXACTLY ONE apply_supersessions call with typed records, session_id, warn=hosted module logger.
**Acceptance:** Spy fails at base (zero calls), passes after Task 4.
**Files:**
- Modify: `tests/test_commit_supersession_parity.py` (append; reuses _pt_id/_seed_baseline/_write_successors/_commit_payload_and_plan/_EXPECTED_END_STATE/SESSION_ID)

**Step 3.1** — Append `test_hosted_commit_wires_apply_supersessions_once(monkeypatch, tmp_path)`: monkeypatch `tortoise.commit_ops.apply_supersessions` with a spy recording (proj, sdk, records, kwargs) that RETURNS `len(records)` (mimic a full apply — the Task-4.1 summary log formats `applied`; a None-returning spy would TypeError inside logging's % formatting); drive `_execute_commit_writes(sdk, payload, plan)`; assert len(calls)==1, proj is sdk._get_proj(), sdk_ is sdk, records == list(payload.supersessions), kwargs["session_id"]==SESSION_ID, **kwargs["warn"] == hosted_api._logger.warning** (`==` NOT `is` — Logger.warning is a bound method, fresh object per access; `is` can never pass).

**Step 3.2** — Run: `uv run pytest tests/test_commit_supersession_parity.py::test_hosted_commit_wires_apply_supersessions_once -v` → FAIL (len(calls)==0 — inline loop never calls the helper).
**Step 3.3** — No commit.

### Task 4 (GREEN): Migrate §6b → one apply_supersessions call; repurpose the parity file

**Intent:** Whole-loop swap (NOT the entity-only slice) + parity repurpose (differential now vacuous — both arms ARE the helper).
**Acceptance:** §6b loop gone; (b)-(h) + spy flip green; E5 green; parity repurposed; helper byte-untouched.
**Files:**
- Modify: `tortoise/hosted_api.py` (replace §6b region)
- Modify: `tests/test_commit_supersession_parity.py` (delete differential; rename arm-alone; rewrite docstrings incl. internal 6140 refs)

**Step 4.1** — Replace the whole §6b comment block + loop (6687-6741) with:
```python
    # ── 6b. Supersessions — client-derived records (the deterministic channel
    # for the Object status fold, #1350), applied via the SHARED
    # apply_supersessions helper (#2164/#2193): pt_<sha> refs → the canonical
    # supersede() CORRECTS (terminal-probed, idempotent); entity records →
    # id-style ObjectSuperseded journal (full provenance incl. session_id) +
    # count-verified fold. ONE consumer-side discipline with capture
    # (_extract_session_v2) and eval ingest_v2 — §6b's inline consumer was the
    # last divergent copy. Guards inherited: terminal keep-first (the fold
    # never blind-overwrites), self-supersession skip, >1-name never-guess,
    # visible-successor gate, legacy id-less canonical-id synthesis. Every skip
    # surfaces via _logger.warning (hosted attribution) — never a silent drop;
    # per-record fail-open (warn-only — never fails the commit). The step-6
    # entity writes above have landed the payload's net-new successors.
    # ──
    from tortoise.commit_ops import apply_supersessions
    applied = apply_supersessions(
        proj, sdk, payload.supersessions,
        session_id=session_id, warn=_logger.warning,
    )
    if payload.supersessions:
        log = (_logger.warning if applied < len(payload.supersessions)
               else _logger.info)
        log("supersessions applied=%d total=%d (session=%s)",
            applied, len(payload.supersessions), session_id)
```
(proj at ~6483 + session_id at ~6485 in scope; the applied-count summary log is the P2-3 observability fix — ops-alertable drop signal, no client-visible change.)

**Step 4.2** — Run endpoint suite → ALL PASS: `uv run pytest tests/test_commit_endpoint.py::Test6bEntitySupersessionGuards tests/test_commit_endpoint.py::TestE5PointSupersessions -v`.
**Step 4.3** — Run parity file → spy PASS; differential + arm-alone PASS.
**Step 4.4** — Repurpose parity file: delete `test_parity_helper_vs_hosted_section6b_identical_end_state` (~208-261); rename arm-alone → `test_hosted_commit_writes_supersession_end_state` (docstring: hosted-path end-state smoke); rewrite module docstring (1-30) + `_commit_payload_and_plan` docstring (drop "hosted_api.py:6140" citation + two-implementation narrative).
**Step 4.5** — Parity file green.
**Step 4.6** — Commit (all three files; RED evidence in message).

### Task 5: Stale-ref sweep (docs + comments + test docstrings)

**Intent:** Remove every "(phase-2) hosted §6b"-deferral/divergence reference the migration eliminates.
**Acceptance:** Sweep clean; no stale citations remain anywhere.
**Files:**
- Modify: `tortoise/commit_ops.py` (139-143, 145-154, 278-279), `docs/event-catalog.md` (line 18), `tests/test_capture_session.py` (1522-1525 edit; ~1461 verify-only — the OUT-OF-BAND/idempotency clause stays TRUE post-migration, no edit), `tests/test_status_projection.py` (~174 verify/edit reframe to LEGACY §6b shape; 258-262, 277 reframe), `docs/ONTOLOGY.md` (132, 357 — annotate #2193 resolved)

**Step 5.1** — commit_ops.py: "(phase-2) hosted §6b" → "hosted commit endpoint (_execute_commit_writes §6b, migrated in #2193)"; drop "deliberate divergence from hosted §6b's blind LIMIT 1" → "a blind LIMIT 1 would fold an arbitrary carrier".
**Step 5.2** — event-catalog.md:18 → all three producers emit via shared apply_supersessions (id-style kwargs incl. session_id).
**Step 5.3** — test_capture_session.py:1522-1526 (divergent keep-first test docstring): replace the WHOLE lead-in + parenthetical block ("This pins the helper's deliberate divergence from hosted §6b's blind clobber (the M5 PHASE-2 GAP: ... NOT fixed in-PR)") ending before "The helper-routed keep-first is the one consumer discipline" → "hosted §6b migrated onto the helper in #2193 — this is now the ONE discipline; the helper-routed keep-first is the one consumer discipline that never blind-overwrites."
**Step 5.4** — test_status_projection.py:258-277 (legacy §6b id-only replay test docstrings): reframe as LEGACY-FORMAT replay compatibility ("historical id-only lines produced pre-#2193 replay unchanged; the hosted producer moved to the kwargs shape in #2193").
**Step 5.5** — ONTOLOGY.md #2193 rows: annotate resolved (fold now keep-first per Object).
**Step 5.6** — Sweep: `git grep -nE "phase-2\) hosted|deliberate divergence from hosted|journals id-only and omits session_id|6140-6185|hosted_api.py:6140|M5 PHASE-2 GAP|T9's parity harness|blind clobber|currently passes the"` → no output (the extended set gates the 5.3/5.4 edits, not just the doc sweeps).
**Step 5.7** — Full supersession suite green + commit.

### Task 6: Full verification + PR

**Intent:** Zero regression across every supersession consumer; ship via repo gate.
**Acceptance:** Full + related suites green; helper byte-unchanged; PR via commit-workflow.
**Files:** none

**Step 6.1** — `uv run pytest tests/test_commit_endpoint.py tests/test_commit_supersession_parity.py tests/test_capture_session.py tests/test_capture_session_supersession_e2e.py tests/test_lme_ingest_v2_supersession.py tests/test_status_projection.py tests/test_extractor_v2.py -q` → PASS.
**Step 6.2** — `git diff a0f5bc47 --stat -- tortoise/commit_ops.py` → comment/docstring lines only (Task 5), no logic delta.
**Step 6.3** — Ruff clean: `uv run python -m ruff check tortoise/hosted_api.py tortoise/commit_ops.py tests/test_commit_endpoint.py tests/test_commit_supersession_parity.py tests/test_capture_session.py tests/test_status_projection.py`.
**Step 6.4** — Delta statement into PR description: A→C terminal conflicts stop clobbering (keep-first); legacy id-less folds now journal; GraphEvent payload gains session_id; log text changes + new summary log; >200-char successor / concurrency-TOCTOU residuals → #2242/#2243.
**Step 6.5** — PR via commit-workflow skill.

## Verification plan
| Indicator | Verification |
|---|---|
| I1 §6b → one apply_supersessions call, warn=_logger.warning-compatible | wiring spy: one call, typed records, session_id, warn == hosted_api._logger.warning |
| I2 C2 closed (id-style emit incl. session_id) | guard (g); event-catalog:18 |
| I3 D6 probe-shape documented | whole-loop swap deletes §6b per-record LIMIT-1 probes |
| I4 divergent blind overwrite fixed | guards (b) keep-first + (c) dedup single journal |
| I5 parity guard green, converted | differential deleted; arm-alone → smoke; spy added |
| I6 hosted commit tests green | E5 + full endpoint file |
| REQ hosted negatives (a)-(h) | Test6bEntitySupersessionGuards |
| REQ visible-terminal-successor fold-skip | guard (h) |
| REQ summary log (P2-3) | Step 4.1 applied-count log |
| REQ sweep extended (test docstrings) | Task 5.3/5.4 |

## Acceptance criteria
1. hosted_api.py has no inline supersession loop — one apply_supersessions call (I1).
2. Test6bEntitySupersessionGuards (a)-(h) green through POST commit; (b)-(h) demonstrated RED at base (I4).
3. GraphEvent payload carries session_id; event-catalog:18 reflects one shared emit path (I2).
4. Parity: differential deleted; smoke renamed; spy added; docstrings rewritten (I5).
5. E5 + full endpoint + capture/eval/projection suites green; commit_ops.py logic byte-unchanged (I6).
6. Sweep clean (REQ).
7. Delta statement in PR (REQ).
