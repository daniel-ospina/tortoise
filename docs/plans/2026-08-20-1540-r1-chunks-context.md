<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# R1 — Turn-Granular Raw Chunks + Context Cap + Session Dedup (+ 3-point granularity micro-test)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Replace whole-session raw-transcript blobs with turn-granular raw chunks, cap the reader's context by token budget (extracted points first, raw chunks backfill — UX decision 3), dedup per-session chunks so one session family can't monopolize the pool, and run the 3-point granularity sweep (run protocol step 2) to select the knob for the pilot + 500-Q run.

**Team:** epistemic-team
**Role:** _(omitted — no AGENT_SESSION_ROLE in this session)_

**Architecture:** Three coordinated changes on the LongMemEval read/write path. (1) **Ingest** (`tools/longmem_eval/ingest.py` + `ingest_v2.py`): a shared chunking helper replaces the single whole-session `session-transcript` point (`lme:{qid}:s{si}:raw`, ~17k tokens/session — the measured 4.4× context bloat) with non-overlapping verbatim turn windows (`lme:{qid}:s{si}:c{ci}`, `chunk_turns` knob), carrying `lme_chunk_index`/`lme_chunk_turns`; v2 mode additionally marks chunks `has_answer` by raw-chunk containment (M6) — **written before extraction so verbatim retention survives extractor failure**. (2) **Retrieval** (`retrieve.py`): candidates are fetched at a larger pool depth (headroom so a monopolizing session can't crowd others out pre-dedup), the pool is deduped (per-session chunk cap), and a pure `_assemble_context` builds the budget-capped, points-first context (UX-3); recall@k is computed on the deduped pool (the pool is the retrieval contract, E2E-1) with the evidence denominator restricted to extracted points (`chunk_evidence_recall@k` reported separately — avoids a granularity-bias confound). (3) **Read-path wiring** (`run.py`/`report.py`): the reader consumes the budget-capped `context_points` (fixing the latent hits-vs-top_k mismatch), knobs are threaded through CLI/env with validation, and the report records the chosen knob values + recall semantics for provenance. Owner invariant honored throughout: raw verbatim evidence is retained and searched — extraction never replaces it.

---

## Pattern Research

> **Findings date:** 2026-08-20

> **Gate skipped: zero third-party dependencies.** The plan touches no new third-party libraries — chunking is pure Python slicing over the existing `_session_transcript` renderer; retrieval uses the in-repo hybrid engine (`TortoiseSDK.tortoise_fts_query`); the reader/judge wiring is the existing in-repo provider pattern. All design-relevant external evidence (context-bloat anti-pattern, LightMem budget-dependence, Verbatim-Chunks union verdict) is already triangulated in the epic brief's `### UX Pattern Research` / `### Tech Stack Research` and `## Raw Notes` (2026-08-20) — no fresh queries needed. The R1 chunk-granularity choice is an **empirical sweep, not an external-knowledge question** (run protocol step 2 — exactly why the micro-test exists).

Prior-research anchors consumed (Step A — epic brief + v2 measurement):
- **Context bloat (measured):** v2 fed the reader ~35k tokens of whole-session transcripts (**4.4× baseline**) → refusals/hallucination under flood (9/18 TR losses had full session recall yet failed). Baseline context ≈ 8k tokens → the default cap is set at 8000.
- **LightMem reproduction:** constructed-memory advantage **vanishes as the answering budget grows** (~330 tokens: +5.5pp; ~935 tokens: small disadvantage) → compact points win only under tight budgets → cap + structure context, don't flood.
- **Verbatim-Chunks (arXiv 2601.00821):** the union (chunks ∪ artifacts) matches chunks; artifacts alone forfeit ~22pp → raw chunks are the recall floor and must stay in the pool AND in the rendered context (points-first *backfill*, not points-only).
- **MemDelta/benchmark-integrity:** pin the reader; record leg-mix and knob values in methodology (A10).

---

## Design Decisions

### D1. Chunking scheme — non-overlapping verbatim turn windows
- Replace the whole-session blob point (`lme:{qid}:s{si}:raw`, content = `_session_transcript(session)`) with non-overlapping consecutive windows of `chunk_turns` turns, rendered with the same role-prefixed verbatim format (`_session_transcript` on the window slice). Union of chunks == full session → verbatim coverage preserved (owner invariant: extraction never replaces verbatim evidence).
- Chunk ids: `lme:{qid}:s{si}:c{ci}` (ci = 0-based window index). **The `:raw` id is retired** (only consumers: tests + `ingest_v2.py` — verified by grep; every eval question uses a fresh isolated graph, so no live graph carries a stale blob).
- Chunk point properties (all additive): `pointKind=SESSION_TRANSCRIPT_KIND` (**reused — no new kind**, epic ontology invariant), `lme_question_id`, `lme_session_index`, `session_id`, `lme_chunk_index` (new, eval-instrumentation, window index), `lme_chunk_turns` (new, eval-instrumentation, **the ACTUAL number of turns in the window** — `len(turn_idxs)`; the remainder window of a 3-turn session at `chunk_turns=2` carries `lme_chunk_turns=1`. The knob VALUE used is recorded in report methodology, not on the chunk), `is_episodic=True`, `status="draft"`.
- `has_answer` marking (D5) is mode-dependent.
- **Non-overlapping, not sliding:** sliding windows create near-duplicate chunks — the exact "many near-duplicate raw chunks from one session" pathology E2E-10 targets. Non-overlapping keeps the pool clean and retrieval deterministic. (Open Q4 revisits if the sweep shows coverage gaps.)
- Last window may be shorter than `chunk_turns` (e.g. 3-turn session at chunk_turns=2 → windows [t0,t1], [t2]).
- **Validation:** `chunk_turns` must be ≥ 1 — `_session_chunks` raises `ValueError` otherwise (`chunk_turns=0` → `range(step=0)` crash mid-ingest; negative → silently zero chunks = verbatim leg silently deleted, the owner invariant violated). CLI rejects it too (T4).

### D2. Granularity knob + sweep points (run protocol step 2)
- Knob: `chunk_turns` (turns per window). Sweep points: **{1, 2, 4} — one very low (1), two middle (2, 4)**. Default code value: `2` (working default until the sweep selects; the pilot and 500-Q run use the *selected* value — run protocol steps 3/5).
- Sweep harness: `tools/longmem_eval/sweep_granularity.py` (T5) runs the eval over a small question subset across the 3 points with all other knobs fixed, and emits a comparison table + a deterministic winner.
- Selection rule (recorded in the sweep output, consumed by the run protocol step 2 gate): **v2 mode** — maximize `evidence_recall@10` (extracted-point evidence; the denominator is granularity-neutral, D5), with `chunk_evidence_recall@10` and `context_tokens_mean` recorded alongside; subject to `context_tokens_mean ≤ context_token_cap`; tie-break → smaller `chunk_turns` (finer evidence localization, less bloat). **Deterministic mode** the selection metric is knob-insensitive (chunks unmarked, D3; turn recall over uncapped turn points) — the deterministic sweep cell is a context-token/underfill-only view, not a granularity selector.

### D3. Per-session dedup — cap applies to the raw-chunk leg
- Cap: `max_chunks_per_session` (default `2`, aligned with E2E-10's ≤1–2 MMR cap; the R6 MMR variant tunes it post-baseline). Applied in search rank order — keep the top `cap` chunks per session.
- **Bucket key: `session_id` when present, else `lme_session_index`** (formatted as a string key) — hits with a missing/invalid index must NOT collapse into one shared `-1` bucket (that would over-dedup chunks from *different* sessions together). Never silently merges sessions.
- **Scope of the cap: raw chunks only** (`pointKind == session-transcript`). Extracted points (v2 `statement`) and deterministic turn points (`event`) are the compact epistemic surface and stay uncapped (they carry the evidence marks; capping them risks dropping evidence).
- The cap applies to the **pool** (before context assembly) — E2E-1's "per-session chunk count in top-k ≤ the cap" is a pool contract. `ret["hits"]` **is** the deduped pool (pinned — T3 Acceptance). Recall@k is computed on the **deduped pool** so metrics reflect what the reader could see (honest; small intended semantics change vs today — only binds when >cap chunks of one session rank in).
- **Deterministic-mode evidence semantics preserved:** deterministic chunks are written **unmarked** (`has_answer` unset) so the deterministic leg keeps its turn-id evidence path (`evidence_turn_ids`) — marking them would flip `retrieve.py` into the v2 evidence-marks branch and silently change baseline turn-recall semantics. v2 chunks are marked (D5).
- **Validation:** `max_chunks_per_session` ≥ 1 (0 would silently delete the raw-evidence leg) — CLI + function boundary.

### D4. Budget-capped context — points first, chunks backfill (UX decision 3)
- Knob: `context_token_cap` (default `8000` ≈ the pre-v2 baseline context size, a 4.4× reduction from the measured 35k flood; LightMem: compact evidence wins under tight budgets). Env `TORTOISE_LME_CONTEXT_CAP`, CLI `--context-cap`. Validated `≥ 1` (cap=0 → empty context; if it happens the empty result is recorded honestly, never silent).
- Assembly (`_assemble_context`, pure function in `retrieve.py`), input = deduped pool truncated to `top_k` items (top_k retains its documented meaning — the max number of context items; the token budget then bounds it further):
  1. Partition into **points** (non-`session-transcript` kinds) and **chunks** (`session-transcript`), each preserving search rank order.
  2. Greedily append points (rank order), then chunks (rank order), while the cumulative rendered-token estimate ≤ cap. **Oversized-hit policy: skip-and-continue** — a hit whose own cost exceeds the cap is dropped, later hits still append (a single oversized rank-1 hit must not starve the whole context).
- **Exact token accounting (the alignment invariant):** factor a per-hit block renderer `_render_block(h)` out of `render_context` (single shared implementation — `render_context(text) == header + "\n\n".join(_render_block(h) for h in hits)`); per-hit cost = `len(_render_block(h).split())` (raw whitespace words — `question_date` affects ONLY the once-prepended `Current Date:` header, never per-hit blocks; per-hit dates come from the hit's own `session_date`); header cost = `len(f"Current Date: {question_date}".split())` when set. Accumulate RAW word counts and apply the 1.1 markup multiplier ONCE to the joined total — then `context_tokens == _estimate_tokens(render_context(context_points, question_date))` holds **exactly** (no per-block `int()` drift). The alignment test pins this.
- `render_context` output stays byte-identical for non-chunk hits (backward compatible).
- **Estimator limitation (documented, tested):** `_estimate_tokens` counts whitespace-separated words ×1.1 — a long whitespace-free turn (URL/base64/code) is undercounted. The cap is enforced in estimate-space; the `token_estimator` methodology string already records the estimator; a pathological-content test (T6) guards that a single such turn cannot silently reproduce the 35k flood.

### D5. Evidence marking on chunks (v2 mode only — M6 raw-chunk containment)
- v2 ingest marks a chunk `has_answer=True` when **any contained turn** has `has_answer` (raw-chunk containment, the third M6 mark; the union of chunk contents is the session, so no evidence turn is orphaned).
- **Denominator hygiene (granularity-bias fix):** `evidence_point_count` (the `evidence_recall@k`/`turn_recall@k` denominator and the top-k numerator) counts **extracted points only** — `pointKind <> 'session-transcript'`. Rationale: if containment-marked chunks entered the shared denominator, the per-session chunk cap would structurally cap the numerator below it (a session with ≥3 evidence chunks could never reach recall 1.0), AND the ceiling would tighten as `chunk_turns` shrinks (more, smaller evidence chunks) — a confound that would bias the granularity sweep toward larger chunks. Keeping the denominator to extracted points preserves comparability with the v2 baseline.
- **New `chunk_evidence_recall@k`** (reported alongside): containment-marked chunk hits in top-k / total containment-marked chunks — the raw-chunk containment view (M6), granularity-aware by construction.
- v2 point `has_answer` marking (existing `_evidence_marked` content-overlap) is **unchanged**.

### D6. Reader consumes the budget-capped context (contract fix)
- Today `run.py` passes `ret["hits"]` (the full, uncapped, undeduped pool) to `reader.answer(...)` while `retrieve_for_question` computes `context_tokens` on `annotated[:top_k]` — the reader can see MORE than the reported context. R1 fixes this: the reader receives `ret["context_points"]` (the D4 output). This is the S25 reader-context-format surface and the M5 pinning contract.
- `render_context` is shared by both — the token metric and the reader input stay identical.
- **`top_k` role post-R1:** `top_k` remains "the maximum number of context items" (pool truncation before the token budget, D4) — the `--top-k` help text and `top_k_context` methodology label keep their meaning; the token cap is the additional, dominant bound. Both are reported.

### D7. Knob provenance (M7/M8 discipline)
- Report methodology gains `chunk_turns`, `context_token_cap`, `max_chunks_per_session` (recorded verbatim — the run protocol step 2 gate consumes them; the 500-Q report must show which granularity ran).
- **Methodology strings updated together** (they describe the corpus + metric semantics — stale strings would misdescribe the published numbers): `retrieval` ("turn-granular raw chunks (chunk_turns) + extracted points …"), `recall_definition` ("session-level: fraction of answer_session_ids in top-k over the DEDUPED pool; turn-level: fraction of has_answer extracted points in top-k; chunk containment reported as chunk_evidence_recall@k"), `reader_context_format` (points-first budget-capped shape).
- `EXTRACTION_APPROACH` / `EXTRACTION_APPROACH_V2` strings updated to describe turn-granular raw chunks.
- `reader_prompt_source()` (run.py) updated to describe the new context shape → **the parity leg's reader-prompt hash changes** → the operator refreshes the #1144 baseline record at the next parity run (a run-time action, no committed baseline exists — `battery/parity/runner.py` receives the baseline dict per call). Flagged in ⛔ G3.

---

## Integration Surface Map

Surfaces from test-design #1515 (28-surface epic map), R1-relevant subset — **with the 2026-08-20 verify-gate correction: S25, NOT S11/S15** (S11 dual-stack → R2/M7; S15 pipeline state → M3/M4).

| # | Surface | R1 component | Test layer |
|---|---|---|---|
| S25 | Reader context format / UX-3 budget-capped context (points first, chunks backfill) | `retrieve.py` `_assemble_context` + `render_context`; `run.py` reader input (`context_points`) | unit (pure assembly) + integration (embedded, mini fixture) |
| S10 | FalkorDBLite embedded mode (CI degradation) | chunking + dedup + cap must work under TF-IDF fallback (hits carry `pointKind` via graph props — available in embedded mode) | integration (embedded mini, CI) |
| S12 | Graph writes | chunk point writes (deterministic + v2 ingest), `lme_chunk_index`/`lme_chunk_turns` props, CONTAINS edges | integration (graph structure assertions) |
| S19 | Evidence marking (M6) | v2 chunk containment `has_answer` marks + denominator split (D5) | unit (`_mark_chunk`/containment) + integration |
| S27 | Dataset fixture | mini fixture + sweep subset; chunk counts in fixture assertions | integration |
| S28 | Temporal/recency | per-session dates must survive on chunk hits (render unchanged) | unit (render date test stays green) |

**Cross-consumer note (review fix):** `tests/test_longmem_reader_prompting.py` is a real cross-consumer — it imports `run_evaluation` + `render_context` and its end-to-end tests use a recording reader that depends on evidence-bearing hits reaching the reader (the exact surface D6 rewires). It stays green because points-first ordering keeps evidence turns within the cap — checked explicitly in T4/T7.

**Explicitly NOT touched (verify-gate correction):** S11 (FTS-vs-TFIDF dual stack — R2/M7), S15 (pipeline state — M3/M4), S7/S8/S9 real-backend surfaces (E2E-1's real stack — gated on P3/M7/R3 deps, see ⛔ G4). `tests/eval/retrieval/` is a self-contained harness with zero imports of `tools.longmem_eval` — not coupled (review-verified).

**Bug pattern flags (from #1515 + review):**
- **One-session monopoly** (the R1 fix) — pool-depth headroom + dedup cap; regression guards: T3 pool test + T6 8-turn monopoly test.
- **Silent evidence-leg emptiness** — unmarked chunks or capped-to-zero chunks would silently drop evidence; guards: containment-mark test (T2), denominator split (D5), `max_chunks_per_session ≥ 1` validation, extractor-failure chunk-retention test (T2).
- **Reader-consumes-uncapped-hits** — the D6 contract fix; guard: run_evaluation integration test capturing the reader's `context_hits` (T4).
- **BVA on window boundaries** — chunk_turns edges (1-turn session, exact multiple, remainder window, empty session) (T1).
- **Idempotency of chunk writes** — re-ingest over the same fresh graph must not double-write chunks; `stats["chunks"]` counts written (post-`_point_exists` guard) chunks, so stats match graph state (T1 + mixed-graph test T6).
- **Knob edge inputs** — `chunk_turns ∈ {0, −1}`, `max_chunks_per_session=0`, `context_cap=0` rejected/recorded, never silent (T1/T3/T4).
- **Oversized-hit context starvation** — skip-and-continue policy + test (T3).

---

## Journey Test Map

| Journey (epic J2/J4) | Steps → Acceptance → Test |
|---|---|
| **J2 — Question answered from memory (compact evidence)** | Reader sees budget-capped context → no 35k flood, points first → `test_reader_receives_capped_context`, `test_context_points_first_chunks_backfill`, `test_context_token_budget_enforced`, `test_context_points_reader_alignment` |
| **J2 — One session can't monopolize the pool** | Per-session chunk cap in top-k and in context, even when the session's points crowd the raw top-20 → `test_session_dedup_cap_in_pool`, `test_e2e1_dedup_cap_assertion` (8-turn monopoly input) |
| **J4 — Operator runs the granularity micro-test** | 3-point sweep on a subset → deterministic winner + recorded knobs → `test_granularity_sweep_ci` + `sweep_granularity.py` output |
| **J2 — Verbatim evidence still searchable (owner invariant)** | Answer-bearing chunks surface + render; chunks survive extractor failure → `test_chunk_containment_marking`, `test_v2_extractor_failure_retains_chunks`, `test_chunk_evidence_recall_non_vacuous` |

### Failure Modes
- Evidence chunk beyond dedup cap → **expected:** pool/context omits it, session recall unaffected (session present via its capped chunks/points); `chunk_evidence_recall` reflects the cap honestly → assert `session_recall` stable under dedup (T6).
- All chunks of a session's evidence beyond cap → **expected:** recall drops for that session — the knob trade the sweep validates (Open Q3).
- Extractor failure mid-session (v2) → **expected:** chunks + containment marks still written (written pre-extraction), `errors` recorded, run continues → `test_v2_extractor_failure_retains_chunks` (T2).
- Oversized single hit > cap → **expected:** that hit dropped, later hits retained, `context_point_count > 0` → `test_context_oversized_hit_skips_not_starves` (T3).
- Empty session / zero turns → no chunks written, no crash (T1 boundary).
- Degenerate knob (0/negative) → clear `ValueError`, never a silent run (T1/T3/T4).
- Context underfill after dedup → reader sees fewer blocks; `context_point_count` records it honestly (Open Q2 note).

---

## Verification Plan

- **Layers:** unit (assembly/dedup/chunking pure functions, window + knob BVA, oversized-hit policy) + integration (embedded FalkorDBLite, mini fixture — CI-safe, no keys) + threaded-dispatch integration (`--workers > 1` with R1 knobs) + **e2e real-backend (E2E-1 dedup assertion + E2E-10 V3-part) — gated on P3/M7/R3** (⛔ G4) + harness micro-test (run protocol step 2 — sweep script, mocked or real extractor).
- **Pool-depth headroom regression guard:** the `max(ks) * 3` candidate depth (R2) is regression-guarded by `test_session_crowded_out_still_surfaces` (T3) — a monopolizing session's points must not crowd other sessions out before dedup runs.
- **Complexity inputs:** UX medium (S25 reader-context — decisions already made in the epic's UX gate: points-first backfill, UX-3), Architecture high (epic-level; this issue standard), Ontology low, Accessibility low.
- **Non-code domains:** none deferred (no content/config/research-domain surface).
- **Command surface:** `uv run pytest tests/test_longmem_runner.py -v` (primary), `uv run pytest tests/test_longmem_reader_prompting.py -v` (cross-consumer), `uv run pytest tests/ -v -m "not slow"` (full CI), `uv run python -m tools.longmem_eval.sweep_granularity --split s --limit 20 --ingest-mode deterministic --mock` (micro-test CI smoke), real-backend e2e per E2E-1 setup once P3/M7/R3 land.

---

## Implementation Tasks

### Task 1: Shared chunking helper + deterministic ingest writes turn-granular chunks

**Intent:** Kill the 4.4× context bloat at its source — the whole-session raw blob. One shared chunker serves both ingest paths so granularity is a single knob.

**Acceptance:** `ingest_haystack` writes windowed `session-transcript` chunk points (ids `lme:{qid}:s{si}:c{ci}`, props `lme_chunk_index`/`lme_chunk_turns`=actual window length, CONTAINS edges) instead of one `:raw` blob; `stats["chunks"]` counts written (post-guard) chunks; `chunk_turns < 1` raises `ValueError`; re-ingest is a no-op; union of chunk contents == full verbatim session; zero-turn session writes nothing and does not crash.

**Files:**
- Modify: `tools/longmem_eval/ingest.py`
- Test: `tests/test_longmem_runner.py`

**Step 1: Write the failing tests** (update structure assertions to chunk semantics):
- `test_ingestion_creates_session_turn_raw_structure`: for `mini_ie_user_001` (2 sessions × 3 turns) at default `chunk_turns=2` → `stats["chunks"] == 4` (windows [t0,t1]+[t2] per session); `session-transcript` count == 4; `lme:mini_ie_user_001:s1` CONTAINS count == 5 (3 turns + 2 chunks); no point with id ending `:raw`; chunk props: `lme_chunk_index ∈ {0, 1}`, **`lme_chunk_turns == [2, 1]`** (actual lengths — remainder window carries 1); evidence turn (s1) is contained in a chunk (chunk `has_answer` stays **unset** in deterministic mode — D3).
- `test_ingestion_idempotent`: 2 sessions × (3 turns + 2 chunks) == 10 points after double ingest; **`stats["chunks"] == 4` after the second ingest too** (post-guard count matches graph state).
- `test_ingestion_chunk_window_boundaries`: 1-turn session → 1 chunk; 2-turn session at `chunk_turns=2` → 1 chunk; 5-turn session → 3 chunks ([0,1],[2,3],[4]); empty session → 0 chunks, no exception.
- `test_session_chunks_union_equals_full_transcript`: joined chunk contents (strip role prefixes) == `_session_transcript(session)`.
- `test_chunk_turns_validation`: `_session_chunks(session, 0)` and `(session, -1)` raise `ValueError` with a clear message.

**Step 2: Run and confirm failure** — `uv run pytest tests/test_longmem_runner.py::test_ingestion_creates_session_turn_raw_structure -v` (and the other new tests): FAIL (today writes `:raw` blob, no `chunks` stat).

**Step 3: Implement** in `tools/longmem_eval/ingest.py`:

```python
def _session_chunks(session: list[dict], chunk_turns: int) -> list[tuple[int, str, list[int]]]:
    """Non-overlapping verbatim turn windows of ``chunk_turns`` turns each.
    Returns [(chunk_index, rendered_text, contained_turn_indices)] — the
    union of rendered texts == the full session transcript (owner invariant:
    raw verbatim evidence is always retained). chunk_turns must be >= 1."""
    if chunk_turns < 1:
        raise ValueError(f"chunk_turns must be >= 1, got {chunk_turns!r}")
    windows = []
    for start in range(0, len(session), chunk_turns):
        window = session[start:start + chunk_turns]
        windows.append((start // chunk_turns, _session_transcript(window),
                        list(range(start, start + len(window)))))
    return windows
```

In `ingest_haystack`, replace the `:raw` blob write with:

```python
        # ── Raw verbatim turn-granular chunks (R1: replaces the whole-session
        # blob — the measured 4.4× context bloat; union of chunks == session) ──
        chunks = _session_chunks(session, chunk_turns)
        for ci, text, turn_idxs in chunks:
            chunk_id = f"lme:{qid}:s{si}:c{ci}"
            if not _point_exists(sdk._get_proj(), chunk_id):
                sdk.create_point(
                    SESSION_TRANSCRIPT_KIND, text, id=chunk_id,
                    session_id=sid, lme_question_id=qid,
                    lme_session_index=si, lme_chunk_index=ci,
                    lme_chunk_turns=len(turn_idxs), is_episodic=True,
                    status="draft",
                )
                stats["chunks"] += 1  # written (post-guard) — stats == graph state
            sdk._get_proj().g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": chunk_id},
            )
```

(The increment is INSIDE the `_point_exists` guard — a re-ingest over the same fresh graph returns `stats["chunks"]` equal to the actual chunk count in the graph, so `test_ingestion_idempotent`'s post-guard assertion holds. Do NOT port the current unconditional `raw_transcripts += 1` pattern.)

Add `chunk_turns: int = 2` as a keyword arg on `ingest_haystack`; update the stats dict (rename `raw_transcripts` → `chunks`; update any remaining consumers of the old key — grep-verified: tests only).

**Step 4: Run and confirm pass** — `uv run pytest tests/test_longmem_runner.py -v`: all ingestion tests green.

**Step 5: Commit** — `git add tools/longmem_eval/ingest.py tests/test_longmem_runner.py && git commit -m "feat(lme): turn-granular raw chunks replace whole-session blobs (R1)"` (via `commit-workflow`).

---

### Task 2: v2 ingest writes chunks with containment evidence marks, before extraction

**Intent:** The V3 primary path (real extractor) gets the same chunking, plus the M6 raw-chunk containment mark so chunk evidence is measurable — and verbatim retention survives extractor failure.

**Acceptance:** `ingest_haystack_v2` writes windowed chunks (shared helper, single binding) **before `extract_session_v2`** (preserving the current `:raw` block position), marks a chunk `has_answer=True` iff any contained turn is an evidence turn; extracted-point marking unchanged; `:raw` id gone; extractor failure still retains chunks + marks + records the error; re-ingest no-op.

**Files:**
- Modify: `tools/longmem_eval/ingest_v2.py`
- Test: `tests/test_longmem_runner.py`

**Step 1: Write the failing tests**:
- Extend `test_v2_ingest_writes_payload_with_evidence_marks`: the 2-turn question session (`s0`, turn 0 has_answer) now yields one chunk `lme:test_v2_q:s0:c0` (chunk_turns=2) whose `has_answer == True` (containment), CONTAINS count == 3 (1 chunk + 2 points); `:raw` id absent.
- `test_v2_chunks_marked_by_containment`: a 4-turn session with evidence in turn 3 → chunk `c1` (turns 2–3) marked, `c0` unmarked.
- `test_v2_extractor_failure_retains_chunks`: monkeypatch `extract_session_v2` to raise → assert the chunk point + CONTAINS edge + containment marks are still written for that session, `stats["errors"]` populated, the run continues, and `retrieve_for_question` still surfaces the session via its chunks (the exact "silent evidence-leg emptiness" guard).

**Step 2: Run and confirm failure** — `uv run pytest tests/test_longmem_runner.py::test_v2_ingest_writes_payload_with_evidence_marks -v`: FAIL (today writes `:raw`).

**Step 3: Implement** in `tools/longmem_eval/ingest_v2.py` — import `_session_chunks` from `.ingest`; **keep the chunk block in the current `:raw` position (before the `extract_session_v2` try/except)**:

```python
        # ── Raw verbatim turn-granular chunks (R1) + containment marks (M6).
        # Written BEFORE extraction so verbatim retention + marks survive an
        # extractor failure on this session (fail-closed, never lost). ──
        chunks = _session_chunks(session, chunk_turns)
        for ci, text, turn_idxs in chunks:
            chunk_id = f"lme:{qid}:s{si}:c{ci}"
            contains_evidence = any(
                bool(turn.get("has_answer")) for ti, turn in enumerate(session)
                if ti in turn_idxs)
            if not _point_exists(sdk._get_proj(), chunk_id):
                sdk.create_point(
                    SESSION_TRANSCRIPT_KIND, text, id=chunk_id,
                    session_id=sid, lme_question_id=qid,
                    lme_session_index=si, lme_chunk_index=ci,
                    lme_chunk_turns=len(turn_idxs), is_episodic=True,
                    has_answer=contains_evidence, status="draft",
                )
                stats["chunks"] += 1  # written (post-guard) — stats == graph state
            sdk._get_proj().g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": chunk_id},
            )
```

Add `chunk_turns: int = 2` kwarg to `ingest_haystack_v2`; update the stats dict (`raw_transcripts` → `chunks`). Keep the import of `SESSION_TRANSCRIPT_KIND` (used as the chunk kind).

**Step 4: Run and confirm pass** — `uv run pytest tests/test_longmem_runner.py::test_v2_ingest_writes_payload_with_evidence_marks -v` + the containment + extractor-failure tests: PASS.

**Step 5: Commit** — `git add tools/longmem_eval/ingest_v2.py tests/test_longmem_runner.py && git commit -m "feat(lme): v2 raw chunks with containment evidence marks (R1/M6)"`.

---

### Task 3: Retrieval — pool-depth headroom + session dedup + budget-capped points-first context

**Intent:** The S25 read-side heart of R1 — enforce E2E-1's per-session chunk cap (with enough candidate depth that a monopolizing session can't crowd others out pre-dedup) and UX-3's points-first budget-capped context, as pure, deterministic logic with exact token accounting.

**Acceptance:** `retrieve_for_question` fetches candidates at `max(ks) * 3` depth, returns `"hits"` = the **deduped pool** (pinned contract), computes recall@k on the deduped pool, splits the evidence denominator (extracted points only + new `chunk_evidence_recall@k`), and returns `context_points` = budget-capped points-first context with `context_tokens == _estimate_tokens(render_context(context_points, question_date))` exactly; oversized hits skip-not-starve; degenerate knobs raise.

**Files:**
- Modify: `tools/longmem_eval/retrieve.py`
- Modify: `tools/longmem_eval/ingest.py` (`point_props_for_hits` gains `pointKind`)
- Test: `tests/test_longmem_runner.py`

**Step 1: Write the failing tests:**
- `test_session_dedup_cap_in_pool`: 5 chunks from one session + 1 point from another → the pool (`ret["hits"]`) contains ≤ 2 chunks from that session; `dedup_stats["chunks_capped"] == 3`; **pins `ret["hits"] == pool`**.
- `test_dedup_missing_session_index_no_collapse`: hits with distinct `session_id`s but missing/`-1` `lme_session_index` → not capped against each other (bucket by `session_id`).
- `test_session_crowded_out_still_surfaces`: one session whose points alone exceed `max(ks)` (20) candidates + a second session's point ranked beyond the raw top-20 → with the pool multiplier, the second session's point still appears in the deduped pool and its `session_recall@k` is not zero (the real E2E-1 assertion — distinct from the synthetic 6-hit test).
- `test_context_points_first_chunks_backfill`: mixed kinds → all non-`session-transcript` hits order before `session-transcript` hits (rank order within each tier), bounded by `top_k` then budget.
- `test_context_token_budget_enforced`: cap below the full pool → `context_tokens ≤ cap`, the truncated tail is chunks (not points).
- `test_context_points_reader_alignment`: `context_tokens == _estimate_tokens(render_context(context_points, question_date=…))` — exact, even with several blocks (no per-block int drift).
- `test_context_oversized_hit_skips_not_starves`: a mid-rank hit whose estimate > cap → later hits still selected (`context_point_count > 0`, `break`-free skip semantics).
- `test_recall_on_deduped_pool`: a session whose chunks rank 3rd+ beyond the cap is absent from the pool → its session_recall reflects the deduped pool (honest contract).
- `test_evidence_denominator_points_only`: a question with containment-marked chunks but no marked extracted points → `evidence_recall@k` is `0.0`/N/A per the M6 N/A semantics while `chunk_evidence_recall@k` > 0 (denominator split, D5).
- `test_degenerate_knobs_raise`: `_dedup_pool(..., max_chunks_per_session=0)` and `_assemble_context(..., max_context_tokens=0)` raise `ValueError` (or the assembly with cap < smallest block returns an honest empty `context_points` with `context_point_count == 0` — pick one contract, pin it).

**Step 2: Run and confirm failure** — `uv run pytest tests/test_longmem_runner.py::test_session_dedup_cap_in_pool -v` etc.: FAIL (today no dedup/cap/depth).

**Step 3: Implement** in `tools/longmem_eval/retrieve.py`:

```python
DEFAULT_MAX_CHUNKS_PER_SESSION = 2
DEFAULT_CONTEXT_TOKEN_CAP = 8000
DEFAULT_POOL_MULTIPLIER = 3  # candidate depth headroom: one session's points
                             # must not crowd out others BEFORE dedup runs


def _is_raw_chunk(h: dict) -> bool:
    return h.get("point_kind") == "session-transcript"


def _dedup_pool(annotated: list[dict], *, max_chunks_per_session: int) -> list[dict]:
    """Per-session chunk cap (rank order): at most ``max_chunks_per_session``
    raw chunks per session survive in the pool (E2E-1). Bucket key = the
    hit's session_id when present, else its lme_session_index — distinct
    sessions NEVER share a bucket (no -1 collapse). Points/turn points are
    never capped (compact epistemic surface, D3)."""
    if max_chunks_per_session < 1:
        raise ValueError("max_chunks_per_session must be >= 1")
    seen: dict[str, int] = {}
    pool: list[dict] = []
    for h in annotated:
        if _is_raw_chunk(h):
            key = h.get("session_id") or f"idx:{h.get('lme_session_index', -1)}"
            if seen.get(key, 0) >= max_chunks_per_session:
                continue
            seen[key] = seen.get(key, 0) + 1
        pool.append(h)
    return pool


def _render_block(h: dict) -> str:
    """One hit's rendered context block — the SINGLE implementation shared by
    render_context and the token budget (factored out of render_context).
    question_date never appears here: it only prepends the Current Date:
    header once in render_context. Per-hit dates come from session_date."""
    idx = h.get("lme_session_index")
    prefix = f"[session {idx}]" if idx is not None and idx >= 0 else "[session ?]"
    sdate = h.get("session_date")
    if sdate:
        prefix = f"{prefix} (session date {sdate})"
    marker = _supersede_marker(h)
    if marker:
        prefix = f"{prefix} {marker}"
    return f"{prefix} {h.get('content', '')}"


def _assemble_context(pool: list[dict], *, top_k: int,
                      max_context_tokens: int,
                      question_date: str | None = None) -> list[dict]:
    """Budget-capped, points-first context (UX decision 3): extracted points
    render in rank order, then raw chunks backfill the remaining token budget,
    over at most ``top_k`` pool items. Token accounting: raw whitespace words
    accumulate per block (question_date-independent) + the once-prepended
    'Current Date: …' header words; the 1.1 markup multiplier applies ONCE to
    the joined total, so context_tokens == _estimate_tokens(render_context(...))
    exactly (no per-block int drift). Oversized hits are SKIPPED (continue),
    never starving the rest."""
    if max_context_tokens < 1:
        raise ValueError("max_context_tokens must be >= 1")
    points = [h for h in pool if not _is_raw_chunk(h)]
    chunks = [h for h in pool if _is_raw_chunk(h)]
    header_words = len(f"Current Date: {question_date}".split()) if question_date else 0
    selected: list[dict] = []
    words = header_words
    for h in (points + chunks)[:top_k]:
        cost = len(_render_block(h).split())
        if int((words + cost) * 1.1) > max_context_tokens:
            continue  # skip this hit; keep later ones (no starvation)
        selected.append(h)
        words += cost
    return selected
```

`render_context` refactor: `text = "\n\n".join(_render_block(h) for h in hits)`; prepend `f"Current Date: {question_date}\n\n"` when set — **byte-identical output** for existing inputs (the existing render tests pin this).

Update `retrieve_for_question`:
- `hits_raw = hybrid_search(sdk, query, limit=max(ks) * DEFAULT_POOL_MULTIPLIER)`.
- `_annotate_hits` gains `point_kind` (via `point_props_for_hits` returning `pointKind` — extend the Cypher in `ingest.py` to `RETURN n.id, session_id, has_answer, lme_session_index, pointKind`).
- `pool = _dedup_pool(annotated, max_chunks_per_session=…)`; **`"hits"` in the return dict = `pool`** (pinned contract).
- All recall@k over `pool[:k]`.
- Evidence denominator split (D5): `evidence_point_count` counts marked points with `pointKind <> 'session-transcript'`; new `chunk_evidence_point_count` counts marked `session-transcript` chunks; compute `evidence_recall@k` over non-chunk marked hits in `pool[:k]`, and new `chunk_evidence_recall@k` over marked chunks in `pool[:k]`. **In v2 mode BOTH `turn_recall@k` and `evidence_recall@k` numerators count only non-`session-transcript` hits** (chunks contribute exclusively to `chunk_evidence_recall@k`) — otherwise marked chunks in `pool[:k]` inflate `turn_recall@k` beyond 1.0 against the points-only denominator (e.g. 2 marked chunks + 1 marked point vs denominator 1). This matches the D7 `recall_definition` string.
- `context_points = _assemble_context(pool, top_k=top_k, max_context_tokens=…, question_date=…)`; `context_text = render_context(context_points, question_date=question_date)`; `context_tokens = _estimate_tokens(context_text)`.
- New kwargs: `max_chunks_per_session=DEFAULT_MAX_CHUNKS_PER_SESSION`, `max_context_tokens=DEFAULT_CONTEXT_TOKEN_CAP`.
- Return adds `"context_points"`, `"chunk_evidence_recall@k"`, and `"dedup_stats": {"chunks_retrieved": …, "chunks_capped": …, "pool_depth_requested": …}`. **`chunk_evidence_recall@k` must be wired end-to-end: `run.py`'s `_run_one` copies it into the outcome (`"chunk_evidence_recall@k": ret.get("chunk_evidence_recall@k")`), `outcomes_to_report`'s extra projection includes it, and `report.py`'s `retrieval` aggregation adds `chunk_evidence_recall@k` parallel to `evidence_recall@k` (T4)** — so T5's sweep collection has a defined source (no `KeyError` / silent `None`).

**Step 4: Run and confirm pass** — `uv run pytest tests/test_longmem_runner.py -v`: all retrieval/context tests green; existing recall tests still green on mini.

**Step 5: Commit** — `git add tools/longmem_eval/retrieve.py tools/longmem_eval/ingest.py tests/test_longmem_runner.py && git commit -m "feat(lme): pool depth + per-session chunk dedup + budget-capped points-first context (R1)"`.

---

### Task 4: Read-path wiring — reader consumes capped context + validated knobs + provenance

**Intent:** Fix the D6 contract (reader must see exactly the budget-capped context), make the R1 knobs configurable via CLI **and** env with validation, and record knob values + recall semantics (D7).

**Acceptance:** `run_evaluation` passes `ret["context_points"]` to `reader.answer`; knobs resolve env-first then CLI (`TORTOISE_LME_CHUNK_TURNS`, `TORTOISE_LME_CONTEXT_CAP`, `TORTOISE_LME_MAX_CHUNKS_PER_SESSION`), with CLI validation (chunk_turns ≥ 1, caps ≥ 1); report methodology records the three values + updated `retrieval`/`recall_definition`/`reader_context_format` strings; `test_longmem_reader_prompting.py` end-to-end tests stay green.

**Files:**
- Modify: `tools/longmem_eval/run.py`
- Modify: `tools/longmem_eval/report.py`
- Test: `tests/test_longmem_runner.py`

**Step 1: Write the failing tests:**
- `test_reader_receives_capped_context`: run `run_evaluation` over `_mini()` with a recording reader (wraps MockReader, captures `context_hits`); assert the captured list == the question's `ret["context_points"]` (bounded by cap, points-first) and its rendered token estimate ≤ cap.
- `test_knob_cli_flags`: monkeypatch `tools.longmem_eval.run.ingest_haystack` to capture the `chunk_turns` kwarg; run `run_main([..., "--chunk-turns", "4", "--context-cap", "5000", "--max-chunks-per-session", "1", "--mock"])` → methodology records the three values and the captured kwarg == 4. (The report does NOT carry per-question ingest stats — assert via the captured kwarg + methodology, not the report's outcomes.)
- `test_knob_env_vars`: set the three `TORTOISE_LME_*` env vars → `run_main` picks them up without CLI flags (mirrors the existing `--reader-model`/env pattern).
- `test_knob_cli_validation`: `--chunk-turns 0`, `--chunk-turns -1`, `--max-chunks-per-session 0`, `--context-cap 0` each exit non-zero with a clear message (argparse `type=` guard).
- `test_report_methodology_records_r1_knobs`: `outcomes_to_report(...)` methodology contains `chunk_turns`, `context_token_cap`, `max_chunks_per_session` and the updated `recall_definition`/`retrieval` strings.
- `test_r1_knobs_threaded_dispatch`: `run_evaluation(_mini()[:2], workers=4, chunk_turns=…, max_context_tokens=…, max_chunks_per_session=…)` → every question completes exactly once, `outcomes` has no duplicates/losses, a checkpoint written mid-run loads back and resumes cleanly (the `--workers > 1` path is otherwise untested).

**Step 2: Run and confirm failure** — `uv run pytest tests/test_longmem_runner.py::test_reader_receives_capped_context -v`: FAIL (reader today gets `ret["hits"]`).

**Step 3: Implement:**
- `run.py`: in `_run_one`, `reader.answer(context_hits=ret["context_points"], …)`; add `--chunk-turns`, `--context-cap`, `--max-chunks-per-session` args **with `type=` guards** (`_positive_int`); resolve each as `os.environ.get("TORTOISE_LME_*", default)` with the argparse value overriding env — mirror the `--reader-model`/`TORTOISE_LME_READER_MODEL` pattern; thread into `run_evaluation(..., chunk_turns=…, max_context_tokens=…, max_chunks_per_session=…)` → `ingest_haystack(_v2)(sdk, question, chunk_turns=…)` and `retrieve_for_question(..., max_context_tokens=…, max_chunks_per_session=…)`; pass the three values into `outcomes_to_report(..., r1_knobs={...})` → `build_report(..., r1_knobs)`.
- `report.py`: `build_report` gains `r1_knobs: dict | None = None` merged into `methodology`; update `retrieval`, `recall_definition`, and `reader_context_format` methodology strings (D7).
- `run.py`: update `EXTRACTION_APPROACH` / `EXTRACTION_APPROACH_V2` (turn-granular raw chunks) and `reader_prompt_source()` (points-first budget-capped shape — ⛔ G3 parity hash note).
- Verify `tests/test_longmem_reader_prompting.py` end-to-end tests still pass (points-first keeps evidence turns within the cap — this is the D6 cross-consumer check).

**Step 4: Run and confirm pass** — `uv run pytest tests/test_longmem_runner.py tests/test_longmem_reader_prompting.py -v`: green. Then `uv run pytest tests/test_battery_parity.py -v` — confirm no in-repo parity test breaks (the hash is compared against an externally-supplied baseline record, not a committed one).

**Step 5: Commit** — `git add tools/longmem_eval/run.py tools/longmem_eval/report.py tests/test_longmem_runner.py && git commit -m "feat(lme): reader consumes budget-capped context + validated R1 knobs + provenance (R1)"`.

---

### Task 5: Granularity sweep micro-test (run protocol step 2)

**Intent:** The owner-specified 3-point sweep ("one very low, two middle") that SELECTS the granularity knob before the pilot — the run protocol's step-2 gate.

**Acceptance:** `python -m tools.longmem_eval.sweep_granularity --split s --limit N` runs the eval across `chunk_turns ∈ {1, 2, 4}` with other knobs fixed, prints a comparison table (per config: evidence/chunk-evidence/session recall@10, context_tokens_mean, context_point_count_mean) and a deterministic winner per the D2 selection rule; `--mock` implies deterministic ingest (fully offline); a CI-safe variant passes in pytest.

**Files:**
- Create: `tools/longmem_eval/sweep_granularity.py`
- Test: `tests/test_longmem_runner.py`

**Step 1: Write the CI test** — `test_granularity_sweep_ci`: run the sweep over `_mini()` in **v2 mode with a monkeypatched `extract_session_v2`** (the existing `_fake_extract` pattern — so the selection metric is actually knob-responsive, per D2) with MockReader/Judge, cap 8000, max_chunks_per_session 2; assert it completes for all 3 granularities, every config's `context_tokens_mean ≤ 8000`, per-session dedup holds, and two consecutive runs select the same winner (determinism).

**Step 2: Run and confirm failure** — `uv run pytest tests/test_longmem_runner.py::test_granularity_sweep_ci -v`: FAIL (no sweep module).

**Step 3: Implement** `tools/longmem_eval/sweep_granularity.py`:
- CLI: `--split`, `--limit` (default 20), `--data`, `--ingest-mode {deterministic,v2}` (default `v2` — the V3 primary path), `--mock` (**implies `--ingest-mode deterministic` when `--ingest-mode` is unset — a "mock" run must be fully offline; real v2 sweeps need `--extractor-model`/keys**), `--chunk-turns 1,2,4`, `--context-cap 8000`, `--max-chunks-per-session 2`, `--extractor-model` (for v2 real runs), `--output <json>`.
- Loop: for each `chunk_turns`, run `run_evaluation(instances[:limit], chunk_turns=…, max_context_tokens=…, max_chunks_per_session=…, reader, judge, …)`; collect `evidence_recall@k["10"]` (v2) / `turn_recall@k["10"]` (deterministic), `chunk_evidence_recall@k["10"]` (v2), `session_recall@k["10"]`, `context_tokens_mean`, `context_point_count_mean`.
- Emit the table (stdout) + JSON; apply the D2 selection rule; print the winner; exit non-zero if the winner's `context_tokens_mean > cap`.
- Docstring records the run-protocol tie-in: "knob selected → pilot (step 3) and 500-Q run (step 5) use the chosen value; the report's methodology records it (D7). The selection metric is meaningful in v2 mode only (D2); the deterministic cell is a context-token/underfill view."

**Step 4: Run and confirm pass** — `uv run pytest tests/test_longmem_runner.py::test_granularity_sweep_ci -v`: PASS. Manual smoke (fully offline): `uv run python -m tools.longmem_eval.sweep_granularity --data tests/fixtures/longmemeval_mini.json --limit 5 --mock` (→ deterministic ingest).

**Step 5: Commit** — `git add tools/longmem_eval/sweep_granularity.py tests/test_longmem_runner.py && git commit -m "feat(lme): 3-point granularity sweep micro-test (R1 run protocol step 2)"`.

---

### Task 6: E2E-1/E2E-10 integration assertions + robustness tests + docs

**Intent:** Pin the E2E-1 dedup-cap and E2E-10 V3-part assertions on distinct, non-duplicative scenarios; cover the estimator/mixed-graph robustness cases; document the new knobs/behavior.

**Acceptance:** The two genuinely distinct E2E scenarios (single-session monopoly; near-duplicate-chunk budget) asserted in embedded CI; pathological-content and mixed-blob/chunk-graph robustness tests pass; README documents the three knobs, the sweep, and the retirement of the `:raw` id. (E2E-1's real-backend variant documented as gated — ⛔ G4. These tests are THIN: they assert the distinct integration scenarios only, not re-assertions of T3/T4 unit coverage.)

**Files:**
- Test: `tests/test_longmem_runner.py`
- Modify: `tools/longmem_eval/README.md`

**Step 1: Add the distinct E2E-derived integration tests** (embedded FalkorDBLite, mini fixture + synthetic inputs):
- `test_e2e1_dedup_cap_assertion` (DISTINCT input — 8-turn single session + second session): with `max_chunks_per_session=2`, per-session chunk count in `ret["hits"]` (the pool) ≤ 2 AND in `context_points` ≤ 2 (E2E-1 "≤ the cap"); `session_recall@k` for the second session is preserved (the crowd-out scenario).
- `test_e2e10_budget_capped_context_v3_part` (DISTINCT input — many near-duplicate chunks from one session): context stays ≤ cap; points render before chunks; `context_tokens` honest (E2E-10 V3 part; cross-encoder/MMR assertions remain V4-conditional — not asserted here).
- `test_context_cap_holds_under_pathological_content`: a chunk whose content is a long no-whitespace string (URL/base64) → the rendered context's real length stays bounded and the estimator limitation is documented in the methodology string (no silent 35k-flood reintroduction).
- `test_ingest_over_mixed_blob_chunk_graph` (defensive): manually insert a `:raw` point under a session, re-run new ingest → the session's `session-transcript` surface respects the cap without double-representation (kind-based `_is_raw_chunk` treats the stale blob as a chunk; fresh per-question graphs make this unreachable in production runs, but stats must reflect written chunks).

**Step 2: Implement the tests** (assertions only — fix any production gap they surface).

**Step 3: Run and confirm pass** — `uv run pytest tests/test_longmem_runner.py -v`.

**Step 4: Update `tools/longmem_eval/README.md`** — pipeline description (turn-granular raw chunks replace the whole-session blob; points-first budget-capped context; per-session chunk dedup), config table rows (`TORTOISE_LME_CHUNK_TURNS`, `TORTOISE_LME_CONTEXT_CAP`, `TORTOISE_LME_MAX_CHUNKS_PER_SESSION`), CLI flags (`--chunk-turns`, `--context-cap`, `--max-chunks-per-session`), the sweep invocation, and the `:raw` → `:c{ci}` id note.

**Step 5: Commit** — `git add tests/test_longmem_runner.py tools/longmem_eval/README.md && git commit -m "test(lme): E2E-1/E2E-10 dedup+budget scenarios + R1 docs"`.

---

### Task 7: Full verification pass

**Intent:** Prove the R1 change set is green end-to-end before the run protocol proceeds to the pilot.

**Acceptance:** Full CI suite green (`uv run pytest tests/ -m "not slow"`); no stray `:raw` id consumers; report methodology shows R1 knobs on a mock run; `tests/test_longmem_reader_prompting.py` (the real cross-consumer of the D6 rewiring) green; verification-before-completion evidence captured.

**Files:**
- Test: whole suite
- Modify: none expected

**Step 1:** `grep -rn ":raw\b\|raw_transcripts" tools/ tests/ --include="*.py"` — confirm only intentional references remain (stats rename fully propagated).
**Step 2:** `uv run pytest tests/test_longmem_runner.py tests/test_longmem_reader_prompting.py -v` — green (the reader-prompting file is the real cross-consumer of the D6 rewiring; its end-to-end tests depend on evidence-bearing hits reaching the reader).
**Step 3:** `uv run pytest tests/ -m "not slow" -v` — full CI green (watch `test_battery_parity.py` and `test_battery_run.py` for methodology-shape coupling; fix any that assert the old blob shape or strings).
**Step 4:** Mock end-to-end smoke — `uv run python -m tools.longmem_eval.run --data tests/fixtures/longmemeval_mini.json --limit 5 --mock --output /tmp/lme_r1.json` → confirm `methodology` contains `chunk_turns`/`context_token_cap`/`max_chunks_per_session` + the updated `recall_definition`/`retrieval` strings, and `retrieval.context_tokens_mean` is bounded (≤ cap).
**Step 5:** Record verification evidence (report JSON path + pytest output) and invoke `verification-before-completion` before claiming done.

---

## Tests (consolidated)

| Test | Layer | Task | Assertion |
|---|---|---|---|
| `test_ingestion_creates_session_turn_raw_structure` (updated) | integration | T1 | chunk points, counts, props (`lme_chunk_turns` = actual lengths), no `:raw` |
| `test_ingestion_idempotent` (updated) | integration | T1 | no double-write; `stats["chunks"]` matches graph state |
| `test_ingestion_chunk_window_boundaries` | unit/integration | T1 | BVA window edges (1, 2, 5-turn sessions; empty) |
| `test_session_chunks_union_equals_full_transcript` | unit | T1 | verbatim coverage invariant |
| `test_chunk_turns_validation` | unit | T1 | `chunk_turns ∈ {0, −1}` → `ValueError` |
| `test_v2_ingest_writes_payload_with_evidence_marks` (updated) | integration | T2 | chunk ids, containment mark |
| `test_v2_chunks_marked_by_containment` | unit | T2 | mark only evidence-containing windows |
| `test_v2_extractor_failure_retains_chunks` | integration | T2 | chunks + marks survive extractor failure; run continues |
| `test_session_dedup_cap_in_pool` | unit | T3 | E2E-1 pool cap + `ret["hits"] == pool` |
| `test_dedup_missing_session_index_no_collapse` | unit | T3 | no `-1` bucket collapse |
| `test_session_crowded_out_still_surfaces` | unit | T3 | pool-depth headroom (real E2E-1 assertion) |
| `test_context_points_first_chunks_backfill` | unit | T3 | UX-3 ordering + top_k bound |
| `test_context_token_budget_enforced` | unit | T3 | cap + chunk-tier truncation |
| `test_context_points_reader_alignment` | unit | T3 | exact `context_tokens` == reader's render (no int drift) |
| `test_context_oversized_hit_skips_not_starves` | unit | T3 | skip-and-continue, no starvation |
| `test_recall_on_deduped_pool` | unit | T3 | recall reflects the pool contract |
| `test_evidence_denominator_points_only` | unit | T3 | D5 denominator split + `chunk_evidence_recall@k` |
| `test_degenerate_knobs_raise` | unit | T3 | cap/chunk-cap 0 → `ValueError` or honest empty contract |
| `test_reader_receives_capped_context` | integration | T4 | D6 contract fix |
| `test_knob_cli_flags` | integration | T4 | CLI threading + methodology (via captured kwarg) |
| `test_knob_env_vars` | integration | T4 | env config surface works |
| `test_knob_cli_validation` | integration | T4 | degenerate CLI values rejected |
| `test_report_methodology_records_r1_knobs` | unit | T4 | D7 provenance + updated strings |
| `test_r1_knobs_threaded_dispatch` | integration | T4 | `--workers > 1` path: no dupes/losses, checkpoint resume |
| `test_granularity_sweep_ci` | integration | T5 | 3-point sweep (v2, mocked extractor) + determinism + cap bound |
| `test_e2e1_dedup_cap_assertion` | integration | T6 | 8-turn monopoly scenario (E2E-1) |
| `test_e2e10_budget_capped_context_v3_part` | integration | T6 | near-duplicate budget scenario (E2E-10 V3-part) |
| `test_context_cap_holds_under_pathological_content` | integration | T6 | estimator undercount can't reproduce the 35k flood |
| `test_ingest_over_mixed_blob_chunk_graph` | integration | T6 | stale `:raw` handled; stats == written chunks |
| Cross-consumer: `tests/test_longmem_reader_prompting.py` suite | integration | T4/T7 | D6 rewiring keeps evidence reaching the reader |

Real-backend E2E (E2E-1 full setup: real FalkorDB + FTS + embedder + structural kind) is gated on P3/M7/R3 — ⛔ G4.

---

## Cross-Lane Interfaces

| Lane | Interface | Contract |
|---|---|---|
| **M7 report / provenance** | `build_report(methodology)` + `reader_prompt_source()` | R1 knobs recorded; `retrieval`/`recall_definition`/`reader_context_format` strings updated with the new corpus + deduped-pool semantics; parity hash changes → baseline record refresh at next parity run (G3) |
| **M6 evidence marking** | chunk `has_answer` containment (v2 only); denominator split (D5) | `evidence_recall@k` over extracted points (baseline-comparable); `chunk_evidence_recall@k` = containment view; deterministic chunks stay unmarked (turn-id semantics) |
| **E2E-1 / E2E-10** | dedup cap + budget-capped context | asserted in CI (T3/T6); real-backend variant gated (G4) |
| **R3 dense leg** | `tortoise_fts_query` pool | limit raised to `max(ks) * 3` (candidate depth only — recall/context still k/top_k-bounded); chunk points indexed like any point |
| **P3 rebase** | origin/main parity | all real-backend e2e gate on it (G4) |
| **UX decision 3** | reader context format | points first, chunks backfill — S25 |
| **Reader prompt pinning (M5)** | `render_context` / `_render_block` shared | `context_tokens` == reader input (T3 alignment test); `test_longmem_reader_prompting.py` stays green (T4/T7) |
| **Cross-consumers** | `tests/test_longmem_reader_prompting.py` | recording reader + end-to-end tests depend on the D6 rewiring — explicitly verified (T4 Step 4, T7 Step 2) |

---

## ⛔ Conditional-Gate Notes

- **G1 — Ontology: NO new kind/edge required.** Chunks reuse the existing `SESSION_TRANSCRIPT_KIND` (`session-transcript`) — the epic's no-new-kind invariant holds. New properties `lme_chunk_index` / `lme_chunk_turns` are **eval-instrumentation only** (parallel to the existing `lme_*` / `has_answer` eval props), not production ontology concepts. **If a reviewer proposes a distinct "raw-chunk" kind: reject — it violates the epic contract (03-scope: "Any ontology change … explicitly excluded").**
- **G2 — Architecture: reader-input contract change.** `run.py` switches from `ret["hits"]` to `ret["context_points"]` (D6). Additive at the function level, but it IS the S25 reader-context-format surface — the M5 pinning record (`reader_prompt_source()`) and parity baseline must be refreshed. No protocol/API change beyond the eval harness.
- **G3 — New field / id-scheme change: `:raw` id retired.** Chunk ids `lme:{qid}:s{si}:c{ci}` replace `lme:{qid}:s{si}:raw`. Verified consumers today: only tests + `ingest_v2.py` (grep); every eval question uses a fresh isolated graph, so no live graph carries a stale blob. Report methodology gains three new keys + updated strings (report-only, no schema change). The parity reader-prompt hash changes → refresh the externally-supplied baseline record at the next parity run.
- **G4 — Dependency gate: real-backend e2e deferred.** E2E-1's real-stack variant (real FalkorDB + FTS + embedder + structural `kind`) and E2E-10's full form gate on **P3** (rebase/drift), **M7** (per-leg recording), and **R3** (embedder in eval env). This issue's unit/integration/CI-embedded assertions (T3/T6) do not depend on them; the **granularity micro-test (run protocol step 2) is the R1 knob gate** and runs before the pilot (step 3).
- **G5 — No ⛔ for M2/M3/M4/M8 or production wiring:** R1 touches the eval harness only; extractor/reader/judge providers and capture paths are untouched.

---

## Open Questions

1. **Exact `context_token_cap` value.** Default 8000 (≈ pre-v2 baseline; 4.4× reduction from the measured 35k flood). The LightMem evidence (~330 tokens: +5.5pp) suggests the optimum may be tighter — the pilot (run protocol step 3, full-context comparison cell) will validate; treat 8000 as the working default, not a final number.
2. **Pool underfill after dedup (monitor, no action).** With the pool multiplier (T3), the candidate pool is deep enough to backfill after dedup; if the micro-test still shows context underfill on chunk-heavy questions, `context_point_count` records it honestly — revisit only if it hurts the pilot's reader answers.
3. **Dedup cap value.** Default 2/session (aligned with E2E-10's ≤1–2 MMR cap). The R1 sweep fixes `chunk_turns` only; `max_chunks_per_session` tuning is E2E-10/R6 (post-baseline) territory per the epic's sequencing.
4. **Overlapping vs non-overlapping windows.** Plan: non-overlapping (near-duplicate avoidance — E2E-10's pathology). If the micro-test shows coverage gaps (evidence split across window boundaries hurting recall), revisit with small overlap.
5. **Deterministic-mode chunk marks.** Plan: deterministic chunks unmarked (preserves turn-id evidence semantics — D3). If the sweep shows raw-chunk evidence materially matters in the deterministic baseline cell, revisit with a mode-flagged mark.

---

## Changelog (plan-review fixes, 2026-08-20)

| # | Sev | Fix |
|---|---|---|
| R1 | P1 | `_assemble_context` rewritten as a correct, single algorithm: `_render_block` factored from `render_context`; raw-word accumulation + one-time 1.1 multiplier → exact `context_tokens` alignment; skip-not-starve for oversized hits; dead `block` variable removed |
| R2 | P1 | Pool-depth headroom `limit = max(ks) * 3` so a monopolizing session can't crowd out others pre-dedup (real E2E-1 assertion + test) |
| R3 | P1 | `stats["chunks"] += len(windows)` NameError fixed — chunker bound once (`chunks = _session_chunks(...)`) in both T1 and T2 |
| R4 | P1 | Methodology `retrieval` + `recall_definition` strings updated (deduped pool, turn-granular chunks) |
| R5 | P1 | Env vars `TORTOISE_LME_*` implemented (env-first resolution mirroring existing pattern) — not just documented |
| R6 | P1 | Knob validation: `chunk_turns ≥ 1`, `max_chunks_per_session ≥ 1`, `context_cap ≥ 1` at CLI + function boundaries; degenerate-knob tests |
| R7 | P1 | `ret["hits"] == pool` (deduped) pinned in the return contract + T3 test |
| R8 | P1 | v2 chunks written BEFORE extraction — extractor-failure chunk-retention test (verbatim leg never silently lost) |
| R9 | P2 | Evidence denominator split (D5): extracted points only + `chunk_evidence_recall@k` — removes the granularity-bias confound on the sweep metric |
| R10 | P2 | Sweep: selection metric meaningful in v2 only; `--mock` implies deterministic ingest; CI test uses mocked v2 extractor |
| R11 | P2 | Task 6 trimmed to distinct scenarios (8-turn monopoly, near-duplicate budget, pathological content, mixed-graph) — no duplicate re-assertion of T3/T4 units |
| R12 | P2 | `test_knob_cli_flags` reworked (kwarg capture via monkeypatch — report carries no per-question ingest stats) |
| R13 | P2 | T7 watch-list: `tests/test_longmem_reader_prompting.py` (real cross-consumer) replaces the non-coupled `tests/eval/retrieval/` |
| R14 | P2 | `top_k` role clarified (pool-truncation bound; token cap is the dominant bound; help/methodology labels accurate) |
| R15 | P2 | `lme_chunk_turns` = actual window length (remainder windows carry their true length); knob value recorded in methodology |
| R16 | P2 | Pathological-content (estimator undercount) + mixed-blob/graph + threaded-dispatch (`--workers > 1`) + `-1`-bucket-collapse tests added |

### Verification-cycle fixes (2026-08-20, fresh-context verifier)

| # | Sev | Fix |
|---|---|---|
| V1 | P1 | `stats["chunks"]` increment moved INSIDE the `_point_exists` guard in both T1 and T2 snippets — stats == graph state on re-ingest (idempotency test now holds); explicit note not to port the unconditional `raw_transcripts += 1` pattern |
| V2 | P2 | T3 states BOTH `turn_recall@k` and `evidence_recall@k` numerators exclude `session-transcript` hits in v2 mode (chunks → `chunk_evidence_recall@k` only) — prevents `turn_recall@k > 1.0` against the points-only denominator |
| V3 | P2 | `chunk_evidence_recall@k` data path specified end-to-end: `_run_one` outcome copy → `outcomes_to_report` extra projection → `build_report` retrieval aggregation (T3/T4) — T5 sweep has a defined source |
| V4 | P2 | Verification Plan gains the pool-depth headroom regression-guard note (`max(ks) * 3` → `test_session_crowded_out_still_surfaces`) |

_Plan-review status: clean (2 review cycles: 3 parallel reviewers + 1 fresh-context verifier; 16 R-fixes + 4 V-fixes applied). Ready for execution handoff._
