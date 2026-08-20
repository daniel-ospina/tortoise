---
title: "Epic Scope — Epic #1509: Extractor V3"
type: plan
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-20
aboutSubjects: tortoise
aboutObjects: extractor
---

# Epic #1509 — Extractor V3: Scope (approved 2026-08-20)

Status: **SCOPE — approved by owner, pending the one flagged decision (§9, item 2.2).**
Owner decisions incorporated: no-v2-baseline philosophy, retry-then-fix, reader-as-controlled-variable, ontology-first (no parallel layers), micro-test-before-formal, DeepSeek-direct production.

---

## 1. Why

The v2 5-stage extractor was built, production-wired, and measured on LongMemEval. The v2 measurement is **void as an extractor measurement**: 89.5% of questions wrote zero points (21,342× HTTP 402 billing wall on the eval-only direct-DeepSeek adapter), the evidence metric was structurally dead (fired 1/12,085), the reader model *and* prompt changed between runs, and main currently can't even generate a report (`outcomes_to_report → None`). No category delta is attributable to the extractor. Six category re-reviews + three setup audits + three syntheses identified the full fix list; the code-level truths (date-blind S1, supersession computed-and-dropped, dead retrieval legs, silent-partial capture) stand independent of the failed run.

## 2. Operating decisions (owner)

1. **No baseline re-run of v2.** Fix all known issues → that is V3 → run once. **The V3 run becomes the baseline for V4** (learn from the next run, then improve).
2. **Fix mechanical issues when found.** Per-question retry on transient failure; on persistent failure, diagnose and fix the bug. A run with an execution bug stops and gets fixed — no "refuse to publish" machinery needed; the integrity *reporting* (valid flag, error census) is kept so failures are visible, but the mechanism is fix-don't-publish.
3. **Reader is a controlled variable.** Pin model + prompt as constants for the run. No hash infrastructure — just do it properly.
4. **Ontology-first.** Use the existing ontology mechanisms; never create a parallel layer doing the same thing with redundant machinery (debugging nightmare). Nothing changes the ontology without a separate proposal; the one genuine schema decision is §9 item 2.2.
5. **Micro-test before formal.** For tunable parameters (chunk granularity §7 R1), run a small local probe across 3 points of the range (one very low, two middle), pick the best, then test formally.
6. **DeepSeek direct = primary production extractor provider, OpenRouter = fallback.** (Confirmed.)
7. Reversible seams stay: `TORTOISE_EXTRACTOR=v1`, `TORTOISE_SESSION_EXTRACTOR=m2`, provider env.

## 3. The run protocol

