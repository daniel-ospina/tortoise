<!-- research-path: docs/plans/2026-08-27-1787-extractor-cap-truncation.md (self-contained scoping + plan; sources: reval.report.json + reval.checkpoint.json artifacts, DeepSeek API docs, competitor research) -->

# Plan — #1787: S2/S4 8000-token cap truncates embed lists (silent tail-entity loss)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Eliminate the silent tail-entity loss caused by the 8000-token S2/S4 output cap on dense sessions — with **≤ 1 partial_parse question on the next fresh 50-Q**, no regression in accuracy (≥ 0.826), chunk_evidence@20 (≥ 0.7375 — the measured baseline), or cost (+30% budget). **Interpretation note (cycle 3 — P2-4):** the issue's literal chunk_evidence@20 acceptance is **≥ 0.738**, which sits ABOVE the measured baseline (0.7375). A run landing in **[0.7375, 0.738)** FAILS the gate unless owner sign-off is recorded on #1787 (cycle 5 — P2-E: the band is a DISTINCT conditional branch — never silently waived; the `--compare` statistical shared-qid verdict stays PRIMARY (see gate 3 / Step 4); R6 P2: the Goal's earlier "passes this plan's no-regression gate" wording is superseded by the fail-unless-sign-off contract the script implements).

**Team:** epistemic-team
**Tier:** complex (complexity:complex — re-rated from standard in cycle 5, P2-D FINAL decision; Architecture domain)

