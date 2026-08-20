---
title: "Epic Scope — Epic #1509: Extractor V3 (Stage 3)"
type: plan
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-20
aboutSubjects: tortoise
aboutObjects: extractor
---

# Epic #1509 — Extractor V3: Scope (Stage 3)

*Generated via epic-scope. Inputs: 01-align.md (PROCEED), 02-research-brief.md, the approved 00-scope.md change lists (M/P/E/R/A).*

## Scope Boundaries

### In Scope
- **M — Measurement integrity:** M1 dead-code fix (main can generate reports again); M2 pre-flight API ping + 4xx fail-fast (401/402/403 fatal; **judge key present — OPENAI_API_KEY — explicitly checked**; billing probe uses a realistic S1-sized call to distinguish balance vs per-request cap); M3 extractor retry/backoff + bounded `max_tokens`; M4 retry-then-fix protocol + integrity reporting (per-question `valid`, error census, printed before score — no publish-gate machinery); M5 reader pinned (model + prompt constants for the run); M6 evidence-marking recalibration (source-session attribution + verbatim anchor + raw-chunk containment; N/A-not-0.0 semantics; calibrated against the 52 healthy questions); M7 self-explanatory report (leg-mix, pool size, evidence written/retrieved, error census, write-path cost per run) + run hygiene (workers, checkpoint fingerprint, Python ≥3.12 guard, **dataset recall-semantics re-validation: `answer_session_ids` vs `answer_turn`/`has_answer` coverage vs the LongMemEval paper before trusting turn_recall as a cross-run metric**); M8 statistical discipline (shared-qid deltas, CIs at small n, flip lists).
- **P — Production wiring:** P1 fail-closed capture (extraction errors surface; truthful `extraction_mode`; never `extracted: 0` on LLM failure); P2 provider routing (DeepSeek-direct primary + OpenRouter fallback; adapter ported from tests/ into `tortoise/`; gate matches consumer); P3 rebase to origin/main + CI drift gate; P4 quota/truncation/`client_commit_id` parity + **MITIGATES-on-capture parity** (capture path must write MITIGATES operators like the commit path does) + **hosted-capture `speaker` parity** (SDK writes `speaker` on turn points, hosted doesn't — align).
- **E — Extraction content:** E1 session-date anchoring into S1/S2/S4 (+ `when` slot, event `startedAt`); E2 state-value facts as Points (option A: verbatim value + `quote` + `when`; master-list user-personal-state vocabulary, NO new kind); E3 atomic points + speaker attribution (existing subject mechanism: `quote`/offsets → source-turn role; `aboutSubject`) + `search_keys`; E4 S4 merges-not-replaces; E5 supersession end-to-end (fact-value contradiction detection, length-guarded; persist through ingest — payload + `client_commit_id` + CORRECTS edges; co-retrieve superseding claim; render `[SUPERSEDED BY]`/`[SUPERSES]` in embedded mode).
- **R — Retrieval (real Tortoise):** R1 turn-granular raw chunks + context cap + session dedup (micro-test: 3-point granularity sweep first); R2 OR-tolerant/BM25 sparse (FTS exists — fix strict-AND; query expansion via `search_keys`); R3 dense leg enabled in the eval env (embedder installed, vector strategy verified, write-time point embeddings); R4 structural leg wired (1–2 hop IMPL/NAND expansion, pass a `kind`); R5 temporal/recency (date weight in RRF fusion, TR-constraint detection, time-ordered rendering, embedded-path decoration, events timeline in the retrieval pool — the eval filters entity_type="point" only today, TR questions should see dated Events too); **R6 (in-scope, last) cross-encoder/LLM rerank + MMR diversity** — measured against the V3 baseline in a follow-up run.
- **A — Reader instructions:** A1 partial-knowledge abstention clause (evidence-derived cues only — never the `_abs` flag); A2 aggregation + answer-from-newer instructions in ontology terms (no parallel mechanism).
- **E6 (in-scope, last) — bi-temporal validity windows:** `valid_at`/`invalid_at` (+ `created_at`/`expired_at`) as first-class point properties, set by the supersession consolidation (E5); rendered `[valid …]` markers; default retrieval prefers live state, point-in-time queries restore history (Graphiti pattern, on top of E5's co-retrieval — not replacing it). Measured against the V3 baseline in a follow-up run.
- **E7 — cross-session consolidation pass (Mem0 4-way, write-time):** after E5's plumbing exists, generalize the S3-retrieved-priors comparison to the 4-way decision per new point — **ADD** (new fact), **UPDATE** (same entity+attribute, new value, later date → supersede, REVISES), **DELETE** (contradiction → invalidate soft via status/supersession, never hard-delete), **NOOP** (duplicate → link-only via existing edges, no new point). Includes a **write-time entity-resolution pass (Graphiti two-phase: deterministic exact/match first, LLM fallback for ambiguous names)** — without entity alignment the 4-way can't link "Joe"/"Joseph". Runs against the shared real graph (real-backend eval). This is the KU/MSR amplifier: cross-session aggregation has real linked data, and duplicated facts don't fragment recall. Depends on E1/E3 (dated, attributed points) + E5 (write-path supersession). No new ontology kinds.
- **The run (phased testing protocol — see §Run/Testing Protocol below):** code review → 50-Q pilot → mechanical fixes → full 500-Q run → mechanical fixes → 50-Q confirmation → 1k only if needed. Real-backend eval (real FalkorDB + FTS index + embedder + structural kind), pre-flight checked, integrity-gated. The V3 500-Q run is the V4 baseline. **Follow-up run:** measures R6 + E6 against that baseline.

### Out of Scope
- Calibration-threshold selective abstention (needs calibration data from the first valid run) — defer to V5.
- `.env.example`/deploy-seam documentation — defer (P4 adjacency; fold in later).
- Any ontology change: new kinds, new edge types, expansion packs — explicitly excluded (facts-as-Points, owner-approved; E7's NOOP/DELETE use existing edges + status machinery).
- **NOTE (in-scope additions 2026-08-20):** cross-encoder rerank + MMR (R6), bi-temporal validity windows (E6), and cross-session consolidation (E7) were moved from Out of Scope to In Scope (R6/E6 sequenced-last; E7 sequenced after E5) per owner. The reader A/B (2×2) and MMR-vs-dedup tuning remain V5.

### Boundary Rationale
The cut is governed by TWO principles: **every item either (a) makes the next run trustworthy (M, P, the run protocol), (b) is a code-verified fix toward the vision whose mechanism is confirmed even where its impact is unmeasured (E, R, A), or (c) is the run-calibration-dependent compounding layer (R6 cross-encoder/MMR, E6 bi-temporal) now in-scope-sequenced-last** — landed after the V3 baseline exists, measured in a follow-up run against it. E7 (cross-session consolidation) is a (b) item — its mechanism is code-verified (Graphiti dedupe-at-ingest, Mem0 4-way) and it shares E5's write-path plumbing; it ships with the V3 build, sequenced after E1/E3/E5. Anything that needs the run's calibration data and is NOT yet justified (abstention calibration) is deferred to V5. Ontology is sacred: no new kinds without a separate proposal — E6's validity windows and E7's NOOP/DELETE are additive properties + existing edges (status, supersession, links).

## Customer Value Map

| Scoped Capability | User-Visible Value |
|---|---|
| M1–M8 measurement integrity | Memory answers come with trustworthy scores — a failed run can no longer masquerade as a result |
| P1 fail-closed capture | Sessions never silently lose extraction — if the LLM fails, the caller is told, not faked out with "extracted: 0" |
| P2 provider routing | Extraction survives provider load/cost failures — DeepSeek-direct keeps memory writes alive under concurrency |
| P3 rebase + drift gate | The shipped system is the tested system — no 82-commit drift between branches |
| P4 production parity | Memory written via hosted capture behaves identically to the commit path (same MITIGATES/speaker/commit-id semantics) — no silent divergence in graph state |
| E1 date anchoring | Memory answers "when" questions (elapsed days, ordering, recency) instead of abstaining |
| E2 state-value facts | Concrete facts (personal bests, schedules, preferences) survive extraction verbatim — not compressed into "user runs" |
| E3 atomicity + speaker + search_keys | Facts are findable when asked with different words; assistant suggestions are never mistaken for user facts |
| E4 extraction merge | Facts discovered early in extraction survive the S4 gap-review pass — the pipeline never silently drops findings |
| E5 supersession surfacing | When a fact changes (gym 6pm → 5pm), the current value wins and the change is visible |
| R1 focused evidence | The reader sees compact evidence, not a 35k-token transcript flood — fewer refusals/hallucinations |
| R2/R3/R4 retrieval legs | Memory found by meaning and graph connections, not just exact word matches |
| R5 temporal ranking | Recent facts rank higher; time-bound questions render in order |
| R6 rerank + diversity (last) | The best-matching evidence ranks first (cross-encoder); one session can't monopolize context (MMR) |
| E6 bi-temporal windows (last) | Memory answers "what was true then" — point-in-time restore alongside the current view |
| E7 cross-session consolidation | A fact stated across sessions is one linked fact (not fragmented recall); contradictions across sessions trigger supersession — cross-session questions have real data to aggregate |
| A1 honest abstention | The system says "I don't know" when the fact genuinely isn't there — instead of committing to a near-miss decoy |
| A2 aggregation/supersession reading | Cross-session counts are correct; the newest version of a changed fact is answered |
| The run | The first honest measurement of the extractor — the V4 baseline every improvement beats |

## Complexity Ratings

| Axis | Rating | Rationale |
|---|---|---|
| UX | medium | No UI, but the reader's context rendering + instructions ARE the memory layer's user experience (abstention/aggregation behavior, decoration) — triggers the ux-design-review gate between scope and plan |
| Architecture | high | Harness rebuild + real-backend eval + retrieval legs (dense/sparse/structural) + supersession wiring + provider abstraction — the largest single-phase change to the extractor's write+read paths |
| Ontology | low | Facts-as-Points (option A) — no new kinds/edges/packs; master-list vocabulary + additive point properties only |
| Accessibility | low | No user-facing UI; eval tooling and SDK surfaces only |

### Axis Research Notes

> **Findings date:** 2026-08-20. Provenance: justified skips (cited brief sections) + one codebase-internal feasibility note. Appended findings also recorded in the epic brief's Raw Notes.

- **Architecture (high) — justified skip:** retrieval-stack consensus, the union (chunks ∪ artifacts) evidence, and the real-backend requirement are covered at sufficient granularity by 02-research-brief.md Tech Stack Research (retrieval consensus; controlled-evidence verdict; embeddings; sparse; real backend; provider) and Workflow Pattern Research (write-path cost; eval discipline). No external query needed.
- **Architecture — real-backend eval feasibility (targeted note):** live FalkorDB via docker compose already exists in the repo's dev tooling (`docker compose -f ../eldato/operations/memory/docker-compose.yml up -d`, per AGENTS.md testing section); tests use embedded FalkorDBLite by default. The eval's real-backend mode needs: the FTS index creation on the real graph (the #1468 Object fulltext fix, in the P3 rebase), sentence-transformers installed in the eval env (R3), and `resolve_backend_mode()` returning `real` when `TORTOISE_DB_URI` is set. Feasible; infra cost is one docker compose + env vars.
- **Ontology (low) — justified skip:** facts-as-Points + no-extension-pack is resolved in the brief's Assumptions Register A4 + Tech Stack Research (Ontology paragraph) + ONTOLOGY §3.1. No external query needed.
- **UX (medium) — justified skip:** reader-behavior evidence (bloat anti-pattern, answer shape, evidence-presentation, benchmark-integrity constraint) is covered in the brief's UX Pattern Research. No external query needed.

## High-Level E2E Test Cases

### E2E-1: The eval runs the REAL retrieval stack
**Given:** a real FalkorDB graph with the FTS index created, an embedder installed, and a structural `kind` passed
**When:** a question is asked whose answer is a paraphrased extracted point (different wording than the query)
**Then:** the point surfaces in top-k via the semantic/vector leg
**And:** `match_source` records which leg found it (fts/vector/structural/tfidf), never null
**And (R1 dedup):** per-session chunk count in top-k ≤ the cap — one session family can't monopolize the pool

### E2E-2: A run cannot silently degrade
**Given:** a funded key (pre-flight billing probe passed) and healthy extraction
**When:** the 500-question run executes
**Then:** the integrity block reports `valid=true`, `invalid_rate==0` (or ≤ threshold with justification), and per-question error census
**And:** the report is a real report (not null), with per-question leg-mix + evidence counts

### E2E-3: Evidence marking is non-vacuous
**Given:** a completed run with healthy extraction (E2E-2)
**When:** evidence recall is computed
**Then:** `evidence_points > 0` for >95% of questions and `evidence_recall` is a real number or explicit N/A — never a forced 0.0 on an empty denominator

### E2E-4: Temporal questions are answerable
**Given:** a session whose extractor received its session date (E1)
**When:** a "how many days between X and Y" / "when did Z happen" question is asked
**Then:** the reader answers from date-anchored points instead of abstaining
**And:** dated Events appear in the TR retrieval pool (R5 — the eval no longer filters entity_type="point" only)
**And:** hits render in time order for time-bound questions (R5)

### E2E-5: Concrete facts survive; speaker is attributed
**Given:** a conversation containing "my personal best 5K time is 27:12" (user-asserted, stated early in the conversation) and an assistant suggestion that is NOT the fact
**When:** a knowledge-update / preference question asks for the value
**Then:** the verbatim value (27:12) is retrievable and evidence-marked
**And:** the answer reflects the user-asserted fact, not the assistant suggestion (speaker derived from the source-turn role via `source_turn_id`)

### E2E-6: Superseded facts surface the new value
**Given:** two sessions changing the same fact ("gym at 6pm" → "gym at 5pm")
**When:** a KU question asks the current value
**Then:** the newer value is answered and the superseded point is co-retrieved/marked as superseded
**And:** the supersession chain exists end-to-end in the graph (CORRECTS edges, no drops at write/ingest/read)

### E2E-7: Abstention comes from evidence, not the label
**Given:** an abstention question whose fact is absent from the graph
**When:** the reader is asked (with no `_abs` flag anywhere in the reader path)
**Then:** the reader abstains cleanly, stating what IS present and that the asked info is absent (A1)

### E2E-8: Capture fails closed
**Given:** a dead/misconfigured LLM key on the capture path
**When:** a session is captured
**Then:** turn points still land, extraction errors surface on the response (non-200 or additive `warnings`), and `extraction_mode` is truthful — never a silent 200 "extracted: 0"
**And (failover variant, P2):** with the primary provider dead and the fallback configured, extraction succeeds via the fallback route and `extraction_mode` records the route taken

### E2E-9: Point-in-time restore (bi-temporal, last phase)
**Given:** a fact that changed (gym 6pm → 5pm) with validity windows recorded (E6) and a question asking what the schedule WAS at an earlier date
**When:** the point-in-time query is asked
**Then:** the reader answers from the historically-valid value, with the current value rendered as context
**And:** default retrieval still prefers the live value (E2E-6 still passes)

### E2E-10: Rerank + diversity (cross-encoder/MMR, last phase)
**Given:** a question whose correct evidence is a paraphrased point among many near-duplicate raw chunks from one session
**When:** retrieval returns top-k
**Then:** the cross-encoder reranks the correct point above near-duplicates, and MMR caps per-session chunks (≤1–2) so the context isn't monopolized
**And:** `match_source` records the rerank pass

### E2E-11: Cross-session consolidation (E7)
**Given:** the same fact stated across two sessions with different wording (duplicate) and a different fact contradicted across sessions (update)
**When:** the second session is captured
**Then:** the duplicate resolves to a NOOP link (one linked fact, no fragmentation), and the contradiction resolves to UPDATE (supersede + REVISES, old fact invalidated soft)
**And:** a cross-session question aggregates over the consolidated graph without double-counting

## Run / Testing Protocol (owner-specified 2026-08-20)

Sequenced, fix-as-you-go protocol — the 1k full benchmark is NOT automatic:

| Step | Action | Gate |
|---|---|---|
| 1 | **Code review + bug pass** — all M/P/E/R/A changes reviewed (code-review skill), no known bugs in code | clean review |
| 2 | **Micro-tests** — R1 granularity sweep (3 points: one very low, two middle) + M6 evidence-marking calibration against the 52 healthy questions | knob selected, marking calibrated (pilot and 500 run the chosen value) |
| 3 | **50-Q pilot** — real extractor + real backend + pre-flight (billing, judge key), includes the full-context comparison cell on the subset (option 5) | pilot completes, integrity block readable |
| 4 | **Mechanical + obvious fixes** — whatever the pilot surfaces (retries, keys, marking, context caps) | pilot findings fixed |
| 5 | **Full 500-Q run** — the V3 baseline (V4 comparison point) | integrity.valid=true, error rate ≤ threshold |
| 6 | **Mechanical + obvious fixes** — whatever the 500 surfaces | findings fixed |
| 7 | **50-Q confirmation** — confirmation set = the step-3 pilot questions (for the delta vs step 3) ∪ a regression sample of step-5 failures (for step-6 fixes); expected direction of the delta stated in advance | 50-Q delta confirms the fixes (direction as stated) |
| 8 | **1k full benchmark — ONLY if needed** — for statistical significance at the V4 iteration (per-question cost ~2×; categories n≈30 can't distinguish ±1 flips) | explicit owner decision; harness already supports both sizes |
| 9 | **Follow-up run (R6/E6)** — measures cross-encoder/MMR + bi-temporal windows against the V3 baseline; owner-gated, post-baseline; gives E2E-9/E2E-10 their owning run | owner decision; delta vs V3 baseline |

Notes: option 5 (full-context baseline cell) rides on the pilot (step 3) and the 500 (step 5) on a ~50-question subset — tells us the ceiling / headroom of the memory layer. Steps 3/5 are retry-then-fix (M4): mechanical failures get fixed, not published around.

## Epic Scope Ready for Review

**Scope:** 19 value-mapped capabilities (value-map table rows, incl. the run protocol row) across 27 item-level changes (M×8, P×4, E×7, R×6, A×2) / out-of-scope: abstention calibration, docs seams, any ontology change. R6 (rerank/MMR) + E6 (bi-temporal) in-scope-sequenced-last; E7 (cross-session consolidation) in the V3 build after E5.
**Customer value map:** 19 capabilities mapped to user-visible value (19-row table)
**E2E test cases:** 11 drafted (before user journeys)
**Complexity:** UX medium / Architecture high / Ontology low (E6/E7 = additive properties + existing edges, no new kinds) / Accessibility low

Review the scope boundaries, customer value map, and E2E test cases.
Reply **"proceed"** to continue to detailed planning, or give feedback.