1. Land all M (measurement integrity) + P (production) fixes first.
2. Land all E/R/A (extraction content, retrieval, reader) fixes — the V3 build.
3. Micro-tests for tunable parameters (R1 granularity sweep; evidence-marking calibration against the 52 healthy questions' 12,085 points).
4. Pre-flight: verify DeepSeek billing (one realistic S1-sized probe), judge key present, Python ≥3.12.
5. **One run** (baseline+v3 in same session, same reader, same judge). V3 run = V4 baseline.
6. V4 from the learnings.

---

## 4. M — Measurement integrity (required; makes the run valid, not "baseline anchoring")

| # | Change | Effort |
|---|---|---|
| M1 | **Fix dead-code regression on main** (`outcomes_to_report → None`, commit 4acb47d4; restore `build_report`; golden test pinning report shape). First PR of the epic. | S |
| M2 | **Pre-flight API ping + fail-fast on 4xx.** One realistic call per model (extractor/reader/judge) before the loop; 401/402/403 = fatal class, abort with clear message. | S |
| M3 | **Extractor reliability in `_complete`:** retry transient (ConnectionError/ReadTimeout/5xx) with backoff; bounded `max_tokens` on S1/S2/S4 so generation can't blow the adapter read window. | M |
| M4 | **Retry-then-fix protocol (owner §2.2):** per-question retry; persistent failure → diagnose and fix the mechanical issue; the run stops on execution bugs. Integrity *reporting* kept (per-question `valid` flag, error-class census, printed before the score) but no publish-gate flag machinery. | M |
| M5 | **Reader as a controlled variable (owner §2.3):** pin reader model + prompt as constants for the run; same reader across compared cells; record model+prompt in methodology. No hash infrastructure. | S |
| M6 | **Evidence-marking recalibration:** three independent marks — (a) source-session attribution (a point written from an evidence-bearing session), (b) verbatim anchoring (point carries its source `quote`; quote-vs-turn containment), (c) raw-chunk containment (a turn/round chunk containing an answer turn is marked). `evidence_recall` = N/A (not 0.0) when no evidence exists. Calibrate against the 52 healthy questions first. | M |
| M7 | **Report self-explanatory + run hygiene:** persist per-question leg-mix (`match_source`), pool size, evidence written/retrieved, first error text; restore `--workers` + checkpoint hygiene; `sys.version_info >= 3.12` guard. | S–M |
| M8 | **Statistical discipline:** shared-qid deltas primary; confidence intervals / McNemar at small n; per-category flip lists published. | S |

## 5. P — Production wiring (parallel; gated on the rebase)

| # | Change | Effort |
|---|---|---|
| P1 | **Fail-closed capture:** `_extract_session_v2` consults `out["errors"]`; extraction failures surface on the response (non-200 or additive `warnings`/`extraction_errors`); truthful `extraction_mode`; never "extracted: 0" on LLM failure. Test: "turns land, extraction errors surface". | M |
| P2 | **Provider routing (confirmed):** port `DeepSeekDirectModel` from `tests/model_adapters.py` into `tortoise/`; `TORTOISE_EXTRACTOR_PROVIDER` routing — deepseek-direct primary, openrouter fallback; gate checks exactly what the adapter consumes; 4xx fatal. | M |
| P3 | **Rebase to origin/main + CI drift gate** (working tree is 82 commits behind; lacks v2 wiring, #1506 fix, FTS sanitization, memoization). | S |
| P4 | **Parity fixes:** v2-aware quota estimate; stored-transcript truncation parity (5000-char window fed to LLM = stored); `client_commit_id` computed consistently with `supersessions`. | S–M |

## 6. E — Extraction content (the V3 improvements)

| # | Change | Effort |
|---|---|---|
| E1 | **Date anchoring:** thread `session_date` into `extract_session_v2` (value in scope at the call site today, dropped); inject into S1/S2/S4 ("anchor every event/decision/state-change to {date}"); points carry a `when` slot; events carry `startedAt`. Zero new infra. | S–M |
| E2 | **State-value facts (owner decision pending — §9):** concrete attribute values preserved verbatim ("personal best 5K time = 27:12, as of <date>"), not compressed away by the "counts are noise" filter. | M–L |
| E3 | **Atomic points + speaker attribution + `search_keys`:** single-fact granularity; speaker via the EXISTING subject mechanism (see §8 note — turn points already carry `[role]`; point links to source turn via `quote`/offsets; `aboutSubject` edges already exist); 2–4 search keys per point (synonyms + likely question phrasings + verbatim source tokens). All additive fields. | M |
| E4 | **S4 merges, not replaces** (currently `complete_list = s4` silently drops S2 items). | S |
| E5 | **Supersession end-to-end:** (a) fact-value contradiction detection (same entity+attribute, different value, later date) — length-guarded `_token_overlap` (fix false-REVISES at ≥0.6 with no length guard); (b) persist through ingest — `supersessions` in payload + `client_commit_id`, `reason`/`supersedes` passed to `create_point`, CORRECTS edges materialized (SDK `supersede_point`/`correct_point` exist, never called); (c) co-retrieve the superseding claim when a superseded point enters top-k; (d) render `[SUPERSEDED BY]`/`[SUPERSES]` in embedded/TF-IDF mode too. | M–L |

## 7. R — Retrieval (audit results folded in — partial implementations confirmed)

| # | Change | Effort |
|---|---|---|
| R1 | **Turn-granular raw chunks + context cap + session dedup.** **Micro-test first (owner §2.5):** local probe across 3 granularity points (one very low, two middle), pick best, then formal. Keep verbatim transcripts (the leg that carried all recall); stop the 4.4× reader-context flood. | M |
| R2 | **Sparse leg:** EXISTS (search_engine RRF: FTS + TF-IDF fallback + circuit breaker). Change: OR-tolerant/BM25 scoring so a paraphrased point survives one-token mismatch; query expansion from question ∪ `search_keys` (E3). | M |
| R3 | **Dense leg:** EXISTS (`tortoise/embeddings.py` — all-MiniLM-L6-v2, 384-dim, calibrated thresholds 0.40/0.75; vector strategy in search_engine). The eval env lacked sentence-transformers → `EmbeddingModel.get()` returned None, so the vector leg silently degraded. Work: install embedder in eval env, verify the vector strategy runs in the eval path, write-time point embeddings. | S–M |
| R4 | **Structural leg:** EXISTS (`run_structural_query`) but inert (`kind=None → []`). Wire 1–2 hop IMPL/NAND expansion on text hits (owner: "could even be 2 steps"). | S–M |
| R5 | **Temporal/recency:** PARTIALLY EXISTS (search_engine: `_created_sort_key`, recency-ordered support-mass, EP confidence breakdown, "mitigated_by/CORRECTS > recency" ordering). Work: date weight in RRF fusion; TR-constraint detection ("between…and…", "ago", "how many days") → time-window filter + time-ordered rendering; make decoration work in the eval's embedded path. | M |

## 8. A — Reader instructions (prompt-only; ontology-aligned)

| # | Change | Effort |
|---|---|---|
| A1 | **Partial-knowledge abstention clause:** "if the context contains related info but NOT the exact fact asked, state what IS present and explicitly state the asked info is absent." **Never the `_abs` flag** — the reader must infer unanswerability from evidence (absence signal, NAND/supersession markers). | S |
| A2 | **Aggregation + answer-from-newer instructions**, expressed in ONTOLOGY terms (subject refs via `aboutSubject`, supersession edges, source sessions) — not a parallel mechanism (§2.4). | S |

**Note on 2.3 (speaker attribution):** the mechanism already exists and needs to be *used*, not invented — the ontology has `Subject`/`aboutSubject` + provenance (`eventId`); turn points are stored as `[user] …`/`[assistant] …`; S2's master list already has subjects. E3 links each fact to its source turn (via `quote`/offsets) so speaker = source turn role, and emits `aboutSubject` per the existing contract.

## 9. Decision 2.2 — where state-value facts live (research-backed options)

**Research summary (2026-08-20):** Mem0 = flat atomic facts (ADD/UPDATE/DELETE/NOOP), no schema; Graphiti = facts as typed edges between entities with bi-temporal `valid_at/invalid_at`; Letta = agent-edited memory blocks; Hindsight = typed facts (world/experience/**opinion-with-confidence**/**observation**); EverMemOS = 7 memory types incl. a distinct **preferences** type; ODP literature = reified standalone claim nodes vs entity-attribute-value; Palantir best-practice = extend core, don't modify it.

**Tortoise ontology has ALREADY decided this class** (`docs/ONTOLOGY.md` §3.1): *"Evaluations of subjects (expertise, reliability) are Statements (Points) with EP confidence — not edges. Facts = confidence 1.0."* Plus: `aboutSubject` edges exist; `user` is an Object subclass; expansion packs are the sanctioned mechanism for new subclasses (§9 `subclassOf`).

| Option | Shape | Footprint | Assessment |
|---|---|---|---|
| **A — Points (recommended)** | State-value facts as Points (existing kind): `aboutSubject`→user, verbatim value in content, `quote` + `when` fields; "user-personal-state" = a master-list vocabulary + `memory_granularity` entry, NOT a new kind | Minimal — no schema change | Reuses EP confidence (preference strength), provenance, supersession machinery, subject resolution. Aligns with the "evaluations of subjects are Statements" principle. |
| B — Object attributes | Attribute properties on the `user` Object (Graphiti-style typed fact-edges) | Object schema extension | Conflicts with the stated principle (evaluations are Statements, not edges); higher footprint. |
| C — Extension pack | `packs/person-state` declaring new subclasses/vocab via `subclassOf` | Sanctioned mechanism, but over-extension risk (owner's concern) | Justified only if A proves insufficient (e.g. a genuine new kind is needed). |

**Owner decision requested: A (recommended) / B / C.**

## 10. Out of scope (deferred — explicit)

- Mem0-style 4-way cross-session consolidation beyond supersession (post-V4).
- Cross-encoder/LLM rerank; MMR (R1's session-dedup covers the immediate bloat).
- Bi-temporal `valid_at/invalid_at` as first-class fields (co-retrieval of the superseding claim ships first, §E5; windows later if the judge rubric demands point-in-time).
- Calibration-threshold selective abstention (needs calibration data from the first valid run).
- `.env.example`/deploy-seam documentation (P4 adjacency; can fold in later).

## 11. Sequencing

1. **Phase 0:** M1 (dead-code fix) + P3 (rebase) — first PRs.
2. **Phase 1:** M2–M8 (harness) + P1/P2/P4 (production).
3. **Phase 2:** E1–E5, R1–R5, A1–A2 (the V3 build).
4. **Phase 3:** micro-tests (R1 granularity sweep; M6 marking calibration) → pre-flight → **one run** → V3 baseline.
5. **Phase 4:** V4 from run learnings.