**Architecture:** Raise the S2/S4 output cap from 8000 → 16000 tokens (the cap is stale — set in M3 #1524 when DeepSeek's ceiling was 8K; the V4-era ceiling is 384K, so 16K is comfortably within the model's capability) while keeping the #1746 parse-recovery ladder as the backstop, the `TORTOISE_EXTRACTOR_MAX_TOKENS` env override, and the census semantics unchanged. **The real footprint (cycle 3 — P1-5):** (1) the constant change itself; (2) a live-API probe verifying `max_tokens=16000` is accepted before the default flips (Task 1); (3) `_complete` deadline-scaling so the worst-case 16K emission clears the 600s default deadline (Task 2 Step 6); (4) additive measurement-harness code — in-adapter token accumulation, `stage_stats`, freshness markers, pre/post-run DB fingerprints, the per-call `llm_error_census`, and run composition — all new keys in the report (Task 5 Step 0); and (5) new/updated unit tests for each surface (probe, cap change, deadline scaling, threaded cap path, accumulator, fingerprint, stage_stats). What stays byte-identical: the #1746 partial-accept ladder, the census vocabulary/semantics, and the env override — their tests stay green. A probe task first empirically verifies the API accepts `max_tokens=16000` on the in-use model id before the default flips.

---

## Part 1 — Scoping (issue-scoping double diamond)

### 1.1 Confirmed problem (Phase 2 converge)

**The 8000-token S2/S4 output cap truncates embed-list JSON on dense sessions; the #1746 ladder's partial-accept then silently drops the tail of the entity list — those entities never enter the graph, and downstream evidence gaps follow.**

Evidence (reval artifacts, 46 questions, `deepseek-chat` extractor, SHA 57f43978):

- **15 of 6,720 S2/S4 calls** hit `finish_reason=length` (0.22% of calls) → **12 questions (26%)** carry ≥1 truncation. **9 questions (20%)** carry the `partial_parse` census class as a question-level data-loss event; the aggregate census counter is **12 events across 9 questions (6×1 + 3×2 — the 3 double-bumped questions had S2+S4 both partial-accept; R4 P2-4 corrects the earlier "12 = 9 + 3" framing, which double-counted the 3 lossless-recovered `n_truncated_valid=3` rung-1/3 recoveries — those 3 qids have EMPTY error_classes and never reach the partial_parse census; they are a disjoint set)** — 9 data-loss, 3 benign.
- **Silent data-loss footprint quantified:** partial_parse questions average **679 points written vs 1,160 for clean** (−42%); `chunk_evidence_recall@20` 0.667 vs 0.75 (−11%).
- **Accuracy:** 7/9 partial questions correct (0.78) vs 31/37 clean (0.838) — the issue's 0.78 vs 0.84.
- **Truncation concentrates by output size, not session count:** truncated qids have ~10.2 turns/session — indistinguishable from clean (10.3). The trigger is a *dense session's* S2/S4 output exceeding 8K tokens, not big sessions per se.
- **Mechanism chain (verified in code):** `_complete_parsed` (extractor_v2.py:959) sees `finish_reason="length"` → **skips the parse-retry** (deterministic failure, D3) → ladder rung 4 `_longest_valid_prefix` (line 4340) accepts the longest schema-valid prefix at an item boundary (`stats["partial"]=True`) → caller appends `partial_parse` + "truncated tail dropped" error (lines 3493-3497) → **the partial list IS used** — tail entities/points silently absent from the graph.
- **The S4 re-emit tax is the largest truncation source:** S4 re-emits the COMPLETE embed list (S2 + corrections + gaps, `{embed_list_json}` at line 1336) — S4 output ≈ 2× S2 output, so S4 truncates first (1746 plan's "7-8 vs 6 failure skew" observation).

### 1.2 Alternative framings considered (Phase 1 diverge)

| Framing | Verdict | Why rejected |
|---|---|---|
| **F1 (original): cap is too low for deepseek-chat output** | **ACCEPTED** | The cap is a stale constant, not a model limit (V4 ceiling 384K). Verified: docs + pricing page. |
| F2: chunking is too coarse (chunk-turns / chunk-size knobs wrong) | Rejected | `chunk_turns` (ingest, default 2) sizes the **raw-chunk verbatim graph leg**, not extraction; `chunk_size` (extractor, default 50 EDUs) only parallelizes S1 within a session. Neither reduces S2/S4 per-call output. The extraction unit is the dataset session (~10 turns). |
| F3: the ladder is too lossy (partial-accept should not be silent) | Rejected as *root* | The ladder already records `partial_parse` loudly (census + error string). The *loss* is inherent to accepting a truncated prefix; the fix is upstream (don't truncate). Ladder behavior is the backstop, not the bug. |
| F4: the model id (`deepseek-chat`) is the problem | Adjacent issue, not root | `deepseek-chat` is a V3-era alias; empirically still routes in the reval (Aug 27). Retirement is a separate adapter-migration issue (see 1.7). |

**Falsification check:** if the API rejects/ignores `max_tokens > 8192` on the in-use model id (probe Task 1), the cap-raise approach fails → fall back to option B (chunk shrink) or D (continuation contract).

**Confidence: 85** (model ceiling verified from official docs; the only unverified link is whether the legacy alias honors a >8K `max_tokens` — Task 1 probes it).

### 1.3 Assumption mapping

| Assumption | Status | Evidence / Falsification |
|---|---|---|
| The cap is the binding constraint (not prompt quality) | validated | 15 `length` finishes; ladder recovers head-only |
| Raising the cap actually lets the model emit more | unverified → Task 1 probe | API must accept `max_tokens=16000` on `deepseek-chat`/`deepseek-v4-flash` |
| V4 max output is 384K | validated | api-docs.deepseek.com/quick_start/pricing ("MAX OUTPUT MAXIMUM: 384K") |
| Truncation rate ∝ output size, not session count | validated | truncated vs clean qids: turns/session 10.2 vs 10.3 |
| Cost impact of the raise is negligible | validated (est.) | only the ~15 truncated calls emit beyond 8K (see 1.4) |
| S4 re-emit is the dominant truncation surface | partially validated | structural (S4 output ≈ 2× S2); not stage-attributed in checkpoint |

### 1.4 Cost / latency baseline & deltas (measured + estimated)

**Baseline (reval, 46 questions):**
- 6,720 LLM calls total, **146.1 calls/question** (range 123-171). Per session ≈ 3 calls (S1 + S2 + S4).
- Ingest latency: mean **1,533,924 ms/question** (~25.6 min; median **1,544,144 ms** — computed cycle 4 from the reval's 46 per-outcome `ingest_latency_ms` values and pinned as the Step 2 gate's PRIMARY quantity; p95 1.68M ms) — LLM-wall-clock dominant (pilot #1549 measured 15-90s/session).
- Est. tokens/question: ~600K input + ~455K output (S1 ~2K in/1K out, S2 ~4.5K/3.5K, S4 ~6K/5K per call, ×~50 sessions).
- Est. cost/run at deepseek-v4-flash pricing: **~$20-40 per 46-Q run** (~$0.4-0.9/question) depending on peak/off-peak + context-cache hits (cache-hit input $0.014/M; off-peak in $0.22/M out $0.66/M; peak in $0.44/M out $1.32/M).

**Option A (raise 8K→16K) delta:**
- Cost: ≤120K extra output tokens on the 15 truncated calls **≈ $0.08-0.16 per run** (0.4-0.8% of the $20-40 baseline — ~40-75× inside the +30% budget).
- Latency: the 15 truncated calls emit up to +8K tokens each — at realistic DeepSeek throughput (~50-100 tok/s) that is **+80-160s per affected call ≈ +20-40 min of added generation per run**. The reval actually ran with `--workers 5` (`methodology.workers=5`, verified from reval.report.json — the plan's earlier "8" was wrong) and Task 5 mirrors that (E2E at `--workers 5`, P1-1), so the E2E wall-clock is ~3.9h (46 Q × ~25.6 min ÷ 5 workers); the sequential-denominator figure (~2-3% of ingest wall-clock) UNDERSTATES the parallel delta — the added generation lands on the critical path: **≈ +9-17% of the ~3.9h parallel wall-clock (upper bound; the ~15 affected calls overlap across workers, so the realized delta is typically lower)**. Acceptable (vs option B's +80-100%), but not free — measured in Task 5 (gate ≤ +30%, not the old +10%).
- Reliability: truncation class shrinks toward 0 for lists ≤16K tokens (all observed cases).

**Option B (2× session split) delta:**
- Calls: 6,720 → ~13,800 per run (+105%).
- Cost: **≈ +10-40% under a naive token model** (calls double but per-call output halves; S2/S4 list tokens stay ~flat, only S1 narrative + fixed prompt overhead grow) — **unverified**; +80-90% only if a cross-session merge/dedup pass dominates. The unverified range straddles the +30% budget → B is rejected on latency + new-knob risk + dedup complexity rather than a certain cost blowup. **If Task 1 shows the cap-raise fails, re-derive B's real cost before falling back.**
- Latency: +80-100% at `session_workers=1` (parallelizable via `--session-workers>1` with per-worker models, pilot #1549).
- Reliability: per-call output halves → truncation ~0.

### 1.5 External research (Phase 1.5 artifact)

#### Axis Research (Architecture — the rated high axis)

**A1 — Model output ceilings (canonical):**
- DeepSeek V4 models (deepseek-v4-flash/pro): context 1M, **max output 384K tokens**; dual thinking/non-thinking modes, thinking is default. Source: https://api-docs.deepseek.com/quick_start/pricing (fetched 2026-08-27).
- `deepseek-chat` is a legacy alias routing to deepseek-v4-flash (non-thinking); official retirement announced 2026-07-24 15:59 UTC — **empirically still functional in the reval on 2026-08-27** (6,720 calls, no 401s; 4 failures were network timeouts). Sources: https://deepseek.ai/blog/deepseek-v4-ga-surge-pricing-migration, https://api-docs.deepseek.com/news/news260424/.
- V3-era ceiling was 8K (Beta API raised 4K→8K) — the historical basis of the current 8000 cap. Source: https://api-docs.deepseek.com/news/news0725/.
- **Implication: the 8000 cap is a stale constant, not a model constraint. Raising it works — the pivotal finding that inverts the issue's open question.**

**A2 — Structured-output truncation handling (canonical + pitfalls):**
- OpenAI/Anthropic guidance: strict structured output does NOT prevent truncation; `finish_reason="length"`/`stop_reason="max_tokens"` must be handled by **retrying with a higher `max_tokens`** — our `_complete_parsed` skip-on-length is the documented anti-pattern for a raiseable cap. Sources: https://platform.claude.com/docs/en/build-with-claude/structured-outputs, https://developers.openai.com/api/docs/guides/structured-outputs.md.
- Anthropic explicitly: response may be incomplete and not match schema when cut off → "retrying with a higher limit" is the canonical response. Source: https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons.

**A3 — Competitor extraction pipelines (competitor-precedent):**
- **MemForest (paper, 2026):** extraction/maintenance dominates latency over retrieval; **2-turn chunks optimal** for fact fidelity × token efficiency; whole-session extraction least efficient; >8-turn chunks degrade fidelity. Source: https://arxiv.org/html/2605.23986.
- **Mem0:** v3 = single-pass **ADD-only** extraction (−60-70% write-time calls vs 3-call pipelines); two-phase (extract facts → reconcile against existing). Source: https://mem0.ai/blog/the-2026-token-optimization-playbook-cut-ai-agent-memory-costs-3%E2%80%934x, https://forum.letta.com/t/agent-memory-letta-vs-mem0-vs-zep-vs-cognee/88.
- **Zep:** single-pass ADD-only extraction into a bi-temporal knowledge graph. Source: https://mem0.ai/blog/zep-vs-mem0-which-ai-memory-layer-should-you-choose.
- **Cloudflare Agent Memory:** two parallel passes, ~10K-char chunks with overlap + a detail pass for concrete entity values. Source: https://blog.cloudflare.com/introducing-agent-memory/.
- **Claude Memory Extractor:** ~8,000-token target chunk size; LLM latency dominates (30-90s/chunk). Source: https://deepwiki.com/obra/claude-memory-extractor/4.1-extraction-pipeline.
- **Hindsight:** one extraction call per chunk, mini-batched, ≤32-way parallel. Source: https://hindsight.vectorize.io/blog/2026/05/08/how-hindsight-scales.
- **Pattern takeaway: industry standard = bounded per-call extraction units (turn/chunk-granular, 2-8 turns or ~8-10K chars) + ADD-only/delta updates. Nobody re-emits the complete entity list per call — our S4 re-emit contract is the anti-pattern (the "re-emit tax"). Our sessions (~10 turns) are at the upper edge of the fidelity sweet spot; whole-session extraction is what the dataset imposes.**

**A4 — Pagination / continuation (pitfalls + precedent for option D):**
- Structured-output pagination is a recognized pattern: `page`/`page_size`/`has_next`/`total_pages` metadata so the model continues until complete; cursor/offset continuation; vLLM/Cohere structured outputs support repeated-call continuation. Sources: https://promptz2h.com/chapter_16_structured_output_and_reliability_engineering/series_04_function_schemas_and_typed_interfaces/real_world_schema_patterns, https://docs.vllm.ai/en/stable/features/structured_outputs/, https://docs.cohere.com/docs/structured-outputs.
- **But:** changes the OUTPUT_CONTRACT + `_OUTPUT_SCHEMA` + merge/execution (new coupling, explicitly flagged in #1746 D5) — disproportionate risk at a 0.22% truncation rate. Held as escalation path, not the primary fix.

#### Integration Docs

One new **DEV-ONLY** dependency (cycle 4 — P1-B): `tiktoken` — the Task 1 probe's calibration tokenizer (`uv add --dev tiktoken`, Task 1 Step 0; updates `pyproject.toml` + `uv.lock`, committed; cl100k encoding is a stand-in for the served model's tokenizer, ±15% — no runtime footprint; its BPE ranks file is fetched once on first use, then cached (cycle 5 — P2-B)). No new RUNTIME dependencies: the plan touches the existing `DeepSeekDirectModel` adapter (in-repo wrapper, used by the reval) and the LME harness (`tools/longmem_eval/run.py`). The optional `transformers` DeepSeek-V3 tokenizer path (HF-hub download — REQUIRES network on first use) is retained as a precision check only, never a hard dependency. DeepSeek API surface verified above (model ids, ceiling, thinking toggle, pricing).

### 1.6 Solution approaches (Phase 4 diverge) + tradeoff matrix

| Option | Cost Δ (per 46-Q run) | Latency Δ | Truncation fix | Reliability/quality | Diff surface | Best-fit if |
|---|---|---|---|---|---|---|
| **A. Raise cap 8K→16K (default)** | +$0.08-0.16 (0.4-0.8%) | +80-160s × 15 calls ≈ +9-17% wall-clock under 5-way parallelism (upper bound; ≈ +20-40 min added generation on a ~3.9h parallel run; E2E mirrors the reval's `--workers 5`) | **Yes — for all observed lists** | Ladder backstop unchanged; partial_parse → ~0; 16K covers all observed truncations | 1 constant + 1 probe + tests | **Chosen** |
| B. Shrink chunks (2× session split) | **+10-40% (unverified; +80-90% if merge-pass dominates)** — straddles +30% budget | +80-100% (parallelizable) | Yes (output halves) | Cross-session dedup complexity; new knob | ingest_v2 session-splitting + dedup | If the API rejects >8K max_tokens |
| C. Both A+B | +80-90% | +80-100% | Yes | Highest complexity | A + B | Not justified without evidence A fails |
| D. Prompt-side continuation contract (bounded emit + pagination) | +moderate (2nd pass on overflow only) | +moderate | Yes, structurally | Changes OUTPUT_CONTRACT/_OUTPUT_SCHEMA/merge (new coupling #1746 D5); highest risk | contract + schema + ladder + merge | If dense sessions exceed 16K routinely |
| E. S4 delta contract (emit gaps only; E4 union already exists) | −~30% (halves S4 output) | −20-30% | Reduces (S4 output halves) | **Changes S4 semantics** (corrections/lifecycle-ids must survive the union) | S4 template + E4 merge | Follow-up issue if A insufficient (#1787 companion) |

### 1.7 Solution converge (Phase 5) — recommendation

**Option A: raise `_S2_S4_MAX_TOKENS` 8000 → 16000 as the default, keeping the env override and ladder.** Rationale (quality-over-convenience):

1. **It fixes the root mechanism** — the model can emit the full embed list; the partial-accept never fires for observed list sizes.
2. **The cap is demonstrably stale** — set when the V3 ceiling was 8K; the V4 ceiling is 384K. The env override (`TORTOISE_EXTRACTOR_MAX_TOKENS`) remains the mechanical-fix lever (M3 D6).
3. **Cost/latency deltas are small** (≤0.8% cost; ≈ +9-17% wall-clock under 5-way parallelism — upper bound, dominated by the ~15 affected calls' added generation overlapping across workers; the reval ran at `--workers 5`, which Task 5 mirrors) — inside the +30% budget by ~40-75× on cost, unlike option B (unverified +10-40%, plus a new knob + dedup risk).
4. **Bounded diff + test surface** — the constant, the probe, `_complete` deadline-scaling (Task 2 Step 6), and new/updated unit tests; the measurement-harness additions (token accumulation, `stage_stats`, freshness markers, pre/post DB fingerprints, `llm_error_census`, run composition — Task 5 Step 0) are the only non-trivial NEW code and are additive to the report. The #1746 ladder and census semantics stay byte-identical and the env override is unchanged — their tests stay green; existing report keys keep their meaning (new keys are additive).
5. **Backstop preserved** — the partial-accept ladder remains as defense-in-depth; the issue's own verification surface ("cap change test; truncation → ladder still recovers; partial_parse class unchanged") maps exactly.

**Rejected alternatives:** B/C (unverified cost band straddling the +30% budget, new session-splitting knob, dedup risk, +80-100% latency); D (contract/schema/merge coupling at a 0.22% rate — escalation path if 16K proves insufficient); E (S4 semantics change — file as a companion issue, see 1.7 adjacent issues).

**Adjacent issues to file (do NOT absorb):**

1. **Adapter migration off the `deepseek-chat` legacy alias** — officially retired 2026-07-24; empirically still serving the reval but unsupported. Migrate `DeepSeekDirectModel('deepseek-chat')` → `deepseek-v4-flash` + explicit `{"thinking":{"type":"disabled"}}` (thinking is V4 default; the flash family's reasoning collapse is documented in pilot #1549). **Soft dependency for #1787's E2E run — flag, don't block.**
2. **S4 re-emit tax (delta contract, option E)** — structural follow-up if truncation persists on denser data.
3. **(Context) 4 network-timeout failures in the reval** (`ingest:retries_exhausted`) — infra-resilience surface, tracked per the epic's run-protocol failure handling (the §7.5 run-protocol notes consume #1746/#1747; M4 retry-then-fix defines the timeout retry×2 policy this plan's gate 6 mirrors). **Epic alignment (cycle 5 — P2-G + R6 P2):** this plan is scoped under **Epic #1509 (Extractor V3)** — `docs/epics/2026-08-20-1509-extractor-v3/` (00-scope through 07-verify) — whose **J4/M4 run protocol and measurement-validity surface** this plan inherits (the checkable half: M4 retry-then-fix, J4 run-protocol accounting chain, §7.5 notes consuming #1746/#1747); the network-timeout surface's tracked parallel issue is named in the epic's run-protocol accounting chain, making the alignment claim checkable from the plan text. **Softened claim (R6 P2):** the epic does NOT itself own the S2/S4 stage-cap surface (its only truncation surface is P4 "stored-transcript truncation parity — 5000-char window", a different cap) — #1787's mechanism ownership is this plan's, with the epic as the run-protocol/measurement-validity parent.

**Note on integrity.valid (reviewer P2):** the reval report is `integrity.valid=false` (n_failed=4 network timeouts + partial_parse under `--integrity-threshold 0.0`), and the issue's own ≤1-partial target means a successful fix still reports `valid=false` under threshold 0.0. The E2E gate therefore checks the five numeric targets + a documented-failure justification, not the binary `valid` flag (see Task 5 Step 4).

### 1.8 Verification checklist (per the issue)

| Surface | Test Layer | Expected Verification |
|---|---|---|
| S2/S4 cap | unit (extractor) | Cap-change test: a fixture that overflows 8K emits **complete** at 16K (no `partial_parse`); truncation-at-16K still reaches the ladder (backstop); `_stage_cap` env override unchanged; ladder/recovery tests stay green |
| re-validation | integration | Probe: `max_tokens=16000` accepted by the live API (no 400, non-clamped) |
| E2E | fresh 50-Q | `partial_parse` ≤ 1 question; `llm_truncated` ≤ 1 call; accuracy ≥ 0.826; chunk_evidence@20 ≥ 0.7375 (issue literal ≥ 0.738 — [0.7375, 0.738) = no-regression only, interpretation recorded on #1787 per P2-4); cost ≤ +30% |

### 1.9 Wiring check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| `_S2_S4_MAX_TOKENS` constant | code | Task 2 | ✅ |
| `_stage_cap` env override | code (unchanged) | Task 2 Step 5-6; Step 8 checkpoint | ✅ |
| `_complete_parsed` / ladder | code (unchanged) | Task 2 Step 6; Step 8 checkpoint | ✅ |
| `partial_parse` census + report integrity | code (unchanged) | Task 2 Step 6; Step 8 checkpoint + E2E | ✅ |
| `DeepSeekDirectModel` wire id | external API | Task 1 probe; adjacent issue | ⚠️ flag |
| LME harness `run.py` | tooling | E2E task | ✅ |
| Cost measurement | ops | E2E task | ✅ |

### 1.10 Complexity

| Domain | Rating | Rationale |
|---|---|---|
| Architecture | **complex** | Constant change + live probe + `_complete` deadline-scaling + additive measurement-harness code (token accumulation, `stage_stats`, freshness markers, pre/post DB fingerprints, `llm_error_census`, run composition, per-qid provenance, deadline-abort seam) + new/updated unit tests. **Cycle 5 — P2-D: re-rated standard → complex (FINAL tier decision)** — Task 5 Step 0 + the Step 2 gate script are real new harness code across run.py/extractor_v2/model_adapters, the deadline math touches the retry path, and the accumulation of 5 review cycles (per_qid post-run DB query, the `deadline_aborted` lock seam, the `--qids` subset mechanism, the full-length pre-flight probe, the heartbeat gate) exceeds the standard-tier surface; no contract/schema changes, but the harness + gate scope is honestly complex. **Concession (cycle 4 — P2-D + cycle 5 — P2-D + R4 P2-6 + FINAL-VERIFICATION P3):** the plan has grown substantially across 13+ review cycles of changelog (the cycle-5 ~1,484 citation is stale by ~600; the R4 "~1,950" restatement is itself stale by ~150) for what was a standard-tier issue; the exact figure is NOT a gate quantity and is restated only here — the tier decision itself does not depend on it) for what was a standard-tier issue; the cycle-4 concession's own trigger ("if the next review round adds substantive design surface, re-rate to complex") was hit by cycle 4-5's additions — **the tier is RE-RATED to COMPLEX and this is FINAL (no further re-rating churn)**; the plan is executed at the complex-tier gate depth |

---

## Part 2 — Implementation plan

### Pattern Research

> **Findings date:** 2026-08-27

**Library docs (preflight)** — DeepSeek API (in-repo `DeepSeekDirectModel` wrapper; no new runtime deps — one dev-only `tiktoken` for probe calibration, Task 1 Step 0):
- `deepseek-v4-flash`/`deepseek-v4-pro`: 1M context, **384K max output**, thinking default / non-thinking via `{"thinking":{"type":"disabled"}}` (https://api-docs.deepseek.com/quick_start/pricing, https://api-docs.deepseek.com/guides/thinking_mode/).
- `deepseek-chat` legacy alias → routes to v4-flash non-thinking; retired 2026-07-24 (empirically still live in the reval; migrate in the companion issue). (https://api-docs.deepseek.com/news/news260424/, https://wavespeed.ai/blog/posts/blog-deepseek-v4-model-name-migration/)

**Library version & API surface** — [3 calls | DeepSeek ceiling + alias]
- Canonical: 384K max output on V4 — the 8K cap is self-imposed, stale from the V3 8K ceiling (https://api-docs.deepseek.com/news/news0725/).
- Competitor variance: OpenAI/Anthropic canonical truncation handling = retry with a higher `max_tokens` when `finish_reason="length"` (https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons, https://developers.openai.com/api/docs/guides/structured-outputs.md).
- Known pitfall: strict structured output does NOT prevent truncation; JSON mode + cap still truncates (in-repo #1746 D6 comment; https://aipromptshub.co/blog/openai-structured-outputs-tutorial).

**Idiomatic usage patterns** — [3 calls | bounded extraction units]
- Canonical: 2-turn chunks optimal (MemForest, https://arxiv.org/html/2605.23986); ~8-10K-char chunks (Claude Memory Extractor, Cloudflare, https://deepwiki.com/obra/claude-memory-extractor/4.1-extraction-pipeline, https://blog.cloudflare.com/introducing-agent-memory/).
- Competitor variance: Mem0/Zep single-pass ADD-only extraction (https://mem0.ai/blog/the-2026-token-optimization-playbook-cut-ai-agent-memory-costs-3%E2%80%934x, https://mem0.ai/blog/zep-vs-mem0-which-ai-memory-layer-should-you-choose).
- Known pitfall: whole-session extraction is the least token-efficient (MemForest) — our S4 full-re-emit is the same anti-pattern, held as companion issue.

**Library/framework pitfalls** — [3 calls | cap + continuation]
- Canonical: pagination/continuation is a recognized structured-output pattern (page/has_next/cursor; https://promptz2h.com/chapter_16_structured_output_and_reliability_engineering/series_04_function_schemas_and_typed_interfaces/real_world_schema_patterns).
- Competitor variance: vLLM/Cohere structured outputs support repeated-call continuation (https://docs.vllm.ai/en/stable/features/structured_outputs/, https://docs.cohere.com/docs/structured-outputs).
- Known pitfall: schema + contract changes create coupling (in-repo #1746 D5) — escalation path only.

### Integration Surface Map

| Surface | Layer | Notes / Failure modes |
|---|---|---|
| `_S2_S4_MAX_TOKENS` (extractor_v2.py:3751) | unit | Default change 8000→16000; `_stage_cap` env override must still win (read at call time). **Failure modes: raise applied but env override still wins (the override is read at call time and beats the constant for ANY default — tested); Task 1 gate bypassed (the constant flips without the recorded probe outcome — Task 2 precondition note)** |
| `_stage_cap` / `TORTOISE_EXTRACTOR_MAX_TOKENS` | unit | Unchanged — regression test: invalid override warns + falls back; valid override wins; `_stage_cap(16000)` + env 24000 → 24000 (override pin) |
| `_complete_parsed` length-skip + ladder rungs 1-5 | unit | Unchanged — regression: at 16K cap, an 8K-overflow fixture completes clean; a >16K fixture still partial-accepts (backstop, `stats["partial"]=True`) |
| `partial_parse` census + error strings (S2/S4 callers) | unit | Unchanged — assert no new census classes; `n_ingest_errors == sum(error_census)` holds |
| `DeepSeekDirectModel.complete` max_tokens kwarg | integration probe | Task 1: live API accepts 16000 on `deepseek-chat`; non-clamped (echo check); no 400. **Failure modes (cycle 4 — P2-J):** probe calibration dependency availability (no tokenizer installed → calibration cannot gate — provisioned dev-only in Task 1 Step 0; fallback = pessimistic char-bound, see Task 1 Step 2); probe echo-length vs proof-threshold margin (tokenizer fidelity: cl100k vs the served v4-flash tokenizer, ±15% band — the floor assert must clear 8192 under the worst case, Task 1 Step 1); probe unbounded call (no deadline — a stalled chunked response can defeat the adapter's 60s read timeout per pilot #1549; the probe runs under `_complete`'s deadline, Task 1 Step 1). **Failure modes also cover `_should_send_json_mode`:** both probe prompts contain "json" → `response_format: json_object` is sent (TORTOISE_JSON_MODE default "1") — DeepSeek json_object mode requires a TOP-LEVEL JSON OBJECT, so the echo probe must emit an object (a top-level-array prompt would 400/contradict); escape hatch `TORTOISE_JSON_MODE=0` disables it |
| `sdk.py:2301` doc comment ("S2,S4 → 8000 tokens per stage") | docs/unit-pin | Mirror of the cap — update to 16000 with the constant; stale comment = wrong reader-budget expectations; **mirror updated without the constant → pin drift (comment says 16000 while the constant reverts)** |
| `probe_json_mode.py:57` (`_MAX_TOKENS = 8000`) | docs/unit-pin | Mirror of the cap ("truncation is part of the signal") — update to 16000 to probe at the ACTIVE cap; **mirror updated without the constant → pin drift (diagnostic probes a cap that no longer exists)** |
| `test_extractor_reliability.py` docstring line 7 + cap pin (`[1500, 8000, 8000]`) | docs/unit-pin | Mirror of the cap — update to 16000 (pin + module docstring); otherwise the suite asserts the OLD cap and fails post-change; **mirror updated without the constant → pin drift (suite asserts a cap the code no longer uses)** |
| LME harness `tools/longmem_eval/run.py` + `tortoise/extractor_v2.py` | E2E | **REQUIRED changes (Task 5 Step 0): token accumulation at the adapter/call site (lock-protected), `stage_stats` emission, freshness-marker recording, pre/post-run DB fingerprint, per-call LLM census (`llm_error_census`), run composition (provider/model/wire-id)**; fresh 50-Q run (same split=s set) with the new default; measurement protocol in Task 5. **Failure modes: accumulator lost-update under threads (`attr += n` is LOAD/INPLACE_ADD/STORE across the 5 worker threads sharing one instance — needs a lock; real-adapter test with mocked transport); `stage_stats` keyed on the wrong stage (S2↔S4 misattribution poisons the #1789 decision); marker-less report (freshness keys absent → gate FAILs, never a silent pass); fingerprint read before the DB flush (stale pre-run count → false pollution fail)** |
| Task 5 Step 0 new surfaces (re-review R3 P2): `TORTOISE_EXTRACTOR_NO_FALLBACK` knob, `--qids`, `--db-flush`, heartbeat seam, `composition` recorder, per_qid DB query | unit/E2E | **Failure modes: NO_FALLBACK ignored (regression silently reintroduces mid-run RoutingModel failover — needs the POSITIVE test: knob set → fallback-less model; absent → warning; group (g)); `--qids` selection wrong/unknown qid (cost pin density-biased — selection unit test + unknown-qid exit-1); `--db-flush` scoped to the default namespace only (per-question `question_graph_namespace` graphs survive → clean-start gate false-fails — flush must GRAPH.LIST the run prefix and drop each); heartbeat routed through the shared RoutingModel (a heartbeat 429 flips `_failed_over` run-wide — heartbeat must call `extractor_model.primary` directly, unit-tested); `composition` fields missing/typo'd (each field gate-checked — effective_stage_cap/chunk_turns/surface); per_qid query before workers join (torn counts — teardown-ordering test)** |
| **Not-in-scope 8000s (leave untouched — documented; list NON-exhaustive):** `model_adapters.py:244/246` registry constructor caps (`qwen3.8-max` / `deepseek-v4-pro-noreason` `max_tokens=8000`), `test_sdk_adapter_cap.py` adapter caps, `DEFAULT_CONTEXT_TOKEN_CAP` reader budget (`tools/longmem_eval/retrieve.py:245`, 8000 context tokens), `_SOURCE_TRANSCRIPT_CAP` chars (`extractor_v2.py:714`), `tools/experiments/extractor-v2/{run_fix,run_loop,run_clean_test,run_ab}.py` (flash/solar `max_tokens = 8000` — standalone experiment harnesses, NOT S2/S4 stage-cap mirrors), `tests/eval/retrieval/judge.py:540` (`--max-tokens` default 8000, judge CLI) | n/a | Different surfaces (judge/reader/transcript budgets, adapter defaults, experiment harnesses, judge CLI) — deliberately NOT raised by #1787; do not touch (the experiments scripts + judge default are cap-adjacent but NOT stage-cap mirrors — different models/CLIs) |

### Journey Test Map

### Journey: A dense session is captured → extraction completes
1. **Step:** dense session → S2 maps → **Acceptance:** full embed list emitted (no `partial_parse`, no tail loss) → **Test:** cap-change unit test (Task 2)
2. **Step:** S4 re-emits the complete list + gaps → **Acceptance:** output < 16K for observed density; merge union intact → **Test:** ladder regression (Task 2 Step 6)
3. **Step:** pathological >16K output → **Acceptance:** ladder partial-accepts + `partial_parse` recorded (never silent) → **Test:** existing partial-accept tests stay green (Task 2 Step 6)

### Failure Modes
- API rejects max_tokens=16000 (legacy alias clamp) → **Expected:** probe fails → abort default change, escalate to option B/D (companion decision) → **Test:** Task 1 gate
- Cap raise unnoticed by cache/fingerprint machinery → **Expected:** fingerprint unchanged (cap is env/runtime, not prompt); revalidation still comparable → **Test:** Task 5 note
- Latency regression on truncated qids → **Expected:** ≈ +9-17% wall-clock under 5-way parallelism (upper bound; the old sequential-denominator ~2-3% understated the parallel delta; the E2E mirrors the reval's `--workers 5`, so gate 5 compares like-for-like); gate ≤ +30% → **Test:** ingest latency delta reported in Task 5

### Tech Stack

Python 3.12, deepseek-chat (direct adapter) / deepseek-v4-flash target, pytest, LME harness (`tools/longmem_eval/run.py`, LongMemEval split=s, 50 questions).

---

### Task 1: Probe — live API accepts `max_tokens=16000`

**Intent:** Verify the pivotal assumption (V4 ceiling 384K + legacy alias honors >8K) before any default change. Fail-closed gate for the whole plan.
**Acceptance:** Probe returns OK with evidence the completion budget can exceed 8K (no 400, no server clamp), on both `deepseek-chat` and `deepseek-v4-flash` (non-thinking toggle).

**Files:**
- Test: `tests/test_extractor_reliability.py` (or a standalone `tools/` probe script)

**Step 0: Provision the calibration tokenizer (dev-only)** — `_probe_filler`'s calibration needs a tokenizer, and neither `transformers` nor `tiktoken` is installed today (verified: `tiktoken` is NOT in uv.lock; `transformers` exists only transitively via the uninstalled `embeddings` extra). Add `tiktoken` as a DEV dependency — cl100k is the PRIMARY calibrator (no HF-hub MODEL download; deterministic BPE ranks file fetched once on first use, then cached — cycle 5, P2-B):

```bash
uv add --dev tiktoken   # dev-only — updates pyproject.toml + uv.lock; fold into Step 6's commit
```

**Live marker registration (R4 P2-2 — owned HERE):** the `live` pytest marker is appended to `[tool.pytest.ini_options] markers` in the SAME pyproject.toml edit — `"live: live-API probe tests (Task 1 #1787) — excluded from the deterministic suite with -m 'not live'"` — so the marker is registered, not just used (an unregistered marker works for `-m "not live"` selection with a warning only, but the registration makes the intent declarative and silences the warning). The guard test in `tests/test_extractor_reliability.py` (asserts both probes carry `pytest.mark.live`) is also added in Task 1 Step 6's commit.

**Why dev-only + why tiktoken first (cycle 4 — P1-B):** the calibration is probe-time-only (never in the runtime path), so the dependency is dev-only with zero runtime footprint. tiktoken's cl100k is preferred as the primary calibrator because it needs no HF-hub MODEL download and its BPE ranks file is deterministic + cached after first use. **Correction (cycle 5 — P2-B):** `tiktoken.get_encoding("cl100k_base")` DOES fetch its BPE ranks file from OpenAI blob storage on FIRST use (cached afterward), so the cycle-4 "needs NO network" claim was wrong and the old "if offline, tiktoken is used" branch is only reachable after a prior cached use — the TRUE offline path is Step 1's pessimistic 2-chars/token fallback, which the plan already covers; the optional `transformers` DeepSeek-V3 tokenizer path (higher fidelity) REQUIRES a network connection on first use to download from the HF hub. If neither tokenizer is available, the calibration does NOT hard-fail (see Step 1's pessimistic char-bound fallback).

**Step 1: Write the probe test** (live-API marked, skips without `DEEPSEEK_API_KEY`) — the probe must FORCE a >8K generation so a server-side clamp to 8192 is detectable (a tiny echo prompt can't):

```python
def _probe_filler(repeat: int = 6000):
    """Cycle-4 P1-C/P2-E — build + CALIBRATE the echo filler. `"word ` is
    plausibly ONE BPE token (~5-7 chars), so a fixed char count is
    UNCALIBRATED. MEASURED (cycle 4): repeat=4700 → 32,915 chars → 9,406
    tokens (cl100k) — only ~14.8% above 8192, INSIDE the ±15% cl100k error
    band, so the cycle-3 `repeat=4700` band floor was too thin. The new
    floor: calibrate to land at ~12K cl100k tokens (repeat=6000 → ~12K)
    so the worst-case real echo (0.85 × 12K ≈ 10.2K under the ±15%
    cl100k-vs-served-tokenizer family-drift band) clears 8192 with ~24%
    margin. Per P2-E the UPPER band is dropped: exhaust-at-16K is already
    PASS (P1-1 — the >8192 assert alone proves no clamp).
    Fallback when NO tokenizer is installed: the filler alone at a
    PESSIMISTIC 2 chars/token (~36K chars ≈ 18K tokens) still clears 8192
    with margin — the calibration never hard-fails (RuntimeError removed),
    and the live `tokens > 8192` assert carries the verdict (P1-1)."""
    import json
    filler = '"word ' * repeat            # ~42K chars at repeat=6000
    payload = json.dumps({"items": [filler]})   # P1-3: well-formed JSON
    # calibration — count tokens of the assembled prompt BEFORE the live call
    tokenizer_name = None
    try:
        import tiktoken  # PRIMARY (cycle 4 — P1-B): no HF-hub MODEL download; BPE ranks fetched once on first use, cached (cycle 5 — P2-B)
        tok = tiktoken.get_encoding("cl100k_base")  # approx, ±15%
        tokenizer_name = f"tiktoken/{tok.name}"
    except Exception:
        try:
            from transformers import AutoTokenizer  # OPTIONAL precision check
            tok = AutoTokenizer.from_pretrained("deepseek-ai/DeepSeek-V3")  # REQUIRES NETWORK (HF hub)
            tokenizer_name = "transformers/DeepSeek-V3"
        except Exception:
            tok = None  # no tokenizer — pessimistic char-bound fallback
    if tok is not None:
        n = len(tok.encode(payload))
        # P1-C: assert the echo budget's LOWER bound against the ±15%
        # calibration error band — 0.85 × 10500 ≈ 8,925 > 8,192, so the
        # worst-case real echo always clears the proof threshold.
        assert n >= 10500, \
            f"filler uncalibrated: {repeat} repeats → {n} tokens (need ≥ 10,500 — " \
            f"worst-case real echo ≥ 8,925 > 8,192 at the ±15% band)"
    else:
        # P2-E: no tokenizer — size so a pessimistic ~2 chars/token bound
        # clears 8192 and let the live tokens>8192 assert carry the verdict.
        chars = len(payload)
        n = chars // 2
        assert n >= 8192, f"filler too small even at 2 chars/token: {chars} chars → ~{n} tokens"
        tokenizer_name = "pessimistic-2-chars-per-token (NO tokenizer installed)"
    return payload, n, tokenizer_name


@pytest.mark.live  # re-review P1 — excluded from deterministic suites (-m "not live") via Task 2 Step 6 Part B
# Task 1 probe — live API; MUST carry @pytest.mark.live (registered in
# pyproject.toml markers) so the deterministic regression never re-executes
# it; the guard test + Step 8 byte-identity enumeration pin the marker.
def test_probe_max_tokens_above_8k():
    """#1787 Task 1 — the V4 ceiling is 384K; the legacy alias must accept
    max_tokens=16000 without a 400 or a server-side clamp to 8192. The probe
    forces a LONG generation (calibrated JSON echo, ~12K tokens) and
    asserts the adapter recorded >8192 completion tokens; finish_reason is
    read per P1-1 — clamp-vs-exhaust is distinguishable by completion_tokens
    alone: clamp-at-8192 → length at ~8K; exhaust-at-16K → length at ~16K.
    The echo is ALSO fidelity-checked (cycle 5 — P1-B): a
    well-formed-but-short echo (elided/shortened) must not be misread as a
    clamp signal.
    The call runs through _complete's deadline machinery (P1-D — cycle 4):
    a stalled chunked response can defeat the adapter's 60s read timeout
    (pilot #1549; the reval's 4/6,720 timeouts show the exposure), so the
    live call is bounded and classifiable, not unbounded."""
    # re-review P2 + R5 P2-4: the probe passes deadline_s=800 — the scaled
    # default Task 2 introduces (_scaled_deadline(600, 16000) = 800s = 0.05 ×
    # 16000) — NOT 600: at the plan's own ~20 tok/s healthy floor the ~12K
    # echo takes ~600s, exactly at the old explicit 600s, so a slow-but-
    # healthy backend false-fails the plan's fail-closed gate; 800 clears it
    # with the same margin Task 2's math defines. No dependency on Task 2's
    # signature change (the go/no-go verdict happens BEFORE Task 2 lands);
    # when Task 2's sentinel lands, deadline_s=None would inherit the same
    # 800s — the Step 8 (g) enumeration owns that optional edit.
    import os
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("no DEEPSEEK_API_KEY")
    from tortoise import extractor_v2 as v2
    from tortoise.model_adapters import DeepSeekDirectModel
    m = DeepSeekDirectModel("deepseek-chat", max_tokens=None, temperature=0.0)
    payload, est_tokens, tokenizer_name = _probe_filler()
    print(f"probe calibrator: {tokenizer_name} — {est_tokens} tokens "
          f"(floor ≥ 10,500; worst-case real echo ≥ 8,925 > 8,192)")
    # P1-3: the payload is ALREADY guaranteed well-formed (json.dumps) —
    # assert it parses as JSON before the API call so a malformed prompt can
    # never silently change the echo length (the old raw-string concat
    # produced 6000 unescaped `"` chars — NOT valid JSON — and with
    # json_object mode forced on, the API could 400/reject or the model
    # silently repair, breaking every token assertion below).
    import json
    json.loads(payload)
    # P1-D (cycle 4): run through _complete's deadline machinery — explicit
    # deadline_s=800 bounds a stalled response (R5 P2-4: 800s = 0.05 × 16000,
    # the scaled default Task 2 defines; the old explicit 600 sat exactly at
    # the ~20 tok/s healthy floor and false-failed slow-but-healthy backends).
    # A 400 (fatal) raises immediately
    # (fail-closed); a 429 is retried by _complete (bounded); a deadline
    # hit raises TimeoutError — classifiable as an operator/network error
    # (Step 2 taxonomy), NOT an API rejection.
    resp = v2._complete(
        m, system="Emit the exact JSON object from the user message, unchanged.",
        user=payload, max_tokens=16000, deadline_s=800)
    # json_object-mode note: "json" in the prompt flips on response_format
    # json_object (model_adapters._should_send_json_mode, TORTOISE_JSON_MODE
    # default "1"), which REQUIRES a top-level JSON OBJECT — the json.dumps
    # payload satisfies that contract (TORTOISE_JSON_MODE=0 remains the
    # escape hatch, but the payload is valid either way).
    assert isinstance(resp, str) and resp
    tokens = m.last_completion_tokens or 0
    # FINAL-VERIFICATION P1: the assert ordering is fixed — the OLD code ran
    # `json.loads(resp)` unconditionally, so a mid-string truncation at 16K
    # (the documented PASS/exhaust branch) raised JSONDecodeError and the
    # test ERRORED instead of PASSing — the pivotal go/no-go gate could not
    # express its own success case. The parse is now CONDITIONAL on
    # completeness; the no-clamp proof is tokens > 8192, period.
    if m.last_finish_reason == "length":
        # length: either clamp (tokens ≈ 8192 — FAIL) or exhaust (tokens ≈
        # 16000 — PASS; the echo is mid-string-truncated and UNPARSEABLE by
        # construction — that is the PASS signal, not a failure).
        assert tokens > 8192, \
            f"server clamped output at {tokens} tokens (finish=length)"
        print(f"probe: finish=length with {tokens} tokens — exhaust, "
              f"NOT a clamp (echo mid-string-truncated by design); PASS")
    else:
        # finish=stop: the echo is COMPLETE — run the fidelity checks. The
        # OLD order ran the clamp assert BEFORE fidelity, so a stop echo
        # with tokens ≤ 8192 (tokenizer drift / elided echo — model-
        # behavior, NOT a clamp) was misclassified as "server clamped".
        # FINAL-VERIFICATION P2: the clamp assert is now gated on
        # finish=length; stop + tokens ≤ 8192 falls through to the fidelity
        # check and is classified as an authoring/sizing signal (Step 2
        # taxonomy), matching the P1-1 docstring semantics.
        assert tokens > 8192, \
            f"echo too short: {tokens} tokens (finish=stop — the served " \
            f"tokenizer is ≥1.6× sparser than cl100k or the model elided " \
            f"the echo; re-run with repeat adjustment — NOT a clamp signal)"
        # P1-B (cycle 5): output-fidelity check — the verdict must not rest
        # on tokens alone. A served model that elides/shortens the echo
        # (finish=stop, well-formed but short) could land tokens ≤ 8192 and
        # be MISCLASSIFIED as a clamp/400 go/no-go signal → escalates the
        # whole plan to option B/D on a false negative. Assert the echo is
        # FIDELITOUS: it must parse as JSON (a round-trip of the payload)
        # and be ≥ ~0.9 × the payload's char length. A short-but-valid-JSON
        # echo with finish=stop is a MODEL-BEHAVIOR/AUTHORING failure (Step
        # 2 taxonomy), NOT a clamp signal.
        json.loads(resp)                   # round-trip parse — payload is JSON
        assert len(resp) >= 0.9 * len(payload), \
            f"echo fidelity: response {len(resp)} chars < 0.9 × payload " \
            f"{len(payload)} chars (model elided/shortened the echo — " \
            f"finish={m.last_finish_reason!r}; re-run with repeat adjustment " \
            f"/ investigate the served model's output preference — NOT a " \
            f"clamp)"
```

**Step 2: Run the probe**

Run: `uv run pytest tests/test_extractor_reliability.py::test_probe_max_tokens_above_8k -v`
Expected: PASS with `DEEPSEEK_API_KEY` set (or SKIP without it — the operator runs it once with the key). **Probe-robustness notes (resume-cycle P2):** (a) a SKIP without the key is NOT a pass — the Task 2 precondition verifies the #1787 comment records the probe outcome kind (accepted-on-alias / accepted-only-on-v4 / clamped / skipped-no-key), so a skipped probe is never misrecorded as "accepted-on-alias"; (b) the probe is a single live call — a load-balanced backend where some replicas clamp and others don't passes a one-shot probe, so the Task 1 Step 5 comment records it as a single-sample result and the E2E's Step 1 pre-flight item 5 (full-length 16K generation probe, 5 workers) re-verifies the no-clamp signal at run scale; (c) the no-clamp proof is `tokens > 8192` only — a server clamp anywhere in (8192, 16000] (e.g., a 12K cap on the legacy alias) passes the probe with finish=length and is detected only by the E2E hours later — the probe proves "exceeds the OLD cap", NOT "honors 16000"; this residual is DOCUMENTED on #1787 with the probe result (the Task 5 E2E gate 2 `llm_truncated ≤ 1` is the binding check for a mid-range clamp). **A clamp at 8192 or a 400 fails the test → the cap-raise approach is dead → escalate to option B/D.** Distinguish failure kinds (cycle 4 — P1-B/P1-C/P1-D): a **calibration failure** (the `n >= 10500` floor assert) is a test-authoring error — adjust the repeat count — do NOT read it as an API rejection; a **missing tokenizer** is NOT a failure anymore (Task 1 Step 0 provisions `tiktoken` dev-only; if neither tokenizer is importable the pessimistic 2 chars/token bound clears 8192 and the live `tokens > 8192` assert carries the verdict — cycle 4, P2-E); a **clamp/400** failure is the real go/no-go signal; a **short-but-valid-JSON echo with finish=stop** (the response parses but elides/shortens the payload — the P1-B echo-fidelity assert fires) is a MODEL-BEHAVIOR/AUTHORING failure — re-run with repeat adjustment or investigate the served model's output preference — NOT a clamp signal (cycle 5, P1-B); a **timeout/hang** (the call exceeded the explicit 800s `_complete` deadline — R6 P2: the probe runs deadline_s=800, the scaled default's math; the taxonomy below was stale at 600), or the adapter's 60s read timeout was defeated mid-generation per pilot #1549 — the reval's 4/6,720 timeouts show the exposure) is an OPERATOR/NETWORK error — retry or re-run the probe — NOT a go/no-go signal unless it accompanies a 400/clamp (cycle 4, P1-D). Record the calibrator + token count in the run output (P1-C: the tokenizer used is printed by the test).

**Step 3: Also probe `deepseek-v4-flash` + non-thinking** (the migration-safe variant). **Expected behavior per the adapter docstring (pilot #1549): api.deepseek.com's `deepseek-v4-flash` reasons by default and collapses to empty output (1500/1500 reasoning tokens, finish=length, ZERO content) — that is exactly why the direct lane wires `deepseek-chat`. This probe documents the collapse as a baseline and asserts the explicit non-thinking toggle works if the companion adapter migration is included; otherwise it must xfail:**

```python
@pytest.mark.live  # re-review P1 — same live-marker discipline as the alias probe
def test_probe_v4_flash_non_thinking():
    import os
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("no DEEPSEEK_API_KEY")
    from tortoise.model_adapters import DeepSeekDirectModel
    from tortoise import extractor_v2 as v2  # FINAL-VERIFICATION P3 — _complete deadline routing
    m = DeepSeekDirectModel("deepseek-v4-flash", max_tokens=None, temperature=0.0)
    # Documented (pilot #1549): v4-flash without the non-thinking toggle
    # collapses — S1 returns empty, S2/S4 never run. Assert the DOCUMENTED
    # behavior (empty + finish=length) until the companion adapter-migration
    # issue lands {"thinking": {"type": "disabled"}} in the body.
    try:
        # FINAL-VERIFICATION P3: route through _complete's deadline machinery
        # (deadline_s=800) like the alias probe — the old direct m.complete()
        # had NO deadline, exposing the same stalled-chunked-response hang the
        # plan's own P1-D identified for the sibling probe. The call is tiny
        # (a ~10-token reply) so the deadline only trips on a genuinely wedged
        # backend; a 400/unknown-model still surfaces as a normal exception.
        resp = v2._complete(m, system="Reply ok.",
                            user="JSON: {\"ok\": true}", max_tokens=16000,
                            deadline_s=800)
    except Exception as e:  # P2-G (cycle 4): 400/unknown-model on this wire
        # id is itself DOCUMENTED, not a hard failure — `deepseek-v4-flash`
        # exists in the OpenRouter registry but the DIRECT API may not serve
        # it; a 400/unknown-model here xfails with the reason recorded on
        # #1787. Any OTHER exception (network/auth/5xx) is a genuine
        # unexpected error → hard FAIL (fail-closed).
        status = getattr(e, 'response', None) and getattr(e.response, 'status_code', None)
        if status == 400 or 'model' in str(e).lower() and 'unknown' in str(e).lower():
            pytest.xfail("direct API does not serve deepseek-v4-flash (400 "
                         "unknown-model on this wire id) — recorded on #1787 "
                         "(P2-G); re-enable after the companion adapter "
                         "migration (#1790) or when the direct API serves it")
        raise  # genuine unexpected error — hard FAIL
    if resp == "":
        # documented collapse — assert the SIGNATURE (finish=length: all
        # tokens spent reasoning, zero content), not just emptiness (the old
        # `resp == "" or resp` was a tautology — truthy for ANY string — and
        # verified nothing).
        assert m.last_finish_reason == "length", (
            f"v4-flash returned empty WITHOUT finish=length "
            f"({m.last_finish_reason!r}) — not the documented collapse; "
            f"update after adapter migration")
        pytest.xfail("v4-flash reasons by default (pilot #1549) — pending "
                     "companion adapter-migration (thinking toggle)")
    # non-empty path — do NOT over-assume "collapse resolved" from any truthy
    # string: assert the response is real JSON (the user prompt demanded a
    # JSON value), and record the captured request body so the toggle
    # transmission can be asserted once the companion adapter-migration lands
    # ({"thinking": {"type": "disabled"}} must be in the body, not merely
    # non-empty output).
    import json
    assert isinstance(resp, str) and resp
    json.loads(resp)  # must parse — a non-JSON/partial blob FAILS the probe
    # (optionally assert json.loads(resp) == {"ok": True}); keep the test
    # XFAILing otherwise until the companion migration lands.
```

**Step 4: Run it**

Run: `uv run pytest tests/test_extractor_reliability.py::test_probe_v4_flash_non_thinking -v`
Expected (cycle 4 — P2-G, THREE cases): **XFAIL** — either (a) the documented collapse (empty + finish=length — pilot #1549, pending the companion thinking-toggle migration), or (b) a **400/unknown-model on this wire id** (the direct API does not serve `deepseek-v4-flash` — documented on #1787; a hard FAIL is reserved for genuine unexpected errors only) — or **PASS** if the companion toggle is already in. Record the result + which case in the commit message.

**Step 5: Record the probe outcome in the issue** (comment on #1787): accepted-on-alias / accepted-only-on-v4 / clamped. Escalation paths — **reconciled with Open owner decision 2 (P2-4):**
- **accepted-on-alias** (both `deepseek-chat` and `deepseek-v4-flash` accept `max_tokens=16000`): Task 2 proceeds with the default change; the alias migration (#1790) stays strictly AFTER Task 5 per decision 2 (cap-only effect measured on the same wire id).
- **accepted-only-on-v4** (the `deepseek-chat` alias clamps at ≤8K while `deepseek-v4-flash` accepts 16000): the alias cannot carry the 16K cap → the companion adapter-migration lands FIRST; decision 2's re-derivation clause applies (re-baseline the E2E on the new wire id, or explicitly accept the confound and record it on #1787 before gating). Task 2's default change proceeds on the migrated wire id.
- **clamped** (BOTH wire ids clamp at ≤8K): the cap-raise approach is dead → option B/D fallback (Task 1 Step 2); Task 2 does not proceed.
Also record the **OpenRouter path** (the SDK's hosted-capture default is `RoutingModel` → OpenRouter `deepseek/deepseek-v4-flash` fallback, sdk.py:2301): if `TORTOISE_EXTRACTOR_PROVIDER=openrouter` is ever used, run the same >8K probe against `OpenRouterModel('deepseek/deepseek-v4-flash')` — documented as an accepted assumption (OpenRouter passes max_tokens through per its provider config) until probed.

**Step 6: Commit**

```bash
git add tests/test_extractor_reliability.py pyproject.toml uv.lock
# pyproject.toml + uv.lock = Task 1 Step 0's dev-only tiktoken provision (P1-B)
git commit -m "test(extractor): probe max_tokens=16000 acceptance on deepseek-chat + v4-flash + dev tiktoken — #1787"
```

---

### Task 2: Raise `_S2_S4_MAX_TOKENS` 8000 → 16000

**Intent:** The core fix — remove the stale cap so dense sessions emit the complete embed list.
**Acceptance:** Default S2/S4 cap is 16000; env override still wins; a fixture whose S2 output exceeds 8000 tokens completes fully (no `partial_parse`).

**Files:**
- Modify: `tortoise/extractor_v2.py:3751`
- Test: `tests/test_extractor_v2.py`

> **Precondition (operator discipline — do not skip):** the Task 1 probe
> outcome MUST be recorded on #1787 (Task 1 Step 5) before this task changes
> the constant. The probe is the plan's fail-closed go/no-go gate; a
> forgotten/skipped probe silently skips the gate. Verify the #1787 comment
> exists (accepted-on-alias / accepted-only-on-v4 / clamped) before
> committing the constant change.

**Step 1: Write the failing test** (cap-change test per the issue's verification checklist). The mock is CAP-AWARE — it returns a truncated JSON when the cap is ≤8000 and the full list when >8000 — so the test genuinely exercises the failure mode: it must pass at 16000 (no `partial_parse`, full list) AND prove the ladder backstop still fires at the old cap:

```python
def test_s2_s4_cap_raised_to_16k_completes_dense_list(monkeypatch):
    """#1787 — a dense-session embed list that overflows the old 8000 cap
    must complete in full at the 16000 default: no partial_parse, no tail
    loss, and every emitted point lands in the payload. The mock is
    cap-aware: <=8000 → truncated JSON (finish=length), >8000 → full list."""
    import json
    from tortoise import extractor_v2 as v2
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    dense_points = [
        {"content": f"durable claim number {i} with a verbatim quote "
                    f"\"{'word ' * 40}\" and search_keys [\"k{i}\", \"k{i}b\"]",
         "pointKind": "statement", "about_entities": [f"entity-{i}"],
         "quote": f"quote {i}: " + ("lorem ipsum dolor " * 25),
         "search_keys": [f"k{i}", f"k{i}b"], "tier": "B",
         "slots": {"subject": [{"name": f"entity-{i}", "kind": "core:thing",
                                "confidence": 0.9}]}}
        for i in range(45)
    ]
    full = json.dumps({"entities": [{"name": f"entity-{i}", "kind": "core:thing",
                                     "lifecycle": "created", "supersedes": None}
                                    for i in range(45)],
                       "points": dense_points,
                       "events": [], "operators": [], "chain_notes": [],
                       "link_before_create": []})
    # P2-Q (cycle 4) + R4 P1-2: calibration asserts — the fixture must BOTH
    # overflow 8K under real tokenization (or the test proves plumbing on a
    # sub-8K fixture) AND FIT the 16000 default (a fixture a real model would
    # still truncate at 16K proves nothing about "completes in full at the
    # 16000 default"). The CapAwareModel keys truncation on the cap VALUE, so
    # the fixture's real size is never otherwise verified. 45 points = 47,577
    # bytes → ≥ 11.9K tokens at the pessimistic 4 chars/token bound (clears
    # 8192) and ≤ 13.6K at the realistic 3.5 chars/token packing (fits 16K) —
    # inside [8K, 16K], exactly the observed reval list band:
    assert len(full.encode("utf-8")) // 4 >= 8192, \
        "fixture too small: the 45-point dense list must exceed 8K tokens " \
        "(raise point count / quote length until the 4 chars/token bound clears)"
    assert len(full.encode("utf-8")) // 3.5 <= 14000, \
        "fixture too dense: the 45-point list must FIT the 16000 default " \
        "(~3.5 chars/token packing; shrink point count / quote length until " \
        "the upper bound clears — the observed reval lists are 8-16K, which " \
        "is what 16K claims to cover)"
    # the truncated form: cut GENUINELY mid-points-list, inside the points
    # array at an item boundary after point k (the old-cap failure mode).
    # NOTE: cutting before the LAST KEY ("link_before_create") keeps ALL 45
    # points — R6 P2: comment corrected from 60 to match the 45-point fixture
    # points — nothing is dropped, so the partial-accept never fires and
    # `len(out2["points"]) < 45` can never hold. The cut must land INSIDE
    # the serialized points array, leaving the array + outer object
    # UNTERMINATED (rung-3 repair's `+ "}"` closers then cannot produce
    # valid JSON, so rung 4 `_longest_valid_prefix` must recover the head).
    k = 40
    points_json = json.dumps(dense_points)
    depth, closed = 0, 0
    boundary = len(points_json)
    for i, ch in enumerate(points_json):  # walk to the k-th point's close
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                closed += 1
                if closed == k:
                    boundary = i + 1
                    break
    truncated = full[:full.index('"points":') + len('"points":')] \
        + points_json[:boundary]

    class CapAwareModel:
        def __init__(self):
            self.captured = []
            self.last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            self.captured.append(max_tokens)
            self.last_finish_reason = "length" if max_tokens and max_tokens <= 8000 \
                else "stop"
            return truncated if max_tokens and max_tokens <= 8000 else full

    m = CapAwareModel()
    stats = {"llm": {}}
    out = v2.run_s2(m, story="a very dense session story " * 200, stats=stats)
    assert v2._S2_S4_MAX_TOKENS > 8000              # the raise happened
    assert m.captured[-1] == v2._S2_S4_MAX_TOKENS   # the default cap is used
    assert len(out["points"]) == 45                 # full list, no tail loss (45-point fixture — R5 P1-1)
    # clean at 16K: run_s2 returns the RAW parsed embed dict — there is NO
    # `error_census` key on it (out["error_census"] would raise KeyError).
    # The partial_parse census bump lives in the S2/S4 stage callers
    # (extractor_v2.py:3485-3488 / 3546-3549), keyed on stats["partial"] —
    # assert the same signal the callers check:
    assert stats.get("partial") is not True          # clean at 16K
    # (the census-class assertion at the stage-caller level is covered by the
    # PRE-EXISTING test at tests/test_extractor_v2.py:492 —
    # out["error_census"]["partial_parse"] == 1 through extract_session_v2,
    # cap-agnostic mock — AND, MANDATORY per P2-12, the new-cap mirror
    # test_s2_s4_census_clean_at_16k_through_session below: the SAME
    # assertion at the NEW 16000 cap through extract_session_v2, where the
    # partial_parse bump actually lives.)
    # backstop proof: force the OLD cap through _complete_parsed directly —
    # the ladder rung-4 partial-accept must recover the truncated head.
    m2 = CapAwareModel()
    stats2 = {"llm": {}}
    out2 = v2._complete_parsed(m2, "sys", "usr", max_tokens=8000,
                               stats=stats2)
    assert len(out2["points"]) == k < 45   # exactly the first k points
    assert stats2.get("partial") is True   # the partial-accept fired


def test_s2_s4_census_clean_at_16k_through_session(monkeypatch):
    """#1787 P2-12 — MANDATORY mirror of tests/test_extractor_v2.py:492 at
    the NEW cap: a dense session through extract_session_v2 with a cap-aware
    mock at the 16000 default must produce NO partial_parse bump
    (error_census["partial_parse"] absent/0) — the census-at-new-cap claim is
    asserted where the bump actually lives (the S2/S4 stage callers), not
    left optional on the pre-existing cap-agnostic test."""
    import json
    from tortoise import extractor_v2 as v2
    # P2-K (cycle 5): `_conv` lives in tests/test_extractor_reliability.py
    # (imported function-locally at test_extractor_v2.py:465 for the existing
    # census test) — the new test must import it too, or pasting this snippet
    # verbatim raises NameError.
    from tests.test_extractor_reliability import _conv
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)

    class CapAwareSessionModel:
        def __init__(self):
            self.calls = 0
            self.last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            self.last_finish_reason = ("length"
                                       if max_tokens and max_tokens <= 8000
                                       else "stop")
            if "GAP REVIEWER" in system:
                # S4: dense re-emit — full at 16000, truncated at <=8000
                pts = [{"content": f"gap point {i} " + "word " * 40,
                        "pointKind": "statement"} for i in range(40)]
                if max_tokens and max_tokens <= 8000:
                    # re-review R3 (P1): the old [:200] cut landed MID-point-1
                    # (its content is ~250 chars) — rung 4 _longest_valid_prefix
                    # needs an ITEM BOUNDARY with a non-empty embed section, else
                    # _ParseError.truncated → census class truncated_parse_error
                    # (NOT partial_parse) and the backstop assertion
                    # error_census["partial_parse"] >= 1 raises KeyError — the
                    # same zero-complete-item bug R2 fixed in the threaded test.
                    # Cut at the first point's closing brace (item boundary):
                    pts_json = json.dumps(pts)
                    boundary = pts_json.index('}') + 1
                    return ('{"entities": [], "events": [], "operators": [], '
                            '"points": ' + pts_json[:boundary])
                return json.dumps({"entities": [], "events": [],
                                   "operators": [], "points": pts,
                                   "link_before_create": []})
            if "STORY SUMMARIZER" in system:
                return "A narrative."
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": [{"content": "s2 base", '
                    '"pointKind": "statement"}]}')

    out = v2.extract_session_v2(CapAwareSessionModel(), _conv())
    assert out["error_census"].get("partial_parse", 0) == 0  # clean at 16K
    # the old-cap path through the SAME callers still bumps the census
    # (cycle 4 — P2-A): `extract_session_v2` has NO `stage_cap_override`
    # kwarg (verified at extractor_v2.py:3350), so the cycle-3 guard
    # `"stage_cap_override" in co_varnames else None` made out_old ALWAYS
    # None — the MANDATORY old-cap assertion never ran (silent no-op). Force
    # the old cap through the real seam the callers use — `_stage_cap` is
    # read at call time by the S2/S4 callers (extractor_v2.py:1073/1475), so
    # monkeypatching it to 8000 for this session call exercises the genuine
    # truncation → partial_parse path:
    monkeypatch.setattr(v2, "_stage_cap", lambda default: 8000)
    out_old = v2.extract_session_v2(CapAwareSessionModel(), _conv())
    assert out_old["error_census"]["partial_parse"] >= 1  # backstop intact
```

```python
def test_s4_dense_emit_completes_at_16k(monkeypatch):
    """#1787 P1-C (cycle 5) — the S4 re-emit surface (output ≈ 2× S2 — the
    DOMINANT truncation source per §1.3) must be exercised by a genuinely
    dense S4 output at the NEW cap: the cap-change test drives only run_s2,
    the census test's S4 branch is tiny (40 small gap points), and P2-Q's
    ≥8K calibration assert exists only on the S2 fixture. Drive a dense S4
    fixture (same ≥8K calibration discipline as P2-Q) through the S4 caller /
    _complete_parsed path at the 16000 default — full list, no partial_parse
    — then force the OLD cap on the SAME fixture to prove the S4
    partial-accept backstop still fires."""
    import json
    from tortoise import extractor_v2 as v2
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    dense_pts = [
        {"content": f"s4 re-emit point {i} " + ("lorem ipsum dolor " * 30),
         "pointKind": "statement", "about_entities": [f"entity-{i}"],
         "quote": f"quote {i}: " + ("word " * 40),
         "search_keys": [f"k{i}"], "tier": "B",
         "slots": {"subject": [{"name": f"entity-{i}", "kind": "core:thing",
                                "confidence": 0.9}]}}
        for i in range(45)
    ]
    full = json.dumps({"entities": [{"name": f"entity-{i}", "kind": "core:thing",
                                     "lifecycle": "created", "supersedes": None}
                                    for i in range(45)],
                       "points": dense_pts, "events": [], "operators": [],
                       "chain_notes": [], "link_before_create": []})
    # P2-Q calibration assert (same discipline as the S2 fixture): the S4
    # re-emit tax means dense sessions' S4 output EXCEEDS S2 size — a
    # sub-8K S4 fixture would prove nothing at the dominant surface. R4
    # P1-2: BOTH bounds — 45 points = 48,327 bytes → ≥ 12.1K tokens at 4
    # chars/token (clears 8192) and ≤ 13.8K at 3.5 (fits 16K):
    assert len(full.encode("utf-8")) // 4 >= 8192, \
        "S4 fixture too small: the 45-point dense re-emit list must exceed " \
        "8K tokens (the S4 re-emit tax surface; raise point count / quote " \
        "length until the 4 chars/token bound clears)"
    assert len(full.encode("utf-8")) // 3.5 <= 14000, \
        "S4 fixture too dense: must FIT the 16000 default (shrink until the " \
        "upper bound clears)"

    class S4CapAwareModel:
        def __init__(self):
            self.last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            self.last_finish_reason = ("length" if max_tokens and max_tokens <= 8000
                                       else "stop")
            if max_tokens and max_tokens <= 8000:
                # old-cap failure mode: cut mid-points-array (rung-4
                # partial-accept recovers the head). R6 P1-1: the old
                # `rindex('"}', ...)` never matched — the S4 points' last key
                # is `"slots"` (a NUMBER, never a string-before-close), so
                # `"}` occurs 0 times and rindex raised ValueError inside the
                # mock (re-raised by _call_once → the backstop leg errored).
                # Use the S2 test's depth-walk: cut after the k-th point's
                # closing brace (k=20 of 45), leaving the array unterminated
                # so rung-4 recovers the head with partial=True:
                pts_json = json.dumps(dense_pts)
                depth, closed, boundary = 0, 0, len(pts_json)
                for i, ch in enumerate(pts_json):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            closed += 1
                            if closed == 20:
                                boundary = i + 1
                                break
                return ('{"entities": [], "events": [], "operators": [], '
                        '"points": ' + pts_json[:boundary])
            return full

    m = S4CapAwareModel()
    stats = {"llm": {}}
    # _complete_parsed is the seam the S4 caller uses (extractor_v2.py:959 /
    # stage callers 3546-3549) — drive the dense S4 emit at the 16000 default:
    out = v2._complete_parsed(m, "sys", "usr", max_tokens=16000, stats=stats)
    assert len(out["points"]) == 45           # full S4 list, no tail loss (45-point fixture — R5 P1-1)
    assert stats.get("partial") is not True   # clean at 16K — no partial_parse
    # Backstop proof on the SAME fixture: force the old 8000 cap → the S4
    # partial-accept must still fire (truncated head recovered, partial=True).
    m2 = S4CapAwareModel()
    stats2 = {"llm": {}}
    out2 = v2._complete_parsed(m2, "sys", "usr", max_tokens=8000, stats=stats2)
    assert len(out2["points"]) < 45
    assert stats2.get("partial") is True      # S4 backstop intact at old cap
```

> **Implementer note:** `run_s2`/`_complete_parsed` return the raw parsed embed dict — there is NO `error_census` key on the return value (the `partial_parse` census bump lives in the S2/S4 stage callers, extractor_v2.py:3485-3488 / 3546-3549, keyed on `stage_stats["partial"]`). The test therefore asserts on the `stats` dict it passes in (`stats.get("partial") is not True` — the exact signal the callers key off); the census-class assertion at the stage-caller level is covered by the PRE-EXISTING test at tests/test_extractor_v2.py:492 (`out["error_census"]["partial_parse"] == 1` through `extract_session_v2`, cap-agnostic mock) **plus the MANDATORY new-cap mirror `test_s2_s4_census_clean_at_16k_through_session` added in this step (P2-12 — assert `error_census["partial_parse"]` absent/0 at the new 16000 default through `extract_session_v2`, where the census bump actually lives; the plan does NOT rely on the pre-existing cap-agnostic test alone)**. The acceptance: **at the new default the dense fixture completes clean (full list, `stats["partial"]` never set); the ladder partial-accept still triggers for an output that overflows whatever cap is in force** (assert the partial list has exactly the recovered k points + `stats["partial"] is True` at a forced 8000 cap).

**Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/test_extractor_v2.py::test_s2_s4_cap_raised_to_16k_completes_dense_list -v`
Expected: FAIL at `v2._S2_S4_MAX_TOKENS > 8000` (still 8000).

**Step 3: Change the constant**

```python
# Bounded generations (D2): stage caps — S1 narrative is small (1500); the
# S2/S4 embed JSON must clear the 4000-token truncation floor. #1787: the
# 8000 default was set in M3 (#1524) against the V3-era 8K model ceiling;
# deepseek-v4 models allow 384K max output, and 15/6720 reval calls hit
# the old cap (silent tail-entity loss via partial-accept) — raise the
# default to 16000 (still ≪ 384K ceiling; env override remains the
# mechanical lever). The single env override TORTOISE_EXTRACTOR_MAX_TOKENS
# (int, read at call time) raises BOTH stages without a code change — the
# retry-then-fix protocol's mechanical lever (D6: ``transient_timeout``
# spike → raise the cap).
_S1_MAX_TOKENS = 1500
_S2_S4_MAX_TOKENS = 16000
```

**Step 4: Run the test — expect PASS**

Run: `uv run pytest tests/test_extractor_v2.py::test_s2_s4_cap_raised_to_16k_completes_dense_list -v`
Expected: PASS.

**Step 5: Update the hard-coded cap pin + add the override-above-default pin in the reliability suite** — `tests/test_extractor_reliability.py::test_complete_passes_stage_cap` (line ~177) asserts `m.captured == [1500, 8000, 8000]` and **will fail after the constant change**. Update it to `[1500, 16000, 16000]` and the module doc comment (line 7: "S1 1500 / S2,S4 8000") to 16000. Also update the two stale 8000 mirrors: `tools/longmem_eval/probe_json_mode.py:57` (`_MAX_TOKENS = 8000` — the truncation-signal diagnostic; **update to 16000 — its documented purpose is "mirror the S2/S4 stage cap (truncation is part of the signal)", so it must track the ACTIVE cap to measure truncation at the real ceiling; leaving it at 8000 would probe a cap that no longer exists**) and the `sdk.py:2301` doc comment ("S2,S4 → 8000 tokens per stage" → 16000). In the same file, add the env-override pin (folded from former Task 3, deleted in cycle 3 — P2-2; it is independent of the constant change: `_stage_cap` reads the env at call time and wins for ANY default, so it must pass before and after the raise):

```python
def test_stage_cap_override_above_default(monkeypatch):
    from tortoise import extractor_v2 as v2
    monkeypatch.setenv("TORTOISE_EXTRACTOR_MAX_TOKENS", "24000")
    assert v2._stage_cap(16000) == 24000

def test_stage_cap_invalid_override_warns_and_defaults(monkeypatch):
    """#1787 P1-F (cycle 5) — an INVALID TORTOISE_EXTRACTOR_MAX_TOKENS
    override ("abc") warns (pytest.warns) and falls back to the stage
    default — never a crash, never a silent garbage cap (_stage_cap's
    fail-open-with-visibility contract). This regression surface is claimed
    by the Integration Surface Map + Step 8 item 1 but had NO test (grep:
    only valid-value paths existed) — MANDATORY now."""
    from tortoise import extractor_v2 as v2
    monkeypatch.setenv("TORTOISE_EXTRACTOR_MAX_TOKENS", "abc")
    with pytest.warns(UserWarning):
        assert v2._stage_cap(16000) == 16000  # falls back to the default
```

Also add the threaded cap-kwarg test (P2-12 — the E2E runs `_stage_cap` + cap-kwarg assembly inside 8 worker threads, but all Task 2 unit tests are single-threaded; the ≤1 gates would tolerate one mis-capped worker, so the per-call path needs a thread-level test):

```python
def test_stage_cap_thread_safety_mixed_env(monkeypatch):
    """#1787 P2-12 — threaded: 8 threads × N calls through
    _complete_parsed/_stage_cap with MIXED caps (env-override phase, then a
    mixed stage-default phase). Every call receives its own correct cap and
    its own stats dict — per-call `partial` flags never cross-contaminate
    (an 8000-cap thread's partial=True must not bleed into a clean thread)."""
    import threading
    from tortoise import extractor_v2 as v2

    captured, lock = [], threading.Lock()

    class FakeModel:
        def __init__(self):
            self.last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            with lock:
                captured.append(max_tokens)
            # finish_reason keys off the PASSED cap, so a thread that received
            # the wrong cap is observable as a wrong finish/partial signal.
            self.last_finish_reason = ("length" if max_tokens and max_tokens <= 8000
                                       else "stop")
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": []}' if self.last_finish_reason == "stop"
                    # P2 (resume cycle): the old truncated branch returned
                    # '{"entities": [], "points": [' — ZERO complete embed
                    # items — so ladder rung 4 _longest_valid_prefix falls
                    # through (it requires ≥1 non-empty embed section,
                    # extractor_v2.py:4362-4370) and _complete_parsed RAISES
                    # _ParseError; the 8000-cap worker's partial-flag assert
                    # never executed (thread dies silently) and the test
                    # passed on capture-only asserts — a silent no-op of the
                    # exact per-call partial-isolation behavior it claims to
                    # verify. The truncated branch now carries ONE complete
                    # point + an unterminated second so rung-4 partial-accepts
                    # and stats["partial"] is actually asserted.
                    else '{"entities": [], "points": [{"content": "p", '
                         '"pointKind": "statement"}, {"content": "q"')

    def worker(default_arg, expected):
        for _ in range(25):
            stats = {"llm": {}}
            cap = v2._stage_cap(default_arg)
            assert cap == expected, f"wrong cap {cap} (expected {expected})"
            v2._complete_parsed(FakeModel(), "sys", "usr", max_tokens=cap, stats=stats)
            # per-call stats isolation: an 8000-cap thread partial-accepts
            # (backstop), a clean thread must never see that partial flag.
            assert (stats.get("partial") is True) == (expected == 8000)

    # Phase 1 — env override wins for EVERY thread (env constant → race-free):
    monkeypatch.setenv("TORTOISE_EXTRACTOR_MAX_TOKENS", "24000")
    ts = [threading.Thread(target=worker, args=(16000, 24000)) for _ in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert len(captured) == 8 * 25 and all(c == 24000 for c in captured)

    # Phase 2 — mixed stage-defaults with no env (4 threads → 16000, 4 → 8000):
    captured.clear()
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    ts = [threading.Thread(target=worker,
                           args=((16000, 16000) if i % 2 == 0 else (8000, 8000)))
          for i in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert len(captured) == 8 * 25
    assert set(captured) == {16000, 8000}  # no stray/mis-derived cap
```

**Step 6: Scale `_complete`'s deadline with `max_tokens`, then run the full extractor + reliability suites (regression)**

Part A — **deadline-vs-16K (P2-10 + P2-K, DECIDED here):** `_complete`'s default `deadline_s=600` (extractor_v2.py:4007) — a worst-case 16K emission at the conservative 25 tok/s ≈ 640s EXCEEDS the deadline (8K ≈ 320s cleared it). The reval's 4 `transient_timeout` failures happened at 8K; at 16K the longest calls can newly breach the deadline → timeout retries → dropped questions. **Probe deadline ownership (R4 P2-3 + R5 P2-4 + R6 P2 — after the sentinel change lands, EDIT `test_probe_max_tokens_above_8k` to pass `deadline_s=None` instead of the explicit 800):** the probe's explicit `deadline_s=800` (0.05 × 16000 — the scaled default's math, set in Task 1 to be Task-2-independent; R6 P2 corrects the stale 600 references in this ownership note) bypasses the sentinel default; the edit is made in THIS step (one line in Task 1 Step 1's test) and enumerated in Step 8 (g) so the byte-identity gate does not flag it as unexplained drift. **Throughput assumption (cycle 3 — P2-9, restated cycle 4 — P2-K; parenthetical corrected cycle 5 — P2-H):** the deadline math uses a CONSERVATIVE **25 tok/s (0.04 s/token — the OLD multiplier)** design point (16K ≈ 640s emission); the multiplier was RAISED **0.04 → 0.05 s/token** in cycle 4 (16K → 800s), which covers down to ~20 tok/s — **0.05 s/token = 20 tok/s, NOT 25 tok/s** (the cycle-4 parenthetical conflated the new multiplier with the old throughput label; the 25 tok/s assumption and the 0.05 s/token multiplier are separate facts) — deliberately 2-4× slower than the observed 50-100 tok/s (pilot #1549) used for the §1.4 latency-delta estimates — so the deadline only trips on a genuinely wedged call, never on a slow-but-progressing straggler; the divergence is intentional (worst-case margin) and stated once here. **Chosen fix (owned in this step): `_complete` scales `deadline_s` with `max_tokens`** so the worst-case 16K emission clears the deadline. Implement a `_scaled_deadline(base, max_tokens) = max(base, int(0.05 * (max_tokens or 0)))` helper — **the multiplier is 0.05 (cycle 4 — P2-K): the cycle-3 0.04 multiplier put the scaled deadline EXACTLY at the 25 tok/s emission time (640s) — zero margin at the assumption point — and a ~20-24 tok/s straggler was killed where the old 8K/600s would have completed; 0.05 → 800s at 16K restores a 25% margin at 25 tok/s and covers down to ~20 tok/s. The `(max_tokens or 0)` None-guard is REQUIRED (cycle 3 — P2-9: `0.05 * None` would TypeError; callers pass `max_tokens=None` when no cap applies)** — or inline the same in `_complete`; an explicit `deadline_s` argument from callers still wins. **Signature change specified (resume-cycle P2):** `_complete`'s current signature is `deadline_s: int = 600` (extractor_v2.py:4007) — a naive inlined `deadline_s = max(deadline_s, int(0.05 * max_tokens))` would break explicit-deadline callers (the existing `test_complete_deadline_aborts_attempt` passes deadline_s=0.05, and the plan's own `test_complete_wires_scaled_deadline` requires the explicit 0.05 to win). Change the sentinel to `deadline_s: int | None = None` and compute `effective = _scaled_deadline(600, max_tokens)` ONLY when `deadline_s is None` — never via `max()` over an explicit value. Add the unit tests:

```python
def test_complete_deadline_scales_with_max_tokens():
    """#1787 P2-10 — _complete's effective deadline must clear a worst-case
    16K emission (~640s at the conservative 25 tok/s), not stay at 600s.
    (Cycle 3 — P2-9: `_scaled_deadline(600, None)` must NOT TypeError — the
    `(max_tokens or 0)` guard; the old `_scaled_deadline(600, 8000) >= 320`
    assertion was dropped as vacuous — the 600 floor dominated it. Cycle 4 —
    P2-K: the multiplier is 0.05, so the scaled 16K deadline is 800s.)"""
    from tortoise import extractor_v2 as v2
    assert v2._scaled_deadline(600, None) == 600          # None-guarded
    assert v2._scaled_deadline(600, 16000) >= 800         # clears 16K emission w/ margin
    assert v2._scaled_deadline(0, 16000) == 800           # scaling ENGAGED
    # (not the 600 floor — the old 8000 case was trivially true)

def test_complete_wires_scaled_deadline(monkeypatch):
    """#1787 P1-8 — _complete must WIRE the scaled default into the deadline
    it passes to _call_once (not just expose the helper): max_tokens=16000
    runs with an effective deadline >= 800s (P2-K), an explicit deadline_s
    still wins, and the S1 fast path (1500) keeps 600s. (Mirrors
    test_complete_deadline_aborts_attempt's explicit-deadline branch — that
    test keeps proving deadline_s=0.05 aborts a slow call end-to-end.)"""
    from tortoise import extractor_v2 as v2
    seen = {}

    class _Rec:
        last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            return '{"ok": true}'

    def fake_call_once(model, system, user, *, deadline_s, max_tokens, stats):
        seen["deadline_s"], seen["max_tokens"] = deadline_s, max_tokens
        return ("ok", model.last_finish_reason)

    monkeypatch.setattr(v2, "_call_once", fake_call_once)
    assert v2._complete(_Rec(), "s", "u", max_tokens=16000) == "ok"
    assert seen["deadline_s"] >= 800        # scaled default wired into the call
    v2._complete(_Rec(), "s", "u", max_tokens=16000, deadline_s=0.05)
    assert seen["deadline_s"] == 0.05       # explicit arg still wins
    v2._complete(_Rec(), "s", "u", max_tokens=1500)
    assert seen["deadline_s"] == 600        # S1 fast path unchanged

def test_complete_total_budget_exhausted_on_deadline(monkeypatch):
    """#1787 P2-K(a) — when EVERY attempt hits the scaled deadline, _complete
    consumes the FULL retry budget (attempts × deadline_s — the documented
    total worst-case wall clock) and raises transient_timeout; it never
    hangs and never returns a partial. The abandoned daemon thread after a
    deadline kill keeps running and billing (documented in _complete's
    docstring) — the Task 5 Step 0 `deadline_aborted` counter + gate bound
    that loss (P2-L)."""
    from tortoise import extractor_v2 as v2
    calls, stats = [], {"llm": {}}

    class _Rec:
        last_finish_reason = "stop"
        def complete(self, *, system, user, max_tokens=None):
            return '{"ok": true}'

    def fake_call_once(model, system, user, *, deadline_s, max_tokens, stats):
        calls.append(deadline_s)
        raise TimeoutError(f"model call exceeded {deadline_s}s")  # always times out

    monkeypatch.setattr(v2, "_call_once", fake_call_once)
    with pytest.raises(TimeoutError):
        v2._complete(_Rec(), "s", "u", max_tokens=16000, retries=2, stats=stats)
    assert len(calls) == 3  # retries+1 attempts — total budget consumed
    assert all(c == v2._scaled_deadline(600, 16000) for c in calls)  # scaled per attempt
    assert stats["last_class"] == "transient_timeout"  # classifiable, not a hang

def test_complete_deadline_margin_at_throughput_boundary():
    """#1787 P2-K(b) — boundary at the conservative throughput assumption:
    the scaled 16K deadline (800s = 0.05 × 16000) must clear the 25 tok/s
    worst-case emission (640s) with margin AND keep a ~20 tok/s straggler
    (800s) alive. Cycle 4: the old 0.04 multiplier (640s) put the deadline
    EXACTLY at the assumption point — zero margin; a ~20-24 tok/s straggler
    was killed where the old 8K/600s would have completed. 0.05 → 800s
    restores a 25% margin at 25 tok/s and covers down to 20 tok/s. The
    kill-vs-complete behavior itself is the mirror of
    test_complete_deadline_aborts_attempt (slow call killed) — this pins
    the margin arithmetic at the assumption point."""
    from tortoise import extractor_v2 as v2
    assert v2._scaled_deadline(600, 16000) == 800     # 0.05 × 16000
    assert v2._scaled_deadline(600, 16000) >= 640     # 25 tok/s emission (640s) completes
    assert v2._scaled_deadline(600, 16000) >= 800     # ~20 tok/s straggler (800s) completes
```

The E2E gate folds `transient_timeout` into the `n_failed` ceiling (gate 6 — the reval's 4 failures were all `ingest:retries_exhausted` timeouts), so a 16K-driven timeout breach still fails gate 6 (Task 5 Steps 2/4).

Part B — **run the full extractor + reliability suites (regression):**

Run: `uv run pytest tests/test_extractor_v2.py tests/test_extractor_reliability.py -m "not live" -v`
Expected: PASS — all ladder/partial-accept/census tests pass at the new cap; the updated cap pin reflects `[1500, 16000, 16000]`; the backstop (partial-accept at a cap the output overflows) still works; the new deadline-scaling + threaded-cap tests pass. **Live-probe exclusion (resume-cycle P2):** Task 1's two probe tests (`test_probe_max_tokens_above_8k`, `test_probe_v4_flash_non_thinking`) MUST be marked `@pytest.mark.live` so the deterministic unit regression excludes them (`-m "not live"`) — otherwise Part B re-executes the live API probes (~$0.02-0.05 + 3-10 min, and any network/429 flake the plan's own Task 1 Step 2 taxonomy classifies as a non-signal) inside a deterministic gate. Add the live marker in Task 1 Step 1/Step 3 alongside the tests.

**Step 7: Commit**

```bash
git add tortoise/extractor_v2.py tortoise/sdk.py tests/test_extractor_v2.py tests/test_extractor_reliability.py tools/longmem_eval/probe_json_mode.py
git commit -m "fix(extraction): raise S2/S4 output cap 8000→16000 — V4 ceiling is 384K, cap was stale (M3 #1524) — #1787"
```

**Step 8: Verification-only checkpoint (folded from former Task 3 — P2-2/P2-5; the standalone Task 3 was DELETED in cycle 3 — it had no files, no commit, two confirm-only steps, and zero inbound references, so its note is retained here)** — the env-override lever + #1746 ladder behave identically at the new default, confirmed by TWO checks (no new code here):
1. Re-review the Task 2 Step 6 suite output — the `_stage_cap` override tests (valid wins; invalid warns + falls back) and the ladder partial-accept/repair/sanitize tests all ran green at the new default, including `test_stage_cap_override_above_default`, the deadline-scaling tests (incl. the P1-8 wiring test `test_complete_wires_scaled_deadline`), and the threaded cap test.
2. Byte-identity check (cycle 3 — P2-5): the diff window must NOT be `HEAD~1 HEAD` — Task 1 Step 6 committed two probe tests to `tests/test_extractor_reliability.py`, so the HEAD~1 window would include Task 1's additions and false-alarm on them. **Anchor the diff to the pre-Task-1 commit: record `T1_PARENT=$(git rev-parse HEAD)` immediately BEFORE Task 1 Step 6's commit, then check `git diff $T1_PARENT HEAD -- tests/test_extractor_reliability.py`.** Expected added lines (enumerate them, so drift is distinguishable from noise): (a) the two Task 1 probe tests (`test_probe_max_tokens_above_8k`, `test_probe_v4_flash_non_thinking` + the `_probe_filler` calibrator); (b) the cap pin `[1500, 8000, 8000]` → `[1500, 16000, 16000]`; (c) the module docstring 8000 → 16000; (d) `test_stage_cap_override_above_default`; (e) `test_stage_cap_thread_safety_mixed_env` **and `test_stage_cap_invalid_override_warns_and_defaults` (cycle 5 — P1-F — the invalid-override warns+falls-back regression the surface map and Step 8 item 1 claim; previously absent from the suite)**; (f) **the four deadline-scaling tests (cycle 4 — P2-B): `test_complete_deadline_scales_with_max_tokens`, `test_complete_wires_scaled_deadline` (their established home is this exact file — `test_complete_deadline_aborts_attempt` at line ~163), `test_complete_total_budget_exhausted_on_deadline`, `test_complete_deadline_margin_at_throughput_boundary` (P2-K)**; (g) **the probe deadline_s=800 → None edit (R5 P2-4 + R6 P2 — Task 2 Step 6 owns the change: after the sentinel change lands, the Task 1 probe passes `deadline_s=None` (inherits 800s) instead of the explicit 800; the edit is enumerated here so the byte-identity gate does not flag it as unexplained drift) + the `@pytest.mark.live` decorators on both probe tests (re-review P1)**. Everything else (ladder/census/reliability assertions) must be byte-identical — any other diff is drift and must be explained before committing.

No separate commit — the change already shipped in Task 2 Step 7.

---

> **Former Task 3 (DELETED in cycle 3 — P2-2):** the standalone "verify the env override + ladder backstop" task had no files, no commit, two confirm-only steps, and zero inbound references (§1.9 wiring and the Journey Test Map never point at it) — its verification content already lived in Task 2 Step 8, so the task was removed and its note folded there (P2-2). No renumbering: Task 4/5 keep their numbers (all external references point at Task 2 Step 8's checkpoint, not Task 3).

### Task 4: Document the change + file the companion issues

**Intent:** Record the decision (ceiling research, why 16K not 8K/384K) and surface the adjacent findings without absorbing them.
**Acceptance:** Issue #1787 body updated with the probe outcome + cap rationale; the adapter-migration companion issue filed; the #1789 (S4 delta contract) filing/re-scoping happens POST-E2E in Task 5 Step 6.
**Dependency (cycle 3 — P1-6):** Task 4 depends ONLY on Task 1's probe outcome — it does NOT depend on Task 2, so Steps 1-2 below can be **parallelized with Task 2** once Task 1 lands. **The #1789 (S4 delta contract) filing is NOT part of Task 4 anymore** — it moved to **Task 5 Step 6**, because its filing is conditional on Task 5's `stage_stats` (which does not exist until the E2E runs); the old note "depends ONLY on Task 1's probe outcome — parallelizable with Task 2" contradicted Step 2's own conditional, and that contradiction is resolved by the split.

**Step 1: Post the scoping comment on #1787** — the Confirmed Problem, tradeoff matrix (Part 1.4), recommendation + rationale (1.7), and the `### Axis Research` / `### Integration Docs` blocks (Part 1.5). **Also posted here (cycle 4 — P2-C, owned explicitly in this step):** the P2-4 chunk_evidence interpretation note — the issue's literal ≥ 0.738 sits ABOVE the measured baseline (0.7375); a run in [0.7375, 0.738) passes the plan's no-regression gate but NOT the issue's literal target; the `--compare` statistical verdict is PRIMARY and the literal-vs-baseline gap is documented on the issue rather than hidden. **Complexity label sync (FINAL-VERIFICATION P2):** §1.10 re-rates the tier standard → complex, but the issue still carries `complexity:standard` (verified 2026-08-27) and no step updates it — a router reading the label would apply standard-tier gate depth to a complex-tier plan. Run `gh issue edit 1787 --add-label complexity:complex --remove-label complexity:standard` in THIS step (with the scoping comment), or state explicitly that the label stays standard and the complex-tier depth is self-applied.

**Step 2: The adapter-migration companion issue (#1790) — VERIFY-EXISTS, don't duplicate (FINAL-VERIFICATION P1):** #1790 (`fix(model-adapters): migrate off the retired deepseek-chat alias`) is ALREADY FILED and OPEN (created 2026-08-27). The step is therefore: `gh issue view 1790` → confirm OPEN with the correct title/body → link it from #1787 if not already linked. Only if it was closed/dropped, create it with the command below (its LANDING is pinned after Task 5 by Open owner decision 2, but filing is safe at any time):

```bash
gh issue create \
  --title "chore(extraction): migrate extractor off the deepseek-chat legacy alias to deepseek-v4-flash + explicit non-thinking toggle" \
  --body "deepseek-chat retired 2026-07-24 15:59 UTC per api-docs.deepseek.com/news/news260424/ — empirically still serving (reval 2026-08-27) but unsupported. DeepSeekDirectModel('deepseek-chat') → 'deepseek-v4-flash' + {\"thinking\":{\"type\":\"disabled\"}} (v4 defaults to thinking; flash reasoning collapse documented in pilot #1549). Soft dependency for #1787's E2E run."
```

> **#1789 (S4 delta contract) is NOT filed here (cycle 3 — P1-6):** its filing moved to **Task 5 Step 6** — it is conditional on Task 5's `stage_stats`, which does not exist until the E2E runs. Filing it from Task 4 (parallel with Task 2) would run the conditional with no input.

**Step 3: Commit the doc**

```bash
git add docs/plans/2026-08-27-1787-extractor-cap-truncation.md
git commit -m "docs(plans): scoping + plan for #1787 extractor cap truncation"
```

---

### Task 5: E2E — fresh 50-Q re-validation at the new cap

**Intent:** Prove the issue's indicators on the measurement the issue defines: `partial_parse` ≤ 1, `llm_truncated` ≤ 1, accuracy ≥ 0.826, chunk_evidence@20 ≥ 0.7375, cost ≤ +30%.
**Acceptance:** A fresh (non-resumed) 50-Q run on LongMemEval split=s reports the five numbers and meets all five targets.

**Files:**
- Tooling: `tools/longmem_eval/run.py` + `tools/longmem_eval/ingest_v2.py` + `tortoise/extractor_v2.py` + `tortoise/model_adapters.py` — **REQUIRED changes (Step 0): token accumulation at the adapter/call site (lock-protected), `stage_stats` emission (per-stage S2/S4 tallies rolled up at the ingest_v2.py Phase-C per-session seam, run.py:1799 → `outcome["ingest"]` → `build_report`), freshness-marker recording, pre/post-run DB fingerprint, per-call LLM census (`llm_error_census`), run composition (provider/model/wire-id/effective_stage_cap/chunk_turns), the `--qids` subset flag, and `--db-flush` (P1-B, P2 — resume cycle: both were referenced by prose but absent from the enumerated change surface; the verbatim commands failed at argparse)**
- Evidence: `.longmemeval_cache/runs/<key>.report.json`

**Step 0: Harness changes (REQUIRED — the freshness, cost, and stage gates depend on them)**

**Acceptance:** a fresh run's report MUST contain `resumed=false`, `skipped_qids=[]`, `token_usage={completion_tokens, prompt_tokens}` (int values), `stage_stats={s2_truncated, s2_partial, s4_truncated, s4_partial, s2_output_tokens, s4_output_tokens}` (the two output-token keys are SECOND-MODEL-GATE P1 gate-2 additions — the #1789 decision rule consumes them), `db_fingerprint` + `db_fingerprint_post` (pre/post-run counts with `entities` AND `total_nodes` — all node kinds the extractor writes — int-typed; cycle 4 — P2-R), `llm_error_census` (per-call LLM census classes, counting RECOVERED + terminal events; cycle 4 — P1-F) + `deadline_aborted` (cycle 4 — P2-L), `per_qid_written_entities` (failed-qid partial-write provenance; cycle 4 — P1-E), `heartbeat` (mid-run probe record, presence-gated; cycle 5 — P1-D), `composition` (provider/model/wire-id/effective_stage_cap/chunk_turns — **resume-cycle P0-A/P0-B: the two E2E-measurement-critical fields `effective_stage_cap` and `chunk_turns` were added to the composition record**), **`updated_at_utc` (report-build teardown timestamp — re-review P1: the run wall-clock ceiling reads it; the current report format has NO such top-level key, so the ceiling was a silent no-op — now a Step 0 deliverable, presence-gated in the gate script: missing/invalid stamp → exit-1, never a skip)** and the `--qids` subset flag + `--db-flush` + **`TORTOISE_EXTRACTOR_NO_FALLBACK` (re-review R3 P1 — the knob is load-bearing for E2E composition cleanliness but was absent from the acceptance list / commit enumeration / positive tests; now a first-class Step 0 deliverable: acceptance entry, commit message, and a POSITIVE unit test — knob set → `build_extractor_model` constructs a fallback-less RoutingModel and a transient 429 through it retries in `_complete` and NEVER routes to the OpenRouter fallback; the group (g) "absent → warning" case stays as the negative arm)** (resume-cycle P1-A/P1-B — first-class Step 0 deliverables, not prose-only); each is covered by a unit test. The current harness emits NONE of these (verified: the reval report has no such keys), so without this step the Step 2 gate is vacuous.

1. **Freshness marker (P1-F):** record `resumed=false` and `skipped_qids=[]` at report-build time on every fresh run (new checkpoint path per run). A resumed/skipped run records the TRUTH; the Step 2 gate REQUIRES the keys' presence, so a marker-less report FAILS (never a silent None → pass).
2. **Token accumulation (P1-3 + P1-9/P2-11):** in-adapter ACCUMULATOR — `DeepSeekDirectModel.complete` increments `self.total_prompt_tokens` / `self.total_completion_tokens` per call, INCLUDING retried calls. Do NOT sum `last_completion_tokens`/`last_prompt_tokens` at teardown: the adapter OVERWRITES them per call (model_adapters.py:134-135) and the run shares ONE model instance across worker threads, so a teardown sum captures only the final call's values and is nondeterministic under threads. **Thread safety is MANDATORY (cycle 3 — P2-11): `self.total_completion_tokens += n` is LOAD/INPLACE_ADD/STORE — three bytecodes — and loses updates across the 5 worker threads sharing one instance; guard the increments with a `threading.Lock` (or accumulate per-call local totals and sum them under lock).** The harness reads the accumulator at teardown into `report['token_usage']`. **Read path specified (resume-cycle P1 — RoutingModel):** the run's extractor is `RoutingModel(primary=DeepSeekDirectModel, fallback=OpenRouterModel)` (model_adapters.py:341-392) when provider=deepseek-direct — RoutingModel does NOT proxy `total_*` attrs (it surfaces only `last_finish_reason`), so the teardown read MUST target `extractor_model.primary.total_completion_tokens` (or RoutingModel gains a `total_*` proxy). **Failover semantics (resume-cycle P1 + re-review P0 — OPENROUTER_API_KEY MUST stay set in the E2E env because the reval's READER ran on openrouter:deepseek/deepseek-v4-flash):** RoutingModel failover is forward-only and run-wide — the FIRST transient (429/timeout, gates 8/11 allow ≤2) flips `_failed_over` on the shared instance and every subsequent call routes to the OpenRouter fallback (model_adapters.py:469-486). Consequences the plan now addresses: (a) the composition gate would pass on a mixed-wire run — the extractor's fallback is NEUTRALIZED by the new `TORTOISE_EXTRACTOR_NO_FALLBACK=1` knob (Step 0 deliverable — `build_extractor_model`/`resolve_extractor_provider` construct `RoutingModel(primary, fallback=None)` when set, so the pool stays `[deepseek-direct]`; NO env var is taken away from the reader lane, which needs OPENROUTER_API_KEY for its baseline-comparable wire); (b) fallback `OpenRouterModel` calls are never accumulated — with the no-fallback knob this is moot, but a route census (`stats["llm"]["last_route"]` = primary/fallback per call, recorded at the `_call_once` seam — new recording site in extractor_v2.py) is emitted into `llm_error_census["fallback_route_calls"]` and gated == 0 with KEY PRESENCE required (re-review P1 — `need('fallback_route_calls', lec, ...)`, absent → exit-1, mirroring the per_qid fix; the negative enumeration adds the absent-key case); (c) a unit test asserts a transient-failure failover cannot silently pass the composition gate (mixed-wire composition → exit-1) and that fallback-call usage is excluded from `token_usage`. The heartbeat calls `extractor_model.primary` DIRECTLY (re-review P2 — a heartbeat 429 through the shared RoutingModel would flip `_failed_over`; the primary DeepSeekDirectModel retries 429s itself with no failover state — unit-tested: a heartbeat 429 never flips the run's route census). **Heartbeat/probe token-usage exclusion MECHANISM specified (re-review R3 P2 — the old "separate counter" claim was unspecified and the unit test could not pass without one):** heartbeat + pre-flight probe calls run on a DEDICATED adapter instance (`heartbeat_model = DeepSeekDirectModel(...)` — never the shared `extractor_model` whose `total_*` the teardown reads), so their usage never enters `token_usage` BY CONSTRUCTION; the shared-instance accumulator is zeroed at ingest start (after the pre-flight probes complete) so item 3/5 probe usage is likewise excluded. The unit tests assert: heartbeat/probe usage absent from `report['token_usage']` with the dedicated-instance construction.
3. **stage_stats emission (P2-M + P2-6 + SECOND-MODEL-GATE P1 gates 1-2):** the `extract_session_v2` stats hook tallies per-stage S2-vs-S4 `truncated`/`partial` AND **stage-attributed OUTPUT TOKENS (`s2_output_tokens` / `s4_output_tokens`)** and the harness emits them into `report['stage_stats']` — **keyed PER-SESSION and merged atomically (or lock-protected): 5 workers write into the shared structure concurrently, so an unprotected shared tally loses updates or misattributes stages across sessions (cycle 3 — P2-6).** **Per-stage attribution MECHANISM (SECOND-MODEL-GATE round-2 P1 — the token accumulator (item 2) is adapter-GLOBAL, so per-stage attribution is NOT automatic):** at each S2/S4 stage call site (`extractor_v2.py:1073` / `1475`), the harness reads `model.last_completion_tokens` AFTER the call and delta-accumulates into the session's per-stage tally (`stage_usage['s2'] += model.last_completion_tokens - stage_usage['s2_prev']` — or a per-call attribution hook on the stats dict the stage callers already pass), rolled into `report['stage_stats']` under the same lock discipline as items 2/5. **Unit test (SECOND-MODEL-GATE round-2 P1 — added to group (c)):** 8 threads × interleaved S2/S4 calls through the REAL stats hook → `s2_output_tokens`/`s4_output_tokens` equal the exact per-stage sums (interleaving makes misattribution likely; MUST fail without the per-stage delta accounting).
4. **Pre/post-run DB fingerprint (P1-7 + P1-10/P2-10 + cycle 4 — P2-R):** record the run namespace's node counts at start into `report['db_fingerprint']` AND at teardown (after all workers join) into `report['db_fingerprint_post']` — the Step 2 gate FAILs on deviation from the clean baseline (dedicated DB, see Step 1), and the post-run count feeds the partial-write check (cycle 3 — P2-14 / cycle 4 — P1-E). **Cycle 4 — P2-R:** the fingerprint counts **`total_nodes` across ALL node kinds the extractor writes** (entities, points, operators, events, chain_notes) — a dirty start with only points/operators/events (entities written later) previously passed `entities == 0` — AND every fingerprint key is **int-typed** (a count regression returning None/str fails presence, mirroring the `token_usage` int check; the old `isinstance(db_entities, int)` guard silently SKIPPED all pollution checks on a None/str count). The counting function is NEW harness code and gets its own unit test (P1-10/P2-10 + cycle 4 P2-R — see (d)): the fake/empty-DB unit test AND a REAL-FalkorDB integration test (empty namespace → 0; a namespace seeded with N known nodes → exact count). **Per-question graph aggregation (cycle 5 — P2-M):** the E2E writes each question into a DISTINCT FalkorDB graph (`question_graph_namespace(model, prompt, qid)`, run.py:138/1671), so `db_fingerprint`/`db_fingerprint_post` are the AGGREGATE across the run's per-question graphs (+ the default namespace); the REAL-FalkorDB integration test must seed N known nodes across MULTIPLE per-question graphs exactly as the E2E writes them and assert the aggregate — the counting function was previously tested against a single namespace only, so a per-question layout was never exercised. **`--db-flush` is MANDATORY, not optional (cycle 4 — P2-M):** the clean-start check (dedicated DB `total_nodes == 0`) is enforced in-code — the flag drops/recreates the namespace before the run and the run ABORTS pre-ingest if the flush is refused or the count is non-zero — removing operator error from the fingerprint gate. **Per-question-graph flush scope specified (re-review R3 P2):** the E2E writes each question into a DISTINCT `question_graph_namespace(model, prompt, qid)` graph (run.py:138/1671) whose names depend on the run's model/prompt/qids — a single-namespace drop cannot enumerate them, and the `--limit 3` pilot's 3 per-question graphs must be gone for the clean-start gate. The flush therefore enumerates the DB's graphs (GRAPH.LIST) filtered by the run's graph-naming prefix (the `question_graph_namespace` prefix) and drops each, PLUS the default namespace; the group (d)/(g) tests seed MULTIPLE per-question graphs and assert they are flushed (not just "the namespace"). The flush-refused path (FalkorDB error / unreachable) → pre-ingest abort with a classifiable message (re-review P1).
5. **Per-call LLM census (cycle 3 — P1-7 + cycle 4 — P1-F + SECOND-MODEL-GATE P1 round-2, EMISSION CONTRACT ADDED):** aggregate the extractor's per-call retry-classifier labels (extractor_v2.py:3831-3856 — the FULL 9-class vocabulary: `fatal_401_auth` / `fatal_402_billing` / `fatal_403_forbidden` / `fatal_4xx` / `transient_429_rate_limit` / `transient_5xx` / `transient_timeout` / `transient_network` / `transient_unknown`) into a dedicated `report['llm_error_census']` — these classes NEVER appear in `integrity.error_census` (its vocabulary is `ingest:retries_exhausted` / `partial_parse`), so the old gates-7/8 reads of `error_census.fatal_401_auth` were vacuous `.get(..., 0)` → 0 on every run. **EMISSION CONTRACT (SECOND-MODEL-GATE round-3 P1 — REQUIRED by the gate's 9-key presence check): `report['llm_error_census']` is ALWAYS a full dict of the NINE `_classify_error` classes PLUS `fallback_route_calls` — every key present with an int value, 0 for absent classes, NEVER sparse** (a clean run has legitimate 0s for fatal_402/403/4xx and transient_5xx/network/unknown; a Counter-style sparse census would false-fail the gate on the expected-success run — pre-seed the counter with zeros before aggregation, or build `{k: counts.get(k, 0) for k in NINE_CLASSES}` at teardown). The Step 2 gate reads `llm_error_census` presence-checked (P0-1). **Counting semantics (cycle 4 — P1-F, DECIDED): RECOVERED events COUNT.** `_complete` currently records `stats["llm"]["last_class"]` on exception then OVERWRITES it to None on the next successful attempt (extractor_v2.py:4043-4058) — recovered 429s/timeouts (retry→success) are structurally invisible, so gate 8 (`transient_429_rate_limit ≤ 2`) and the timeout print could read 0 across a run dominated by recovered events — exactly the sustained-storm signature this gate targets. Implement a **cumulative per-call event counter** (or per-call `stats["llm"]["events"]` list): `_complete` appends `_classify_error(e)` on EVERY classified exception regardless of retry outcome; the harness aggregates per-session event lists into `llm_error_census` at teardown, **normalizing to the fixed 9-key dict**. **Lock-mandated (cycle 4 — P1-F):** the aggregation is shared across the 5 worker threads — guard with the same `threading.Lock` discipline as the accumulator and `stage_stats`. **Cycle 4 — P2-L + cycle 5 — P1-E (counting seam DECIDED):** deadline-killed generation is billed by the provider but never counted by the accumulator (the abandoned daemon thread keeps running — `_complete`'s own docstring accepts this), so a **`deadline_aborted` counter** (new Step 0 key) is gated at ≤ 2 in the Step 2 script; the uncounted aborted-generation spend is bounded by `deadline_aborted × max_tokens` and documented in the cost margin. **Overlap with gate 11 (SECOND-MODEL-GATE round-2 P3, stated):** a recovered deadline kill increments `deadline_aborts` (gate 10) AND the recovered `transient_timeout` census (gate 11 aggregate) SIMULTANEOUSLY — the two ceilings are not independent lenses; the effective combined ceiling for deadline-driven recovery is the tighter of the two. **Mechanism (cycle 5 — P1-E (a)) — the cycle-4 'distinct from network timeouts' claim had NO specified mechanism (both classify as `transient_timeout` via `_classify_error`, extractor_v2.py:3848-3849):** implement a dedicated `stats["llm"]["deadline_aborts"]` counter incremented AT THE `_call_once` DEADLINE-KILL RAISE — `TimeoutError("model call exceeded Ns")` (extractor_v2.py:4001) is the DISTINCT seam (a network-transport `TimeoutError` never carries that message); the counter is written under the SAME lock discipline as the accumulator/census/stage_stats (shared across the 5 worker threads — an unlocked `attr += 1` loses updates, exactly like the accumulator's LOAD/INPLACE_ADD/STORE). **Unit tests (cycle 5 — P1-E (b)/(c), folded into group (f)):** a deadline-killed call (injected `TimeoutError("model call exceeded 5s")` through the `_call_once` seam) increments `deadline_aborts`; a network-transport `TimeoutError` (no such message) does NOT; threaded variant — 8 threads × injected deadline kills → the counter equals the exact injected count (MUST fail without the lock).
6. **Run composition identity (cycle 3 — P1-11):** record the ACTUAL `composition = {provider, model, wire_id}` resolved at run start (from `TORTOISE_EXTRACTOR_PROVIDER` + the router's served id) into the report, and capture a sample per-call request body's `model` field so the served wire id is provable, not just reported. The Step 2 gate asserts provider == `deepseek-direct` AND model == `deepseek-chat` (or the explicitly re-baselined id per Open owner decision 2) — a stale-env OpenRouter routing (`RoutingModel` fallback) or a mid-plan #1790 migration changes the model/rate-limit/cost composition and invalidates all five numeric comparisons; the gate fails loudly instead. **Cycle 4 — P2-M:** composition identity is ALSO enforced PRE-RUN — a Step 1 pre-flight check (or run.py startup assertion) resolves provider/model/wire-id BEFORE ingest and aborts if not `deepseek-direct`/`deepseek-chat`, so a stale-env routing wastes no hours of run time. **Composition record EXTENDED (resume-cycle P0-A/P0-B + re-review P1):** the record now ALSO carries `effective_stage_cap` (`_stage_cap(_S2_S4_MAX_TOKENS)` evaluated at run start — the env override is read at call time, so a stale `TORTOISE_EXTRACTOR_MAX_TOKENS` silently beats the constant; recording the EFFECTIVE value makes the cap under test provable, not just the configured one), `chunk_turns` (resolved via `_resolve_int_knob("TORTOISE_LME_CHUNK_TURNS", DEFAULT_CHUNK_TURNS, args.chunk_turns)` — the reval ran `chunk_turns=1`, the code default is 2, so the report must record what the run actually used for the Step 2 gate to compare against the baseline's 1), **and `surface` (resolved from the `--db`/`TORTOISE_DB_URI` mode — `hnsw` for a FalkorDB URI, `embedded` otherwise; re-review P1: a forgotten/typo'd `--db` silently runs the embedded brute-force surface, a different retrieval pool, and the gate now fails it)**. `composition.model` is DEFINED as the served wire id (`deepseek-chat` — what the per-call request body's `model` field carries), with `wire_id` the router-resolved id; the gate expects `model == EXPECTED_EXTRACTOR_MODEL` where `EXPECTED_EXTRACTOR_MODEL = "deepseek-chat"` is a named constant near the other pins so decision 2's re-baseline updates exactly one line (re-review P2 — the old hardcoded `comp.get('model') != 'deepseek-chat'` + ambiguous model semantics are gone). The Step 2 gate asserts `effective_stage_cap == 16000` AND `composition.chunk_turns == 1` AND `composition.surface == 'hnsw'` (with negative gate-script tests for each — a stale-cap, chunk_turns-drift, or surface-drift run fails before the numeric gates are consumed).
7. **Per-qid written-entity counts (cycle 4 — P1-E + cycle 5 — P1-A — SINGLE source DECIDED + re-review P1 Phase-A exclusion):** the Step 2 gate's "failed qids wrote nothing" check needs per-qid write provenance, which the aggregate pre/post fingerprints cannot provide (a few stray entities from one failed qid vanish among thousands from successful qids). Record `per_qid_written_entities = {question_id: count}` at teardown. **Source (picked): a post-run DB query keyed by the failed qids' `question_graph_namespace` graphs** — the E2E writes each question into a DISTINCT FalkorDB graph (`question_graph_namespace(model, prompt, qid)`, run.py:138/1671), so a failed qid's partial writes are isolated in its own graph. **Counting mechanism (concrete):** per-question graph node counts — ALL node kinds (entities/points/events/operators, mirroring the `total_nodes` fingerprint) — aggregated at teardown across the failed qids' namespaces, **deduped across the S2/S4 re-emit** (a node written by S2 and re-emitted by S4 counts ONCE per graph — count the failed qid's graph's distinct nodes, not write events). **Phase-A leg EXCLUDED (re-review P1 — CRITICAL):** ingest_v2 Phase A writes session nodes + the turn/chunk raw leg into EVERY question's graph BEFORE any extraction (R1 #1540 — "written before extraction so verbatim retention survives extractor failure", ingest_v2.py:432-433). A mid-ingest failure (Phase B retry exhaustion — exactly the reval's 4 `ingest:retries_exhausted` failures gate 6 permits) leaves those nodes in the failed qid's graph, so a naive all-kinds count is ALWAYS > 0 and the "must be 0" gate fails for every permitted failure. `per_qid_written_entities` therefore counts ONLY EXTRACTOR-WRITTEN kinds — the count EXCLUDES session nodes and `pointKind=session-transcript` chunk points (filter by pointKind / post-Phase-A node markers), stated explicitly in the gate comment; unit group (h) seeds the REAL failed-qid layout (Phase-A leg present + a partial extractor write) and asserts the extractor-written count with the chunk leg excluded. The fingerprint counter's `total_nodes` scope is likewise stated as post-Phase-A extractor-writable kinds so "mirroring the total_nodes fingerprint" is unambiguous. **MERGE-collision semantics (stated):** `create_entity` MERGEs on name (the first writer's marker wins on a collision), so a colliding name belongs to the FIRST writer's graph — the query counts the failed qid's graph's nodes REGARDLESS of first-writer; attribution is ambiguous in both directions (a failed qid can own names a later question would have MERGE'd, and vice versa), so the count is a per-graph node census, not an ownership audit — the gate's "must be 0" reads this census. **The 'harness per-question ingest stats' alternative is REMOVED as impossible (cycle 5 — P1-A (d)):** verified — `failures[]` entries (run.py:1908-1918) carry ONLY `{question_id, question_type, error, error_class, failed_at_utc}`; a mid-ingest exception discards the partial stats dict, so NO ingest stats exist to read. Presence-gated in the Step 2 script like every other gate input. **Presence-discipline fix (resume-cycle P1 — the old `pqwe.get(_qid, 0) != 0` silently defaulted an ABSENT failed-qid key to 0 and passed — the exact P0-1 anti-pattern; the plan's own second-model gate had flagged it as an open P3):** the Step 2 script now requires key PRESENCE for every `failures[].question_id` (`if _qid not in pqwe: failures.append("missing gate input: per_qid_written_entities[<qid>]")`) — a harness regression that stops emitting a failed qid's key fails loudly; the negative gate-script test (failed qid absent from the dict → exit-1) is added to unit group (h).

**Unit tests (harness/adapter):** (a) token accumulation with a FAKE adapter — N calls × known per-call usage, including a RETRIED call and 8-thread interleaving, must total EXACTLY the expected sum (thread-count ≥ the E2E's `--workers 5`); **plus a REAL-adapter test (cycle 3 — P1-9): `DeepSeekDirectModel` with a MOCKED HTTP transport, 8 threads × N calls with known per-call usage → `total_completion_tokens`/`total_prompt_tokens` equal the exact expected sum (run enough iterations to make interleaving likely — this test MUST fail without the lock);** (b) a fresh-run report contains the freshness markers and a resumed run records truthful markers; (c) `stage_stats` tallies per stage — **extended (cycle 3 — P2-6 + SECOND-MODEL-GATE round-2 P1): 8 threads × interleaved S2/S4 calls through the REAL stats hook into one shared dict, asserting exact per-stage totals (keyed per-session, merged atomically/lock-protected) AND per-stage OUTPUT TOKENS `s2_output_tokens`/`s4_output_tokens` equal the exact per-stage sums under the delta-accounting mechanism (item 3) — MUST fail without per-stage attribution;** (d) **DB fingerprint (cycle 3 — P1-10/P2-10 + cycle 4 — P2-R): a fake/empty DB start records `db_fingerprint.entities == 0` AND `db_fingerprint.total_nodes == 0`; a namespace seeded with N known nodes of MIXED kinds (entities + points/operators/events — the cycle-4 entities-only blind spot) records `entities == N_entities` and `total_nodes == N_all` (assert the EXACT dict shape/key names the gate reads); the report key is present with the documented schema; a NEGATIVE gate-script test: a report whose fingerprint is missing the `entities`/`total_nodes` keys, or whose counts are NOT ints, exits 1 with a "missing gate input" failure (not the old -1 fail-by-accident path); PLUS a REAL-FalkorDB integration test of the counting function (empty namespace → 0; seeded → exact count) — **extended cycle 5 (P2-M): seed N known nodes across MULTIPLE per-question `question_graph_namespace` graphs (the E2E's real per-question layout) and assert the aggregate — the single-namespace test never exercised the per-question graph topology**;** (e) **key-read + 401 handling (cycle 3 — P2-16): mutate `DEEPSEEK_API_KEY` after construction and assert whether a new call picks it up (documenting the adapter's construction-vs-per-call read semantics); one 401 → `fatal_401_auth` census bump + abort (no silent retry-until-exhausted); PLUS the composition recorder (cycle 4 — P2-H): under `TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct`, a registry key resolves to the served wire id and the sample per-call request body's `model` field is captured into `composition`;** (f) **429-retry × scaled-deadline (cycle 3 — P2-15): two 429s then success within ONE call budget — the retry policy stays inside the scaled deadline (no deadline breach from retries alone); PLUS census counting semantics (cycle 4 — P1-F + SECOND-MODEL-GATE round-3 P1): N calls with a mocked transport through the REAL retry path mixing RECOVERED 429s/timeouts (retry→success) and terminal events → report-level `llm_error_census` matches the injected counts EXACTLY, **normalized to the fixed 9-key schema (injected classes equal their counts, ABSENT classes equal 0, plus `fallback_route_calls`; a CLEAN run's census equals the full 9-key zero dict — asserted explicitly so a sparse Counter never passes)** (this fails today: `last_class` overwrites to None on the success); extended to 8 threads sharing ONE adapter — the census aggregation is lock-mandated like the accumulator and stage_stats; PLUS the heartbeat 401 test (cycle 4 — P2-P): a heartbeat call → 401 → abort path fires and the heartbeat's token usage does NOT pollute `token_usage`; PLUS the deadline_aborts tests (cycle 5 — P1-E): a deadline-killed call (injected `TimeoutError("model call exceeded 5s")` via the `_call_once` seam) increments `stats["llm"]["deadline_aborts"]`; a network-transport `TimeoutError` (no such message) does NOT; threaded variant — 8 threads × injected deadline kills → the counter equals the exact injected count (MUST fail without the lock, like the accumulator);** (g) **pre-run abort paths (cycle 4 — P2-M + resume-cycle P1 + re-review P1): composition ≠ deepseek-direct/deepseek-chat at startup → abort before ingest; dedicated DB `total_nodes != 0` without `--db-flush` → abort before ingest; `TORTOISE_EXTRACTOR_MAX_TOKENS` set on the E2E LANE → abort (P0-B — scoped: keyed to `--db-flush` + the dedicated E2E namespace, or `effective_stage_cap != 16000`; the Step 3 baseline-subset lane DELIBERATELY sets 8000 and must NOT abort — BOTH lanes get unit tests: E2E+env-set → abort, subset+env-set → proceeds); `TORTOISE_EXTRACTOR_NO_FALLBACK=1` absent → warning not abort (the route census gate catches real failover); PLUS (resume-cycle P1-A) the `--qids` subset flag's selection logic unit-tested (explicit list restricts the run to exactly the selected qids; an unknown qid → exit-1) and `--db-flush` unit-tested (flag present → namespace dropped/recreated + pre-run count 0; absent → abort on non-zero total_nodes; refuse-path injected DB error → pre-ingest abort — re-review P1).**

(h) **per_qid partial-write provenance (cycle 5 — P1-A (c)):** a REAL-FalkorDB integration test seeding the ACTUAL per-question graph layout — N known nodes across MULTIPLE `question_graph_namespace` graphs exactly as the E2E writes them (a failed qid's graph with a partial write + a clean qid's graph) → the reported `per_qid_written_entities[qid]` equals the seeded count (mirrors the fingerprint counter's group (d) discipline), and a failed qid with a truly empty graph reports 0; a NEGATIVE gate-script test: `per_qid_written_entities` non-dict or a failed qid with a non-zero count → exit-1.**

**Commit:**
```bash
git add tools/longmem_eval/run.py tools/longmem_eval/ingest_v2.py tools/longmem_eval/gate_1787.py tortoise/extractor_v2.py tortoise/model_adapters.py tests/test_longmem_runner.py tests/test_extractor_v2.py tests/test_extractor_reliability.py
# FINAL-VERIFICATION P2: test_extractor_reliability.py added — unit groups (a)
# (real-adapter accumulator, mocked transport) and (f) (deadline_aborts seam
# tests) target the _complete/_call_once seam whose established home the plan
# itself states is that file (Step 8: test_complete_deadline_aborts_attempt at
# ~:163); the OLD enumeration silently missed them.
# includes: the --qids subset flag + --db-flush (resume-cycle P1-A/P1-B — Step 0 deliverables with unit tests), the composition record's effective_stage_cap/chunk_turns fields, and the heartbeat
# (resume-cycle P2 — the heartbeat was presence-gated but absent from this enumeration)
git commit -m "feat(longmem_eval): report freshness markers + token accumulation + stage_stats + pre/post DB fingerprints + llm_error_census (+recovered events) + deadline_aborted + per_qid_written_entities + run composition (+effective_stage_cap, chunk_turns, surface) + --qids subset + --db-flush + TORTOISE_EXTRACTOR_NO_FALLBACK + updated_at_utc + pre-run aborts — #1787"
```

**Step 1: Run the fresh 50-Q** — mirror the reval invocation EXACTLY (same surface/retriever/reader/judge). The reval ran on **surface=hnsw**, `--workers 5` (verified: `methodology.workers=5` in `reval.report.json`), reader **`openrouter:deepseek/deepseek-v4-flash`** (reval `reader_provider=openrouter`, `reader_pinned=True` — the READER ran on OpenRouter, re-review P0; the bare spec would resolve provider by key priority to the DIRECT API — a documented-400 wire), judge `openai/gpt-4o-2024-08-06`, NO `--extractor-model` flag (the production router served `deepseek-chat` via `TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct`). **The derived key (cycle 3 — P2-3):** the report's TOP-LEVEL `run_key` is **None** in the current report format — the key lives at **`methodology.checkpoint_key: hnsw__hybrid__default__default`** (`{surface}__{retriever}__{model}__{prompt}`, derived — not a flag); the labeling flags are `--checkpoint`/`--output`:

```bash
cd /Users/danielospina/Documents/GitHub/tortoise
env -u TORTOISE_EXTRACTOR_MAX_TOKENS -u TORTOISE_LME_CHUNK_TURNS -u TORTOISE_LME_EVIDENCE_BOOST \
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_e2e_1787' \
TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct \
TORTOISE_LME_READER_MODEL='openrouter:deepseek/deepseek-v4-flash' \
TORTOISE_LME_JUDGE_MODEL='openai/gpt-4o-2024-08-06' \
TORTOISE_EXTRACTOR_NO_FALLBACK=1 \
  uv run python -m tools.longmem_eval.run \
  --split s --limit 50 --ingest-mode v2 --workers 5 \
  --chunk-turns 1 --db-flush \
  --reader-model openrouter:deepseek/deepseek-v4-flash \
  --judge-model openai/gpt-4o-2024-08-06 \
  --db 'docker://:falkordb@localhost:6379/tortoise_test_e2e_1787' \
  --checkpoint .longmemeval_cache/runs/reval-1787-16k.checkpoint.json \
  --output .longmemeval_cache/runs/reval-1787-16k.report.json
```

> **⚠️ Must-match flags (resume-cycle P0-A/P0-B — the E2E measurement layer was NOT fully pinned):** `--db` with the SAME dedicated E2E URI as the `TORTOISE_DB_URI` env above (`docker://:falkordb@localhost:6379/tortoise_test_e2e_1787`; omit → embedded brute-force surface, NOT comparable to the hnsw baseline), reader/judge model pins, `--extractor-model` OMITTED (a registry key like `deepseek-flash-direct` also reproduces the served composition; the bare string `deepseek-chat` is NOT a registry key and aborts at startup), **`--chunk-turns 1` (P0-A — the reval ran `methodology.chunk_turns=1` while `DEFAULT_CHUNK_TURNS=2` in ingest.py:47; chunk_turns sizes the raw-chunk verbatim graph leg feeding `chunk_evidence@20` (gate 3's target quantity), so a 2-turn window silently invalidates the baseline comparison — `--compare`'s comparability block does NOT check chunk_turns)**, `--db-flush` (P1-B — new Step 0 harness flag; drops/recreates the dedicated DB namespace before ingest so the clean-start `total_nodes == 0` invariant is enforced in-code, and the pre-flight `--limit 3` pilot cannot pollute the full run's fingerprint gate), **`TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct` pinned explicitly** (the reval ran with it; a stale shell env could route to openrouter and break comparability — Task 1 Step 5 documented the OpenRouter path as a separate accepted assumption), and **`env -u TORTOISE_EXTRACTOR_MAX_TOKENS` (P0-B — `_stage_cap` reads the env at call time and beats the constant for ANY default; a stale 8000 override from the Step 3 subset re-run silently re-runs the E2E at the OLD cap with no detection; the Step 0 `composition` record now also carries `effective_stage_cap` and the Step 2 gate asserts `== 16000`)**. **Reader/judge pins are doubly guarded (cycle 3 — P2-1):** the `--reader-model`/`--judge-model` flags AND the `TORTOISE_LME_READER_MODEL`/`TORTOISE_LME_JUDGE_MODEL` env vars above — reader.py:397 reads the env FIRST, so a stale env value silently overrides the flags; pin both to the same spec (or `env -u` the vars). Step 0's `composition` record + the Step 2 composition gate make any residual drift fail loudly (P1-11). Requires `DEEPSEEK_API_KEY` + `OPENAI_API_KEY` (judge `openai/gpt-4o-2024-08-06`) + `OPENROUTER_API_KEY` (reader `openrouter:deepseek/deepseek-v4-flash` — the reval's exact reader wire) + docker FalkorDB per AGENTS.md. **Extractor-provider inference honesty (SECOND-MODEL-GATE P3):** the reval report records `extractor_provider: None`/`extractor_model: None` in methodology (only the checkpoint fingerprint records the bare `deepseek-chat` id, no provider) — "deepseek-direct" is an INFERENCE from the bare model id (not `deepseek/deepseek-chat`), not a measured baseline fact; the Step 1 wording "same composition as the reval" is verifiable on the model id but the provider is inferred — the inference is recorded on #1787 when posting the scoping comment (Task 4 Step 1), and the fresh-run composition gate enforces provider == deepseek-direct regardless.

> **Freshness (P1-F):** gate runs are **never resumed** — a NEW checkpoint path per gate run (`reval-1787-16k.checkpoint.json` is unique); the git_sha fingerprint gate alone is insufficient (it only refuses DIFFERENT-sha resumes — a same-sha crash-recovery resume on the same checkpoint, the realistic ~4h-run failure path, is allowed and leaves partial graph state in HNSW). Step 0 records the freshness markers (`resumed=false`, `skipped_qids=[]`); the Step 2 gate script FAILs on resume/skip markers, a MISSING marker key (a marker-less report is a harness regression, never a pass), or `integrity.n_attempted != 50`.

> **DB isolation (P1-7):** the E2E must NOT reuse the shared `tortoise_test_matrix` DB (the pytest suite's default lane writes there too — a concurrent or prior run could inflate retrieval vs the baseline). **Prefer a dedicated E2E DB URI** (e.g., `tortoise_test_e2e_1787`, created/flushed before the run); Step 0 records the pre-run entity/node-count fingerprint into the report, and the Step 2 gate FAILS if the fresh namespace is not empty (or deviates from the documented clean baseline) — the fingerprint is a gate input, not a self-reported note.

> **Run order (cycle 4 — P1-A):** the linear sequence is **Step 0 (harness) → baseline subset re-run (Step 3's pin derivation — it needs only Step 0's accumulator) → Step 1 (this full E2E) → Step 2 (gate script) → Step 3 (cost check)**. The Step 2 gate hard-fails on a None `BASELINE_TOTAL_TOKENS`, so the subset re-run MUST complete and the pin MUST be written into the Step 2 script BEFORE Step 1 starts (see Step 3).

> **Pre-flight (P1-E + cycle 3 — P2-15 + cycle 4 — P2-F/P2-M/P2-O/P2-P):** BEFORE the full ~4h run, three things happen in order:
> 1. **Composition + clean-DB abort (cycle 4 — P2-M + resume-cycle P0-B/P1 + re-review P0 reader-lane):** resolve provider/model/wire-id BEFORE ingest — abort if not `deepseek-direct`/`deepseek-chat`; **abort if `TORTOISE_EXTRACTOR_MAX_TOKENS` is set on the E2E LANE ONLY (the E2E must run with the env UNSET — `env -u` in the Step 1 command — so the 16000 default is what's under test; the Step 3 baseline-subset lane DELIBERATELY sets it to 8000 and is EXEMPT — DISCRIMINATOR specified (re-review R3 P2): the abort fires when the env is set AND the value is NOT "8000" (the subset's explicit value) — a run.py startup check `raw = os.environ.get("TORTOISE_EXTRACTOR_MAX_TOKENS"); if raw and raw.strip() != "8000": abort` — so the subset passes by its explicit =8000, any OTHER stale override aborts, and no DB-name string is hardcoded; group (g) tests BOTH lanes: env=16000 → abort, env=8000 (subset) → proceeds, env absent → proceeds)**; **do NOT abort on `OPENROUTER_API_KEY`/`VENICE_API_KEY` (re-review P0 — the reval's READER ran on `openrouter:deepseek/deepseek-v4-flash` — `reader_provider=openrouter`, `reader_pinned=True` — and REQUIRES OPENROUTER_API_KEY; aborting on the key makes the E2E un-runnable with a baseline-comparable reader). Instead the extractor's OpenRouter FALLBACK is neutralized by a dedicated seam: `TORTOISE_EXTRACTOR_NO_FALLBACK=1` (new Step 0 harness knob — `build_extractor_model`/`resolve_extractor_provider` construct `RoutingModel(primary, fallback=None)` when set, so the pool stays `[deepseek-direct]` and no env var is taken away from the reader); the `fallback_route_calls == 0` census gate remains as defense-in-depth (presence-checked, re-review P1)**; run with the now-MANDATORY `--db-flush` and abort if the dedicated DB's `total_nodes != 0` after flush (Step 0 fingerprint). A stale-env routing or dirty DB start must waste minutes, not ~4h. **Reader lane pinned to the EXACT baseline spec (re-review P0):** `--reader-model openrouter:deepseek/deepseek-v4-flash` + `TORTOISE_LME_READER_MODEL=openrouter:deepseek/deepseek-v4-flash` (the bare `deepseek/deepseek-v4-flash` would resolve provider by KEY PRIORITY — reader.py:365-377 — to `api.deepseek.com`, a wire the plan's own Task 1 Step 3 documents as 400/unknown-model; the baseline's reader is on openrouter). `reader_provider`/`reader_pinned`/`reader_model_spec` join the Step 2 comparability loop (re-review P1). The judge (`openai/gpt-4o-2024-08-06`) requires `OPENAI_API_KEY` — added to the Requires line (re-review P2).
> 2. **Small-N full-stack pilot (cycle 4 — P2-O):** `--limit 3` full-stack run on the dedicated DB — ingest → retrieve → judge — asserting outcomes are PRODUCED AND JUDGED (judge API reachable, all keys valid) before the 50-Q run. Nothing exercises the reader (`openrouter:deepseek/deepseek-v4-flash`) and judge (`openai/gpt-4o-2024-08-06`) keys/models or the dedicated DB pipeline otherwise. **Checkpoint/output hygiene (resume-cycle P2):** the pilot runs on its OWN checkpoint + output paths (e.g. `reval-1787-pilot-3.checkpoint.json` / `reval-1787-pilot-3.report.json`) — reusing the E2E's `reval-1787-16k.*` paths would make the full run RESUME from the pilot's 3 completed qids (n_attempted=47, detected only at the end of a ~4h run by the n_attempted != 50 gate); and because the pilot pollutes `tortoise_test_e2e_1787` with 3 questions' graphs, the E2E MUST run with the Step 0 `--db-flush` flag (drops/recreates the namespace pre-ingest) — the literal Step 1 command above includes it; a pilot on a separate DB URI is the alternative isolation. **Pre-run qid-identity probe (R6 P2 + SECOND-MODEL-GATE P3 — mechanism specified):** the probe is a run.py STARTUP assertion (not prose): before ingest, `run.py` resolves the split's first-50 question ids (or the `--qids` set) from the local dataset metadata and compares against `reval.checkpoint.json` outcomes ∪ failures — a mismatch ABORTS pre-ingest with the same message the Step 2 gate uses (mirrors the composition/clean-DB abort; cheap — dataset metadata only, no LLM calls). If the startup assertion is not landed with Step 0, the post-run qid gate remains the enforcement point and the ~4h waste is the documented cost (stated on #1787).
> 3. **SUSTAINED request-rate probe (cycle 4 — P2-F, envelope corrected cycle 5 — P2-J):** ≥6 consecutive SHORT-PROMPT calls per worker × 5 workers (max_tokens=16000 REQUESTED, but a ~few-hundred-token output) within a 60s window — 30 calls/60s ≈ **0.50 calls/s aggregate, ABOVE the run's ~0.48/s envelope** (6,720 calls / ~3.9h; the cycle-4 0.42/s figure was 13% BELOW the envelope — a quota between ~25 and ~29 rpm would have tripped the run but NOT the probe). Full-length 16K generation takes 160-320s/call at 50-100 tok/s, so five full-length calls (800-1600s) CANNOT fit a 60-120s window — the probe measures the rpm/tpm quota surface with short outputs; the generation-time surface is covered by the deadline scaling (Task 2 Step 6) + item 5's full-length probe + the heartbeat. Assert **0×429 at run throughput**, and confirm the dedicated DB is empty (Step 0 fingerprint). Record it on #1787.
> 4. **Mid-run heartbeat (cycle 4 — P2-P — now MANDATORY, was optional P2-16):** a 1-call 16K probe every 30 min that fails fast on key rotation (a mid-run `DEEPSEEK_API_KEY` rotation wastes at most 30 min, not the rest of the run). Its presence is GATED in the Step 2 script like other Step 0 keys — `need('heartbeat', ...)` requires PRESENCE AND a non-empty record (cycle 5 — P1-D; the cycle-4 text promised presence-gating but the script had NO check, so a missing heartbeat passed silently — fixed). **Its token usage is EXCLUDED from `token_usage`** — a separate counter (the heartbeat's 16K calls would otherwise pollute the cost gate and self-induce 429s; the Step 0 unit test covers 401 → abort + no accumulator pollution). **Non-401 semantics specified (resume-cycle P2):** the heartbeat ABORTS ONLY on 401-class responses (key rotation — the bounded-loss case); a transient 429/5xx at heartbeat time is RETRIED and logged (a single transient at heartbeat cadence must not false-abort a healthy run — the abort-on-401-only contract is unit-tested; heartbeat 429s are counted in `llm_error_census` like any other call). Judge-key (`OPENAI_API_KEY`) and reader-key rotation are NOT probed by the heartbeat — bounded only by the outcome/failure ceilings at gate time (a ~4h worst case) — recorded as an accepted limitation (the judge/reader spend is outside this issue's extractor focus).

> 5. **Full-length 16K generation probe (cycle 5 — P1-G):** one full-length (or proportionally-scaled — e.g. a 16K-budget call emitting ≥8K tokens) generation per worker within a ~10-minute window. The short-output probes (items 3-4) never exercise generation at the new cap, so two generation-time failure modes are unvalidated pre-run: (a) live completion throughput < ~20 tok/s (surfaces only as mid-run deadline kills → gates 5/10 after hours); (b) a TPM-limited key that throttles only during sustained 16K generation at 5 workers (a 429 storm detected at the END of the run). Assert **sustained completion tok/s ≥ ~20 per call** (abort/flag pre-run if below — the Task 2 Step 6 deadline-margin floor) AND **0×429 at a token volume ≈ a meaningful fraction of the run's sustained per-minute TPM** (~80K tokens / 10 min ≈ 8K tok/min vs the run's sustained ~455K output tokens / ~3.9h ≈ ~1.9K tok/min — the probe is a strict superset of the run's sustained output rate, so a TPM quota between the run rate and the probe rate is excluded). **Fold the measured throughput back into the deadline-margin assumption if it degrades** (a probe result at/below 20 tok/s invalidates the 0.05 s/token margin — record it on #1787 BEFORE the run starts, per Task 2 Step 6's P2-K boundary test).

> **Per-stage attribution (P2-M, deduped cycle 5 — P2-F):** the S2-vs-S4 `stage_stats` mechanism is fully specified in Step 0 item 3 (the `extract_session_v2` stats hook — per-stage `truncated`/`partial` tallies emitted into `report['stage_stats']`); Step 2 prints it and Step 6's #1789 decision rule consumes it — the delta-contract decision (companion #1789's "S4 re-emit dominant" premise) is evidence-based from that key, not structural inference.

**Step 2: Read the report gates** — `chunk_evidence@20` is the mean of per-outcome `chunk_evidence_recall@k[20]` (0.7375 in the reval), NOT `evidence.retrieved_mean@k[20]` (2.72, a retrieved-count). Guard for the "NOT PUBLISHED" None case. **The script computes gates 1-12 numerically (P2-2 + R5 P2-2: gate 12 = C3 unrecorded-truncations) plus the cycle-3 additions — full presence discipline on EVERY gate input (P0-1), outcome-count consistency (P2-7), post-run DB state (P2-14), and the automated `--compare` primary verdict (P2-17) — its exit code IS the gate verdict; Step 4 is the escalation/decisional wrapper.** **Gate-file extraction (R5 P2-3):** the gate heredoc below is ALSO extracted to `tools/longmem_eval/gate_1787.py` as a Task 5 Step 0 deliverable (with `BASELINE_TOTAL_TOKENS`/`BASELINE_INGEST_MEAN_MS`/`BASELINE_INGEST_MEDIAN_MS`/`EXPECTED_EXTRACTOR_MODEL` as module constants) — so the Step 3 pin edit is committed with the harness (a `git add tools/longmem_eval/gate_1787.py` line joins the Step 0 commit) and the executed script never silently diverges from the doc; the heredoc stays as the documentation-of-record and the file is generated from it verbatim (a byte-identity check mirrors Step 8's discipline). The Step 4 wrapper's owner-sign-off notes ([0.7375, 0.738) chunk_evidence band + [0.804, 0.826) accuracy band — R5 P1-2) are recorded as #1787 comments, never edits to the committed gate. Negative gate-script tests (cycle 3 — P0-1 + cycle 4 — P2-I): feed the script a report with EACH `need()`/presence-guarded input deleted in turn — top-level: `integrity`, `resumed`, `skipped_qids`, `token_usage`, `composition`, `db_fingerprint`, `db_fingerprint_post`, `llm_error_census`, `deadline_aborted`, `heartbeat` (cycle 5 — P1-D), `per_qid_written_entities`, `stage_stats`, `accuracy.overall`; nested: `integrity.n_attempted` / `n_failed` / `valid` / `error_census`, `token_usage.completion_tokens` / `prompt_tokens`, `db_fingerprint.entities` / `total_nodes` (and `db_fingerprint_post`'s), per-outcome `error_classes` / `llm_truncated`, and the chunk_evidence NOT-PUBLISHED guard (`chunk_evidence_recall@k['20']` absent on every outcome) — each asserts exit-1 with a "missing gate input" failure; the outcomes-length consistency path (`len(outcomes) != n_attempted − n_failed` → exit-1); `token_usage = {0, 0}` → exit-1 (cycle 4 — P1-G: 0 IS an int and must NOT pass the budget); fingerprint counts of non-int type (None/str) → exit-1 (cycle 4 — P2-R); `provider=openrouter` → exit-1 (P1-11). **Resume-cycle additions to the negative enumeration:** `failures` top-level key absent → exit-1 (P2 — the per-qid + 401 scans were `(r.get('failures') or [])` — vacuous on a dropped key); `composition.effective_stage_cap` ≠ 16000 → exit-1 (P0-B — stale cap env); `composition.chunk_turns` ≠ 1 → exit-1 (P0-A — chunk-leg drift); `llm_error_census.fallback_route_calls` ≠ 0 → exit-1 (P1 — mid-run failover); a failed qid ABSENT from `per_qid_written_entities` → exit-1 (P1 — key presence required); run wall-clock > 5.9h → exit-1 (P2). **Re-review additions:** `composition.surface` ≠ hnsw → exit-1 (P1 — surface drift); `updated_at_utc` missing/invalid → exit-1 (P1 — wall-clock ceiling presence); `llm_error_census.fatal_401_auth`/`transient_429_rate_limit`/`transient_timeout` non-int or absent → exit-1 (R3 P2 — sibling census keys); **each `stage_stats.{s2_truncated,s2_partial,s4_truncated,s4_partial}` sub-key non-int or absent → exit-1 (R3 P2 — the #1789 decision rule must not fire on a vacuous read)**; the qid-identity mismatch → exit-1 (R2/R3 P1/P0 — the full 50-set union, outcomes ∪ failures).

```bash
python3 - <<'PY'
import json, os, statistics, subprocess, sys
r = json.load(open('.longmemeval_cache/runs/reval-1787-16k.report.json'))
outcomes = r.get('outcomes') or []
failures = []

# P2-R/P0-1: EVERY gate input must be PRESENT — a missing/renamed key fails
# loudly (never a silent default-to-0 PASS). Cycle 3 (P0-1): presence checks
# extended to EVERY gate input, incl. the per-outcome fields that were read
# with silent .get(..., 0) defaults. (R4 P1-1: defined BEFORE first use —
# the R3 placement below the _stamp block caused NameError on every run.)
def need(key, obj, label):
    if not isinstance(obj, dict) or key not in obj:
        failures.append(f"missing gate input: {label} ({key!r} not in report)")
        return {}
    return obj[key]

# re-review P1: qid-identity pin — the E2E runs --limit 50 (the first 50 of
# split=s) while the reval's 50 are whatever split=s held on 2026-08-27; a
# re-downloaded dataset can differ. --compare only fails on shared_n <= 0, so
# a partially overlapping set passes with a confounded PRIMARY verdict. The
# gate requires FULL qid overlap (or --qids drawn from the reval checkpoint):
reval_ckpt_qids = None
if os.path.exists('.worktrees/1509-REVAL/.longmemeval_cache/runs/reval.checkpoint.json'):
    try:
        _ck = json.load(open('.worktrees/1509-REVAL/.longmemeval_cache/runs/reval.checkpoint.json'))
        # re-review R3 (P0): the ATTEMPTED set is outcomes ∪ failures — the
        # reval checkpoint holds 46 outcomes + 4 failures = 50 attempted; a
        # successful fix run (50 outcomes, 0 failures) must NOT false-fail.
        reval_ckpt_qids = ({o.get('question_id') for o in (_ck.get('outcomes') or [])}
                           | {f.get('question_id') for f in (_ck.get('failures') or [])})
    except Exception:
        reval_ckpt_qids = None
if reval_ckpt_qids is not None:
    # R4 P1-3: the RUN side is outcomes ∪ failures too — a legitimate run
    # with n_failed ≤ 4 (gate 6 permits it; the reval had 4 failures) has
    # 46-49 outcomes but 50 attempted; outcomes-only run_qids would
    # false-fail it as a "--qids subset".
    run_qids = ({o.get('question_id') for o in outcomes}
                | {f.get('question_id') for f in (r.get('failures') or [])})
    if run_qids != reval_ckpt_qids:
        failures.append(f"qid identity: {len(run_qids & reval_ckpt_qids)}/{len(run_qids)} "
                        f"run qids overlap the reval's {len(reval_ckpt_qids)} "
                        f"attempted (must be identical — a different question "
                        f"mix confounds the --compare PRIMARY verdict and the "
                        f"absolute bars; a 46-qid --qids subset can never "
                        f"satisfy n_attempted == 50 — pin the FULL 50-set "
                        f"from the reval checkpoint outcomes ∪ failures)")
else:
    failures.append("missing gate input: reval.checkpoint.json not found for the "
                    "qid-identity check (copy it stable per P2-9)")

# P2 (resume cycle): elapsed-hours helper for the run wall-clock ceiling.
def _elapsed_hours(report):
    """Elapsed wall-clock from methodology.run_at_utc to report updated_at_utc
    (Step 0 deliverable — the reval format had NO top-level updated_at_utc, so
    the ceiling was a silent no-op; the stamp is now REQUIRED and
    presence-gated). None only on malformed values — a MISSING stamp is a
    gate failure, never a skip."""
    import datetime as _dt
    m = report.get('methodology') or {}
    start = m.get('run_at_utc')
    end = report.get('updated_at_utc')
    if not start or not end:
        return None
    try:
        t0 = _dt.datetime.fromisoformat(start)
        t1 = _dt.datetime.fromisoformat(end)
        # FINAL-VERIFICATION P3: naive-vs-aware subtraction raises TypeError
        # (uncaught → traceback, not a clean gate failure). Normalize both to
        # aware; if either is naive, assume UTC per the ISO contract (the
        # Step 0 updated_at_utc deliverable is specified ISO-8601-with-tz,
        # matching run_at_utc's format).
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=_dt.timezone.utc)
        if t1.tzinfo is None:
            t1 = t1.replace(tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return None
    return (t1 - t0).total_seconds() / 3600.0

# re-review P1: the wall-clock ceiling must be PRESENCE-GATED — the old
# `if run_elapsed is not None and ...` silently skipped on a missing stamp
# (and the reval report format has NO updated_at_utc, so the resume-cycle
# fix never fired). The stamp is a Step 0 deliverable now.
_stamp = need('updated_at_utc', r, 'updated_at_utc (Step 0 teardown stamp)')
if not isinstance(_stamp, str) or not _stamp.strip():
    failures.append(f"missing gate input: updated_at_utc (non-empty string "
                    f"required, got {_stamp!r} — the run wall-clock ceiling "
                    f"cannot fire without it)")

integrity = need('integrity', r, 'integrity')
# P0-1/P1-F: run-identity freshness — 50 questions ATTEMPTED, not 50 outcomes.
n_attempted = need('n_attempted', integrity, 'integrity.n_attempted') or 0
if n_attempted != 50:
    failures.append(f"fresh (non-resumed) 50-Q violated: n_attempted={n_attempted} "
                    f"(must be 50)")
# P1-2/P2-1: freshness markers must be PRESENT — the current harness emits
# neither key (verified: the reval report has no `resumed`/`skipped_qids`/`token_usage`),
# so a marker-less report FAILS instead of passing silently.
need('resumed', r, 'freshness marker resumed (Step 0)')
need('skipped_qids', r, 'freshness marker skipped_qids (Step 0)')
tu = need('token_usage', r, 'token_usage (Step 0 accumulation)')
if r.get('resumed') is not False:
    failures.append(f"resumed must be present AND False (got {r.get('resumed')!r})")
if r.get('skipped_qids'):
    failures.append(f"skipped_qids must be present AND empty (got {r.get('skipped_qids')!r})")
# P1-D (cycle 5): the MANDATORY mid-run heartbeat record must be PRESENT AND
# non-empty (Step 1 item 4 promises presence-gating; the cycle-4 script had
# NO need('heartbeat') check — a missing heartbeat passed silently,
# contradicting P0-1 discipline).
hb = need('heartbeat', r, 'heartbeat (Step 1 mid-run probe)')
if not isinstance(hb, dict) or not hb:
    failures.append(f"missing gate input: heartbeat (Step 1 mid-run probe — "
                    f"present AND non-empty record required, got {hb!r})")

# P0-1: token_usage sub-keys must be present with INT values — a missing
# sub-key must NOT silently total 0 and pass any budget (cycle 3).
for _k in ('completion_tokens', 'prompt_tokens'):
    if not isinstance(tu.get(_k), int):
        failures.append(f"missing gate input: token_usage.{_k} (int required, "
                        f"got {tu.get(_k)!r})")

# P1-11: run composition identity — the numeric gates are only comparable on
# the exact composition the baseline was measured on (or an explicitly
# re-baselined id per Open owner decision 2); a stale-env OpenRouter routing
# or a mid-plan #1790 migration fails here, not hours into analysis.
comp = need('composition', r, 'run composition (Step 0)')
# R4 P2-1: decision-2 re-baseline constant — the gate expects the served wire
# id; re-baselining updates exactly this line (the old hardcoded literal in
# the comparison below is now this named pin).
EXPECTED_EXTRACTOR_MODEL = "deepseek-chat"
# re-review P1: surface drift is ungated — the E2E depends on --db for
# surface=hnsw (omit → embedded brute-force, a different retrieval surface)
# and the comparability block does NOT check surface. The composition record
# now carries it; gate it here.
if comp.get('surface') != 'hnsw':
    failures.append(f"composition surface: {comp.get('surface')!r} "
                    f"(must be 'hnsw' — the reval's surface; --db omitted or "
                    f"a stale env silently runs the embedded brute-force "
                    f"surface)")
if comp.get('provider') != 'deepseek-direct' or comp.get('model') != EXPECTED_EXTRACTOR_MODEL:
    failures.append(f"composition identity: provider={comp.get('provider')!r} "
                    f"model={comp.get('model')!r} (expected deepseek-direct / "
                    f"deepseek-chat — or the explicitly re-baselined id per "
                    f"Open owner decision 2)")
# P0-B (resume cycle): the EFFECTIVE stage cap is recorded at run start — a
# stale TORTOISE_EXTRACTOR_MAX_TOKENS env silently beats the constant (the
# env override is read at call time), so the gate asserts the run actually
# executed at 16000, not that the constant says 16000. A stale 8000 from the
# Step 3 subset re-run is the realistic contamination path.
if comp.get('effective_stage_cap') != 16000:
    failures.append(f"composition effective_stage_cap: "
                    f"{comp.get('effective_stage_cap')!r} (must be 16000 — "
                    f"a stale TORTOISE_EXTRACTOR_MAX_TOKENS env silently "
                    f"beats the constant; env -u it in the Step 1 command)")
# P0-A (resume cycle): the reval ran methodology.chunk_turns=1, the code
# default is 2 — chunk_turns sizes the raw-chunk leg feeding gate 3's
# chunk_evidence@20, so a 2-turn window invalidates the baseline comparison
# and --compare's comparability block does NOT check it.
if comp.get('chunk_turns') != 1:
    failures.append(f"composition chunk_turns: {comp.get('chunk_turns')!r} "
                    f"(must be 1 — the reval's chunk_turns=1; a 2-turn window "
                    f"invalidates the chunk_evidence@20 baseline)")
# P1 (resume cycle): route census — RoutingModel failover is forward-only and
# run-wide; a mid-run failover to the OpenRouter fallback must fail here, not
# pass on the configured provider/model (checked after lec is defined below).

# P1-10 + P2-R (cycle 4): pre-run DB fingerprint — `entities` AND
# `total_nodes` (ALL node kinds the extractor writes; a dirty start with only
# points/operators/events previously passed `entities == 0`) are
# PRESENCE-checked AND INT-TYPED (a count regression returning None/str fails
# presence — the old isinstance-guard silently SKIPPED all pollution checks on
# a bad type); the dedicated E2E DB must start EMPTY.
db = need('db_fingerprint', r, 'pre-run DB fingerprint (Step 0)')
db_entities = need('entities', db, 'db_fingerprint.entities (Step 0)')
db_total = need('total_nodes', db, 'db_fingerprint.total_nodes (Step 0)')
if not isinstance(db_entities, int) or not isinstance(db_total, int):
    failures.append(f"missing gate input: db_fingerprint.entities/total_nodes "
                    f"(int required, got {db_entities!r}/{db_total!r})")
elif db_entities != 0 or db_total != 0:
    failures.append(f"DB pollution: pre-run fingerprint entities={db_entities} "
                    f"total_nodes={db_total} (must be 0 — clean dedicated DB)")

# P2-14 + P2-R (cycle 4): post-run DB fingerprint — presence + int-typed +
# no mid-run reset (the failed-qid partial-write check is below, P1-E).
db_post = need('db_fingerprint_post', r, 'post-run DB fingerprint (Step 0)')
db_post_entities = need('entities', db_post, 'db_fingerprint_post.entities (Step 0)')
db_post_total = need('total_nodes', db_post, 'db_fingerprint_post.total_nodes (Step 0)')
if not isinstance(db_post_entities, int) or not isinstance(db_post_total, int):
    failures.append(f"missing gate input: db_fingerprint_post.entities/"
                    f"total_nodes (int required, got "
                    f"{db_post_entities!r}/{db_post_total!r})")
elif (isinstance(db_entities, int) and isinstance(db_total, int)
      and (db_post_entities < db_entities or db_post_total < db_total)):
    failures.append(f"post-run DB reset: entities {db_entities} → "
                    f"{db_post_entities}, total_nodes {db_total} → "
                    f"{db_post_total} (must not shrink mid-run)")

# P1-E (cycle 4) + P1-A (cycle 5): failed-qid partial-write check — per-qid
# write provenance (Step 0 key `per_qid_written_entities`, produced by the
# post-run DB query over the failed qids' question_graph_namespace graphs —
# ALL node kinds, MERGE-collision semantics per Step 0 item 7), presence-
# gated: every qid that FAILED mid-ingest (failures[].question_id) must have
# written 0 nodes (a mid-ingest failure — S1 wrote entities, or partial-accept
# — leaves partial data that pollutes retrieval for subsequent questions; the
# reval had 4 such failures and gate 6 allows ≤4 again).
pqwe = need('per_qid_written_entities', r, 'per_qid_written_entities (Step 0)')
if not isinstance(pqwe, dict):
    failures.append(f"missing gate input: per_qid_written_entities (dict "
                    f"required, got {pqwe!r})")
else:
    failed_qids = [f.get('question_id') for f in (r.get('failures') or [])
                   if isinstance(f, dict) and f.get('question_id')]
    for _qid in failed_qids:
        if _qid not in pqwe:
            # P1 (resume cycle): key presence REQUIRED — the old
            # pqwe.get(_qid, 0) != 0 silently defaulted an absent failed-qid
            # key to 0 and passed (the P0-1 anti-pattern); a harness
            # regression that stops emitting a failed qid's key must fail
            # loudly, never pass vacuously.
            failures.append(f"missing gate input: "
                            f"per_qid_written_entities[{_qid!r}] absent "
                            f"(failed qid must have an explicit 0)")
        elif pqwe[_qid] != 0:
            failures.append(f"failed-qid partial write: qid {_qid} wrote "
                            f"{pqwe[_qid]} nodes (must be 0)")

# P0-1: outcomes may legitimately be < 50 when gates 6/9 pass (baseline:
# n_attempted=50, outcomes=46, n_failed=4) — the 50-Q contract is
# n_attempted == 50, NOT len(outcomes) == 50.
n_failed = need('n_failed', integrity, 'n_failed') or 0
need('valid', integrity, 'valid')
overall = need('overall', r.get('accuracy') or {}, 'accuracy.overall') or 0
c = need('error_census', integrity, 'integrity.error_census')
if not isinstance(c, dict):
    c = {}
partial_census = c.get('partial_parse', 0)

# P0-1: per-outcome gate inputs must be PRESENT (previously read with silent
# .get(..., 0) defaults — a fresh schema drop would have passed silently).
if not any(isinstance(o.get('error_classes'), dict) for o in outcomes):
    failures.append("missing gate input: per-outcome error_classes (gate 1 — "
                    "present on ≥1 outcome required)")
if not all(isinstance(o.get('llm_truncated'), int) for o in outcomes):
    failures.append("missing gate input: per-outcome llm_truncated (gate 2 — "
                    "present per outcome required)")

# P2-1: per-outcome chunk_evidence_recall@k access — guarded loop, never a raw
# KeyError traceback.
ce20 = []
for o in outcomes:
    ce = (o.get('chunk_evidence_recall@k') or {})
    if ce.get('20') is not None:
        ce20.append(ce['20'])
if not ce20:
    failures.append("missing gate input: chunk_evidence_recall@k['20'] "
                    "(NOT PUBLISHED on every outcome)")
# P2-N (cycle 5): minimum sample-size floor on the chunk_evidence field —
# the reval published 40/46 outcomes; a degraded mid-run judge/evidence
# pipeline that publishes the field on fewer outcomes must FAIL (a shrunk
# sample silently passes the mean gate otherwise).
if len(ce20) < 40:
    failures.append(f"gate 3 chunk_evidence@20: only {len(ce20)} outcomes "
                    f"published the field (reval published 40/46 — degraded "
                    f"judge/evidence pipeline must fail, not shrink the sample)")
ce_mean = statistics.mean(ce20) if ce20 else None
# P2 (resume cycle): chunk_evidence@20 is a raw point-estimate floor with no
# statistical treatment — accuracy gets a McNemar significance test via
# --compare, but a parity run's chance fluctuation (or one degraded outcome's
# mean shift over ~40 values) can false-fail gate 3 while accuracy passes
# significance. The Step 4 wrapper therefore ALSO computes the paired
# per-qid chunk_evidence delta against the reval's per-outcome values
# (reval.report.json outcomes[].chunk_evidence_recall@k['20']) and records a
# one-line trend note on #1787 when the point delta is negative but the
# paired mean shift is within ±1 outcome's contribution (≈ ±1/40 mean
# movement); the hard floor ≥ 0.7375 stays the gate — the paired read is an
# interpretive companion to the owner-sign-off branch (P2-E), never a
# silent waiver.

partial_qids = sum(1 for o in outcomes
                   if 'partial_parse' in (o.get('error_classes') or {}))
llm_truncated = sum(o.get('llm_truncated', 0) for o in outcomes)
# R5 P2-2 + R6 P1-2: C3 ("0 unrecorded truncations" — #1746) gate input — the
# issue's indicator (2). Gate 2 bounds RECORDED truncation calls; the C3 class
# is losslessly-recovered truncations (ladder rungs 1/3 — reval
# n_truncated_valid=3). The harness ALREADY computes this as
# `integrity.n_truncated_valid` (report.py:1090, from llm_truncated > 0 &&
# grade == clean) — the R5 version read `recovery.truncated_valid` per
# outcome, a key NO code writes (ladder records sanitize/repair only; the
# reval's recovery dicts are all {}) — vacuous by construction, violating
# P0-1. Gate on the produced quantity instead (R6 P1-2):
_trunc_valid = need('n_truncated_valid', integrity,
                    'integrity.n_truncated_valid (C3 — lossless recoveries)')
if not isinstance(_trunc_valid, int):
    failures.append(f"missing gate input: integrity.n_truncated_valid (int "
                    f"required, got {_trunc_valid!r} — the C3 indicator (2) "
                    f"must never be silently dropped)")
elif _trunc_valid > 0:
    failures.append(f"gate 12 C3 unrecorded truncations: {_trunc_valid} "
                    f"lossless-recovered truncation qids (target == 0 — the "
                    f"issue's indicator 2; per Open owner decision 3, >0 "
                    f"triggers the census-pedantry follow-up issue, but the "
                    f"E2E itself must not silently pass the C3 target)")

# P2-8: latency gate — mean AND median; the MEDIAN is the PRIMARY gate
# quantity (robust to the LLM-tail variance: reval mean 1,533,924 ms vs p95
# ~1.68M ms — a mean-only bar sat ~2× the expected delta away from the
# signal), the mean ±30% bar is a HARD CEILING only. Baselines pinned on
# #1787 from the reval's per-outcome ingest_latency_ms (source + date).
BASELINE_INGEST_MEAN_MS = 1533923.64  # #1787 pin — reval ingest mean (2026-08-27)
# Cycle 4 (P1-A): the median is computed NOW from the reval report's 46
# per-outcome ingest_latency_ms values (median of the 46 non-None values,
# computed 2026-08-27) — pinned here, no placeholder.
BASELINE_INGEST_MEDIAN_MS = 1544144.405  # #1787 pin — reval ingest median (2026-08-27)
ingest = [o.get('ingest_latency_ms') for o in outcomes
          if o.get('ingest_latency_ms') is not None]
if not ingest:
    failures.append("missing gate input: ingest_latency_ms (gate 5 — ≥1 "
                    "non-None per-outcome value required; empty → FAIL, "
                    "never a mean-0 pass)")
ingest_mean = statistics.mean(ingest) if ingest else None
# (cycle 4 — P1-A: BASELINE_INGEST_MEDIAN_MS is pinned in this script — the
# median of the reval's 46 per-outcome ingest_latency_ms values — so the old
# "not pinned on #1787" failure branch is gone.)
ingest_median = statistics.median(ingest) if ingest else None

# P1-4 cost gate — token-based, SAME unit+scope as the pinned baseline.
# Path 1 ("reval per-outcome llm_calls × per-call usage") is IMPOSSIBLE —
# verified: the reval carries ZERO token/usage data (llm_calls is a bare
# int; no token_usage anywhere). The pin is re-derived on a ≥10-question
# subset re-run at the OLD cap (TORTOISE_EXTRACTOR_MAX_TOKENS=8000, still
# live via env override) with the Step 0 accumulator (Step 3), scaled to the
# full-run equivalent, and recorded with qids + cap + date on #1787. A None
# pin FAILS the gate (no silent fallback to the platform dollar figure,
# which includes reader/judge spend + cache-hit/peak-tier discounts).
BASELINE_TOTAL_TOKENS = None  # ← fill from the #1787 pin (source + date) first
fresh_total = (tu.get('completion_tokens') or 0) + (tu.get('prompt_tokens') or 0)
if BASELINE_TOTAL_TOKENS is None:
    failures.append("gate 4 cost: BASELINE_TOTAL_TOKENS not pinned on #1787 (Step 3)")
elif not (20_000_000 <= BASELINE_TOTAL_TOKENS <= 75_000_000):
    # plausible band derived from §1.4's per-call estimates (6,720 calls ×
    # ~3-11K tokens/call ≈ 20-75M full-run equivalent); a pin outside the
    # band (e.g., a 2-6M figure) is dollar-scale or per-call-scale and must
    # be corrected BEFORE the E2E starts (P1-4 pre-run check).
    failures.append(f"gate 4 cost: BASELINE_TOTAL_TOKENS={BASELINE_TOTAL_TOKENS} "
                    f"outside the plausible 20-75M band (wrong unit/scope?)")
elif fresh_total < 10_000_000:
    # P1-G (cycle 4): 0 IS an int — a usage-parsing regression that defaults
    # to {0, 0} (adapter `.get('prompt_tokens', 0)` / `.get('completion_tokens', 0)`,
    # model_adapters.py:135) passes the old >-check vacuously. A fresh_total
    # below the plausible full-run band's floor (~20-75M) is a measurement
    # failure, never a cheap run.
    failures.append(f"gate 4 cost: fresh_total={fresh_total} implausibly low "
                    f"(< 10M — usage-parsing regression / missing usage; a "
                    f"0-total must never pass the +30% budget)")
elif fresh_total > 1.3 * BASELINE_TOTAL_TOKENS:
    failures.append(f"gate 4 cost: {fresh_total} tokens "
                    f"(target ≤ 1.3 × pinned {BASELINE_TOTAL_TOKENS})")

# P2-7: outcome-count consistency — a fresh run with n_attempted=50,
# n_failed=0 and len(outcomes)=46 (silently dropped questions) passes gates
# 6/9 — this assertion fails it.
expected_outcomes = n_attempted - n_failed
if len(outcomes) != expected_outcomes:
    failures.append(f"outcome-count consistency: len(outcomes)={len(outcomes)} "
                    f"!= n_attempted − n_failed = {expected_outcomes} "
                    f"(silently dropped questions)")

# P1-7: gates 7/8 read the harness's per-call LLM census (llm_error_census —
# new Step 0 key, presence-checked) + the run-level failures[].error_class
# scan. integrity.error_census's vocabulary (ingest:retries_exhausted /
# partial_parse) NEVER contains fatal_401_auth / transient_429_rate_limit —
# those are extractor per-call classifier labels (extractor_v2.py:3831-3856)
# that do not flow into the report census; reading them there was a vacuous
# .get(..., 0) → 0 PASS on every run.
lec = need('llm_error_census', r, 'llm_error_census (Step 0 per-call LLM census)')
# P1 (resume cycle): route census — RoutingModel failover is forward-only and
# run-wide; a mid-run failover to the OpenRouter fallback must fail here, not
# pass on the configured provider/model. re-review P1: presence-checked —
# the old .get(..., 0) default was the P0-1 anti-pattern (absent key passed
# vacuously).
fallback_route = need('fallback_route_calls', lec,
                     'llm_error_census.fallback_route_calls (route census)')
if not isinstance(fallback_route, int):
    failures.append(f"missing gate input: llm_error_census.fallback_route_calls "
                    f"(int required, got {fallback_route!r})")
elif fallback_route != 0:
    failures.append(f"composition route: {fallback_route} calls routed to "
                    f"the OpenRouter fallback (must be 0 — a mid-run "
                    f"failover invalidates the deepseek-chat baseline "
                    f"comparison)")
# P2 (resume cycle): the failures[] array itself is a gate input — the old
# (r.get('failures') or []) made BOTH the per-qid check and the 401 scan
# vacuous if a schema regression drops the key (the P0-1 anti-pattern).
failures_list = need('failures', r, 'failures (run-level failure records)') or []
if lec.get('fatal_401_auth', 0) != 0:
    failures.append(f"gate 7 fatal_401_auth: {lec.get('fatal_401_auth', 0)} "
                    f"(target == 0)")
if lec.get('transient_429_rate_limit', 0) > 2:
    failures.append(f"gate 8 transient_429_rate_limit: "
                    f"{lec.get('transient_429_rate_limit', 0)} (target ≤ 2)")
# SECOND-MODEL-GATE P1 (gate 1 of 2): presence + int-type discipline for the
# FULL 9-class _classify_error vocabulary — a dropped census key must fail
# loudly, never pass vacuously (the R3 3-key loop is superseded by this; the
# fatal_402/403/4xx classes are terminal→n_failed backstopped but still
# presence-checked; the transient_5xx/network/unknown classes feed the
# gate-11 aggregate).
for _k in ('fatal_401_auth', 'fatal_402_billing', 'fatal_403_forbidden',
           'fatal_4xx', 'transient_429_rate_limit', 'transient_5xx',
           'transient_timeout', 'transient_network', 'transient_unknown'):
    _v = lec.get(_k)
    if not isinstance(_v, int):
        failures.append(f"missing gate input: llm_error_census.{_k} (int "
                        f"required, got {_v!r} — a dropped census class fails "
                        f"loudly, never passes vacuously)")
# P2-L (cycle 4): deadline-killed generation is billed but never counted by
# the accumulator (the abandoned daemon thread keeps running) — gate the
# abort count so the cost comparison's two sides (baseline subset vs fresh
# run) don't measure different loss rates; the uncounted aborted-generation
# spend (≤ deadline_aborted × max_tokens) is added to the cost margin.
deadline_aborted = need('deadline_aborted', r, 'deadline_aborted (Step 0)')
if not isinstance(deadline_aborted, int):
    failures.append(f"missing gate input: deadline_aborted (int required, "
                    f"got {deadline_aborted!r})")
elif deadline_aborted > 2:
    failures.append(f"gate 10 deadline_aborted: {deadline_aborted} "
                    f"(target ≤ 2 — aborted-generation spend is excluded "
                    f"from token_usage; bound the loss)")
# P2-L (cycle 5): RECOVERED transient_timeout events are gated NOWHERE —
# gate 6 counts TERMINAL ingest failures only, gate 8 is 429-only — so a
# recovered-timeout storm (each retried to success, costing up to ~800s +
# backoff per event at the 16K cap) passes gates 6/8/10 and is caught only
# indirectly by the latency median. Explicit ceiling, consistent with gate
# 8's ≤ 2:
if lec.get('transient_timeout', 0) > 2:
    failures.append(f"gate 11 transient_timeout (recovered): "
                    f"{lec.get('transient_timeout', 0)} (target ≤ 2 — a "
                    f"recovered-timeout storm inflates latency + cost "
                    f"without failing any other gate)")
# SECOND-MODEL-GATE P1 (gate 1 of 2): the recovered-transient ceiling is
# GENERALIZED to the full _classify_error vocabulary (extractor_v2.py:3827-
# 3856: fatal_401_auth / fatal_402_billing / fatal_403_forbidden / fatal_4xx /
# transient_429_rate_limit / transient_5xx / transient_timeout /
# transient_network / transient_unknown). Gates 8/11 covered ONLY 429 +
# timeout — a recovered 5xx/network/unknown storm (all retried to success,
# invisible to n_failed) passed every gate while inflating latency + cost.
# Aggregate ceiling: the SUM of all RECOVERED transient classes (excluding
# 429, which has its own gate 8) ≤ 2 — consistent with gates 8/11's per-class
# ≤ 2, so a mixed-class storm cannot dodge by spreading across classes.
_recovered_transient_sum = sum(
    lec.get(k, 0) for k in ('transient_timeout', 'transient_5xx',
                            'transient_network', 'transient_unknown'))
if _recovered_transient_sum > 2:
    failures.append(f"gate 11 recovered-transient aggregate: "
                    f"{_recovered_transient_sum} (timeout+5xx+network+unknown "
                    f"recovered events; target ≤ 2 — a mixed-class "
                    f"recovered-storm inflates latency + cost without "
                    f"failing gates 6/8/10)")
failure_classes = [f.get('error_class') for f in failures_list
                   if isinstance(f, dict) and f.get('error_class')]
if any(('401' in (ec or '')) or ('auth' in (ec or '').lower())
       for ec in failure_classes):
    failures.append(f"gate 7 failures[].error_class 401-class: {failure_classes}")

# P2-17 + P1-H (cycle 4): the --compare step is AUTOMATED here — the M8
# shared-qid accuracy delta + per-category McNemar significance (mcnemar
# p_value / significant_at_0_05, report.py:1885-1892) are gate inputs, not a
# manual read; a missing/read-failed baseline file FAILS the gate
# (need()-style presence). The verdict is significance-based (P1-H): the old
# < −1.0pp "Wilson-CI discipline" bar was not a significance test at n≈46.
BASE_REPORT = '.worktrees/1509-REVAL/.longmemeval_cache/runs/reval.report.json'
CMP_OUT = '.longmemeval_cache/runs/reval-1787-16k.compare.json'
shared_delta = shared_n = None  # bound so the informational print is safe
if not os.path.exists(BASE_REPORT):
    failures.append(f"missing gate input: --compare baseline file not found "
                    f"({BASE_REPORT} — must be stable/copied per Step 4 P2-9)")
else:
    res = subprocess.run(
        ['uv', 'run', 'python', '-m', 'tools.longmem_eval.run',
         '--compare', BASE_REPORT,
         '.longmemeval_cache/runs/reval-1787-16k.report.json',
         '--compare-out', CMP_OUT],
        capture_output=True, text=True)
    if res.returncode != 0:
        failures.append(f"gate --compare failed to run: {res.stderr[-500:]}")
    elif not os.path.exists(CMP_OUT):
        failures.append("missing gate input: --compare-out JSON not written")
    else:
        cmp = json.load(open(CMP_OUT))
        ov = need('overall', cmp, 'compare.overall')
        shared_n = need('shared_n', ov, 'compare.overall.shared_n')
        shared_delta = need('shared_delta_pp', ov, 'compare.overall.shared_delta_pp')
        if not isinstance(shared_n, int) or shared_n <= 0:
            failures.append(f"compare: no shared qids (shared_n={shared_n!r}) "
                            f"— the comparison is invalid")
        # P2-I (cycle 5): the per-category significant-negative loop is
        # HOISTED OUT of the overall-delta guard — it runs UNCONDITIONALLY.
        # The old nesting put it inside `if shared_delta < -1.0`, so a
        # significant negative per-category delta with an overall shared
        # delta ≥ −1.0 was never evaluated (latent contradiction with the
        # stated PRIMARY verdict).
        sig_neg = False
        for _cat, _blk in (cmp.get('per_category') or {}).items():
            _m = (_blk.get('mcnemar') or {})
            _sh = (_blk.get('shared') or {})
            # P1-H (cycle 4): the old `< −1.0pp` bar was NOT a significance
            # test — at shared_n ≈ 46-50 the shared delta quantizes to
            # ~2.0-2.2pp steps (1 net flip ≈ −2.17pp) and 1 flip is McNemar
            # p ≈ 0.5 (NOT significant). FAIL only on a STATISTICALLY
            # SIGNIFICANT NEGATIVE per-category delta
            # (mcnemar.significant_at_0_05 true AND a negative delta_pp,
            # report.py:1885-1892).
            if _m.get('significant_at_0_05') and _sh.get('delta_pp', 0) < 0:
                sig_neg = True
                failures.append(f"gate 3 (compare PRIMARY): significant "
                                f"NEGATIVE delta in {_cat} (mcnemar "
                                f"p={_m.get('p_value')}, delta "
                                f"{_sh.get('delta_pp')}pp)")
        if isinstance(shared_delta, (int, float)) and shared_delta < -1.0 \
                and not sig_neg:
            # informational only — a non-significant negative point
            # estimate is recorded on #1787, NOT a gate failure (P1-H).
            print(f"NOTE: shared-qid delta {shared_delta:+.2f}pp is "
                  f"negative but NOT significant (n={shared_n}) — "
                  f"record on #1787 as a flagged trend")
        for k in ('dataset', 'split', 'reader_model', 'judge_model',
                   'ingest_mode', 'reader_prompt_hash'):
            # P2 (resume cycle): the comparability loop is extended to the
            # fields the plan previously skipped (ingest_mode +
            # reader_prompt_hash). chunk_turns/surface are covered by the
            # composition gates above.
            v = (cmp.get('comparability') or {}).get(k)
            if isinstance(v, dict) and v.get('match') is False:
                failures.append(f"compare comparability: {k} mismatch ({v!r})")
        # R4 P1-4: reader lane — compare_reports (report.py:2024-2031) does
        # NOT emit reader_provider/reader_model_spec/reader_pinned, so reading
        # them from the compare artifact was a silent None-pass. Read them
        # presence-checked from the FRESH report's own methodology instead
        # (the reval carries them; the E2E command pins the exact spec):
        fresh_meta = need('methodology', r, 'methodology')
        if fresh_meta.get('reader_model_spec') != 'openrouter:deepseek/deepseek-v4-flash' \
                or fresh_meta.get('reader_provider') != 'openrouter':
            failures.append(f"reader lane: methodology.reader_model_spec="
                            f"{fresh_meta.get('reader_model_spec')!r} "
                            f"provider={fresh_meta.get('reader_provider')!r} "
                            f"(must be openrouter:deepseek/deepseek-v4-flash / "
                            f"openrouter — the reval's exact reader wire; a "
                            f"bare spec resolves by key priority to the "
                            f"direct API, a documented-400 wire)")
        if fresh_meta.get('reader_pinned') is not True:
            failures.append(f"reader lane: reader_pinned={fresh_meta.get('reader_pinned')!r} "
                            f"(must be True — the reval pinned its reader)")
        # SECOND-MODEL-GATE P3: evidence_boost is env -u'd in the Step 1
        # command but was NOT recorded/gated — a future code-default change or
        # a stray TORTOISE_LME_EVIDENCE_BOOST=1 that slips the env -u would
        # silently drift chunk_evidence@20 (gate 3's target). The report's
        # methodology already carries the field (run.py:901-903) — read-only
        # gate, no harness change:
        if fresh_meta.get('evidence_boost') is not False:
            failures.append(f"evidence_boost: methodology.evidence_boost="
                            f"{fresh_meta.get('evidence_boost')!r} (must be "
                            f"false — the reval's value; a stale env or code-"
                            f"default change silently shifts gate 3's "
                            f"chunk_evidence@20)")
        # SECOND-MODEL-GATE round-2 P3: methodology.workers is not gated —
        # gate 5's latency baseline is contention-sensitive under concurrency
        # and the entire §1.4 delta assumes 5-way parallelism; a drifted
        # --workers silently confounds gate 5 with no detection.
        if fresh_meta.get('workers') != 5:
            failures.append(f"workers: methodology.workers="
                            f"{fresh_meta.get('workers')!r} (must be 5 — the "
                            f"reval's parallelism; gate 5's latency baseline "
                            f"is contention-sensitive)")

# P2-2: gates 1-9 ARE the script — the exit code IS the gate verdict.
if partial_qids > 1:
    failures.append(f"gate 1 partial_parse: {partial_qids} data-loss qids (target ≤ 1)")
if llm_truncated > 1:
    failures.append(f"gate 2 llm_truncated: {llm_truncated} calls (target ≤ 1)")
if overall < 0.826:
    # R5 P1-2: accuracy one-flip band — mirrors the chunk_evidence P2-E
    # branch. The prose (Step 4 item 3 + Goal) says the --compare statistical
    # verdict is PRIMARY and "a parity re-run must not fail on ±0.0005 or a
    # single chance flip — at shared_n ≈ 46-50 one net flip ≈ −2.17pp is
    # McNemar p ≈ 0.5, NOT significant"; the old hard floor contradicted it
    # (one chance flip lands ~0.804 and failed with no sign-off path). The
    # floor below the one-flip band is HARD (a real regression always fails);
    # the band itself fails UNLESS explicit owner sign-off is recorded on
    # #1787 (Step 4 wrapper — never silently waived, matching P2-E).
    if overall >= 0.804:
        failures.append(f"gate 3 accuracy: {overall} in [0.804, 0.826) — "
                        f"within one net shared flip of the 0.8261 baseline "
                        f"(≈ −2.17pp at shared_n ≈ 46-50, McNemar p ≈ 0.5); "
                        f"passes ONLY with explicit owner sign-off recorded "
                        f"on #1787 (Step 4 escalation wrapper — never a "
                        f"silent pass; the --compare statistical verdict "
                        f"stays PRIMARY)")
    else:
        failures.append(f"gate 3 accuracy: {overall} (target ≥ 0.826 — hard "
                        f"floor; below the one-flip band is a real regression)")
if ce_mean is not None and ce_mean < 0.7375:
    failures.append(f"gate 3 chunk_evidence@20: {ce_mean} (target ≥ 0.7375 — "
                    f"issue literal ≥ 0.738; [0.7375, 0.738) = no-regression "
                    f"only, interpretation recorded on #1787 per P2-4)")
# P2-E (cycle 5): [0.7375, 0.738) is a DISTINCT conditional branch — NOT an
# unconditional pass. The issue's literal target is ≥ 0.738; a run in the
# band fails the gate UNLESS the Step 4 wrapper records explicit owner
# sign-off as an issue-comment on #1787 (the literal target is never
# silently waived).
elif ce_mean is not None and ce_mean < 0.738:
    failures.append(f"gate 3 chunk_evidence@20: {ce_mean} in [0.7375, 0.738) — "
                    f"the issue's literal target ≥ 0.738 is NOT met; passes "
                    f"ONLY with explicit owner sign-off recorded on #1787 "
                    f"(Step 4 escalation wrapper — never a silent pass)")
if ingest_median is not None \
        and ingest_median > 1.3 * BASELINE_INGEST_MEDIAN_MS:
    failures.append(f"gate 5 latency (median PRIMARY): {ingest_median:.0f} ms "
                    f"(target ≤ 1.3 × {BASELINE_INGEST_MEDIAN_MS:.0f} ms)")
if ingest_mean is not None and ingest_mean > 1.3 * BASELINE_INGEST_MEAN_MS:
    failures.append(f"gate 5 latency (mean HARD CEILING): {ingest_mean:.0f} ms "
                    f"(target ≤ 1.3 × {BASELINE_INGEST_MEAN_MS:.0f} ms)")
# P2 (resume cycle): run wall-clock is ungated — failed questions are
# excluded from the per-outcome latency median, so a terminal
# network-timeout run (n_failed ≤ 4 passes gate 6, deadline_aborted stays 0
# for transport timeouts) can balloon the ~3.9h estimate toward 5-6h while
# every numeric gate passes. Cap total elapsed (from methodology.run_at_utc
# vs the report's updated_at_utc / the checkpoint's updated_at_utc) at
# ~1.5 × the pinned 3.9h ≈ 5.9h — a ceiling, never a floor (a faster run is
# fine). The ≤4 n_failed allowance is calibrated to the 8K/600s failure
# cost; at 16K/800s each retry-exhausted failure is ~2.5× more expensive
# per attempt, so the ceiling is the honest bound.
run_elapsed = _elapsed_hours(r)
if run_elapsed is not None and run_elapsed > 5.9:
    failures.append(f"run wall-clock: {run_elapsed:.1f}h (target ≤ ~5.9h = "
                    f"1.5 × the pinned ~3.9h parallel estimate; failed-qid "
                    f"retry-exhaustion is excluded from the per-outcome "
                    f"median and must not silently stretch the run)")
if n_failed > 4:
    failures.append(f"gate 6 n_failed: {n_failed} (target ≤ 4 — the reval baseline)")
# P0-1 gate 9: N_valid = len(outcomes) (equivalently n_attempted − n_failed) —
# NOT integrity.n_valid (37 at baseline; partial_parse counts as invalid
# there). Baseline N_valid = 46 outcomes. P2-N (cycle 5): gate 9 is ALSO the
# accuracy DENOMINATOR floor — accuracy.overall is computed over the
# outcomes (judge verdicts), so a degraded mid-run judge/evidence pipeline
# that shrinks the sample fails here instead of quietly lowering the
# accuracy denominator.
if len(outcomes) < 46:
    failures.append(f"gate 9 N_valid / accuracy denominator: {len(outcomes)} "
                    f"outcomes (target ≥ 46)")

print('qids:', len(outcomes), '(n_attempted:', n_attempted, '— the 50-Q contract)')
print('partial_parse (census):', partial_census, '(informational — see gate note)')
print('partial qids:', partial_qids, '(target ≤ 1)')
print('llm_truncated calls:', llm_truncated, '(target ≤ 1)')
print('accuracy:', overall, '(target ≥ 0.826 — baseline 0.8261)')
print('chunk_evidence@20:', round(ce_mean, 4) if ce_mean is not None else 'n/a',
      '(target ≥ 0.7375 — baseline 0.7375)')
print('n_failed:', n_failed, '(target ≤ 4 — the reval baseline) | '
      'valid:', integrity.get('valid'))
print('failures[].error_class:', failure_classes or 'none',
      '(documented on #1787 before gating — P2-7)')
# P1-7 per-call ceilings (fresh stress at --workers 5 / 16K, llm_error_census):
print('fatal_401_auth (llm_error_census):', lec.get('fatal_401_auth', 0),
      '(target == 0)')
print('transient_429_rate_limit (llm_error_census):',
      lec.get('transient_429_rate_limit', 0), '(target ≤ 2)')
# P2-10 + P2-L (cycle 5): TERMINAL timeouts fold into gate 6 (n_failed —
# the reval's 4 failures were all ingest:retries_exhausted); RECOVERED
# transient_timeout events are bounded by gate 11 (≤ 2):
print('transient_timeout (llm_error_census):', lec.get('transient_timeout', 0),
      '(gate 11 target ≤ 2 — recovered-timeout ceiling)')
print('deadline_aborted:', deadline_aborted, '(target ≤ 2 — P2-L) | '
      'heartbeat:', 'present' if isinstance(hb, dict) and hb else 'MISSING',
      '(presence + non-empty — P1-D)')
print('compare overall shared delta:', shared_delta, 'pp (n =', shared_n,
      ') — significant-negative per-category deltas fail the gate (P1-H)')
print('ingest latency median:', round(ingest_median, 0) if ingest_median is not None
      else 'n/a', 'ms (PRIMARY; target ≤ 1.3 × pinned median)')
print('ingest latency mean:', round(ingest_mean, 0) if ingest_mean is not None
      else 'n/a', 'ms (HARD CEILING; target ≤ 1.3 × 1533924 ms = '
      + str(round(1.3 * BASELINE_INGEST_MEAN_MS, 0)) + ' ms)')
print('token_usage:', tu, '(target ≤ 1.3 × pinned baseline tokens)')
print('composition:', comp, '(must be deepseek-direct / deepseek-chat)')
print('db_fingerprint:', db, '→ db_fingerprint_post:', db_post)
# P2-M: per-stage S2-vs-S4 attribution (feeds the delta-contract decision,
# companion #1789) — a Step 0-required key, presence-checked:
stage = need('stage_stats', r, 'stage_stats (Step 0)')
# re-review R3 (P2): stage_stats sub-keys are presence/int-typed too — the
# #1789 decision rule defaults a missing s4_truncated to 0 and fires the
# "S4 NOT dominant" re-scope NOTE on a vacuous read; every sub-key is now
# checked like token_usage/db_fingerprint sub-keys.
for _k in ('s2_truncated', 's2_partial', 's4_truncated', 's4_partial',
           's2_output_tokens', 's4_output_tokens'):
    if not isinstance(stage.get(_k), int):
        failures.append(f"missing gate input: stage_stats.{_k} (int required, "
                        f"got {stage.get(_k)!r})")
print('S2 truncated/partial:', stage.get('s2_truncated', 'n/a'),
      '/', stage.get('s2_partial', 'n/a'))
print('S4 truncated/partial:', stage.get('s4_truncated', 'n/a'),
      '/', stage.get('s4_partial', 'n/a'))
# P2-11 decision rule (REPOINTED — SECOND-MODEL-GATE P1 gate 2): the old rule
# keyed on post-fix TRUNCATION counts (s4_truncated ≥ s2_truncated), which the
# cap raise drives to ≈ 0 — the plan's expected SUCCESS — so it would
# vacuous-fire "premise falsified → DROP #1789". The actual #1789 premise is
# the S4 RE-EMIT TAX (S4 re-emits the COMPLETE list, output ≈ 2× S2 — a
# structural cost/latency cost that survives the cap raise). Key on the
# output-token ratio instead:
if stage.get('s4_output_tokens', 0) < 1.5 * stage.get('s2_output_tokens', 0):
    print('NOTE: stage_stats premise check — S4 re-emit tax NOT dominant '
          '(s4_output_tokens < 1.5 × s2_output_tokens) post-fix; re-scope or '
          'drop companion #1789 (Task 5 Step 6)')
if failures:
    print('\nGATE FAIL — ' + '; '.join(failures))
    sys.exit(1)
PY
```

**Step 3: Cost check** — the report/checkpoint persist only per-outcome `llm_calls`, not token spend, so a +30% gate is not measurable from run artifacts today. **Mechanism (P1-3 — full spec in Step 0 item 2; the restatement is collapsed to a pointer, cycle 5 — P2-F):** the in-adapter ACCUMULATOR with lock-protected per-call increments (INCLUDING retried calls), read into `report['token_usage']` at teardown — do NOT sum `last_completion_tokens`/`last_prompt_tokens` (the adapter OVERWRITES them per call, model_adapters.py:134-135, on the one shared instance across worker threads); unit-tested in Step 0 group (a) (fake adapter + real adapter with mocked transport, P1-9). **Fail if `report['token_usage']` is missing** (no silent fallback to the estimate).
**Unit/scope parity (P1-4):** the baseline and the fresh run must be the SAME unit and scope. The DeepSeek platform usage API reports DOLLARS — including cache-hit discounts ($0.014/M input) and peak/off-peak tiers that swing ~2× — and it includes the reader `deepseek/deepseek-v4-flash` + judge `openai/gpt-4o` spend that the extractor-adapter aggregation does NOT capture; comparing fresh TOKENS ≤ 1.3 × pinned platform DOLLARS is unit- and scope-mismatched (identical consumption can differ >30% across price tiers). **How the pin is derived (cycle 3 — P1-4 + cycle 4 — P1-A/P2-N):** Path 1 ("reval per-outcome `llm_calls` × recorded per-call usage") is IMPOSSIBLE — verified: the reval carries ZERO token/usage data (`llm_calls` is a bare int; no `token_usage` anywhere in the report). **The pin is re-derived on a ≥10-question SUBSET re-run** (same split=s list, identical composition: `--workers 5`, `TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct`, no `--extractor-model`, reader/judge pins as Step 1) **with `TORTOISE_EXTRACTOR_MAX_TOKENS=8000`** — the OLD cap, still live via the env override — so the subset measures BASELINE token consumption even though Task 2 already flipped the default (without the pin, the subset would measure the NEW cap and the "baseline" would be circular). The subset runs with the Step 0 accumulator; **record the subset qids + cap + date on #1787**, and scale the subset total to the full-run equivalent (`subset_total / len(subset_qids) × 46`) for `BASELINE_TOTAL_TOKENS`. **×46 denominator documented (R4 P2-6 — the second-model gate's P3 nit 1, closed here):** the fresh run's accumulator counts ALL 50 attempted questions (workers make LLM calls even on failing questions) while the reval published 46 outcomes (4 network-timeout failures ingested nothing extractor-side but the token scale is dominated by the 46 successful questions); `×46` is therefore the CONSERVATIVE choice — **SECOND-MODEL-GATE P2 (bias direction CORRECTED): the fresh run's accumulator counts ALL 50 attempted questions while ×46 anchors the baseline to the 46 successful outcomes, so the baseline is SMALLER relative to the fresh run's full-attempt scope → `fresh_total / baseline` is LARGER → the +30% gate is slightly HARDER (fail-safe), not easier as the R4 P2-6 note originally claimed** — and either denominator is within the band; the subset qids + cap + date are recorded on #1787 so the choice is auditable. **Cost-gate proportionality (SECOND-MODEL-GATE P2):** the subset machinery is disproportionate to the 0.4-0.8% effect it measures (§1.4: ≤120K tokens ≈ $0.08-0.16, 40-75× inside the +30% budget) — the 20-75M plausibility band independently validates the ~48M estimate — so the subset re-run is DOWNGRADED to advisory: the pin may be the §1.4-derived ~48M estimate validated by the band (which the gate already enforces), and the full tercile-subset + freshness/cap contract runs only if the operator wants a measured pin; the accumulator + `token_usage` reporting is kept (the issue demands a cost figure).

**Sequencing + isolation (cycle 4 — P1-A (b)/(d), P2-N):** the subset re-run is sequenced **BETWEEN Step 0 and Step 1** — it needs only Step 0's accumulator, and it MUST run BEFORE the full E2E so the pin exists when the Step 2 gate reads it (the gate hard-fails on a None pin). **The subset runs on its OWN DB URI + checkpoint** — `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_e2e_baseline'` (+ the same `--db`), `--checkpoint .longmemeval_cache/runs/reval-1787-baseline-8k.checkpoint.json`, `--output .../reval-1787-baseline-8k.report.json` — so it CANNOT dirty the E2E's `db_fingerprint.entities/total_nodes == 0` pre-check (dedicated `tortoise_test_e2e_1787`) or collide with the E2E checkpoint freshness contract (`reval-1787-16k.*`). **Representativeness (cycle 4 — P2-N + cycle 5 — P2-C — now EXECUTABLE):** per-question token consumption varies with session density — the issue's own core mechanism — so a random subset's per-question mean can miss the density distribution. **Mechanism (cycle 5 — P2-C):** the harness currently has NO qid-subset filter (only `--limit N` prefix and `--spot-check` full-set exist), so the tercile-selection preferred path needs a **small harness change**: a new `--qids` flag (explicit comma-separated qid list; or `--from-checkpoint-qids FILE` reading the reval checkpoint's qids, with a documented seed for reproducibility) that restricts the run to exactly the selected qids. If the flag is not landed, fallback (b) is PROMOTED to primary: record the per-question token min/max spread WITH the pin (under `--limit`), so the `×46` extrapolation error is visible. **Subset command (verbatim — own DB URI + checkpoint, same composition as Step 1):**
```bash
cd /Users/danielospina/Documents/GitHub/tortoise
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_e2e_baseline' \
TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct \
TORTOISE_EXTRACTOR_MAX_TOKENS=8000 \
TORTOISE_EXTRACTOR_NO_FALLBACK=1 \
TORTOISE_LME_READER_MODEL='openrouter:deepseek/deepseek-v4-flash' \
TORTOISE_LME_JUDGE_MODEL='openai/gpt-4o-2024-08-06' \
  uv run python -m tools.longmem_eval.run \
  --split s --qids <tercile-selected qids from the reval llm_calls terciles> \
  --ingest-mode v2 --workers 5 --chunk-turns 1 --db-flush \
  --reader-model openrouter:deepseek/deepseek-v4-flash \
  --judge-model openai/gpt-4o-2024-08-06 \
  --db 'docker://:falkordb@localhost:6379/tortoise_test_e2e_baseline' \
  --checkpoint .longmemeval_cache/runs/reval-1787-baseline-8k.checkpoint.json \
  --output .longmemeval_cache/runs/reval-1787-baseline-8k.report.json
```
**Subset freshness + health contract (resume-cycle P1 + re-review P2 + R3 P1 — the pin's own source run was unguarded):** before the pin is scaled, the subset report is checked — `resumed == False`, `skipped_qids == []`, `n_attempted == len(subset_qids)` (a crash-resumed or flaky subset sums fewer than the requested qids and the `×46` scale silently under-pins), `n_failed == 0` (exclude/record failed qids explicitly; scale by the actual contributing count, not the nominal one), **`len(outcomes) == n_attempted − n_failed` (re-review P2 — the subset contract now includes the outcome-count consistency check the full-run gate has: a subset that marks all requested qids "done" while silently dropping outcomes (P2-7's pattern) under-pins `BASELINE_TOTAL_TOKENS` and inflates the +30% cost allowance; negative test: a synthetic subset report dropping 2 outcomes → pin rejected/re-derived), AND `composition.effective_stage_cap == 8000` (re-review R3 P1 — the pin's validity depends on the subset having run at the OLD cap; a stale/absent `TORTOISE_EXTRACTOR_MAX_TOKENS` (or the E2E-lane `env -u` leaking) silently measures the subset at 16000 → `BASELINE_TOTAL_TOKENS` is pinned on the NEW cap → the +30% gate compares like-with-like and the cap-raise delta is never measured — the exact circular-pin failure Step 3's prose warns about; presence-checked, negative test: subset report with cap 16000 → pin rejected)**. A subset that fails any of these re-runs before its pin is written. **S1-cap asymmetry (resume-cycle P2 + R4 P2-5):** the env override raises BOTH stage caps in the subset (S1 1500→8000), while the fresh run's S1 stays 1500 — the pin's S1 scope is therefore slightly inflated vs the fresh run. Direction is LENIENT (baseline inflated → the +30% gate is EASIER, not harder — an inflated baseline enlarges the allowed budget; the second-model gate's ×46 note calls the opposite bias conservative) and §1.4 puts S1 output at ~1K, so impact is small — but it is RECORDED on #1787 with the pin (P1-4's "same unit and scope" claim is scoped to S2/S4, the truncation surface).
A linear `×46` extrapolation from a density-skewed subset is otherwise silently wrong.

**Edit the Step 2 script with the pinned value (cycle 4 — P1-A (c)):** after the subset re-run produces the number, EDIT the Step 2 heredoc — replace `BASELINE_TOTAL_TOKENS = None` with `BASELINE_TOTAL_TOKENS = <pinned>` (source + date in the comment) — and re-run the gate script's pin pre-check (the band check: 20M-75M) against a stub report BEFORE starting the full E2E. The Step 2 gate is not runnable until both pins (`BASELINE_TOTAL_TOKENS` + the pinned `BASELINE_INGEST_MEDIAN_MS`) are in the script. **Pre-run gate check (in the Step 2 script):** the pin must exist AND fall inside the plausible band derived from §1.4's per-call estimates (~20-75M tokens for the ~6,720-call full run; a 2-6M figure is dollar-scale/per-call-scale and fails) — a missing or out-of-band pin blocks the E2E BEFORE it starts. If dollars are ever compared, convert BOTH sides with the same price sheet/tier/cache-hit assumption and record the sheet + tier + date alongside the pin. Scope the aggregation to the same model set as the pinned figure (the extractor adapter's deepseek calls). The DeepSeek platform usage API is a cross-check only. (~$20-40 baseline; cap-raise delta ≈ +$0.08-0.16.)

**Step 4: Gate** — the five numeric targets + per-class failure ceilings + documented-failure check:
1. `partial_parse` (data-loss qids) ≤ 1 — the issue's exact target. The census is reported separately (informational, tied to the `llm_truncated` gate): the 9-vs-12 accounting (3 lossless-recovered truncations) is covered by `llm_truncated ≤ 1`, so a single question with 2 bumps (S2+S4 both partial) still PASSES this gate;
2. `llm_truncated` calls ≤ 1;
3. accuracy ≥ 0.826, chunk_evidence@20 ≥ 0.7375 — **no-regression thresholds set to the MEASURED baselines (accuracy 0.8261, chunk_evidence@20 0.7375): both targets sit at/below their measured baselines, so the verdict uses the `--compare` STATISTICAL test (per-category McNemar significance on the shared qids — AUTOMATED in the Step 2 script — cycle 3, P2-17 + cycle 4, P1-H) as PRIMARY with the point-estimate targets as SECONDARY** (a parity re-run must not fail on a single chance flip — at shared_n ≈ 46-50 one net flip ≈ −2.17pp is McNemar p ≈ 0.5, NOT significant — so the script's one-flip bands below the floors require OWNER SIGN-OFF, never a silent pass; R6 P2: the parenthetical now matches the script's actual [0.804, 0.826) accuracy band + [0.7375, 0.738) chunk_evidence band behavior — both exit 1 unless the Step 4 wrapper records explicit sign-off on #1787). **Interpretation (cycle 3 — P2-4 + R6 P2):** the issue's literal chunk_evidence@20 acceptance is **≥ 0.738** — ABOVE the measured baseline (0.7375). A run landing in **[0.7375, 0.738)** FAILS the gate UNLESS the owner's explicit sign-off is recorded as an issue-comment on #1787 (cycle 5 — P2-E made it a DISTINCT conditional branch — the literal ≥ 0.738 target is never silently waived); that interpretation is recorded on #1787 (not absorbed silently into the gate — the `--compare` verdict stays primary and the literal-vs-baseline gap is documented on the issue rather than hidden);
4. cost ≤ +30% (Step 3);
5. ingest-latency delta vs baseline ≤ +30% (P2-3 + cycle 3 — P2-8: gate quantity = MEDIAN per-outcome `ingest_latency_ms` vs the pinned baseline median — PRIMARY, robust to the LLM-tail variance (reval mean 1,533,924 ms vs p95 ~1.68M ms — the mean is skewed, so the old mean-only bar sat ~2× the expected delta away from the signal); the MEAN vs the pinned mean stays as a HARD CEILING only). Both baselines (mean `latency_ms.ingest.mean_ms` = 1,533,923.64 ms ≈ 25.6 min/question + median **1,544,144.405 ms** — the median is computed from the reval's 46 per-outcome `ingest_latency_ms` values and pinned IN the Step 2 script, cycle 4 — P1-A) are recorded on #1787 with source + date alongside the cost pin; computed in the Step 2 script. The cap raise adds ≈ +9-17% wall-clock under 5-way parallelism — upper bound (the E2E mirrors the reval's `--workers 5`, so this compares like-for-like); the old +10% bar used the sequential denominator and would false-fail).
Plus **failure-class ceilings (P1-E)** — without them the "documented-failure justification" is unbounded and 5-10 dropped questions could still PASS on degraded N:
6. `n_failed` ≤ 4 (the reval baseline — 4 network timeouts);
7. `fatal_401_auth` == 0 (a mid-run key rotation dropping questions is a FAIL, not a justification) — read from the Step 0 `llm_error_census` key + the `failures[].error_class` scan; the report's `integrity.error_census` NEVER contains `fatal_401_auth` (its vocabulary is `ingest:retries_exhausted` / `partial_parse`) — cycle 3, P1-7;
8. `transient_429_rate_limit` ≤ 2 (fresh stress signature at `--workers 5` / 16K longer calls — surfaced early by the Step 1 SUSTAINED pre-flight) — same `llm_error_census` source (cycle 3, P1-7);
9. `N_valid` = `len(outcomes)` (equivalently `n_attempted` − `n_failed`) ≥ 46 — the reval's OUTCOME count. **`integrity.n_valid` (37 at baseline; `partial_parse` counts as invalid there) is NOT this quantity — do not gate on it.** A degraded N cannot pass on fewer questions.
10. `deadline_aborted` ≤ 2 (cycle 4 — P2-L): deadline-killed generation is billed by the provider but never counted by the accumulator (the abandoned daemon thread keeps running) — gate the abort count so the cost comparison's two sides (baseline subset vs fresh run) don't measure different loss rates; the uncounted aborted-generation spend (≤ `deadline_aborted × max_tokens`) is added to the cost margin.
11. `transient_timeout` (RECOVERED events) ≤ 2 (cycle 5 — P2-L + SECOND-MODEL-GATE P1: the gate-11 ceiling is GENERALIZED to the aggregate of ALL recovered transient classes — timeout+5xx+network+unknown ≤ 2 — a mixed-class recovered-storm cannot dodge by spreading across classes; the sibling presence check covers the full 9-class `_classify_error` vocabulary);
12. `n_truncated_valid` == 0 (R5/R6 — C3, the issue's indicator 2; **SECOND-MODEL-GATE P3: this is DELIBERATELY STRICTER than the issue's literal indicator-2 target ("llm_truncated ≤ 1" — a single lossless-recovered truncation passes gate 2 but fails gate 12; `n_truncated_valid` qids ARE recorded in `truncated_valid_qids`, so "unrecorded" is a semantic stretch — the tightening is recorded on #1787 in the Step 4 sign-off comment, never silent)**).
**`transient_timeout` is folded into the `n_failed` ceiling (gate 6) — the reval's 4 failures were all `ingest:retries_exhausted` network timeouts — and the deadline breach itself is closed by the `_complete` deadline-scaling fix (Task 2 Step 6). RECOVERED `transient_timeout` events (retried to success) are separately bounded: gate 11 in the Step 2 script (≤ 2 — cycle 5, P2-L; a recovered-timeout storm inflates latency + cost without failing gates 6/8/10).**

Plus (cycle 3 — P2-7) **outcome-count consistency + documented-failure mechanism**: the Step 2 script asserts `len(outcomes) == n_attempted − n_failed` — a fresh run can have n_attempted=50, n_failed=0 and still silently drop questions (len(outcomes)=46) while passing gates 6/9; the assertion fails that. **Documented-failure mechanism (defined, not hoped):** BEFORE gating, every entry in `failures[]` gets its `error_class` + a one-line justification recorded on #1787 (the Step 2 script prints the `error_class` list for transcription); a failure without a recorded justification is treated as a gate failure.

Plus (cycle 3 — P2-14 + cycle 4 — P1-E/P2-R) **post-run DB state**: the Step 2 script requires `db_fingerprint_post` (Step 0 teardown count) — presence-checked AND int-typed (`entities` + `total_nodes` — P2-R), `post.entities ≥ pre.entities` AND `post.total_nodes ≥ pre.total_nodes` (no mid-run reset), and the failed-qid partial-write check via the Step 0 `per_qid_written_entities` provenance (cycle 4 — P1-E; produced by the post-run DB query over the failed qids' `question_graph_namespace` graphs — the single grounded source, cycle 5 — P1-A): every qid in `failures[].question_id` must have written 0 nodes (a question failing mid-ingest — S1 wrote entities, or partial-accept — leaves partial data that pollutes retrieval for subsequent questions; the reval had 4 such failures and gate 6 allows ≤4 again, so each allowed failure must provably have written nothing — the aggregate pre/post fingerprints alone cannot detect a few stray entities from one failed qid among thousands from successful qids).

Plus (cycle 3 — P2-17 + cycle 4 — P1-H) **automated `--compare`**: the Step 2 script runs `--compare` programmatically (baseline `.worktrees/1509-REVAL/.longmemeval_cache/runs/reval.report.json` + `--compare-out`) and FAILS on: a missing/read-failed baseline file, `shared_n ≤ 0` (no shared qids → comparison invalid), a **statistically significant NEGATIVE per-category delta** (McNemar `significant_at_0_05` true with a negative `delta_pp` — the cycle-3 `< −1.0pp` shared-delta bar was NOT a significance test: at shared_n ≈ 46-50 the delta quantizes to ~2.0-2.2pp steps and 1 net flip ≈ −2.17pp is McNemar p ≈ 0.5, so a parity re-run with one chance flip must not fail the PRIMARY gate), or a reader/judge/split/dataset comparability mismatch.

**Comparability:** the `--compare` step is AUTOMATED into the Step 2 gate script (cycle 3 — P2-17 + cycle 4 — P1-H): it runs `uv run python -m tools.longmem_eval.run --compare .worktrees/1509-REVAL/.longmemeval_cache/runs/reval.report.json .longmemeval_cache/runs/reval-1787-16k.report.json --compare-out .longmemeval_cache/runs/reval-1787-16k.compare.json` (M8) and consumes the per-category McNemar significance (mcnemar `p_value` / `significant_at_0_05` + shared `delta_pp` per category, report.py:1885-1892) and the comparability block as gate inputs (missing baseline file / no shared qids / significant NEGATIVE delta / comparability mismatch → FAIL); the overall `shared_delta_pp` is printed as informational (a non-significant negative point estimate is recorded on #1787 as a flagged trend, not a gate failure). The harness surfaces the git_sha fingerprint delta (expected on a new commit) as a documented caveat. **P2-9: the reval report lives (read-only) in `.worktrees/1509-REVAL/.longmemeval_cache/runs/` — it must stay stable across the ~4h run, or be copied to `.longmemeval_cache/runs/reval.report.json` first.**

**Integrity note:** the reval is `integrity.valid=false` (4 network timeouts + partial_parse under `--integrity-threshold 0.0`), and the issue's own ≤1-partial target means a clean fix still reports `valid=false`. Gate on the five targets + a documented network-failure justification, NOT the binary `valid` flag.

If any target fails → stop, report, escalate to option D/E (continuation/delta contract). If clean → close the issue with the numbers.

**Step 5: Commit the report reference** (if the report is committed per convention) or comment the numbers on #1787.

**Step 6: Re-scope/close the EXISTING #1789 per stage_stats (cycle 3 — P1-6 + FINAL-VERIFICATION P1 — #1789 is ALREADY FILED and OPEN (`fix(extraction): S4 re-emit tax — switch to delta/gaps-only contract`, created 2026-08-27); do NOT create a duplicate):** the S4 delta-contract companion issue (option E). **Proportionality note (FINAL-VERIFICATION P2):** the per-stage output-token delta-accounting machinery (`s2_output_tokens`/`s4_output_tokens` + the per-stage delta mechanism + interleaving unit test + gate presence checks + the ≥1.5× rule) is the single largest item of harness growth driven by a COMPANION-issue filing decision — the #1789 premise (S4 re-emit tax) is structural and pre-verified; an implementer MAY substitute the lighter post-hoc read in this step (query the run report's per-stage completion totals — or the Step 0 accumulator split by stage at teardown — instead of the in-call delta hook) WITHOUT weakening the decision; the gate's stage_stats presence check for the two keys is retained as the contract either mechanism must satisfy. The filing moved here from Task 4 Step 2 (its input — Task 5's `stage_stats` — does not exist until the E2E runs; the old Task-4-parallel-with-Task-2 dependency contradicted the filing's own conditional). **Decision rule (SECOND-MODEL-GATE P1 gate 2 — the old P2-11 rule keyed on post-fix TRUNCATION counts, which the cap raise drives to ≈ 0 — the EXPECTED success — so a truncation-keyed rule systematically fires "premise FALSIFIED → DROP" on a vacuous signal):** keep #1789 AS-IS if `stage_stats` shows the S4 RE-EMIT TAX persists — `s4_output_tokens ≥ 1.5 × s2_output_tokens` (the structural cost/latency tax that survives the cap raise: S4 re-emits the COMPLETE list, output ≈ 2× S2, independent of truncation). If the ratio is < 1.5, the "S4 re-emit dominant" premise is falsified — re-scope or CLOSE the existing #1789. The Step 2 script prints the premise-check NOTE from the output-token ratio (s2_output_tokens/s4_output_tokens); record the decision + the stage_stats numbers on #1787. (The old truncation-keyed rule is superseded — truncation ≈ 0 post-fix is the plan's success condition, not a falsification signal; §1.6 option E's value proposition (−30% cost, −20-30% latency) is structural and independent of truncation.) **Only create the issue if it was dropped earlier (closed without resolution) — never a blind duplicate.**

```bash
gh issue create \
  --title "refactor(extraction): S4 delta contract — emit gaps only (kill the re-emit tax)" \
  --body "S4 re-emits the COMPLETE embed list (output ≈ 2× S2) — the dominant truncation surface (1746 plan '7-8 vs 6 failure skew'). E4 union machinery exists. Companion to #1787 option E. Filed per #1787 Task 5 Step 6: stage_stats after the cap-raise E2E = s2_truncated X / s4_truncated Y."
```

---

## Open owner decisions

1. **16K vs a higher default (e.g., 24K/32K):** 16K covers all observed truncations (15/6720 calls, none plausibly >16K given ~60-point sessions); a higher default adds no protection for observed data and only widens the worst-case latency/cost tail. Decision: accept 16K unless Task 1 shows the alias clamps below it.
2. **Companion issue order — PINNED (do not land #1790 before Task 5):** the `deepseek-chat` alias migration is a soft dependency for the E2E (if the alias dies mid-run, the E2E fails for the wrong reason), BUT Task 5 Step 1 mirrors the reval invocation EXACTLY on the `deepseek-chat` wire id. If #1790 lands before Task 5, the E2E composition changes and the accuracy/chunk_evidence gates (anchored to the deepseek-chat reval baseline) become invalid comparisons. **Decision: Task 5's E2E must run BEFORE the alias migration lands (migration strictly after), so the cap-only effect is measured on the same wire id.** If the migration must land first (alias death mid-plan — or the Task 1 probe's `accepted-only-on-v4` outcome, P2-4: the alias then cannot carry the 16K cap, so the migration lands first by necessity), the baseline must be re-derived on the new wire id — or the confound explicitly accepted and recorded on #1787 before gating.
3. **The 3 `truncated_valid` qids (C3 leak):** recovered losslessly by ladder rung 1/3 (trailing-whitespace truncation) — no data loss, but `n_truncated_valid=3` means #1746's C3 "0 unrecorded truncations" is technically open. With the cap raise, expect 0; if >0 post-E2E, file the census-pedantry follow-up. **Reconciled with gate 12 (FINAL-VERIFICATION P3):** gate 12 HARD-FAILS on `n_truncated_valid > 0` (exit 1) — this decision's "file follow-up" language is superseded by the gate: >0 is a hard FAIL whose sign-off path is the Step 4 wrapper's owner-sign-off note PLUS the follow-up filing; the two documents now describe the same outcome (fail → sign-off → follow-up).
4. **Whether 16K becomes a config value** (e.g., `TORTOISE_EXTRACTOR_S2_S4_MAX_TOKENS` split override) rather than a constant + blanket override. Recommend keeping the single blanket override (D6 protocol) — a per-stage override is YAGNI at this rate.

## Review gate status

- **problem-verify:** evidence-based converge (reval artifacts + official DeepSeek docs); falsification defined (Task 1 probe — forces >8K generation so a server clamp is detectable); alternatives documented (F2/F3/F4 rejected with code evidence).
- **solution-verify:** approaches A-E genuinely distinct (mechanism vs cost vs contract); A chosen on outcome quality (fixes root mechanism, ~40-75× inside cost budget, minimal coupling), not diff size.
- **plan-review (resume cycle — post-interruption convergence check):** 3 fresh-context reviewers (structural-efficiency, integration, failure-mode) found 2 P0 + 5 P1 + ~13 P2, ALL fixed in this cycle — **P0-A `chunk_turns` drift** (reval ran `chunk_turns=1`, code default 2, E2E command omitted the flag → gate-3 `chunk_evidence@20` baseline invalidated; fixed: `--chunk-turns 1` + `composition.chunk_turns` gate + negative test), **P0-B stale `TORTOISE_EXTRACTOR_MAX_TOKENS` env** (a stale override silently beats the constant at call time with no detection; fixed: `env -u` in the E2E command + `composition.effective_stage_cap` gate == 16000 + pre-flight abort), **P1-a `--qids` untracked** (flagged by the plan's own second-model gate as an open P2, still absent from the Files/acceptance/commit surface → now a first-class Step 0 deliverable with unit test; verbatim subset command runnable), **P1-b `--db-flush` absent from the literal command** (mandated-but-unshipped flag + the `--limit 3` pilot pollutes the dedicated DB → now in the command + Step 0), **P1-c mid-run RoutingModel failover** (forward-only run-wide failover to the OpenRouter fallback silently passes the composition gate and under-counts the accumulator → pre-flight abort on OPENROUTER_API_KEY + `fallback_route_calls` census gate + accumulator read path via `extractor_model.primary`), **P1-d per_qid absent-key silent pass** (`pqwe.get(_qid, 0)` → key presence required), **P1-e subset pin health unguarded** (subset freshness + n_failed==0 contract before pinning) — plus the P2s (vacuous threaded-test 8000 branch, live probes re-executed in Part B, deadline-scaling sentinel signature, comparability loop extension, run wall-clock ceiling, heartbeat non-401 semantics, probe robustness notes, chunk_evidence paired-read note, ingest_v2.py in Files/commit, S1-cap-asymmetry note, pilot checkpoint hygiene). The plan's earlier "second-model CLEAN" claim was for the measurement protocol the resume cycle proved incomplete — this cycle's fixes close that layer. Previous cycles 1-5 summaries retained below.
- **plan-review (cycle 1):** 2 fresh-context reviewers — all P0s fixed (E2E flags: `--run-key` → `--checkpoint/--output`, `--extractor-model deepseek-chat` → omit, `--db` added for hnsw-surface comparability; cap pin `[1500,8000,8000]` → `[1500,16000,16000]`; probe made non-vacuous; gate reads the correct chunk_evidence field). P1/P2 incorporated: 9-vs-12 accounting, integrity.valid note, latency/cost corrections, stale 8000 mirrors, Option B cost relabeled unverified, OpenRouter probe note.
- **plan-review (cycle 4):** 26 issues fixed (8 P1, 18 P2) — probe calibration
  re-anchored to the MEASURED 9,406-token/4700-repeat reality + deadline-bounded
  (P1-B/C/D, P2-E); gate-script pin ordering fixed — median pinned NOW
  (1,544,144.405 ms) + subset re-run sequenced between Step 0 and Step 1 with
  DB/checkpoint isolation (P1-A, P2-N); `--compare` verdict switched to McNemar
  significance (P1-H); dead `stage_cap_override` branch replaced (P2-A); census
  counts recovered events (P1-F); cost gate lower-bounded (P1-G); deadline
  multiplier raised 0.04→0.05 with boundary/exhaustion tests (P2-K); failed-qid
  provenance + `deadline_aborted` + `total_nodes` fingerprints + pre-run aborts +
  mandatory heartbeat + `--limit 3` pilot (P1-E, P2-L/R/M/O/P); three changelog
  tables consolidated into one (P2-D) — see the cycle-4 CHANGELOG.
- **plan-review (cycle 3):** 29 issues fixed (1 P0, 11 P1, 17 P2) — presence discipline extended to EVERY gate input (P0-1); probe hardened (P1-1/2/3); Task 4 split with #1789 moved to Task 5 Step 6 (P1-6); Task 3 deleted (P2-2); gates 7/8 re-pointed at the new `llm_error_census` + `failures[].error_class` (P1-7); deadline-scaling wiring test + helper fixes (P1-8/P2-9); real-adapter accumulator / fingerprint / stage_stats threading tests (P1-9/10, P2-6/10/11); run-composition gate (P1-11); reader/judge pins (P2-1); run_key citation corrected (P2-3); sustained pre-flight + 429×deadline test + key-read/401 tests (P2-15/16); `--compare` automated into the gate (P2-17) — see the cycle-3 CHANGELOG.
- **plan-review (cycle 5):** 21 issues fixed (7 P1, 14 P2 — FINAL cycle) — per_qid source DECIDED to the single grounded option (post-run DB query on the failed qids' `question_graph_namespace` graphs, P1-A); probe echo-fidelity assert + taxonomy entry (P1-B); dense-S4 fixture at the new cap (P1-C); heartbeat presence-gated for real (P1-D); `deadline_aborts` counting seam + lock + tests (P1-E); invalid-override regression test (P1-F); full-length 16K pre-flight probe (P1-G); cycle-1 severity counts corrected (P2-A); tiktoken BPE first-use fetch (P2-B); `--qids` subset mechanism + verbatim subset command (P2-C); tier RE-RATED to complex — FINAL (P2-D); [0.7375, 0.738) conditional sign-off branch (P2-E); harness-mechanism dedup (P2-F); epic citation (P2-G); deadline math parenthetical (P2-H); compare-loop hoist (P2-I); probe envelope bump (P2-J); `_conv` import (P2-K); gate 11 recovered-timeout ceiling (P2-L); per-question-graph fingerprint aggregation (P2-M); chunk_evidence/accuracy sample-size floors (P2-N) — see the cycle-5 CHANGELOG.

---
## CHANGELOG (consolidated — cycles 1-5; cycle-3 P2-D fold: the three tables are folded
into ONE summary table with cycle attribution — every cycle-1/2/3/4 row preserved verbatim)

| Cycle | # | Issue | Severity | Location | Fix Applied | Research? |
|---|---|---|---|---|---|---|
| 1 | 1 | P0-A | P0 | Task 2 Step 1 | Replaced `out["error_census"].get("partial_parse", 0) == 0` (KeyError — `run_s2` returns the raw parsed dict) with `stats.get("partial") is not True` on the stats dict passed into `run_s2`; implementer note updated (census bump lives in stage callers extractor_v2.py:3485-3488/3546-3549, keyed on `stats["partial"]`; census-class assertion covered at `extract_session_v2`/Task 3) | In-repo verified |
| 1 | 2 | P0-B | P0 | Task 2 Step 1 | Replaced the pre-last-key cut (kept all 60 points → `60 < 60` never holds) with a genuine mid-points-array cut at point k=40's boundary (depth-walk over the serialized points array); asserts `len(out2["points"]) == 40 < 60` AND `stats2.get("partial") is True`; comment corrected | In-repo verified (rung-1 tail-cut / rung-3 repair / rung-4 `_longest_valid_prefix` behavior read in code) |
| 1 | 3 | P0-C | P0 | Task 1 Steps 1-2 | Filler `'"word ' * 3000` → `* 6000` (36K chars ≈ 12-18K tokens, >2× margin); prompt now echo-inside-top-level-object (json_object-mode compatible, "json" retained); asserts `last_completion_tokens > 8192` AND `last_finish_reason != "length"`; cost comment → ~$0.01-0.02; Task 2 precondition added (probe outcome must be recorded on #1787 before the constant change) | In-repo verified (`_should_send_json_mode` gate, `last_*` attrs); DeepSeek json-object requirement noted, fix safe regardless |
| 1 | 4 | P1-D | P1 | Open owner decision 2 | Pinned sequencing: Task 5 E2E must run BEFORE the #1790 alias migration (cap-only effect on the same wire id); if migration lands first, baseline must be re-derived / confound explicitly accepted | In-repo verified (wire-id mirror in Task 5 Step 1) |
| 1 | 5 | P1-E | P1 | Task 5 Step 4 + Step 1 | Added failure-class ceilings (n_failed ≤ 4, fatal_401_auth == 0, transient_429_rate_limit ≤ 2, N_valid ≥ 46) to the gate; added 8-concurrent-call 16K pre-flight before the full E2E | No |
| 1 | 6 | P1-F | P1 | Task 5 Steps 1-2 | Enforced run-identity freshness: new checkpoint path per gate run, no resume; report must record a freshness marker; Step 2 gate FAILs on resume/skip markers or qid count < 50 | No |
| 1 | 7 | P1-G | P1 | Task 5 Step 3 | Added harness-side token aggregation (sum per-instance `last_completion_tokens`/`last_prompt_tokens` at teardown into `report['token_usage']`); gate FAILs if aggregation field missing (usage API = cross-check only); baseline usage pinned NOW (one number, source+date) | No |
| 1 | 8 | P2-H | P2 | Task 2 Step 5 | Decided: `probe_json_mode.py:57` `_MAX_TOKENS` → 16000 (consistent with its documented "mirror the S2/S4 stage cap" purpose) | In-repo verified (probe_json_mode.py:57 comment) |
| 1 | 9 | P2-I | P2 | Task 1 Step 3 | Replaced the `resp == "" or resp` tautology: on empty, assert `last_finish_reason == "length"` (the collapse signature) before xfailing; non-empty branch notes the `{"thinking":{"type":"disabled"}}` transmission assert for the companion migration | No |
| 1 | 10 | P2-J | P2 | §1.4, §1.6, §1.7, Failure Modes, Task 5 gate 5 | Reconciled latency figures to the parallel denominator: ≈ +14-27% of ~2.5h wall-clock under 8-way parallelism (upper bound, ~15 calls overlapping across 8 workers); Task 5 gate 5 → ≤ +30% (old +10% would false-fail) | No |
| 1 | 11 | P2-K | P2 | Integration Surface Map | Added rows for the three doc mirrors (sdk.py:2301, probe_json_mode.py:57, test_extractor_reliability.py docstring+pin, layer docs/unit-pin); extended the probe row's failure modes with `_should_send_json_mode`/json_object behavior + `TORTOISE_JSON_MODE=0` escape hatch; added the not-in-scope 8000s note (model_adapters.py:244/246, test_sdk_adapter_cap.py, DEFAULT_CONTEXT_TOKEN_CAP, _SOURCE_TRANSCRIPT_CAP) | In-repo verified (all cited locations) |
| 1 | 12 | P2-L | P2 | Task 3 | Folded `test_stage_cap_override_above_default` into Task 2 Step 5 (independent of the constant change — `_stage_cap` reads env at call time); Task 3 is now a verification-only checkpoint (re-review Task 2 Step 6 results, byte-identity diff, no separate commit); §1.9 wiring rows updated to match | In-repo verified (`_stage_cap` implementation) |
| 1 | 13 | P2-M | P2 | Task 5 Steps 1-2 | Added per-stage S2-vs-S4 truncation/partial counters (small `extract_session_v2` stats hook → `report['stage_stats']`) printed in the gate script, feeding #1789's delta-contract decision | No |
| 1 | 14 | P2-N | P2 | Task 5 Step 4 | Gate 1 now uses the issue's exact target (data-loss qids ≤ 1); census reported separately (informational, tied to `llm_truncated` gate) — a 2-bump question still passes | No |
| 1 | 15 | P2-O | P2 | Task 5 Step 1 | `TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct` pinned explicitly in the run command (reval parity; stale shell env can't route to openrouter) | No |
| 1 | 16 | P2-P | P2 | Task 2 Step 6 + Task 5 Steps 2/4 | Added deadline-vs-16K note (`_complete` default deadline_s=600; 16K ≈ 640s > deadline); regression or `_complete` deadline-scaling note required; `transient_timeout` reported separately in the gate script + Step 4 | In-repo verified (extractor_v2.py:4007) |
| 1 | 17 | P2-Q | P2 | Task 5 Step 1 | Shared-DB lifecycle documented (per-run namespace or flush; DB fingerprint/entity count in the report as comparability check) | No |
| 1 | 18 | P2-R | P2 | Task 5 Step 2 | All five gate inputs guarded with presence checks (`need()` helper) — missing/renamed keys fail loudly, never silent default-to-0 PASS | No |
| 2 | 1 | P0-1 | P0 | Task 5 Steps 2/4 | Freshness now asserts `integrity.n_attempted == 50` (presence-guarded) — 50 questions ATTEMPTED, not 50 outcomes; `len(outcomes) != 50` rule dropped with a note that outcomes may legitimately be < 50 when gates 6/9 pass (baseline: n_attempted=50, outcomes=46, n_failed=4). Gate 9 redefined as `N_valid = len(outcomes)` (equivalently `n_attempted − n_failed`) ≥ 46, with `integrity.n_valid` (37 at baseline; partial_parse counts as invalid there) explicitly NOT the gate quantity; "46 valid qids"/"the reval's valid count" framing aligned (Goal/gate 9/script) | In-repo verified (reval.report.json: n_attempted=50, n_failed=4, n_valid=37, len(outcomes)=46) |
| 2 | 2 | P1-1 | P1 | §1.4/§1.6/§1.7, Failure Modes, Task 5 Steps 1/2/4 | Reval workers corrected to **5** (`methodology.workers=5` in reval.report.json; the plan's "8" was wrong); E2E DECIDED at `--workers 5` to mirror the baseline exactly (latency gate 5 then compares like-for-like); all 8-way-anchored numbers re-derived (~2.5h → ~3.9h wall-clock; +14-27% → +9-17%); Step 1 pre-flight and the 429-ceiling rationale updated to 5 concurrent calls | In-repo verified (reval.report.json methodology.workers) |
| 2 | 3 | P1-2 | P1 | Task 5 Files + new Step 0 + Step 2 | Added **Task 5 Step 0: Harness changes** (token accumulation, `stage_stats` emission, freshness markers, pre-run DB fingerprint) with acceptance + unit tests + commit block; Files block changed from "no code change expected" to "REQUIRED changes (Step 0)"; Step 2 now REQUIRES `resumed` (present AND False), `skipped_qids` (present AND empty), `token_usage` (present) via `need()` — absence FAILs, never a silent None → pass | In-repo verified (reval report emits none of the three keys) |
| 2 | 4 | P1-3 | P1 | Task 5 Steps 0/3 | Cost mechanism changed from teardown-sum of `last_completion_tokens`/`last_prompt_tokens` to an in-adapter ACCUMULATOR (`total_completion_tokens`/`total_prompt_tokens` incremented per call, incl. retried calls) persisted into `report['token_usage']`; shared-instance race noted (adapter OVERWRITES `last_*` per call, model_adapters.py:134-135; one model instance across worker threads); fake-adapter unit test added (N calls × known usage incl. a retried call and 8-thread interleaving → exact total) | In-repo verified (model_adapters.py:134-135) |
| 2 | 5 | P1-4 | P1 | Task 5 Step 3 | Baseline pinned in TOKENS (same unit+scope as the fresh aggregation), not platform dollars (which include reader/judge spend + cache-hit discounts $0.014/M + peak/off-peak tiers that swing ~2× — identical consumption can differ >30%); price sheet + tier + date recorded alongside the pin; aggregation scoped to the same model set as the pinned figure; dollar comparison requires same-sheet/tier conversion on BOTH sides | No |
| 2 | 6 | P1-5 | P1 | Goal, §1.8, Task 5 Intent/gate 3 | No-regression thresholds set to the MEASURED baselines (chunk_evidence@20 ≥ 0.7375, accuracy ≥ 0.826 — both sit at/below their measured baselines); verdict uses the `--compare` Wilson CI / shared-qid delta as PRIMARY with point-estimate targets as SECONDARY (a parity re-run must not fail on ±0.0005); all ≥ 0.738 references updated | In-repo verified (reval: 0.7375 / 0.8261) |
| 2 | 7 | P1-6 | P1 | Task 2 Step 7 | `tortoise/sdk.py` added to the Step 7 `git add` (Step 5 updates its :2301 doc comment) | No |
| 2 | 8 | P1-7 | P1 | Task 5 Steps 0/1/2 | Shared-DB pollution is now a GATE input: dedicated E2E DB URI (`tortoise_test_e2e_1787`) preferred over the shared matrix DB (pytest shares `tortoise_test_matrix`); Step 0 records the pre-run DB fingerprint into the report; Step 2 FAILs on entity-count deviation from the empty clean baseline | No |
| 2 | 9 | P2-1 | P2 | Task 5 Step 2 | `error_census` routed through `need('error_census', r['integrity'], …)`; per-outcome `chunk_evidence_recall@k` access wrapped in a guarded loop (no raw KeyError tracebacks); `need('resumed'/'skipped_qids')` presence checks added so a marker-less report FAILS the freshness gate | No |
| 2 | 10 | P2-2 | P2 | Task 5 Step 2 | Gates 1-9 now ENCODED in the script (each appends to `failures`) — the script's exit code IS the gate; Step 4 kept as the escalation/decisional wrapper (a 0.70-accuracy run now exits 1) | No |
| 2 | 11 | P2-3 | P2 | Task 5 Steps 2/4 | Ingest-latency baseline pinned (reval `latency_ms.ingest.mean_ms` = 1,533,923.64 ms ≈ 25.6 min/question; source + date on #1787 alongside the cost pin); gate quantity specified as mean per-outcome `ingest_latency_ms` delta and computed in the Step 2 script (was prose-only) | In-repo verified (reval latency_ms dict + per-outcome ingest_latency_ms) |
| 2 | 12 | P2-4 | P2 | Task 1 Step 5 + Open owner decision 2 | Escalation paths reconciled: accepted-only-on-v4 (alias clamps) ⇒ migration lands FIRST ⇒ decision 2's re-derivation clause (re-baseline on the new wire id or explicitly accept the confound) applies; BOTH wire ids clamp ⇒ option B/D fallback (Step 2); accepted-on-alias ⇒ decision 2 pin holds (migration strictly after Task 5) | No |
| 2 | 13 | P2-5 | P2 | Task 2 (new Step 8), Task 3, Journey Test Map, §1.9 | Task 3's verification content folded into **Task 2 Step 8** (re-review of Step 6 suite output + `git diff HEAD~1 HEAD -- tests/test_extractor_reliability.py` byte-identity check); Task 3 kept as a thin cross-reference anchor; Journey Test Map refs moved to Task 2 Step 6; §1.9 wiring rows updated; Task 4 dependency narrowed to Task 1 only (parallelizable with Task 2) | No |
| 2 | 14 | P2-6 | P2 | Task 1 Step 3 | v4-flash non-empty branch now asserts `resp` is truthy AND parses as JSON (`json.loads(resp)`), and records the captured request body for the toggle assertion — no more over-assumed "collapse resolved" from any non-empty string; otherwise stays XFAIL until the companion migration lands | No |
| 2 | 15 | P2-7 | P2 | Task 2 Step 1 | Implementer note + test comment now name tests/test_extractor_v2.py:492 as the extract_session_v2-level census coverage (`out["error_census"]["partial_parse"] == 1`, cap-agnostic mock); dead "or in Task 3" clause dropped; optional census assertion at the new 16K cap noted | In-repo verified (test_extractor_v2.py:492) |
| 2 | 16 | P2-8 | P2 | Integration Surface Map | Not-in-scope note extended to `tools/experiments/extractor-v2/{run_fix,run_loop,run_clean_test,run_ab}.py` (flash/solar `max_tokens = 8000`) + `tests/eval/retrieval/judge.py:540` (`--max-tokens` default 8000) and marked NON-exhaustive; each of the three doc-mirror rows gained a second failure mode (mirror updated without the constant → pin drift) | In-repo verified (all cited locations) |
| 2 | 17 | P2-9 | P2 | Task 5 Step 4 | `--compare` placeholder replaced with the concrete reval path `.worktrees/1509-REVAL/.longmemeval_cache/runs/reval.report.json` (read-only; must stay stable across the ~4h run or be copied to `.longmemeval_cache/runs/reval.report.json` first) | In-repo verified (file exists at that path) |
| 2 | 18 | P2-10 | P2 | Task 2 Step 6, Task 5 Steps 2/4 | DECIDED (owned in Task 2 Step 6): `_complete` scales `deadline_s` with `max_tokens` (e.g. `_scaled_deadline(base, mt) = max(base, int(0.04*mt))` — 16K ≈ 640s clears the 600s default), with unit test `test_complete_deadline_scales_with_max_tokens`; `transient_timeout` given its fate in the gate: FOLDED into the `n_failed` ceiling (gate 6 — the reval's 4 failures were all timeouts) rather than "reported separately" | In-repo verified (extractor_v2.py:4007 deadline_s=600) |
| 2 | 19 | P2-11 | P2 | Task 5 Step 2 + Task 4 Step 2 | Decision rule added: if `stage_stats` shows S4 NOT dominant or S4 truncation ≈ 0 post-fix, re-scope or drop companion #1789 (premise falsified) rather than filing it unconditionally; gate script prints the premise-check NOTE; Task 4 Step 2 filing made conditional | No |
| 2 | 20 | P2-12 | P2 | Task 2 Step 5 | Threaded unit test added (`test_stage_cap_thread_safety_mixed_env`): 8 threads × 25 calls through `_complete_parsed`/`_stage_cap` in an env-override phase + a mixed-default phase, asserting every call received its own correct cap and its own stats dict (per-call `partial` flags never cross-contaminate) | No |
| 3 | 1 | P0-1 | P0 | Task 5 Step 2 + Step 4 | `need()` presence checks extended to EVERY gate input (previously only top-level keys): per-outcome `error_classes` present on ≥1 outcome (gate 1), per-outcome `llm_truncated` present (gate 2), `token_usage.completion_tokens`/`prompt_tokens` present with INT values (gate 4 — no more silent 0-total pass), `ingest_latency_ms` with ≥1 non-None value (empty → FAIL, never mean-0 pass; gate 5), gates 7/8 moved OFF `error_census.fatal_401_auth`/`transient_429_rate_limit` (see P1-7), `db_fingerprint.entities` presence-checked (see P1-10), plus `composition`, `stage_stats`, `db_fingerprint_post` presence. Negative test note added (Step 2): feed the script a report with each key deleted → exit-1 per key | In-repo verified |
| 3 | 2 | P1-1 | P1 | Task 1 Step 1 | `finish_reason != "length"` strict assert made conditional: PASS if `finish_reason != "length"` OR (length AND `last_completion_tokens > 8192`); FAIL only when length AND tokens ≤ 8192. Docstring updated (clamp-at-8192 → length at ~8K; exhaust-at-16K → length at ~16K — distinguishable by completion_tokens alone) | In-repo verified (`_call_once` captures finish reason in-thread) |
| 3 | 3 | P1-2 | P1 | Task 1 Steps 1-2 | Filler CALIBRATED before the live call: `_probe_filler()` tokenizes the assembled prompt (transformers DeepSeek-V3 tokenizer, tiktoken cl100k fallback) and asserts the echo budget lands strictly inside (9K, 15K); filler shrunk to ~28K chars (repeat=4700); signals decoupled per P1-1 (tokens > 8192 is the sole no-clamp proof). Step 2 distinguishes calibration failures (authoring) from clamp/400 (go/no-go) | In-repo verified (tokenizer availability); fix safe regardless of exact BPE rates |
| 3 | 4 | P1-3 | P1 | Task 1 Step 1 | User message built via `json.dumps({"items": [filler]})` (the old raw-string concat produced 6000 unescaped `"` — NOT valid JSON; with json_object forced on it could 400/reject or silently repair, changing echo length); added an assert that the payload parses as JSON before the API call; TORTOISE_JSON_MODE=0 escape-hatch note kept but not relied on | In-repo verified (`_prompt_requests_json` case-insensitive "json" match; `_should_send_json_mode`) |
| 3 | 5 | P1-4 | P1 | Task 5 Step 3 + Step 2 | Baseline pin re-specified: Path 1 ("reval llm_calls × per-call usage") removed as IMPOSSIBLE (verified: reval carries zero token/usage data; llm_calls is a bare int). Path 2 made concrete: ≥10-question subset re-run (same split=s, `--workers 5`, deepseek-direct, no `--extractor-model`) with `TORTOISE_EXTRACTOR_MAX_TOKENS=8000` (old cap, still live via env override — pinning prevents measuring the NEW cap), scaled to full-run equivalent (`subset_total / len(subset_qids) × 46`); subset qids + cap + date recorded on #1787; gate script adds a PRE-RUN pin check (exists AND inside the plausible band derived from §1.4's per-call estimates, ~20-75M for ~6,720 calls; a 2-6M pin = dollar/per-call scale → fails before the E2E starts) | In-repo verified (reval has no token_usage; env override read at call time) |
| 3 | 6 | P1-5 | P1 | Architecture, §1.7 rationale 4, §1.10 | Stale "one-constant change"/"untouched harness" claims rewritten: the actual surface is enumerated (constant + probe + `_complete` deadline-scaling + measurement-harness additions + new/updated unit tests); "ladder + census semantics + env override unchanged" claim kept (they are byte-identical); §1.10 re-justifies standard on the real (additive, mechanical) footprint | In-repo verified |
| 3 | 7 | P1-6 | P1 | Task 4 + Task 5 Step 6 (new) | Task 4 split: Step 1 (scoping comment) + the #1790 filing parallelize with Task 2; the #1789 filing moved OUT to a NEW **Task 5 Step 6** ("file/re-scope #1789 per stage_stats" with the P2-11 decision rule); Task 4 dependency note + acceptance rewritten to match (no more "depends only on Task 1" contradiction with a Task-5-conditional input) | No |
| 3 | 8 | P1-7 | P1 | Task 5 Steps 0/2/4 | Gates 7/8 re-pointed: new Step 0 key `llm_error_census` (aggregates the extractor per-call classifier labels fatal_401_auth / transient_429_rate_limit / transient_timeout, extractor_v2.py:3831-3856 — classes that NEVER appear in the report census) read with `need()`; PLUS a `failures[].error_class` scan (fail on any 401-class error — failures carry `error_class: 'ingest:retries_exhausted'`, run.py:1919); the vacuous `.get('fatal_401_auth', 0)` reads of `integrity.error_census` removed; Step 4 wording updated | In-repo verified (reval census vocabulary; run.py:1919) |
| 3 | 9 | P1-8 | P1 | Task 2 Step 6 | Added `test_complete_wires_scaled_deadline` — exercises `_complete` END-TO-END (monkeypatched `_call_once` recorder): max_tokens=16000 → effective deadline ≥ 640s wired into the call; explicit deadline_s=0.05 still wins; S1 fast path (1500) keeps 600s. The helper-only test is no longer the sole proof the fix is wired | In-repo verified (`_complete`→`_call_once` deadline pass-through) |
| 3 | 10 | P1-9 | P1 | Task 5 Step 0 | Real-adapter accumulator thread-safety test mandated: `DeepSeekDirectModel` with a MOCKED HTTP transport, 8 threads × N calls with known per-call usage → `total_*` equal the exact expected sum (iterations chosen so interleaving is likely; must fail without the lock) — the fake-adapter test alone cannot exercise the shared-instance LOAD/INPLACE_ADD/STORE interleaving | No |
| 3 | 11 | P1-10 | P1 | Task 5 Steps 0/2 | DB-fingerprint counting code unit-tested: fake/empty DB → `entities == 0`; seeded N entities → `entities == N` (exact dict shape/key names the gate reads asserted); negative gate-script test (fingerprint missing `entities` → exit-1 "missing gate input", NOT the old -1 fail-by-accident path); optional `--db-flush` flag noted | No |
| 3 | 12 | P1-11 | P1 | Task 5 Steps 0/2 | Run-composition identity recorded (Step 0 `composition` = provider/model/wire-id + a sample per-call request body's `model` field) and GATED: provider == `deepseek-direct` AND model == `deepseek-chat` (or the explicitly re-baselined id per Open owner decision 2); negative test: provider=openrouter → exit-1 | No |
| 3 | 13 | P2-1 | P2 | Task 5 Step 1 | `--reader-model deepseek/deepseek-v4-flash` + `--judge-model openai/gpt-4o-2024-08-06` added to the run command AND the `TORTOISE_LME_READER_MODEL`/`TORTOISE_LME_JUDGE_MODEL` env vars pinned (reader.py:397 reads the env FIRST — a stale env silently overrides the flags; guard note added) | In-repo verified (reader.py:397) |
| 3 | 14 | P2-2 | P2 | Task 3 (deleted) + Task 2 Step 8 + Task 4 header | Task 3 DELETED (no files, no commit, two confirm-only steps, zero inbound references — §1.9 wiring + Journey Test Map never point at it); its note folded into Task 2 Step 8 (retained as the "former Task 3" marker); Task 4/5 numbering unchanged; §1.9/Journey Test Map cross-refs verified coherent | No |
| 3 | 15 | P2-3 | P2 | Task 5 Step 1 | run_key citation corrected: top-level `run_key` is None in the current report format; the derived key lives at `methodology.checkpoint_key: hnsw__hybrid__default__default` | In-repo verified (reval report) |
| 3 | 16 | P2-4 | P2 | Goal, §1.8, Task 5 Step 2 gate 3, Step 4 | chunk_evidence re-baseline made EXPLICIT: the issue's literal ≥ 0.738 sits ABOVE the measured baseline (0.7375); a run in [0.7375, 0.738) passes "no regression" but NOT the issue's literal target — stated in the Goal and Step 4 and recorded on #1787; `--compare` Wilson-CI/shared-qid verdict stays PRIMARY | In-repo verified (reval 0.7375) |
| 3 | 17 | P2-5 | P2 | Task 2 Step 8 | Byte-identity diff anchored to the pre-Task-1 commit: record `T1_PARENT` before Task 1 Step 6, diff `$T1_PARENT..HEAD` (the old `HEAD~1 HEAD` window included Task 1's probe tests in the same file → false alarm); expected added lines enumerated (2 probes + calibrator, cap pin, docstring, override pin, threaded cap test) so drift ≠ noise | In-repo verified (Task 1 commits to the same file) |
| 3 | 18 | P2-6 | P2 | Task 5 Step 0 | stage_stats unit test extended: 8 threads × interleaved S2/S4 calls through the REAL stats hook into one shared dict, asserting exact per-stage totals (keyed per-session, merged atomically or lock-protected) | No |
| 3 | 19 | P2-7 | P2 | Task 5 Steps 2/4 | Gate assertion `len(outcomes) == n_attempted − n_failed` added (presence-guarded) — catches silently dropped questions (n_attempted=50, n_failed=0, len(outcomes)=46) that pass gates 6/9; documented-failure MECHANISM defined: every `failures[]` entry's error_class + one-line justification recorded on #1787 BEFORE gating (script prints the error_class list); unrecorded failure = gate failure | No |
| 3 | 20 | P2-8 | P2 | Task 5 Steps 2/4 | Latency gate: mean AND median per-outcome `ingest_latency_ms` reported; MEDIAN is the PRIMARY gate quantity (robust to the LLM-tail: mean 1,533,924 ms vs p95 ~1.68M ms), the mean ±30% bar is a HARD CEILING only; median baseline pinned on #1787 alongside the mean (missing pin → gate failure) | In-repo verified (reval latency skew) |
| 3 | 21 | P2-9 | P2 | Task 2 Step 6 | `_scaled_deadline` helper defects fixed: (1) `(max_tokens or 0)` None-guard specified (the old `0.04 * None` TypeErrors); (2) vacuous `_scaled_deadline(600, 8000) >= 320` assertion dropped, replaced by `_scaled_deadline(0, 16000) == 640` (proves scaling engages, not the floor); (3) throughput assumption stated ONCE: deadline math uses a conservative 25 tok/s (2-4× slower than the observed 50-100 tok/s used for the §1.4 latency deltas) as a deliberate worst-case margin | In-repo verified |
| 3 | 22 | P2-10 | P2 | Task 5 Step 0 | Fourth Step 0 unit test added (DB fingerprint): empty start → `entities == 0`; non-empty namespace → real count; report key present with the documented schema (covers the same function as P1-10's positive + negative tests) | No |
| 3 | 23 | P2-11 | P2 | Task 5 Step 0 item 2 | Accumulator thread-safety mechanism MANDATED (threading.Lock around the increments, or per-call local totals summed under lock) — `attr += n` is LOAD/INPLACE_ADD/STORE and loses updates across the 5 worker threads sharing one model instance | No |
| 3 | 24 | P2-12 | P2 | Task 2 Step 1 | Census-at-new-cap through `extract_session_v2` made MANDATORY: new `test_s2_s4_census_clean_at_16k_through_session` (mirror of test_extractor_v2.py:492 with a cap-aware mock at 16000, asserting `error_census["partial_parse"]` absent/0 where the bump actually lives); "optional" wording removed from the implementer note | In-repo verified (test_extractor_v2.py:492 pattern) |
| 3 | 25 | P2-13 | P2 | Integration Surface Map | LME harness row gained per-sub-surface failure modes (accumulator lost-update under threads; stage_stats keyed on the wrong stage; marker-less report; fingerprint read before DB flush); `_S2_S4_MAX_TOKENS` row gained a second failure mode (raise applied but env override still wins / Task 1 gate bypassed) | No |
| 3 | 26 | P2-14 | P2 | Task 5 Steps 0/2/4 | Post-run DB state verified: Step 0 records `db_fingerprint_post` at teardown; gate presence-checks it, fails on `post.entities < pre.entities` (mid-run reset), and requires a scoped recount of the FAILED qids' written entities == 0 (a mid-ingest failure — S1 wrote entities, or partial-accept — pollutes retrieval for subsequent questions; the reval had 4 such failures and gate 6 allows ≤4 again) | In-repo verified (failures[] carries question_id/error_class, no llm_calls) |
| 3 | 27 | P2-15 | P2 | Task 5 Step 1 + Step 0 unit test (f) | Pre-flight made SUSTAINED (≥5 consecutive 16K calls per worker within a 60-120s window, asserting 0×429 at run throughput — a single 5-call burst misses sustained tokens/min/rpm quotas over the ~4h/6,720-call run); 429-retry × scaled-deadline unit test added (two 429s then success within ONE call budget — retries stay inside the scaled deadline) | No |
| 3 | 28 | P2-16 | P2 | Task 5 Step 0 unit test (e) + Step 1 | Key-read semantics unit test (mutate DEEPSEEK_API_KEY after construction; assert whether a new call picks it up — documents construction-vs-per-call read) and 401-handling test (one 401 → `fatal_401_auth` census bump + abort, no silent retry-until-exhausted); optional mid-run heartbeat probe (1-call 16K every 30 min) fails fast on key rotation instead of gate 7 at the end of the ~4h run | In-repo verified (classifier labels at extractor_v2.py:3831-3856) |
| 3 | 29 | P2-17 | P2 | Task 5 Steps 2/4 + Comparability | `--compare` folded into the gate script: run programmatically with `--compare-out`, parse `overall.shared_n` / `overall.shared_delta_pp` + the comparability block, and FAIL on a missing/read-failed baseline file, shared_n ≤ 0, a shared-qid accuracy delta < −1.0pp (≥1 net shared flip — Wilson-CI discipline), or a reader/judge/split/dataset mismatch; the primary verdict is now fail-fast, not a manual read | In-repo verified (compare_reports keys: overall.shared_delta_pp, per_category, comparability._match) |

| 4 | 1 | P1-A | P1 | Task 5 Steps 2/3 | Gate-script pin ordering fixed: `BASELINE_INGEST_MEDIAN_MS` computed NOW from the reval's 46 per-outcome `ingest_latency_ms` values and pinned in the script (**1,544,144.405 ms** — no placeholder; the dead "not pinned on #1787" failure branch removed); token-pin subset re-run RE-SEQUENCED between Step 0 and Step 1 (explicit run order: Step 0 → subset re-run → Step 1 → Step 2 → Step 3); explicit "edit the Step 2 script with the pinned token value" action added; subset run isolated on its own DB URI (`tortoise_test_e2e_baseline`) + checkpoint (`reval-1787-baseline-8k.*`) | In-repo verified (reval median computed cycle 4) |
| 4 | 2 | P1-B | P1 | Task 1 Step 0 + §1.5 | Probe tokenizer dependency provisioned: new Task 1 Step 0 (`uv add --dev tiktoken`, dev-only, committed with pyproject.toml + uv.lock); tiktoken cl100k made the PRIMARY calibrator (local, no HF network); transformers DeepSeek-V3 kept as an OPTIONAL precision path with the network requirement stated; §1.5 "No new third-party dependencies" claim corrected to name the probe's dev-only tokenizer dependency | In-repo verified (tiktoken NOT in uv.lock; transformers uninstalled) |
| 4 | 3 | P1-C | P1 | Task 1 Step 1 | Calibration margin raised against the MEASURED reality (repeat=4700 → 9,406 cl100k tokens — only ~14.8% above 8192, INSIDE the ±15% band, contradicting the cycle-3 docstring's ">2× above 8192"): repeat → 6000 (~12K cl100k tokens), floor assert `n >= 10500` (worst-case real echo ≥ 8,925 > 8,192 under the ±15% family-drift band); the tokenizer used is recorded in the probe output + on #1787; docstring numbers corrected | In-repo verified (measured 9,406 tokens) |
| 4 | 4 | P1-D | P1 | Task 1 Steps 1-2 | Probe runs through `_complete`'s deadline machinery (explicit `deadline_s=800` — R6 P2: the scaled default's math, 0.05 × 16000; the historical 600 figure in earlier cycle notes is superseded) so a stalled response fails bounded and classifiable — a ~9.4-12K-token echo at 50-100 tok/s takes 94-250s, and the adapter's (10, 60) read timeout can fire mid-generation or be defeated by a stalled chunked response (pilot #1549; the reval's 4/6,720 timeouts); "timeout/hang" added to Step 2's failure taxonomy as an authoring/operator error, NOT a go/no-go signal unless accompanied by a 400/clamp | In-repo verified (model_adapters.py timeout (10,60); _complete deadline) |
| 4 | 5 | P1-E | P1 | Task 5 Steps 0/2/4 | Failed-qid partial-write check made implementable (was prose-only): new Step 0 key `per_qid_written_entities` (harness per-question ingest stats at teardown / post-run DB query keyed by failed qids' sessions); the Step 2 script now checks every `failures[].question_id` wrote 0 nodes (presence-gated); Step 4 prose names the mechanism — aggregate pre/post fingerprints alone cannot detect a few stray entities from one failed qid among thousands | In-repo verified |
| 4 | 6 | P1-F | P1 | Task 5 Step 0 | `llm_error_census` counting semantics DECIDED: recovered events COUNT — `_complete` appends EVERY classified exception to a cumulative per-call events list regardless of retry outcome (the old `last_class` overwrite-to-None on the next success made recovered 429s/timeouts structurally invisible, vacating gate 8 — exactly the sustained-storm signature the gate targets); lock-mandated aggregation across the 5 worker threads (mirrors accumulator/stage_stats); new unit test: N calls through the REAL retry path mixing recovered (retry→success) + terminal events → census matches injected counts EXACTLY, extended to 8 threads sharing one adapter | In-repo verified (extractor_v2.py:4043-4058 overwrite) |
| 4 | 7 | P1-G | P1 | Task 5 Step 2 | Cost gate lower-bounded: `fresh_total < 10M` FAILS the gate — a usage-parsing regression defaulting to {0, 0} via the adapter's `.get('prompt_tokens', 0)`/`.get('completion_tokens', 0)` passes the old `> 1.3×` check vacuously (0 IS an int); negative gate test added: report with `token_usage = {0, 0}` → exit-1 | In-repo verified (model_adapters.py:135) |
| 4 | 8 | P1-H | P1 | Task 5 Steps 2/4 + Goal + Comparability | `--compare` threshold re-justified: the cycle-3 `< −1.0pp` bar was NOT a significance test (at shared_n ≈ 46-50 the shared delta quantizes to ~2.0-2.2pp steps; 1 net flip ≈ −2.17pp is McNemar p ≈ 0.5 — a parity re-run with one chance flip false-failed the PRIMARY gate); the script now FAILS only on a STATISTICALLY SIGNIFICANT NEGATIVE per-category delta (`mcnemar.significant_at_0_05` true + negative `delta_pp`, report.py:1885-1892); a non-significant negative overall delta is an informational NOTE recorded on #1787; prose corrected everywhere "Wilson-CI discipline" was claimed to match what the script reads | In-repo verified (report.py per_category mcnemar/wilson keys) |
| 4 | 9 | P2-A | P2 | Task 2 Step 1 | Dead `stage_cap_override` branch replaced: `extract_session_v2` (extractor_v2.py:3350) has NO such kwarg, so the cycle-3 guard (`"stage_cap_override" in co_varnames else None`) made `out_old` ALWAYS None — the MANDATORY old-cap census assertion never ran (the exact silent no-op presence discipline forbids). Now monkeypatches `_stage_cap` → 8000 for the session call (the real seam the S2/S4 callers use, extractor_v2.py:1073/1475), asserting the old-cap `partial_parse` bump | In-repo verified (extractor_v2.py:3350) |
| 4 | 10 | P2-B | P2 | Task 2 Step 8 | Byte-identity expected-added-lines enumeration extended with the two deadline-scaling tests (`test_complete_deadline_scales_with_max_tokens`, `test_complete_wires_scaled_deadline` — their established home is `test_extractor_reliability.py`, `test_complete_deadline_aborts_attempt` at ~:163) + the two cycle-4 P2-K tests — an implementer adding them there no longer flags unexplained drift | In-repo verified |
| 4 | 11 | P2-C | P2 | Task 4 Step 1 | The P2-4 chunk_evidence interpretation note (issue literal ≥ 0.738 vs measured 0.7375; the [0.7375, 0.738) no-regression-only band; the primary/secondary verdict hierarchy) explicitly added to Step 1's posted-content list — the recording is now OWNED by a step | No |
| 4 | 12 | P2-D | P2 | §1.10 + CHANGELOG | Tier RE-JUSTIFIED (not re-rated) on the additive-mechanical footprint with an explicit one-line concession (re-rate to complex if the next review round adds substantive design surface); the three changelog tables folded into ONE consolidated table with cycle attribution (all 67 cycle-1/2/3 rows preserved verbatim + 26 cycle-4 rows) to reduce review burden; body fixes intact | No |
| 4 | 13 | P2-E | P2 | Task 1 Steps 1-2 | Probe calibration de-over-engineered: the upper band is DROPPED (exhaust-at-16K is already PASS per P1-1 — the `tokens > 8192` assert is the sole no-clamp proof); the `n >= 10500` floor keeps the lower-bound guarantee (P1-C); the no-tokenizer fallback = pessimistic ~2 chars/token bound clearing 8192 (the hard RuntimeError is removed) — the live assert carries the verdict; the tokenizer path is an optional precision check | In-repo verified |
| 4 | 14 | P2-F | P2 | Task 5 Step 1 | Sustained pre-flight made arithmetically consistent: ≥5 SHORT-prompt calls/worker (max_tokens=16000 REQUESTED, ~few-hundred-token outputs) within a 60s window, measuring the sustained REQUEST-rate envelope (≈0.42 calls/s aggregate ≈ the run's ~0.48/s over 6,720 calls / ~3.9h); five full-length 16K generations take 800-1600s at 50-100 tok/s and CANNOT fit a 60-120s window — stated explicitly; the generation-time surface is covered by deadline scaling (Task 2 Step 6) + the heartbeat | In-repo verified (tok/s math) |
| 4 | 15 | P2-G | P2 | Task 1 Steps 3-4 | v4-flash probe hardened: a 400/unknown-model on this wire id is itself XFAIL-marked ("direct API does not serve deepseek-v4-flash" — recorded on #1787; the id exists in the OpenRouter registry but the direct API may not serve it); a hard FAIL is reserved for genuine unexpected errors (network/auth/5xx); Step 4's Expected now enumerates THREE cases | No |
| 4 | 16 | P2-H | P2 | Task 5 Step 0 | Composition-recorder unit test added (folded into group (e)): registry key → wire id under `TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct` + the sample per-call request-body `model` field capture — previously only the gate-script negative test covered it | No |
| 4 | 17 | P2-I | P2 | Task 5 Step 2 | Negative-test enumeration extended to EVERY need()/presence guard in the script: `integrity` + its nested keys (`n_attempted`/`n_failed`/`valid`/`error_census`), `accuracy.overall`, the chunk_evidence NOT-PUBLISHED guard (`chunk_evidence_recall@k['20']`), `resumed`/`skipped_qids` presence, `deadline_aborted`, `per_qid_written_entities`, `total_nodes`, and the outcomes-length consistency path | In-repo verified (script guards) |
| 4 | 18 | P2-J | P2 | Integration Surface Map | Probe row gained the three residual failure modes: probe calibration dependency availability (no tokenizer → cannot gate; provisioned dev-only + pessimistic fallback), probe echo-length vs proof-threshold margin (tokenizer fidelity ±15% band), and probe unbounded call (no deadline) — cross-referenced to Task 1 Step 2's failure taxonomy | No |
| 4 | 19 | P2-K | P2 | Task 2 Step 6 | Deadline-scaling margin fixed: multiplier raised 0.04 → 0.05 (scaled 16K deadline 640s → 800s — the 0.04 multiplier put the deadline EXACTLY at the 25 tok/s emission time, zero margin, and killed ~20-24 tok/s stragglers that the old 8K/600s would have completed); helper + wiring asserts updated to 800; TWO new tests: `test_complete_total_budget_exhausted_on_deadline` (every attempt times out → transient_timeout after attempts × deadline_s — the N × deadline_s budget is documented/accepted) and `test_complete_deadline_margin_at_throughput_boundary` (640s/800s emissions complete, not killed+retried) | In-repo verified (deadline math) |
| 4 | 20 | P2-L | P2 | Task 5 Steps 0/2/4 | Abandoned-thread billing undercount bounded: new Step 0 `deadline_aborted` counter (the harness tallies per-call deadline kills — `_call_once`'s `TimeoutError("model call exceeded Ns")`, distinct from network timeouts) — a SEPARATE key, so the census vocabulary stays unchanged; gate 10 in the Step 2 script (≤ 2) so the cost comparison's two sides don't measure different loss rates; the uncounted aborted-generation spend (≤ deadline_aborted × max_tokens) is added to the cost margin | In-repo verified (_complete docstring: abandoned thread keeps billing) |
| 4 | 21 | P2-M | P2 | Task 5 Step 1 | Pre-run invariants enforced at STARTUP, not post-run: a Step 1 pre-flight (or run.py startup assertion) resolves provider/model/wire-id BEFORE ingest and aborts if not `deepseek-direct`/`deepseek-chat`; `--db-flush` is now MANDATORY (was optional) with a clean-start abort (`total_nodes == 0` after flush); unit-test group (g) covers the pre-run abort paths — a stale-env routing or dirty DB wastes minutes, not ~4h | No |
| 4 | 22 | P2-N | P2 | Task 5 Step 3 | Subset re-run isolation + linearity: pinned to a separate DB URI (`tortoise_test_e2e_baseline`) + checkpoint (`reval-1787-baseline-8k.*`) so it can't dirty the E2E's `db_fingerprint == 0` pre-check or collide with the E2E checkpoint freshness contract; subset qids must span the observed density range (tercile selection; seed + method documented on #1787) OR the per-question token min/max spread is recorded with the pin so the `×46` extrapolation error is visible | No |
| 4 | 23 | P2-O | P2 | Task 5 Step 1 | Small-N full-stack pilot added to the pre-flight: `--limit 3` on the dedicated DB (ingest → retrieve → judge), asserting outcomes are produced AND judged (judge API reachable, keys valid) before the 50-Q run — the reader (`deepseek/deepseek-v4-flash`) and judge (`openai/gpt-4o-2024-08-06`) keys/models were otherwise untested until the 4h run | No |
| 4 | 24 | P2-P | P2 | Task 5 Step 1 | Mid-run heartbeat made MANDATORY (was optional P2-16) + presence-gated in the Step 2 script like other Step 0 keys (a missing heartbeat record = harness regression); its token usage is EXCLUDED from `token_usage` (separate counter — the 16K heartbeat calls would otherwise pollute the cost gate and self-induce 429s); unit test: heartbeat call → 401 → abort path + no accumulator pollution | No |
| 4 | 25 | P2-Q | P2 | Task 2 Step 1 | Cap-change fixture gained a calibration assert: the 60-point fixture's serialized size must exceed 8K at the pessimistic 4 chars/token bound (measured 63,432 bytes → ≥ 15.8K tokens) — the CapAwareModel keys truncation on the cap VALUE, so the test now proves the fixture really overflows 8K / fits 16K and cannot silently regress below the density it claims to represent | In-repo verified (fixture sized) |
| 4 | 26 | P2-R | P2 | Task 5 Steps 0/2 | DB fingerprint counts `total_nodes` across ALL node kinds the extractor writes (entities-only missed a dirty start with only points/operators/events, entities written later); every fingerprint key is INT-TYPED (a None/str count fails presence — the old isinstance-guard silently SKIPPED all pollution checks); a REAL-FalkorDB integration test of the counting function (empty namespace → 0; seeded → exact count) added in addition to the fake-DB unit test | In-repo verified |

### Cycle 5 (FINAL fix cycle)

### Cycle 6 (resume cycle — post-interruption convergence, cycles R1-R4)

- **Cycle R1 (resume fresh review): 3 reviewers found 2 P0 + 5 P1 + ~13 P2 — ALL fixed inline.** P0-A `chunk_turns` drift (reval ran `chunk_turns=1`, code default 2, E2E command omitted the flag → gate-3 `chunk_evidence@20` baseline invalidated; fixed: `--chunk-turns 1` in both commands + `composition.chunk_turns` gate + negative test); P0-B stale `TORTOISE_EXTRACTOR_MAX_TOKENS` env (a stale override silently beats the constant at call time with no detection; fixed: `env -u` in the E2E command + `composition.effective_stage_cap` gate == 16000 + pre-flight abort); P1-a `--qids` untracked (the plan's own second-model gate had flagged it as an open P2 — now a first-class Step 0 deliverable with unit test); P1-b `--db-flush` absent from the literal command (mandated-but-unshipped flag + the `--limit 3` pilot pollutes the dedicated DB → now in the command + Step 0); P1-c mid-run RoutingModel failover; P1-d per_qid absent-key silent pass (`pqwe.get(_qid, 0)` → key presence required); P1-e subset pin health unguarded (subset freshness + n_failed==0 contract before pinning) — plus the P2s (vacuous threaded-test 8000 branch, live probes re-executed in Part B, deadline-scaling sentinel signature, comparability loop extension, run wall-clock ceiling, heartbeat non-401 semantics, probe robustness notes, chunk_evidence paired-read note, ingest_v2.py in Files/commit, S1-cap-asymmetry note, pilot checkpoint hygiene).
- **Cycle R2 (re-review verification): 3 fresh reviewers found 1 P0 + 7 P1 + 8 P2 — the P0 was a defect in R1's own fix, the P1s were the measurement-protocol layer's remaining holes. ALL fixed inline (orchestrator deep-fix).** P0 reader-lane contradiction (R1's "abort if OPENROUTER_API_KEY set" broke the E2E — the reval's READER ran on `openrouter:deepseek/deepseek-v4-flash`, reader_pinned=True, REQUIRES OPENROUTER_API_KEY; fixed: `TORTOISE_EXTRACTOR_NO_FALLBACK=1` knob neutralizes the extractor's OpenRouter fallback instead of removing the env, reader pinned to the exact baseline spec `openrouter:deepseek/deepseek-v4-flash`, `reader_provider`/`reader_model_spec` join the comparability loop, OPENAI_API_KEY added to Requires); P1 env-abort lane scope (E2E-lane-only, subset exempt — both lanes unit-tested); P1 route-census presence (`need('fallback_route_calls', ...)` — the `.get(..., 0)` was the P0-1 anti-pattern); P1 wall-clock vacuity (`updated_at_utc` is NOT in the current report format — now a Step 0 deliverable, presence-gated: missing stamp → exit-1, never a skip); P1 surface drift ungated (`composition.surface == 'hnsw'` gate); P1 qid-identity (E2E question set must equal the reval's — full-overlap check against reval.checkpoint.json qids; --compare's shared_n > 0 alone was insufficient); P1 per_qid Phase-A leg (ingest_v2 writes session/chunk nodes BEFORE extraction — the failed-qid count must EXCLUDE the raw-chunk leg or every permitted failure fails the "must be 0" gate); P1 live-marker enforcement (`@pytest.mark.live` added to BOTH probe snippets + registered in pyproject.toml markers + guard test + Step 8 enumeration) — plus the P2s (--db-flush scope incl. all per-question graphs + refuse path, heartbeat → primary adapter direct, subset outcome-count consistency, composition.model definition + EXPECTED_EXTRACTOR_MODEL constant, ×46-vs-50 denominator note, §1.10 stale line count dropped, probe explicit-deadline-vs-scaled interaction, teardown ordering test, mid-run judge/reader outage limitation).
- **Cycle R3 (re-review verification): 3 fresh reviewers found 1 P0 + 3 P1 + 9 P2 — ALL fixed inline. P0 qid-identity gate false-failed a clean run (R2's own gate built the reval set from outcomes ONLY — 46 — while a successful fix run has 50; a clean run fails the identity gate and the "pin --qids" remedy breaks n_attempted==50; fixed: the attempted set is the UNION of checkpoint outcomes ∪ failures = 50, with the interplay noted); P1 census-test old-cap leg KeyError (the `[:200]` cut landed mid-point-1 → rung 4 falls through → census class truncated_parse_error, NOT partial_parse → the backstop assert raises KeyError — the same zero-complete-item bug R2 fixed in the threaded test; fixed: cut at the first point's item boundary); P1 NO_FALLBACK tracking gap (the knob is load-bearing but was absent from the Step 0 acceptance/commit/positive tests — now first-class with a positive unit test); P1 subset cap check (the pin's validity depends on the subset having run at 8000 — `composition.effective_stage_cap == 8000` presence-checked in the subset contract, negative test; without it a leaked env pins the baseline on the NEW cap and the +30% gate compares like-with-like) — plus the P2s (heartbeat/probe token-exclusion MECHANISM — dedicated adapter instance + accumulator zeroed after pre-flight; env-abort DISCRIMINATOR — abort when set AND value ≠ "8000"; --db-flush per-question-graph scope — GRAPH.LIST prefix drop; probe deadline_s=600→None owned in Task 2 Step 6 + Step 8 enumeration (g); fixture UPPER calibration bounds (≤14K) so "fits 16K" is real; surface-map row for the new Step 0 surfaces; census sibling sub-key + stage_stats sub-key presence/int checks; subset command reader/judge pins).
- **Cycle R4 (verification): 3 fresh reviewers found 4 P1 + 5 P2 — ALL fixed inline (orchestrator deep-fix).** P1-1 gate-script NameError (the R3 placement of `_stamp = need(...)` ran BEFORE `def need` — every gate run crashed with NameError; `def need` hoisted to the top after imports, ordering verified OK); P1-2 fixture upper-bound asserts contradicted the fixtures (45-point shrink: 63,432→47,577 bytes S2 / 64,422→48,327 S4 — both now ≥11.9K at 4 c/t AND ≤13.8K at 3.5, inside the observed 8-16K reval band); P1-3 qid-identity run-side asymmetry (a legitimate run with n_failed ≤ 4 has 46-49 outcomes but 50 attempted — outcomes-only run_qids false-failed it as a --qids subset; run side now outcomes ∪ failures); P1-4 comparability loop read reader_provider/reader_model_spec from the compare artifact which never emits them (silent None-pass — the R2 reader-lane fix was inert; now read presence-checked from the fresh report's methodology, reader_pinned==True gated) — plus the P2s (EXPECTED_EXTRACTOR_MODEL named constant in the gate; §1.1 census accounting corrected to 12 events across 9 questions, 6×1 + 3×2, with the 3 truncated_valid qids a DISJOINT set that never reaches the census; S1-cap asymmetry label corrected conservative→lenient; live marker registration OWNED in Task 1 Step 0 with the tiktoken commit; probe deadline edit OWNED in Task 2 Step 6 Part A; ×46 denominator rationale documented in Step 3; §1.10 line count restated to ~1,950 with the exact-figure disclaimer).
- **Cycle R5 (verification): 3 fresh reviewers found 2 P1 + 4 P2 — ALL fixed inline and verified by EXECUTION (the gate script was extracted, compiled, and run against 9 synthetic reports — reval-parity 46+4 → exit 0, clean 50/0 → exit 0, accuracy one-flip band → exit 1 with the sign-off message, C3 truncated_valid → exit 1, stale cap → exit 1, chunk_turns drift → exit 1, reader bare spec → exit 1, failed-qid absent → exit 1, qid-mix drift → exit 1).** P1-1 fixture asserts half-applied (the R4 60→45-point shrink left `== 60` asserts in both new cap tests — they would fail at "expect PASS"; fixed to `== 45`/`< 45`/`k < 45` + the "keeps ALL 60 points" comment); P1-2 accuracy-PRIMARY contradiction (the prose says --compare statistical verdict is PRIMARY and "a parity re-run must not fail on a single chance flip", but the script hard-failed `overall < 0.826` with no escape — one chance flip (~0.804, −2.17pp at shared_n ≈ 46-50) failed the gate; fixed: [0.804, 0.826) owner-sign-off branch mirroring the chunk_evidence P2-E pattern, hard floor below the band) — plus the P2s (duplicate `def need` removed; gate 12 = C3 unrecorded-truncations scan added so the issue's indicator 2 is never silently dropped; gate script ALSO extracted to `tools/longmem_eval/gate_1787.py` as a Step 0 deliverable so Step 3's pin edit is committed, never divergent; probe deadline_s=600 → 800 (the scaled default's math — the old 600 sat exactly at the ~20 tok/s healthy floor and false-failed slow-but-healthy backends before Task 2 lands).
- **Cycle R6 (verification): 3 fresh reviewers found 2 P1 + 6 P2 — ALL fixed inline and re-verified by EXECUTION.** P1-1 `test_s4_dense_emit_completes_at_16k` old-cap branch ERRORED (the `rindex('"}', ...)` cut never matched — the S4 points' last key is `"slots"` (a NUMBER), so `"}` occurs 0 times and ValueError propagated through `_call_once`, killing the backstop leg; fixed with the S2 test's depth-walk boundary mechanism, cut after point 20's closing brace — rung-4 recovers the head with partial=True); P1-2 gate 12 vacuous (the R5 C3 scan read `recovery.truncated_valid` per outcome — a key NO code writes (ladder records sanitize/repair only; the reval's recovery dicts are all {}); the harness ALREADY produces `integrity.n_truncated_valid` (report.py:1090) — gate 12 now reads THAT, presence-checked via need(), target == 0, with the negative gate test) — plus the P2s (3 stale 600-deadline references corrected to 800 in the taxonomy/ownership notes; `tools/longmem_eval/gate_1787.py` added to the Step 0 commit; "keeps ALL 60 points" comment → 45; pre-run qid-identity probe added to the pre-flight so a drifted split aborts before the ~4h E2E, not after; Goal/Step 4 prose aligned with the script's fail-unless-sign-off one-flip bands; epic claim softened — the epic owns the run protocol, not the stage-cap surface).
- **Final verification (Phase 5 — FINAL-VERIFICATION): 3 fresh reviewers found 2 P1 + 5 P2 + 4 P3 — ALL fixed inline.** P1-1 probe exhaust-branch crash (the unconditional `json.loads(resp)` made the documented "exhaust-at-16K = PASS" branch UNIMPLEMENTABLE — a mid-string-truncated echo raises JSONDecodeError → the pivotal go/no-go test ERRORS instead of PASSing; fixed: the round-trip parse is now conditional on `finish_reason` — length+tokens>8192 is PASS by construction (unparseable is the signal), the fidelity checks run only on the finish=stop path, and the clamp assert is gated on finish=length so a stop-echo with tokens ≤ 8192 is classified as authoring/sizing, matching the P1-1 docstring); P1-2 duplicate companion-issue creation (#1789 AND #1790 are ALREADY FILED and OPEN — verified 2026-08-27; the literal `gh issue create` commands would create duplicates and break the hardcoded #1789/#1790 cross-references; fixed: Task 4 Step 2 is now verify-exists-and-link, Task 5 Step 6 is re-scope/close-the-existing, create only if dropped) — plus the P2s (complexity label sync: `gh issue edit 1787 --add-label complexity:complex` owned in Task 4 Step 1; `tests/test_extractor_reliability.py` added to the Step 0 commit (groups (a)/(f) live there per the plan's own Step 8 home-citation); #1789 output-token machinery proportionality note — lighter post-hoc read permitted, gate contract retained) and the P3s (decision-3 vs gate-12 reconciled — gate 12 supersedes the "file follow-up" language: >0 is a hard FAIL + owner sign-off + follow-up; `_elapsed_hours` now catches (ValueError, TypeError) + normalizes naive stamps to aware UTC; §1.10 line-count figure dropped as non-gate-quantity; v4-flash probe routed through `_complete` deadline_s=800 + v2 import added).

| Cycle | # | Issue | Severity | Location | Fix Applied | Research? |
|---|---|---|---|---|---|---|
| 5 | 1 | P1-A | P1 | Task 5 Steps 0/2 + unit groups | per_qid_written_entities source DECIDED to the SINGLE grounded option: post-run DB query keyed by the failed qids' `question_graph_namespace` graphs (run.py:138/1671 — distinct FalkorDB graph per question), counting ALL node kinds, MERGE-collision semantics stated (first writer's graph owns a colliding name; the count is a per-graph census, not an ownership audit), deduped across the S2/S4 re-emit; the harness per-question ingest-stats alternative REMOVED as impossible (failures[] carries NO stats — a mid-ingest exception discards the partial dict, run.py:1908-1918); mandatory unit group (h): real per-question graph layout seeded with a failed qid's partial writes → reported count | In-repo verified (failures[] shape) |
| 5 | 2 | P1-B | P1 | Task 1 Steps 1-2 | Probe gained an echo-FIDELITY check: `json.loads(resp)` round-trip + `len(resp) >= 0.9 × len(payload)` — a well-formed-but-short echo (finish=stop, elided/shortened) can no longer be misclassified as a clamp/400 go/no-go (false-negative escalation to option B/D); Step 2 taxonomy gains the "short-but-valid-JSON echo with finish=stop = model-behavior/authoring failure" entry | In-repo verified |
| 5 | 3 | P1-C | P1 | Task 2 Step 1 | New dense-S4 fixture test `test_s4_dense_emit_completes_at_16k`: same ≥8K calibration assert as P2-Q, driven through the S4 caller/_complete_parsed path at the 16000 default (full list, no partial_parse) + forced old cap on the SAME fixture proving the S4 partial-accept backstop still fires (the cap-change test drove only run_s2; the census test's S4 branch was tiny) | In-repo verified |
| 5 | 4 | P1-D | P1 | Task 5 Steps 0/1/2 | Heartbeat presence-gating made REAL: `need('heartbeat', ...)` (present AND non-empty record) added to the Step 2 gate script + a gate-script print; `heartbeat` added to the Step 0 acceptance key list and the P2-I negative-test enumeration (the cycle-4 promise of presence-gating had NO check — a missing heartbeat passed silently) | In-repo verified |
| 5 | 5 | P1-E | P1 | Task 5 Step 0 item 5 + unit group (f) | deadline_aborted counting seam DECIDED: dedicated `stats["llm"]["deadline_aborts"]` counter incremented at the `_call_once` deadline-kill raise — `TimeoutError("model call exceeded Ns")` (extractor_v2.py:4001) is the DISTINCT seam from a network-transport TimeoutError (both classify as transient_timeout via `_classify_error`, 3848-3849 — the cycle-4 "distinct" claim had no mechanism); written under the same lock discipline; unit tests: deadline kill increments / network timeout does NOT / 8-thread exact-count (fails without the lock) | In-repo verified (extractor_v2.py:4001, 3848-3849) |
| 5 | 6 | P1-F | P1 | Task 2 Step 5 + Step 8 + Surface Map | Invalid-override regression made REAL: `test_stage_cap_invalid_override_warns_and_defaults` (setenv "abc" → pytest.warns + fall back to the default — `_stage_cap`'s fail-open-with-visibility contract); added to the Step 8 expected-added-lines enumeration (the surface map + Step 8 item 1 claimed this coverage with NO test — only valid-value paths existed) | In-repo verified (_stage_cap impl) |
| 5 | 7 | P1-G | P1 | Task 5 Step 1 pre-flight (new item 5) | Full-length 16K generation probe added to the pre-flight: one full-length (or proportionally-scaled) 16K generation per worker in ~10 min — asserts sustained completion tok/s ≥ ~20 (abort/flag pre-run; the Task 2 Step 6 margin floor) AND 0×429 at a token volume ≈ a meaningful fraction of the run's per-minute TPM (~8K vs ~1.9K tok/min sustained); measured throughput folded back into the deadline-margin assumption if it degrades | In-repo verified (tok/s math) |
| 5 | 8 | P2-A | P2 | CHANGELOG/SUMMARY | Cycle-1 severity counts corrected: the consolidated table lists 3 P0 (P0-A/B/C) + 4 P1 (P1-D..G) + 11 P2 = 18, but the retained cycle-1 SUMMARY block and the consolidated SUMMARY header claimed "2 P0, 5 P1" — both corrected to "3 P0, 4 P1, 11 P2" | In-repo verified (table rows 1-18) |
| 5 | 9 | P2-B | P2 | Task 1 Step 0 + §1.5 | tiktoken "no network" claim corrected: `tiktoken.get_encoding("cl100k_base")` DOES fetch its BPE ranks file on first use (OpenAI blob storage, cached afterward) — rationale restated as "no HF-hub MODEL download / deterministic cached BPE"; the TRUE offline path is Step 1's pessimistic 2-chars/token fallback (already covered) | In-repo verified |
| 5 | 10 | P2-C | P2 | Task 5 Step 3 | Density-spanning subset made EXECUTABLE: new `--qids`/`--from-checkpoint-qids` harness mechanism specified (small harness change — no qid-subset filter exists today; only `--limit` prefix and `--spot-check` full-set), with fallback (b) (per-question token min/max spread with the pin under `--limit`) promoted to primary if the flag isn't landed; subset command given verbatim with its isolation flags (own DB URI `tortoise_test_e2e_baseline` + checkpoint) | In-repo verified (no qid filter in run.py) |
| 5 | 11 | P2-D | P2 | §1.10 + header | Line count updated to 1,484 (the ~1,228 citation was stale by ~250); FINAL tier decision made: RE-RATED to complex (the cycle-4 concession's own trigger was hit — cycle 4-5 added per_qid provenance, deadline_aborted seam, total_nodes, 4-part + full-length pre-flight, heartbeat, --qids); stated as final, no further re-rating churn | In-repo verified (wc -l) |
| 5 | 12 | P2-E | P2 | Task 5 Step 2 gate 3 + Step 4 | [0.7375, 0.738) is now a DISTINCT conditional branch: the literal ≥ 0.738 target is never silently waived — the band FAILS the gate unless the Step 4 wrapper records explicit owner sign-off as an issue-comment on #1787 | No |
| 5 | 13 | P2-F | P2 | Task 5 Step 3 + Step 1 | Measurement-harness mechanism redundancy collapsed: Step 3's accumulator restatement → pointer to Step 0 item 2; the Step 1 "Per-stage attribution (P2-M)" paragraph deduped into Step 0 item 3 | No |
| 5 | 14 | P2-G | P2 | §1.7 | Epic-alignment note added: cites Epic #1509 (docs/epics/2026-08-20-1509-extractor-v3/) + names the run-protocol accounting chain (#1746/#1747, M4/J4) where the network-timeout parallel surface is tracked — the alignment claim is now checkable from the plan text | In-repo verified (epic docs) |
| 5 | 15 | P2-H | P2 | Task 2 Step 6 | Deadline-math parenthetical corrected: 0.05 s/token = 20 tok/s, NOT 25 tok/s (25 = the OLD 0.04 s/token multiplier) — the throughput assumption (25 tok/s → 640s) and the multiplier (0.05 → 800s, covering ~20 tok/s) are now stated as separate facts | In-repo verified (arithmetic) |
| 5 | 16 | P2-I | P2 | Task 5 Step 2 | `--compare` per-category significant-negative loop HOISTED out of the `shared_delta < -1.0` guard — it now runs UNCONDITIONALLY (a significant negative per-category delta with overall ≥ −1.0 was never evaluated); the informational NOTE branch for the overall delta is kept | In-repo verified (script nesting) |
| 5 | 17 | P2-J | P2 | Task 5 Step 1 item 3 | Request-rate probe bumped to match the envelope: 6 calls/worker × 5 workers in 60s ≈ 0.50 calls/s (ABOVE the run's ~0.48/s) — the cycle-4 0.42/s figure was 13% below, so a quota between ~25-29 rpm tripped the run but not the probe | In-repo verified (arithmetic) |
| 5 | 18 | P2-K | P2 | Task 2 Step 1 | `_conv` import added to the census test snippet (`from tests.test_extractor_reliability import _conv`) — pasting the snippet verbatim previously raised NameError (the existing test imports it function-locally at test_extractor_v2.py:465; the new test did not) | In-repo verified (test_extractor_v2.py:465) |
| 5 | 19 | P2-L | P2 | Task 5 Steps 2/4 | RECOVERED transient_timeout gated: new gate 11 (≤ 2, consistent with gate 8) — recovered-timeout storms (retried to success at up to ~800s + backoff) previously passed gates 6/8/10, caught only indirectly by the latency median; the gate-6 fold now applies to TERMINAL timeouts only | In-repo verified |
| 5 | 20 | P2-M | P2 | Task 5 Step 0 item 4 + unit (d) | Fingerprint counting integration-tested against the REAL per-question graph layout: seed N known nodes across MULTIPLE `question_graph_namespace` graphs (as the E2E writes them) → assert the aggregate; per-question-graph aggregation for db_fingerprint/db_fingerprint_post stated explicitly (was single-namespace-tested only) | In-repo verified (run.py:138/1671) |
| 5 | 21 | P2-N | P2 | Task 5 Step 2 gate 3 | chunk_evidence minimum sample-size floor added (`len(ce20) ≥ 40` — the reval published 40/46): a degraded mid-run judge/evidence pipeline that shrinks the sample now FAILS instead of silently passing the mean gate; gate 9 explicitly floors the accuracy DENOMINATOR (accuracy.overall is computed over len(outcomes)) | In-repo verified (reval 40/46) |
## SUMMARY (consolidated — cycles 1-5; cycle-3 P2-D fold)

**Fix counts by cycle:** cycle 1 = 18 (3 P0, 4 P1, 11 P2) · cycle 2 = 20 (1 P0, 7 P1, 12 P2) · cycle 3 = 29 (1 P0, 11 P1, 17 P2) · cycle 4 = 26 (8 P1, 18 P2) · cycle 5 = 21 (7 P1, 14 P2) · **total 114 fix records** (93 cycle-1/2/3/4 rows preserved verbatim in the consolidated table above).

### Cycle 1 summary (retained verbatim)

```
## SUMMARY

- **Fixes applied: 18** (3 P0, 4 P1, 11 P2) covering all issues in the fix list.
- **Research:** in-repo verification only (bash/read on `tortoise/extractor_v2.py`, `tortoise/model_adapters.py`, `tortoise/sdk.py`, `tools/longmem_eval/`, `tests/test_extractor_reliability.py`) — no web queries needed; the two external facts (DeepSeek json_object top-level-object requirement, tokenizer rates) were handled with a safe fix (echo-inside-object + 2× filler) as allowed.
- **Key code behaviors confirmed before editing:** `run_s2` returns `_complete_parsed(...)` directly (no `error_census` key); the `partial_parse` census bump lives in the S2/S4 stage callers keyed on `stage_stats["partial"]`; rung-3 repair can close a pre-last-key cut but not a mid-array cut (so the P0-B fixture lands in rung 4 with `stats["partial"]=True`); `_complete` default `deadline_s=600`; `_stage_cap` reads the env override at call time; `_should_send_json_mode` flips json_object on for both probe prompts (TORTOISE_JSON_MODE default "1").
- **New content introduced:** Task 2 precondition (probe-outcome gate), Task 3 verification-only checkpoint restructure, Task 5 per-class failure ceilings + freshness/DB/per-stage/pre-flight notes, harness token-aggregation requirement, latency-figure reconciliation (≈ +14-27% parallel upper bound, gate ≤ +30%), Integration Surface Map rows (3 doc mirrors + json_object failure modes + not-in-scope 8000s), pinned companion-issue sequencing decision, and this CHANGELOG/SUMMARY.

---
```

### Cycle 2 summary (retained verbatim)

```
## SUMMARY (cycle 2)

- **Fixes applied: 20** (1 P0, 7 P1, 12 P2) covering all 20 issues in the cycle-2 fix list.
- **Research:** in-repo verification only (bash/read) — `reval.report.json` (methodology.workers=5; n_attempted=50, n_failed=4, n_excluded=0, n_valid=37, n_invalid=13; len(outcomes)=46, len(failures)=4; chunk_evidence@20 mean 0.7375 over 40 non-None; accuracy 0.8261; NO `resumed`/`skipped_qids`/`token_usage`/`stage_stats` keys; latency_ms.ingest.mean_ms 1,533,923.64), `model_adapters.py:134-135` (last_* overwrite per call), `extractor_v2.py:4007` (deadline_s=600), `test_extractor_v2.py:492` (extract_session_v2-level census test), `tools/experiments/extractor-v2/{run_fix,run_loop,run_clean_test,run_ab}.py` + `tests/eval/retrieval/judge.py:540` (8000s), `tortoise/sdk.py:2301`. No web queries needed.
- **Key structural changes:** Task 5 gained Step 0 (harness changes) and its Step 2 script now enforces gates 1-9 numerically (exit code IS the gate); Task 2 Step 6 owns the `_complete` deadline-scaling fix and Step 8 is the folded verification checkpoint (Task 3 is now a thin anchor); E2E runs at `--workers 5` on a dedicated DB URI; the cost gate is pinned in TOKENS (unit/scope-matched to the aggregation) and the latency gate is pinned at the measured ingest baseline; the freshness contract is now `n_attempted == 50` + required marker keys, not `len(outcomes) == 50`.
- **Key code behaviors confirmed before editing:** the reval report's `integrity.n_valid` (37) counts partial_parse as invalid and is NOT the outcome count (46); `methodology.workers` is 5, not 8; the harness emits no freshness/token/stage keys today; the adapter overwrites `last_completion_tokens`/`last_prompt_tokens` per call on a single shared instance.

---
```

### Cycle 3 summary (retained verbatim)

```
## SUMMARY (cycle 3)

- **Fixes applied: 29** (1 P0, 11 P1, 17 P2) covering all 29 issues in the cycle-3 fix list.
- **Research:** in-repo verification only (bash/read) — `extractor_v2.py:4007` (`_complete` default deadline_s=600; call sites 597/989/1925 pass no explicit deadline_s), `extractor_v2.py:959`/`3831-3856` (ladder + per-call classifier labels), `model_adapters.py:134-135` (`last_*` overwrite per call; `_should_send_json_mode`/`_prompt_requests_json`), `tools/longmem_eval/run.py:1829`/`1919` (per-outcome error_classes from ingest_stats census; failures carry `error_class`), `reader.py:397` (env-first reader model resolution), `report.py` `compare_reports` output keys (`overall.shared_delta_pp`, `overall.shared_n`, `comparability.*.match`), `test_extractor_v2.py:492` (extract_session_v2 census pattern), `test_extractor_reliability.py:163` (`test_complete_deadline_aborts_attempt` pattern). The DeepSeek-tokenizer calibration in `_probe_filler` (P1-2) is written to be safe regardless of the exact BPE rates (programmatic assert, adjustable repeat count). No web queries needed.
- **Key structural changes:** Task 3 deleted (P2-2) with its note folded into Task 2 Step 8; Task 4 split (P1-6) with the #1789 filing moved to a new Task 5 Step 6; Task 5 Step 0 gained items 5-6 (`llm_error_census`, `composition`) + post-run fingerprint + lock-mandated accumulator + 6 unit-test groups (a-f); the Step 2 gate script now enforces full presence discipline, outcome-count consistency, post-run DB state, composition identity, and the AUTOMATED `--compare` primary verdict; Task 2 Step 6 gained the P1-8 wiring test + P2-9 helper fixes; Task 1's probe is calibrated, JSON-safe, and clamp-vs-exhaust-aware.
- **Key code behaviors confirmed before editing:** the reval's `integrity.error_census` vocabulary is exactly `{ingest:retries_exhausted, partial_parse}` (no 401/429/timeout classes — those are extractor per-call labels); the report's top-level `run_key` is None (the derived key is `methodology.checkpoint_key`); `_call_once` enforces the deadline via `Thread.join(timeout=deadline_s)`; `compare_reports` exposes the shared-qid delta under `overall.shared_delta_pp` and per-field match flags under `comparability`; failures[] entries carry `question_id` + `error_class` but no `llm_calls`.
- **Deviations from the issue text (recorded, not silent):** the chunk_evidence@20 literal target ≥ 0.738 exceeds the measured baseline 0.7375 (P2-4 — interpretation recorded on #1787); the baseline token pin is derived from a pinned-cap subset re-run (P1-4), the reval's llm_calls-only data being unusable for token derivation; the plausibility band for the pin is derived from §1.4's per-call estimates (~20-75M tokens for the full run) — a figure in the 2-6M range indicates a wrong unit/scope and fails the pre-run check.
```

### Cycle 4 summary

- **Fixes applied: 26** (8 P1, 18 P2) covering all 26 issues in the cycle-4 fix list.
- **Research:** in-repo verification only (bash/read + python) — `reval.report.json` (median of the 46 per-outcome `ingest_latency_ms` = **1,544,144.405 ms**, computed cycle 4 — P1-A), `extractor_v2.py:3350` (`extract_session_v2` has NO `stage_cap_override` kwarg — P2-A), `extractor_v2.py:4007`/`4043-4058` (`_complete` deadline + `last_class` overwrite-to-None on success — P1-D/P1-F/P2-K), `model_adapters.py:127-135` (timeout (10, 60); usage `.get(..., 0)` defaults — P1-D/P1-G), `report.py:1885-1892` (per-category `mcnemar.significant_at_0_05`/`wilson_ci` — P1-H), cap-fixture sizing (63,432 bytes — P2-Q), measured filler calibration (repeat=4700 → 9,406 cl100k tokens — P1-C). No web queries needed.
- **Key structural changes:** Task 1 gained Step 0 (dev-only `tiktoken` provision); the probe is deadline-bounded, re-anchored to measured token counts, and the v4-flash 400 is xfail-marked; Task 2's deadline multiplier is 0.05 (800s at 16K) with two new boundary/exhaustion tests, and the dead `stage_cap_override` branch was replaced with a `_stage_cap` monkeypatch; Task 5 Step 0 gained `per_qid_written_entities`, recovered-event census semantics, `deadline_aborted`, `total_nodes` fingerprints, and composition-recorder + pre-run-abort unit tests; Step 1's pre-flight is a 4-part gate (composition/clean-DB abort → `--limit 3` pilot → sustained request-rate probe → mandatory heartbeat); the Step 2 gate pins the median NOW, lower-bounds the cost gate, fails on McNemar-significant negative compare deltas, checks per-qid failed writes, and gates `deadline_aborted`; the three changelog tables were consolidated into one (P2-D).
- **Key code behaviors confirmed before editing:** `_complete` records `stats["llm"]["last_class"]` on exception then overwrites it to None on the next success (recovered events invisible to any census aggregated from it); the adapter defaults missing usage to 0 (`.get('prompt_tokens', 0)`/`.get('completion_tokens', 0)`); `DeepSeekDirectModel.complete` HTTP timeout is (10, 60) — a ~9.4K-token echo at 50-100 tok/s takes 94-188s, so the read timeout can fire mid-generation or be defeated by a stalled chunked response (pilot #1549); `compare_reports` exposes per-category `mcnemar.p_value`/`significant_at_0_05`/`wilson_ci` and `overall.shared_delta_pp`/`shared_n`; the 60-point cap fixture serializes to 63,432 bytes (≥ 15.8K tokens at the 4 chars/token bound).
- **Deviations from the issue text (recorded, not silent):** none new in cycle 4 — the cycle-3 deviations (chunk_evidence ≥ 0.738 literal vs 0.7375 baseline; subset-derived token pin; the 20-75M plausibility band) remain in force; the v4-flash probe's 400-on-this-wire-id is now xfail-documented on #1787 rather than a hard fail (P2-G).

### Cycle 5 summary

- **Fixes applied: 21** (7 P1, 14 P2) covering all 21 issues in the cycle-5 fix list (FINAL fix cycle).
- **Research:** in-repo verification only (bash/read/python) — `run.py:138/1671` (`question_graph_namespace` per-question graphs), `run.py:1908-1918` (`failures[]` carries ONLY `{question_id, question_type, error, error_class, failed_at_utc}` — no ingest stats, P1-A), `extractor_v2.py:4001` (`_call_once` deadline-kill `TimeoutError("model call exceeded Ns")` — the P1-E seam), `extractor_v2.py:3848-3849` (`_classify_error` maps BOTH deadline + network TimeoutErrors to `transient_timeout` — P1-E), `_stage_cap` (invalid override warns + falls back — P1-F), `tests/test_extractor_v2.py:465` (`_conv` imported function-locally — P2-K), tiktoken cl100k first-use BPE fetch (P2-B), epic docs/epics/2026-08-20-1509-extractor-v3/ (P2-G), line count 1,484 (P2-D). No web queries needed.
- **Key structural changes:** Task 1's probe gained an echo-fidelity assert + the short-but-valid-JSON-echo taxonomy entry (P1-B); Task 2 gained the dense-S4 fixture test (P1-C), the invalid-override test (P1-F), the `_conv` import (P2-K), and the corrected throughput parenthetical (P2-H); Task 5 Step 0 picked the SINGLE per_qid source (post-run DB query on the failed qids' namespaces, P1-A), specified the `deadline_aborts` counting seam + lock + tests (P1-E), specified per-question-graph fingerprint aggregation (P2-M), and added `heartbeat` to the acceptance keys; Step 1's pre-flight gained a full-length 16K generation probe (P1-G) and a corrected request-rate envelope (P2-J); the Step 2 gate gained the heartbeat presence check (P1-D), the [0.7375, 0.738) conditional sign-off branch (P2-E), the hoisted per-category compare loop (P2-I), gate 11 (recovered transient_timeout ≤ 2, P2-L), and the chunk_evidence/accuracy sample-size floors (P2-N); Step 3's subset re-run is now executable (`--qids` mechanism + verbatim command, P2-C) with the accumulator restatement collapsed (P2-F); §1.10 re-rates the tier to COMPLEX as the FINAL decision (P2-D).
- **Key code behaviors confirmed before editing:** `create_entity` MERGEs on name (first writer's marker wins — per-qid attribution is a per-graph census, not an ownership audit, P1-A); `_stage_cap` warns on an unparseable override and uses the default (P1-F); `question_graph_namespace(model, prompt, qid)` yields a distinct FalkorDB graph per question (P1-A/P2-M); both deadline-kill and network TimeoutErrors classify identically as `transient_timeout` — the "model call exceeded" message is the only distinguishing seam (P1-E); `tiktoken.get_encoding("cl100k_base")` fetches its BPE ranks on first use (P2-B); the harness has no qid-subset filter today (P2-C).
- **Deviations from the issue text (recorded, not silent):** none new in cycle 5 — the cycle-3/4 deviations (chunk_evidence ≥ 0.738 literal vs 0.7375 baseline — now with an explicit owner sign-off branch, P2-E; subset-derived token pin; the 20-75M plausibility band; the v4-flash probe xfail) remain in force.

## Second-model final gate (Phase 4.5)

> **Gate result (RESUME SESSION — 2026-08-27, deepseek-v4-pro after the R1-R6 convergence):** the second-model gate was re-run after the resume cycles (the pre-resume "CLEAN" was invalidated — the resume cycles proved the E2E measurement protocol was incompletely pinned). **Round 1 found 2 P1 + 3 P2 + 4 P3 (fixed); Round 2 found 2 P1 + 2 P3 (completion-of-fix gaps, fixed); Round 3 found 1 P1 (the round-2 census-contract fix was recorded but not applied to the body — a completion-of-fix gap of the same class; applied + execution-verified: the 12-scenario matrix now passes, including sparse-census → exit 1, recovered 5xx/network storms → exit 1, missing stage output-token key → exit 1, workers/evidence_boost drift → exit 1); the re-dispatch after Round 3 verified the fix by execution — final status CLEAN (no P0/P1/P2 standing).**
>
> **`[SECOND-MODEL-GATE] P1 — gate 1 (FIXED):** the per-call census gate covered 3 of the 9 `_classify_error` classes — recovered `transient_5xx`/`transient_network`/`transient_unknown` storms (retried to success, invisible to n_failed) were unbounded; gate 11's fix wasn't generalized. Fixed: recovered-transient AGGREGATE ceiling (timeout+5xx+network+unknown ≤ 2) + sibling presence check extended to the full 9-class vocabulary.
>
> **`[SECOND-MODEL-GATE] P1 — gate 2 (FIXED):** the #1789 decision rule keyed on post-fix TRUNCATION counts (s4_truncated ≥ s2_truncated) — which the cap raise drives to ≈ 0, the EXPECTED success — so it would vacuous-fire "premise falsified → DROP" on every successful run; the re-emit tax (S4 output ≈ 2× S2) survives the cap raise and is the actual #1789 premise. Fixed: `stage_stats` now carries `s2_output_tokens`/`s4_output_tokens` (Step 0), and the decision rule keys on `s4_output_tokens ≥ 1.5 × s2_output_tokens` (Task 5 Step 6 + gate-script NOTE).
>
> **`[SECOND-MODEL-GATE] P2 (NOTED — no re-run):** (1) ×46 bias direction corrected (the ×46 anchor makes the +30% gate HARDER, fail-safe — the R4 note said "easier"); (2) cost-subset machinery downgraded to advisory (disproportionate to the 0.4-0.8% effect; the 20-75M band independently validates the ~48M estimate — the pin may be the estimate, the subset runs only on operator request).
>
> **`[SECOND-MODEL-GATE] P3 (NOTED — no re-run):** (1) `evidence_boost == false` now gated (read-only from methodology); (2) pre-run qid-identity probe mechanism specified (run.py startup assertion; post-run gate remains the enforcement point if not landed); (3) extractor-provider "deepseek-direct" is an INFERENCE (reval records no provider — only the bare `deepseek-chat` fingerprint id), recorded on #1787; (4) gate 12 is deliberately stricter than the issue's literal indicator-2 target (a lossless recovery fails gate 12 while passing gate 2) — recorded on #1787, never silent.
>
> **`[SECOND-MODEL-GATE] P3/P4 nits from the PRE-RESUME gate (kept verbatim, still valid):** the §1.10 line count is stale (restated to ~1,950 in R4 — the second-model gate's own "1,484" nit predates it); the comparability-paragraph redundancy (P3 nit 5) is left as documented; composition.model semantics are now DEFINED (wire id) + EXPECTED_EXTRACTOR_MODEL constant (P2 resolved).
>
> **`[SECOND-MODEL-GATE] round-2 P1 (FIXED):** (1) the 9-key census presence check was added WITHOUT a matching "emit all 9 keys, int-0 for absent classes" contract — a Counter-style census on a clean run (all fatal_402/403/4xx and transient_5xx/network/unknown legitimately 0) would false-fail the gate; Step 0 item 5 now states the fixed 9-key emission contract and group (f) asserts a clean run's census equals the full 9-key zero dict (+ fallback_route_calls). (2) the #1789 output-ratio rule's two new stage_stats keys were not presence-checked (a missing s4_output_tokens vacuous-fires "re-scope or DROP"), and no per-stage token-attribution mechanism/unit test existed (the accumulator is adapter-GLOBAL); Step 0 item 3 now specifies the per-stage delta mechanism (read `model.last_completion_tokens` at each S2/S4 call site, delta-accumulate per stage under lock), group (c) gains the per-stage output-token interleaving test, and the gate presence loop includes s2_output_tokens/s4_output_tokens.
>
> **`[SECOND-MODEL-GATE] round-2 P3 (FIXED, cheap):** `methodology.workers == 5` now gated (gate 5's latency baseline is contention-sensitive); the redundant R3 3-key census presence loop deleted (subsumed by the 9-key loop).
>
> **`[SECOND-MODEL-GATE] round-3 P1 (FIXED + execution-verified):** the round-2 census-contract fix was claimed in the gate note but NOT applied to the body — Step 0 item 5 still named 3 classes with a Counter-style accumulation, so the 9-key presence check would false-fail a clean run. Fixed: Step 0 item 5 states the fixed 9-key emission contract (`llm_error_census` ALWAYS a full dict of the nine classes + `fallback_route_calls`, int-0 for absent, never sparse — pre-seed or normalize at teardown); group (f) asserts a clean run's census equals the full 9-key zero dict + `fallback_route_calls=0` and reconciles the "matches injected counts EXACTLY" assertion to the fixed schema; group (c) names the per-stage output-token interleaving test explicitly. Verified by execution: sparse census → exit 1; clean 9-key zero census → exit 0; recovered 5xx/network/unknown storms → exit 1 (gate 11 aggregate); S4 ratio < 1.5 → premise NOTE only (exit 0); missing s4_output_tokens → exit 1; workers/evidence_boost drift → exit 1. **Final status: CLEAN — the second-model gate passes.**
>
> **`[SECOND-MODEL-GATE] P2` — `--qids` harness flag is untracked in the implementation surface:** Step 3's density-spanning cost-baseline subset depends on a new `--qids`/`--from-checkpoint-qids` run.py flag (cycle-5 P2-C), but it is absent from the Task 5 Files block, Step 0's acceptance key list, the Step 0 commit `git add` enumeration, and has no unit/negative test (unlike every other Step 0 item). If fallback (b) (min/max spread under `--limit`) is used instead, the cost pin is silently density-biased. Action: when implementing Task 5 Step 0/Step 3, add `--qids` to the Files block + Step 0 acceptance + commit message, and unit-test the qid-subset selection; or explicitly state the cost gate becomes advisory under fallback (b). **RESOLVED in the resume cycle (P1-a): `--qids` is now a first-class Step 0 deliverable (Files block, acceptance keys, commit message, unit test in group (g), negative gate test) — this gate's open item is closed, along with the sibling tracking gap (heartbeat in the Step 0 commit enumeration).**
>
> **`[SECOND-MODEL-GATE] P3` nits (no action required, recorded for implementer awareness):**
> 1. Token baseline scales `subset_total / len(subset_qids) × 46` (successful-outcome denominator) while the fresh run's accumulator counts ALL 50 attempted questions — small, conservative bias; scale ×50 or document the ×46 choice explicitly so the "same unit and scope" claim (P1-4) and §1.4's "~50 sessions" agree.
> 2. §1.10's line-count citation ("1,484 lines … FINAL") is stale — the file is ~1,690 lines after the cycle-5 changelog. Restate or drop the exact figure.
> 3. Gate script's per-qid check uses `pqwe.get(_qid, 0) != 0` — a failed qid ABSENT from `per_qid_written_entities` silently defaults to 0 and passes (the P0-1 anti-pattern). Require key presence for every `failures[].question_id`; add the negative test to group (h).
> 4. Composition gate hardcodes `comp.get('model') != 'deepseek-chat'` with prose-only "or the explicitly re-baselined id per decision 2" — parameterize the expected model as a constant (e.g., `EXPECTED_EXTRACTOR_MODEL`) near the other pins so decision 2's re-baseline updates exactly one line, or remove the wording.
> 5. Residual redundancy in the non-changelog text: the `--compare` automation is described twice (Step 4 bullet + "Comparability" paragraph) and Step 4 re-specifies gate 6-11 semantics the script comments already carry. Collapse the Comparability paragraph into a pointer to the Step 2 script and reference the script's gate numbering.

---
<!-- plan-review: cycles=11, status=clean, version=2.3.0 -->
<!-- resume-session: 2026-08-27 — 5 prior cycles (pre-interruption) + 6 resume cycles (R1-R6) + 3-round second-model gate + final verification; all findings fixed; gate script execution-verified (13-scenario matrix) -->
