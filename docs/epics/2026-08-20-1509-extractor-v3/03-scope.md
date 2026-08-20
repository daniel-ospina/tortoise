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
- **M — Measurement integrity:** M1 dead-code fix (main can generate reports again); M2 pre-flight API ping + 4xx fail-fast (401/402/403 fatal); M3 extractor retry/backoff + bounded `max_tokens`; M4 retry-then-fix protocol + integrity reporting (per-question `valid`, error census, printed before score — no publish-gate machinery); M5 reader pinned (model + prompt constants for the run); M6 evidence-marking recalibration (source-session attribution + verbatim anchor + raw-chunk containment; N/A-not-0.0 semantics; calibrated against the 52 healthy questions); M7 self-explanatory report (leg-mix, pool size, evidence written/retrieved, error census) + run hygiene (workers, checkpoint fingerprint, Python ≥3.12 guard); M8 statistical discipline (shared-qid deltas, CIs at small n, flip lists).
- **P — Production wiring:** P1 fail-closed capture (extraction errors surface; truthful `extraction_mode`; never `extracted: 0` on LLM failure); P2 provider routing (DeepSeek-direct primary + OpenRouter fallback; adapter ported from tests/ into `tortoise/`; gate matches consumer); P3 rebase to origin/main + CI drift gate; P4 quota/truncation/`client_commit_id` parity.
- **E — Extraction content:** E1 session-date anchoring into S1/S2/S4 (+ `when` slot, event `startedAt`); E2 state-value facts as Points (option A: verbatim value + `quote` + `when`; master-list user-personal-state vocabulary, NO new kind); E3 atomic points + speaker attribution (existing subject mechanism: `quote`/offsets → source-turn role; `aboutSubject`) + `search_keys`; E4 S4 merges-not-replaces; E5 supersession end-to-end (fact-value contradiction detection, length-guarded; persist through ingest — payload + `client_commit_id` + CORRECTS edges; co-retrieve superseding claim; render `[SUPERSEDED BY]`/`[SUPERSES]` in embedded mode).
- **R — Retrieval (real Tortoise):** R1 turn-granular raw chunks + context cap + session dedup (micro-test: 3-point granularity sweep first); R2 OR-tolerant/BM25 sparse (FTS exists — fix strict-AND; query expansion via `search_keys`); R3 dense leg enabled in the eval env (embedder installed, vector strategy verified, write-time point embeddings); R4 structural leg wired (1–2 hop IMPL/NAND expansion, pass a `kind`); R5 temporal/recency (date weight in RRF fusion, TR-constraint detection, time-ordered rendering, embedded-path decoration).
- **A — Reader instructions:** A1 partial-knowledge abstention clause (evidence-derived cues only — never the `_abs` flag); A2 aggregation + answer-from-newer instructions in ontology terms (no parallel mechanism).
- **The run:** real-backend eval (real FalkorDB + FTS index + embedder + structural kind), pre-flight checked, ONE run, integrity-gated — the V4 baseline.

### Out of Scope
- Mem0-style 4-way cross-session consolidation beyond supersession — defer to V4.
- Cross-encoder/LLM rerank; MMR (R1 session-dedup covers the immediate bloat) — defer to V4.
- Bi-temporal `valid_at/invalid_at` as first-class fields (co-retrieval of the superseding claim ships first; windows if the judge rubric demands point-in-time) — defer to V4.
- Calibration-threshold selective abstention (needs calibration data from the first valid run) — defer to V4.
- `.env.example`/deploy-seam documentation — defer (P4 adjacency; fold in later).
- Any ontology change: new kinds, new edge types, expansion packs — explicitly excluded (facts-as-Points, owner-approved).

### Boundary Rationale
The cut is governed by ONE principle: **every item either (a) makes the next run trustworthy (M, P, the run protocol), or (b) is a code-verified fix toward the vision whose mechanism is confirmed even where its impact is unmeasured (E, R, A)**. Anything that needs the run's calibration data (rerank thresholds, abstention calibration, consolidation semantics) or is a V4+ compounding layer (bi-temporal windows, cross-encoder) is deferred. Ontology is sacred: no new kinds without a separate proposal.

## Customer Value Map

| Scoped Capability | User-Visible Value |
|---|---|
| M1–M8 measurement integrity | Memory answers come with trustworthy scores — a failed run can no longer masquerade as a result |
| P1 fail-closed capture | Sessions never silently lose extraction — if the LLM fails, the caller is told, not faked out with "extracted: 0" |
| P2 provider routing | Extraction survives provider load/cost failures — DeepSeek-direct keeps memory writes alive under concurrency |
| P3 rebase + drift gate | The shipped system is the tested system — no 82-commit drift between branches |
| E1 date anchoring | Memory answers "when" questions (elapsed days, ordering, recency) instead of abstaining |
| E2 state-value facts | Concrete facts (personal bests, schedules, preferences) survive extraction verbatim — not compressed into "user runs" |
| E3 atomicity + speaker + search_keys | Facts are findable when asked with different words; assistant suggestions are never mistaken for user facts |
| E5 supersession surfacing | When a fact changes (gym 6pm → 5pm), the current value wins and the change is visible |
| R1 focused evidence | The reader sees compact evidence, not a 35k-token transcript flood — fewer refusals/hallucinations |
| R2/R3/R4 retrieval legs | Memory found by meaning and graph connections, not just exact word matches |
| R5 temporal ranking | Recent facts rank higher; time-bound questions render in order |
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
**And:** hits render in time order for time-bound questions (R5)

### E2E-5: Concrete facts survive; speaker is attributed
**Given:** a conversation containing "my personal best 5K time is 27:12" (user-asserted) and an assistant suggestion that is NOT the fact
**When:** a knowledge-update / preference question asks for the value
**Then:** the verbatim value (27:12) is retrievable and evidence-marked
**And:** the answer reflects the user-asserted fact, not the assistant suggestion (source_role via source-turn)

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

## Epic Scope Ready for Review

**Scope:** 14 in-scope capabilities (M×8, P×4, E×5, R×5, A×2, run protocol — clusters, not one-per-line above) / out-of-scope: consolidation, rerank, bi-temporal windows, calibration abstention, docs, any ontology change
**Customer value map:** 14 capabilities mapped to user-visible value
**E2E test cases:** 8 drafted (before user journeys)
**Complexity:** UX medium / Architecture high / Ontology low / Accessibility low

Review the scope boundaries, customer value map, and E2E test cases.
Reply **"proceed"** to continue to detailed planning, or give feedback.
