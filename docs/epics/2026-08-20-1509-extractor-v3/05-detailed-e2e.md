# Epic #1509 — Extractor V3: Detailed E2E Test Cases (appendix to 04-plan.md §7)

> Each test: **Layer** (test-design #1515 surface refs) · **Setup** (concrete) · **Given/When/Then** · **Assertions** (verifiable) · **Owned negatives**. E2E-9/10 carry V4-conditional markers per the deferred R6/E6 mechanisms; E2E-1..8 are the V3 gate set.

## E2E-1: The eval runs the REAL retrieval stack
- **Layer:** e2e on live FalkorDB (surfaces 7–11) · **Setup:** `docker compose -f ../eldato/operations/memory/docker-compose.yml up -d`; P3 landed (worktree == origin/main, drift gate green); FTS index created on the real graph (Point/Event/Subject/Document; Object via #1468 fix); `pip install -e '.[embeddings]'`; `TORTOISE_DB_URI` set; harness real-backend mode wired (`resolve_backend_mode() == "real"`, recorded per question).
- **Given:** a question whose answer is a paraphrased extracted point (different wording than the query)
- **When:** retrieval runs with all 4 legs on
- **Then:** the point surfaces in top-k via the vector leg (dense, R3), with the vector leg's contribution visible in the recorded leg-mix (precondition: M7/R3 records per-leg contribution — the engine emits `rrf`/`tfidf` today, `vector` only in unit mocks)
- **And:** a same-session duplicate is deduped from the pool (R1 — per-session chunk count ≤ cap)
- **Owned negatives:** embedder load failure → vector leg recorded `degraded`, never silent; FTS index missing → loud warning + leg recorded `tfidf`; no-match → empty pool with `pool_size` recorded (not an error).

## E2E-2: A run cannot silently degrade
- **Layer:** harness integration (surfaces 19/20/22) · **Setup:** funded key (M2 pre-flight passed), judge key present, fresh checkpoint, workers ≥ 8 under flock.
- **Given:** the 500-Q run executes per the phased protocol
- **When:** the run completes
- **Then:** `integrity.valid == true`, `invalid_rate ≤ threshold` (or justified), error-class census reported
- **And:** the report is a real dict (M1 dead-code fix), with per-question leg-mix + evidence counts + write-path cost
- **Owned negatives:** corrupt checkpoint → fingerprint mismatch → clear abort, refuses stale resume; two workers racing → no lost checkpoint updates, consistent resume; judge key absent → pre-flight aborts with a clear message.

## E2E-3: Evidence marking is non-vacuous
- **Layer:** harness integration + unit (surface 19) · **Setup:** M6 calibration landed (marks = source-session + verbatim anchor + raw-chunk containment); **dataset-semantics re-validation (M7) committed**: audit `answer_session_ids`/`has_answer` coverage in `xiaowu0162/longmemeval-cleaned`, assert field semantics match the recall definitions, record in report methodology.
- **Given:** a completed run with healthy extraction (E2E-2)
- **When:** evidence recall is computed
- **Then:** `evidence_recall`/`turn_recall` are real numbers or explicit N/A — never forced 0.0 on an empty denominator
- **And:** `evidence_points > 0` for >95% of questions (excluding ground-truth-absent abstentions), asserted as a real-number gate per the scope
- **And:** no evidence-bearing question scored with a forced-0.0 empty-denominator; vacuity rate reported over evidence-bearing questions only (ground-truth-absent abstentions excluded), recorded in the report methodology after M6/M7 calibration (run protocol step 6) as the expectation band
- **Owned negatives:** true-abstention question (zero evidence legitimately) does not drag the denominator; paraphrase-only question has evidence marks via the calibrated predicate (n-gram/verbatim anchor).

## E2E-4: Temporal questions are answerable
- **Layer:** integration (surface 28, E1) · **Setup:** `session_date` threaded into `extract_session_v2` (E1); events written with `startedAt`.
- **Given:** a session whose extractor received its session date
- **When:** a "how many days between X and Y" / "when did Z happen" question is asked
- **Then:** the reader answers from date-anchored points instead of abstaining
- **And:** dated Events appear in the TR retrieval pool (R5 — no `entity_type="point"` filter only)
- **And:** time-bound hits render in ascending time order (R5 ordering)
- **Owned negatives:** undated session (no `startedAt`) → no false date-answer, graceful fallback recorded; TR question with no date-bearing evidence → clean abstention (A1).

## E2E-5: Concrete facts survive; speaker is attributed
- **Layer:** integration (surfaces 15/17; E2/E3/E4) · **Setup:** M6 recalibrated marks landed (E2E-5's evidence-marked assertion depends on them); a pinned dataset instance: a KU/preference question with a user-asserted verbatim value + an assistant decoy.
- **Given:** a conversation containing "my personal best 5K time is 27:12" (user-asserted, stated early in the conversation) and an assistant suggestion that is NOT the fact
- **When:** a KU/preference question asks for the value
- **Then:** the verbatim value (27:12) is retrievable and evidence-marked (M6 marks)
- **And:** the answer reflects the user-asserted fact, not the assistant suggestion (speaker derived at read time from the source-turn link → the turn's existing `speaker`/`[role]`)
- **And:** the fact asserted early in the conversation survives the S4 pass (E4 merges-not-replaces)
- **Owned negatives:** assistant-suggestion-only turn → must NOT surface as a user fact (the negative complement); value compressed by the "counts-are-noise" filter → E2 regression, assertion fails loudly.

## E2E-6: Superseded facts surface the new value
- **Layer:** integration (surface 13; E5) · **Setup:** E5 write-path (supersessions in payload + `client_commit_id` 3-site agreement) + CORRECTS edges materialized.
- **Given:** two sessions changing the same fact ("gym at 6pm" → "gym at 5pm")
- **When:** a KU question asks the current value
- **Then:** the newer value is answered; the superseded point is co-retrieved and rendered `[SUPERSEDED BY: …]`
- **And:** the supersession chain exists end-to-end in the graph (CORRECTS edges; no drops at write/ingest/read); terminal-status exclusion does not hide the superseded point (include_terminal opt-in for co-retrieval)
- **Owned negatives:** self-supersede → no point→itself edge, no crash; identical-value re-assertion → NOOP, no new supersession (E7); length-guarded overlap → a 5-token point sharing 3 tokens with a 50-token point is NOT a REVISES.

## E2E-7: Abstention comes from evidence, not the label
- **Layer:** reader unit + e2e (surface 4; A1) · **Setup:** reader pinned (M5); A1 fragment loaded; `_abs` never crosses (assert the reader call site receives `question_type` only).
- **Given:** an abstention question whose fact is absent from the graph
- **When:** the reader is asked (no `_abs` flag anywhere in the reader path)
- **Then:** the reader abstains cleanly, stating what IS present and that the asked info is absent (A1 phrasing)
- **Owned negatives:** decoy-commit case (related-but-not-target fact in context) → the reader states the related fact AND the absence; partial-knowledge case → "state what IS present; explicitly state the asked info is absent".

## E2E-8: Capture fails closed (+ provider failover)
- **Layer:** integration (surfaces 18/21; P1/P2) · **Setup:** fail-closed capture wired; provider routing (DS-direct primary, OR fallback).
- **Given:** a dead/misconfigured LLM key on the capture path
- **When:** a session is captured
- **Then:** turn points still land; extraction errors surface on the response (non-200 or additive `warnings`); `extraction_mode` is truthful — never a silent 200 "extracted: 0"
- **And (failover variant):** with the primary provider dead and the fallback configured, extraction succeeds via the fallback route and `extraction_mode` records the route
- **Owned negatives:** empty/blank conversation → never `ok=True` for nothing committed; fatal 4xx (401/402/403) → must NOT trigger failover (P2 guard); fallback flapping → no infinite provider flip-flop.

## E2E-9: Point-in-time restore (V3 mechanism; window assertions V4-conditional)
- **Layer:** integration (surface 13; E5 + E6-last) · **Setup:** V3 restore mechanism = walk the supersession/CORRECTS chain from the current value to the point whose validity interval covers the target date (no first-class windows needed); E6 window-based assertions marked V4-conditional on the follow-up run.
- **Given:** a fact that changed (gym 6pm → 5pm) and a question asking what the schedule WAS at an earlier date
- **When:** the point-in-time query is asked
- **Then:** the pre-change value returns via the supersession-chain walk; the current value is rendered as context
- **And:** default retrieval still prefers the live value (E2E-6 still passes)
- **Owned negatives:** ambiguous restore (two candidates, unclear interval) → explicit ambiguity signal, no silent wrong answer.

## E2E-10: Diversity + budget-capped context (R1; cross-encoder/MMR assertions V4-conditional)
- **Layer:** integration (surfaces 11/15; R1 + UX-3) · **Setup:** R1 session-dedup + context budget cap; UX-3 rendering (points first, chunks backfill).
- **Given:** a question whose evidence spans many near-duplicate raw chunks from one session
- **When:** retrieval returns top-k and the context is rendered
- **Then:** per-session chunk count ≤ cap (R1 dedup) — one session family can't monopolize the pool; the context stays within the token budget; extracted points render first, raw chunks backfill
- **And (V4-conditional):** cross-encoder rerank orders the correct point above near-duplicates, and the rerank pass is recorded in the leg-mix (R6-last, follow-up run) — asserted only in the post-baseline follow-up run
- **Owned negatives:** duplicate paraphrase across sessions → collapsed (NOOP link), no double-count in aggregation — **depends on E7/E2E-11 machinery** (R1 alone caps per-session chunks at retrieval; it does not create NOOP links); alternatively asserted at R1 level: cross-session duplicate chunks don't double-count in the context pool.

## E2E-11: Cross-session consolidation (E7)
- **Layer:** integration (surface 17; E7) · **Setup:** E7 write-time 4-way on the shared real graph, with the Graphiti two-phase entity-resolution pass; depends on E1/E3/E5.
- **Given:** the same fact stated across two sessions with different wording (duplicate) and a different fact contradicted across sessions (update), plus a fact withdrawn in a later session (retraction)
- **When:** the later sessions are captured
- **Then:** the duplicate resolves to NOOP (one linked fact, no fragmentation); the contradiction resolves to UPDATE (supersede + REVISES, old fact invalidated soft); the withdrawal resolves to DELETE-soft (no resurrect on recall)
- **And:** a cross-session question aggregates over the consolidated graph without double-counting
- **Owned negatives:** ambiguous entity resolution → NOOP link, never UPDATE/supersede; identical-value no-op → NOOP, aggregation count unchanged; self-supersede → guarded.

---

## Negative-case ownership table (each owned by a test above)

| Negative case | Owning E2E | Concrete assertion |
|---|---|---|
| no-evidence | E2E-3/E2E-7 | N/A-not-0.0; clean abstention |
| paraphrase-only | E2E-1/E2E-11 | vector leg surfaces it; NOOP not supersede |
| identical-value no-op | E2E-6/E2E-11 | NOOP link, no new supersession, count unchanged |
| self-supersede | E2E-6/E2E-11 | no self-edge, no crash |
| ambiguous entity | E2E-11 | NOOP link, never UPDATE |
| empty conversation | E2E-8 | never ok=True / silent extracted:0 |
| dead key | E2E-8 | errors surface, failover guard on 4xx |
| corrupt checkpoint | E2E-2 | fingerprint abort, refuses stale resume |
| workers race | E2E-2 | flock, no lost updates |
| undated session | E2E-4 | no false date-answer |
| assistant-only suggestion | E2E-5 | not surfaced as user fact |
| truncated/compressed value | E2E-5 | E2 regression fails loudly |

## Preconditions / fixtures

1. **M6 calibration fixture:** commit the 52 healthy qids + their v2 checkpoint subset (points + evidence marks) as `tests/fixtures/lme_v2_healthy52.json` — the M6 micro-test (run protocol step 2) and E2E-3/E2E-5 depend on it.
2. **Dataset-semantics audit (M7):** committed as a precondition for any `turn_recall`/`evidence_recall` number (E2E-3).
3. **Provider + key preconditions (M2):** pre-flight pings per model; realistic S1-sized billing probe.
4. **P3 rebase:** all real-backend tests gate on "worktree == origin/main, drift gate green".
