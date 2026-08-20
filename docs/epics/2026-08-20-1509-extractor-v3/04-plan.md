# Epic #1509 — Extractor V3: Implementation Plan (Stage 4)

*Generated via epic-plan. Inputs: 00/01/02/03-scope docs, the 12-report corpus, the test-design surface map (#1515), UX decisions (1/1/1 evidence-backed defaults).*

---

## 1. User Journeys

Personas: **MU** — memory user (agent/human relying on epistemic memory answers); **OP** — eval operator (the team running the phased protocol).

| J# | Journey | Persona | Entry → Exit | Covers |
|---|---|---|---|---|
| J1 | **Session captured → facts extracted** | OP | A session is committed/captured → extraction runs (S1–S5) → points/entities/events written with provenance; errors surface loudly if extraction fails | E1–E5, P1, E7 (consolidation runs on capture) |
| J2 | **Question answered from memory** | MU | A question is asked → retrieval (4 legs, real backend) → rerank/dedup → decoration → reader answers; abstains cleanly when the fact is absent | R1–R6, A1–A2, M5 |
| J3 | **Fact changes → new value wins** | MU | "gym at 6pm" → "gym at 5pm": the newer value is answered, the superseded one marked; point-in-time restore works | E5–E7, A2, E2E-6/9 |
| J4 | **Operator runs the phased eval** | OP | Code review → micro-tests → 50-Q pilot → fixes → 500-Q run → fixes → 50-Q confirm → (1k only if owner says) → follow-up run (R6/E6) | M1–M8, the run protocol |
| J5 | **Dead key / provider failure** | OP/MU | Pre-flight catches a dead key; provider failover keeps extraction alive; capture never silently reports success | M2, P2, E2E-8 |

Edge cases covered per journey: J1 — empty/blank conversation (never ok=True for nothing committed), LLM timeout/402 (M4 retry-then-fix), truncation parity; J2 — no evidence at all (N/A not 0.0, M6), one-session context monopoly (R1 dedup), paraphrased evidence (R2/R3); J3 — same-value no-op (E7 NOOP), self-supersede guard, ambiguous match; J4 — corrupt checkpoint, stale resume, workers race, judge key absent; J5 — 402 vs 429 classification, fallback flapping, `extraction_mode` truthfulness.

## 2. Workflows

**Write path (capture → extract → consolidate → index):**
1. Capture (fail-closed): turn points land; `_extract_session_v2` consults `out["errors"]`; errors surface on the contract; `extraction_mode` truthful (P1).
2. Extract (two-tier, date-anchored): S1 date-anchored digest → S2 classifies EDUs → Tier A state-value points (verbatim value, `quote`, `when`, `search_keys`, `source_role`) / Tier B narrative points (compressed); S4 merges-not-replaces (E4).
3. Consolidate (E5+E7, on the shared graph): S3 real-backend search (entity-resolved) → 4-way decision (ADD/UPDATE/DELETE soft/NOOP link) → supersession records in payload + `client_commit_id`; CORRECTS edges materialized; validity windows set (E6 later).
4. Index: BM25-OR sparse + dense embeddings (write-time) + raw turn-chunks + events timeline; evidence marked (source-session + verbatim + raw-chunk containment, M6).

**Read path (query → retrieve → rerank/diversify → decorate → reader):**
5. Query parse: entity, attribute, time constraint, aggregation shape; expand via `search_keys`.
6. Retrieve: 4 legs always on (sparse BM25-OR / dense / structural 1–2 hop / raw chunks), RRF + date weight (R5).
7. Rerank/diversify: cross-encoder (R6, last) + MMR/session-dedup (R1) → budget-capped context (points first, chunks backfill — UX decision 3).
8. Decorate: `[SUPERSEDED BY]`, `[valid …]`, absence signal, time-order (UX decision 2); reader with type-fragment prompts (A1/A2; never the `_abs` flag).

**Eval workflow (J4):** the 9-step phased protocol from 03-scope (code review → micro-tests → 50-Q pilot → fixes → 500-Q → fixes → 50-Q confirm → 1k-only-if-needed → follow-up R6/E6). Mechanical failures are retried then fixed (M4), never published around.

**Production ops:** provider routing (DS-direct primary, OR fallback, P2); pre-flight ping per provider before runs and on capture warm-up (M2); fail-closed capture (P1); quota/truncation/commit-id parity (P4).

Failure modes documented per workflow step (surface map #15/#16/#18/#20/#21): silent-skip cluster (S4 warn-only, S5 payload=None), resolver divergence, silent-partial-capture, checkpoint race, fatal-4xx failover guard.

## 3. Prototype (non-GUI — architecture diagram)

```
WRITE PATH                                   READ PATH
─────────                                    ─────────
session ─▶ CAPTURE (fail-closed, P1)         question ─▶ QUERY PARSE
              │                                          │
              ▼                                          ▼
        EXTRACT (two-tier, E1–E4)                  RETRIEVE (4 legs, R2–R5)
         S1 date-anchored digest                    sparse OR-tolerant · dense
         S2 Tier A (state-value) / Tier B           structural 1–2 hop · raw chunks
         S4 merge-not-replace                      ── RRF + date weight ──
              │                                          │
              ▼                                          ▼
        CONSOLIDATE (E5+E7, shared graph)          RERANK/DIVERSIFY (R1, R6-last)
         S3 entity-resolved search                   cross-encoder · MMR · session-dedup
         4-way ADD/UPDATE/DELETE/NOOP              ── budget-capped context ──
         supersessions · CORRECTS · validity              │
              │                                          ▼
              ▼                                    DECORATE (E5/A1, UX-2)
        INDEX (all content, R2/R3)                  [SUPERSEDED BY] · [valid] · absence
         BM25-OR · dense · chunks · events               │
         evidence-marked (M6)                           ▼
                                                  READER (A1/A2, pinned M5)
                                                     │
                                                     ▼
        HARNESS: pre-flight (M2) · integrity gate (M4/M7) · report (M7/M8) ─▶ JUDGE
        PROVIDER: DS-direct primary / OR fallback (P2) · retry/backoff (M3)
```

## 4. Data Model

**No new kinds, no new edge types, no expansion packs** (ontology invariant, owner-approved). All changes are additive properties + existing edges.

| Entity | Change | Type |
|---|---|---|
| Point | **no new `source_role` property** — speaker is DERIVED at read time from the source-turn link (`quote`/offsets → the turn's existing `speaker`/`[role]`); the point carries a `source_turn_id` reference | additive property (E3) |
| Point | `quote` (verbatim source text — already in commit_schema) | existing (E3/M6) |
| Point | `when` (date/validity slot) + `valid_at`/`invalid_at` (E6-last) | additive property (E1/E6) |
| Point | `search_keys` (2–4 aliases + verbatim tokens) | additive property (E3/R2) |
| Point | `source_session_id` (source-session attribution for evidence marking) | additive property (M6) |
| Point | `has_answer` — **eval-instrumentation only** (the LME ground-truth mark for evidence metrics; NOT a production ontology concept); M6 marks raw transcript chunks that contain the answer turn (substring containment) | eval-instrumentation property (M6) |
| Point | state-value Tier-A marker + confidence (EP machinery already exists) — **scope: extraction-selection guidance + optional retrieval/decoration bias, NOT a per-type retrieval pipeline** (see Data Model Research Notes below) | additive property (E2) |
| Point↔Point | CORRECTS edges on supersession (SDK `supersede_point`/`correct_point` exist, never called) | existing edge, now written (E5/E7) |
| Point | NOOP duplicates: additive `duplicates`/link property on the existing point (NOT a new edge — the ontology's Point↔Point edges are IMPL/NAND/hasPart/CORRECTS, none express "duplicate of"; IMPL would couple EP weights — the how-to-use-tortoise hazard; prior art: REPHRASE is a dedup label, not a written operator) | additive property (E7) |
| Session/Event | `startedAt`/session date threaded into extraction (E1) | existing fields |
| Status | superseded points → status fold via existing `ObjectSuperseded` machinery (already shipped #1425) | existing (E5) |

### Data Model Research Notes

> **Findings date:** 2026-08-20. Sources: Hindsight (arXiv 2512.12818 + vectorize docs), EverMemOS (Synix source-level analysis), redhat-ai-americas memory-hub survey. Also appended to the epic brief's Raw Notes.

**Who uses typed-fact classification — and is the Tier-A marker optimal?**
- **Hindsight (benchmark leader: 91.4% LongMemEval) uses exactly this pattern:** every extracted fact is classified at extraction into world / experience / opinion / observation; retrieval is type-aware (a `types` filter narrows which networks are searched); a background **consolidation layer folds facts into observations** (deduplicated, evidence-grounded beliefs with quotes + proof counts, refined-not-overwritten) — structurally very close to our Tier A/B split + E7 consolidation + EP-confidence. It also grounds every fact on TWO temporal axes (occurrence time + mention time), validating our `when` vs `createdAt` split.
- **EverMemOS goes further (7 memory types, per-type extractors/stores/retrieval) but pays infra complexity** (4 backends, no cross-system transactions — a consistency hazard).
- **The adversarial counterpoint (redhat memory-hub survey):** "type classification earns its keep at extraction, not retrieval" — tags don't change how the retrieval pipeline processes a memory (semantic search already surfaces what's relevant), and "if the type genuinely matters it belongs in the memory text itself". Its corollary is exactly our design: Tier-A's value is (a) extraction-selection guidance (the value-filter carve-out: don't strip "27:12") and (b) the verbatim value lives IN the text (via `quote` + verbatim retention).
- **Conclusion: keep the Tier-A marker, refined scope** — it is a classification HINT used for extraction selection + optional retrieval/decoration bias (state questions, current-state rendering), NOT a per-type retrieval pipeline (Hindsight's per-type parallel pipelines are the over-engineering trap; EverMemOS's per-type backends are the anti-pattern). The value must live in the text, which it does.

Integrity constraints: `client_commit_id` covers `supersessions` at ALL 3 compute sites (execute_embed / _post_commit / ingest) — surface map #23/#24 three-site agreement test; length-guarded `_token_overlap` (no false REVISES at ≥0.6 on short points); self-supersede + ambiguous-match guards.

Research check: justified skip — brief Tech Stack Research (ontology paragraph) + ONTOLOGY §3.1 cover facts-as-Points at sufficient granularity; no novel schema pattern (EAV/reification decision already resolved to Points).

## 5. Architecture

Components (boundaries from the surface map):
1. **Extractor** (`extractor_v2.py`) — S1–S5 + consolidation; `_complete` gains retry/backoff + bounded `max_tokens` + 4xx fatal (M3); `session_date` kwarg (E1).
2. **Retrieval** (`search_engine.py` + eval retrieve) — 4 legs + RRF + date weight + rerank/diversity; FTS OR-tolerant fix (R2); vector leg enabled (R3); structural `kind` wired (R4); events in pool (R5).
3. **Reader** (`tools/longmem_eval/reader.py`) — pinned model+prompt (M5); fragments A1/A2; decoration UX-2.
4. **Harness** (`tools/longmem_eval/*`) — pre-flight (M2), integrity gate + report (M7), stats (M8), dead-code fix (M1), checkpoint hygiene + workers (M7).
5. **Production wiring** — provider abstraction (P2), fail-closed capture (P1), parity (P4), rebase (P3).

Failure modes addressed: circuit-breaker exists for FTS (search_engine); provider failover must NOT trigger on fatal 4xx (P2 guard); retry on transient only (M3); capture fails closed (P1); integrity gate prevents garbage publication (M4/M7). Deployment: real FalkorDB for eval (real-run protocol), DS-direct + OR fallback for extraction, judge on OpenRouter gpt-4o.

Research check: justified skip — brief Tech Stack Research covers retrieval consensus, union design, real-backend requirement at sufficient granularity; no novel architecture pattern (this is the competitor-convergent stack + Tortoise's existing machinery).

**Surface ownership (test-design #1515, 28 surfaces → component / deferral):**
1 DeepSeek direct → P2 provider · 2 OpenRouter fallback → P2 · 3 judge gpt-4o → harness M2 · 4 reader LLM → M5/A1/A2 · 5 sentence-transformers → R3 · 6 hosted commit endpoint → P3 rebase · 7 FalkorDB real → eval real-mode · 8 FTS index → R2/P3 · 9 vector index → R3 · 10 FalkorDBLite → eval mode guard · 11 FTS-vs-TFIDF dual stack → R2/M7 leg-mix · 12 graph writes → E1–E5 · 13 supersession CORRECTS → E5 · 14 checkpoint → M7 · 15 pipeline state → M3/M4 · 16 supersession derivation → E5 · 17 consolidation → E7 · 18 capture events → P1 · 19 evidence marking → M6 · 20 workers → M7 · 21 provider failover → P2 · 22 Layer-1 payload → M1/P4 · 23 supersessions payload → E5 · 24 client_commit_id → E5/P4 · 25 reader context format → UX-1/2/3 · 26 extraction_mode → P1 · 27 dataset fixture → M7 · 28 temporal/recency → E1/R5. Every surface owned; none deferred out of the epic.

## 6. Interfaces

Contract-first. All additive / reversible (env seams preserved).

| Interface | Contract |
|---|---|
| `extract_session_v2(model, conversation, *, session_id, chunk_size, session_date)` | new `session_date` kwarg (E1); returns payload + errors + supersessions + warnings (never silent) |
| S2/S4 OUTPUT_CONTRACT | + `source_role`, `search_keys`, `when`, `quote`; Tier-A state-value section (E2/E3) |
| Layer-1 payload | + `supersessions` (already client-carried #1425 — now always populated E5); `client_commit_id` includes supersessions (3-site agreement) |
| Capture response | `extraction_mode` truthful; extraction errors surface (non-200 or additive `warnings`) — never silent `extracted: 0` (P1) |
| Provider routing | `TORTOISE_EXTRACTOR_PROVIDER` = deepseek-direct | openrouter; gate checks exactly what the adapter consumes; 401/402/403 fatal (P2/M2) |
| E7 entity resolution (write-time) | deterministic-first phase; LLM fallback routes through P2/M3; **on LLM failure degrades to ADD (resolution skipped) — never blocks/fails capture** (P1 invariant) |
| Reader context | `Current Date` header + `[SUPERSEDED BY]`/`[valid …]`/absence signals; points-first then chunks under token cap (UX 1–3) |
| Eval report | `integrity` block (valid, invalid_rate, error classes) printed BEFORE score; leg-mix, pool size, evidence written/retrieved, write-path cost, vacuity (M7/M8); N/A-not-0.0 evidence semantics (M6) |
| Checkpoint | code fingerprint (git_sha + config + prompt), refuse stale resume, flock (M7) |

Error responses defined per surface (map #18/#26): LLM 402 → fatal-class abort (pre-flight catches); timeout → retry×2 then per-session error surfaced; corrupt checkpoint → clear abort; missing FTS index → loud warning + leg recorded as `tfidf` (never silent).

## 7. Detailed E2E Test Cases

The 11 high-level E2Es from scope, fleshed to runnable form (setup + assertions). Full detail in **05-detailed-e2e.md** (per-test Layer/Setup/Given-When-Then/Assertions/Owned-negatives, negative-case ownership table, fixtures + preconditions); each maps to surfaces + test layers:
- E2E-1 real stack (surfaces 7–11): real FalkorDB + FTS + embedder + structural kind; per-leg contribution visible in the recorded leg-mix, never null (precondition: M7/R3 per-leg recording — the engine emits `rrf`/`tfidf` today); dedup cap asserted.
- E2E-2 run integrity (19/20/22): integrity.valid=true, report real, leg-mix persisted.
- E2E-3 evidence non-vacuous (19): evidence_points > 0 for >95%, N/A-not-0.0.
- E2E-4 temporal (28, E1): date-anchored answers + dated Events in pool.
- E2E-5 state-value + speaker (15/17, E2/E3): verbatim value retrievable + evidence-marked; user-asserted wins over assistant suggestion; early-asserted fact survives S4 (E4).
- E2E-6 supersession (13, E5): new value wins, superseded co-retrieved/marked, chain end-to-end (CORRECTS, no drops).
- E2E-7 abstention (4, A1): clean abstention, no `_abs` anywhere in the reader path.
- E2E-8 capture fails closed (18, P1) + failover variant (21, P2).
- E2E-9 point-in-time restore (13/14, E6-last).
- E2E-10 rerank + diversity (R6-last, R1).
- E2E-11 consolidation (17, E7): NOOP link + UPDATE supersede across sessions; no double-count.

Negative cases per test: see the ownership table in 05-detailed-e2e.md (12 negatives, each owned by a test with a concrete assertion). E2E-9/E2E-10 carry V4-conditional markers (restore via supersession-chain walk in V3; window/cross-encoder assertions only in the post-baseline follow-up run).

## 8. Coherence Review + Risk Analysis

**Cross-substep consistency checkpoints:** Journeys↔E2E (J1–J5 ↔ E2E-1..11 all covered); Data Model↔E5/E7 (no new kinds — verified); Architecture↔Surface map (28 surfaces each own a component or explicit deferral); Interfaces↔UX decisions (reader context format implements UX 1–3); E2E↔scope (11 high-level tests all detailed).

**Risks & mitigations:**
| Risk | Mitigation |
|---|---|
| V3 bet doesn't pay on a valid run (parity vs raw) | Success criteria per-category (A9); union design evidence; run protocol isolates (pilot → fixes → 500) |
| Real-backend infra friction (FalkorDB docker, FTS, embedder) | E2E-1 gates it early; cut order protects E7 (thesis) over R2/R5 |
| 402 recurrence (billing vs cap unknown) | M2 pre-flight with realistic S1-sized probe; fatal-4xx class; budget assumption A6 |
| Consolidation quality weak (entity resolution) | E7 includes Graphiti two-phase entity resolution; E2E-11 |
| E7 entity-resolution LLM failure on the write path | deterministic-first phase; LLM fallback degrades to ADD (resolution skipped) — never blocks capture (P1); routes through P2/M3 |
| Reader confounds survive (context shape) | M5 pinning; report records context tokens; dataset recall re-validation (M7) |
| 50-Q pilot too small to surface failures | Confirmation set = pilot ∪ regression sample of 500 failures (run protocol step 7) |
| Scope creep (R6/E6/E7 pulled in) | Sequencing: E7 with build, R6/E6 post-baseline; deferrals explicit |

**Improvement opportunities flagged:** merge M7-cost into M7 (done); the evidence-marking calibration against the 52 healthy questions can start BEFORE the build (no run needed) — schedule as a micro-test step 2.

**Ready for decomposition:** yes — the child-issue breakdown follows the M/P/E/R/A clusters with dependencies (**P3 rebase + drift gate is the global first dependency alongside M1** — every real-backend E2E gates on 'worktree == origin/main'; E7 after E1/E3/E5; A1 wired to E2E-7 and A2 to E2E-6/9; R6/E6 after the V3 baseline).
