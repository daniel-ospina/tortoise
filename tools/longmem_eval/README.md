# LongMemEval external comparability runner (issue #1144, axis 2)

Runs the official **LongMemEval-S** benchmark (500 questions, ~40-session
histories) against Tortoise as an external comparability measurement: ingest
each question's chat history into the graph → hybrid retrieval (graph points
+ raw session transcripts) → reader LLM answers from retrieved context →
official GPT-4o answer-check judge. Reports overall + per-category accuracy,
retrieval recall@k, context tokens and latency, with the full methodology in
a provenance JSON. **No "#1" claims — published numbers carry their
methodology** (design-locked 2026-08-15, issue #1144 axis 2).

## Usage

```bash
# CI / offline smoke (committed mini fixture, mocked reader+judge, no keys):
python -m tools.longmem_eval.run --data tests/fixtures/longmemeval_mini.json \
    --limit 5 --mock --output /tmp/lme_smoke.json

# Full run (downloads the dataset + real LLM reader/judge):
python -m tools.longmem_eval.run --split s            # 500 questions, LongMemEval-S
python -m tools.longmem_eval.run --split s --limit 10 # first 10 (sanity)
```

## Dense-leg setup (R3, #1542)

The vector/dense retrieval leg runs **only when sentence-transformers is
installed** (the `[embeddings]` extra — `all-MiniLM-L6-v2`, the MemDelta-pinned
384-dim embedder, #399). The eval env must install it once; the R3 pre-flight
**refuses to start a real (non-`--mock`) run without a working embedder** — a
dense-less report is never published silently.

```bash
# eval env (repo root): dev tooling + the embeddings extra
uv sync --group dev --extra embeddings

# pre-download the ~90MB model (first run downloads it on demand; pre-warm
# avoids a >30s cold-load during the run's first create_point)
uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# verify the embedder is usable (R3 pre-flight gate)
uv run python -c "from tortoise.embeddings import EmbeddingModel; m = EmbeddingModel.get(load_timeout=600); assert m is not None; print('embedder OK')"
```

Contract: with the embedder present, every `create_point` writes a 384-dim
`embedding` and the vector strategy runs at query time (`embedding_coverage`
per question is `1.0`); without it, coverage is a recorded `0.0` and the
vector leg traces `no_embedder` — observable, never silent. `--mock` runs warn
and continue offline (CI smoke stays runnable without the extra).

## Configuration (env-driven, never hardcoded)

| Var | Purpose | Default |
|---|---|---|
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` | provider keys (existing repo pattern, `tortoise.ingest._PROVIDERS`) | required for non-mock runs |
| `TORTOISE_LME_READER_MODEL` | reader model spec `<provider>:<model>` or bare | `openrouter:deepseek/deepseek-v4-flash` (M5 pinned — #1525) |
| `TORTOISE_LME_JUDGE_MODEL` | judge model spec | `openai:gpt-4o-2024-08-06` (the official judge model) |
| `TORTOISE_LME_CACHE_DIR` | dataset cache dir (outside the repo) | `~/.cache/tortoise-longmemeval` |
| `TORTOISE_LME_CHUNK_TURNS` | turns per raw-chunk window (R1 #1540) | `2` (the run protocol step-2 sweep selects the pilot/500-Q value) |
| `TORTOISE_LME_CONTEXT_CAP` | reader context token budget — points first, chunks backfill (UX decision 3) | `8000` |
| `TORTOISE_LME_MAX_CHUNKS_PER_SESSION` | per-session raw-chunk cap in the retrieval pool (E2E-1 session dedup) | `2` |
| `TORTOISE_LME_RERANK` | R6 rerank gate (fail-safe OFF — only `1/true/yes/on` enables; explicit `--rerank`/`--no-rerank` beat it) | unset = OFF |
| `TORTOISE_LME_RERANK_MODEL` | cross-encoder model name (R6 #1545) | `cross-encoder/ms-marco-MiniLM-L6-v2` |
| `TORTOISE_LME_RERANK_POOL` | rerank pool depth (R6; applied pool = `max(pool, max(k))`; only read while rerank is on) | `40` |
| `TORTOISE_LME_RERANK_CAP` | per-session MMR cap (R6; ≥ 1) | `2` |
| `TORTOISE_LME_RERANK_LAMBDA` | MMR λ in [0,1] (R6; 1.0 = pure rerank, 0.0 = pure similarity; boundary values accepted) | `0.7` |

CLI flags: `--split s|m|oracle`, `--limit N`, `--data <local json/jsonl>`,
`--k 5,10,20`, `--top-k 20`, `--mock`, `--output <report.json>`,
`--cache-dir`, `--no-download`, `--work-dir`,
`--checkpoint <state.json>` (partial-results checkpoint + resume),
`--max-retries N` (per-question LLM-call retries with exponential backoff;
questions that still fail are recorded in `report['failures']` and the run
continues — one transient error never aborts the 500-Q run),
`--chunk-turns N`, `--context-cap N`, `--max-chunks-per-session N`
(R1 #1540 knobs — env-first, CLI overrides, all validated ≥ 1),
`--integrity-threshold F` / `--integrity-justification <text>` (M7 #1527:
override the `integrity.valid` gate — max allowed `invalid_rate`; the
override is recorded with its justification and a *violated* override still
yields `valid=false`; default 0.0 = any failed/ingest-error question marks
the run invalid).

**R6 rerank (issue #1545, epic #1509) — cross-encoder + MMR, OFF by default:**
`--rerank` / `--no-rerank` (tri-state; `--no-rerank` beats a leaked env),
`--rerank-pool N`, `--rerank-cap N`, `--rerank-lambda F`, `--rerank-model
<name>`. The layer is a **post-fusion stage in the eval retrieval layer only**
(`tools/longmem_eval/rerank.py`): the RRF-fused pool is deepened to the
rerank pool, re-scored by a sentence-transformers `CrossEncoder` (sigmoid
normalized), and greedily MMR-selected with a hard per-session cap — the
selected hits ARE the reader's context. **Off-path is byte-identical to the
V3 baseline** (no rerank keys, no extra queries, production
`search_engine.py`/`sdk.py` untouched). Failure semantics: a model load or
score failure degrades that question to rerank-off with a recorded reason
(never a run abort; transient load failures are TTL-cached ~1/min); the
degraded question falls back to the **baseline** pool. Knob sweeps (λ×cap)
are V5 — the run records the effective config + per-question
`rerank_pass` (moved/dropped/selected_count/max_session_chunks,
pool_recall@k) and the report's `rerank` block (applied_fraction,
degraded_n, max_session_chunks_max — E2E-10 assertable from the report
alone).

**Run hygiene (M7 #1527):** the runner refuses Python < 3.12 with a clear
message (pyproject `requires-python >=3.12`); checkpoints carry a code
fingerprint (git_sha + python + dataset hash + config + prompt hashes) and a
stale resume — different config, or a pre-fingerprint v1 checkpoint — is
refused with the differing fields named; concurrent run processes sharing
one checkpoint merge under an exclusive flock (no lost updates).

### #1349 embedder-selection flags

- `--retriever {hybrid,vector}` (default `hybrid`) — the retrieval arm.
  `vector` is the #1349 gate arm: the query is encoded by the injected model
  and retrieval is `tortoise.search_engine.run_vector_query` ONLY (never
  `tortoise_fts_query`); the per-question outcome adds nDCG@10 (binary
  has_answer gains, log₂(i+2) discount, IDCG = all evidence turns first
  capped at 10, zero-evidence → 0.0) + P@10 (secondary) + P@5 (tertiary),
  plus `ranked_ids` + `evidence_turn_matches`.
- `--model <name>` — probe model short name (`minilm|arctic-xs|arctic-s|
  bge-small`); invokes `tools.embedder_probe.inject_model` BEFORE ingest and
  query encoding (the singleton is the candidate). HARD-FAIL semantics: a
  graph with zero embedding-bearing points aborts the run with
  MODEL_ENCODE_FAILED (exit 4) — empty recall is never reported as a result.
- `--query-prompt <name>` — threaded to `inject_model` (e.g. `query` for the
  snowflake-arctic vendor config re-validation).
- `--retrieval-only` — skips reader/judge entirely (same retrieval output as
  `--mock`, structurally immune to reader/judge contamination); the report's
  accuracy is `None` with a methodology note — no bogus accuracy from
  unset labels.
- `--db <uri>` — FalkorDB connection mode (`docker://|redis://|rediss://|
  bolt://`; also honors `$TORTOISE_DB_URI`). Replaces the per-question
  tempdir embedded db so the HNSW `queryNodes` branch is reachable
  (`_is_embedded=False`). **Per-RUN graph isolation:** each (question,
  model-run) gets a distinct graph name (`{model}__{prompt}__{qid}`) — the
  HNSW index is global per graph, so without this a winner-vs-control
  spot-check's second run would silently reuse the first model's vectors.
- `--spot-check` — named reproducible HNSW producer: runs `--model` (winner)
  AND control (`minilm`) in one pass over the question set, emitting ONE
  paired artifact at
  `docs/research/2026-08-17-1349-embedder-selection/hnsw-spotcheck-{winner}.json`
  (`{cleared, n, metric_deltas}`) for gate_1349.py's "HNSW artifact
  present+cleared" check.

Checkpoints are keyed per config
(`{surface}__{retriever}__{model}__{prompt}`, surface ∈ embedded|hnsw) in a
versioned format — a `--db` run never resumes against embedded-mode
brute-force checkpoints and stale #1144-era checkpoints are unreadable, not
misread. Writes are atomic (temp-file-then-rename); resume against a
truncated/corrupt record re-encodes just that question with a warning.
Breaker-open questions (vector arm) are marked `breaker_open` and routed
through the report's dropped-question accounting — excluded from means,
count surfaced (`report['dropped']`), never silently counted as recall 0.

Encode cache (`encode_cache.py`): model-keyed
(`sha256(model_id + prompt_name + text)`), disk-persisted, namespaced per
(model, prompt) under the cache dir — the cross-question haystack redundancy
is 5-10×, and the cache is what makes the 12-45h burn feasible. Active when
`--model` is set. Concurrency model: **sequential workers** (one question at
a time) — the simplest correct choice; per-question isolated graphs + the
shared encode cache make parallelism a coordination cost with no correctness
benefit.

## Dataset

Official **`xiaowu0162/longmemeval-cleaned`** (the canonical cleaned
replacement for the deprecated `xiaowu0162/longmemeval`) — fetched on demand
via urllib into the cache dir, **never committed** (tens of MB). A local
path via `--data` skips the download. Split S = `longmemeval_s_cleaned.json`
(~40 sessions ≈ 115k tokens of history per question).

## Pipeline (per question, isolated fresh graph)

1. **Ingest** (`tools/longmem_eval/ingest.py`) — deterministic, no LLM keys:
   `:Session` nodes, episodic turn Points (`pointKind=event`, `[role] text`,
   `has_answer` stamped on evidence turns), and **turn-granular raw chunk
   Points** per session (`pointKind=session-transcript`, ids
   `lme:{qid}:s{si}:c{ci}` — non-overlapping verbatim windows of
   `chunk_turns` turns each; the union of chunks == the full session, so
   verbatim coverage is preserved; the whole-session `:raw` blob is
   retired). Chunks are written **unmarked** in deterministic mode (D3) and
   **containment-marked** in v2 mode (D5, written BEFORE extraction so an
   extractor failure never loses the verbatim leg). Idempotent re-runs
   (MERGE + existence guards).
2. **Retrieve** (`retrieve.py`) — production hybrid search
   (`TortoiseSDK.tortoise_fts_query`: RRF fusion of FTS + vector +
   structural with the TF-IDF degradation fallback; embedded/CI degrades
   automatically, Docker/HNSW uses the full stack). Candidates are fetched
   at `max(k)*3` depth (pool-depth headroom), the pool is deduped per-session
   to `max_chunks_per_session` raw chunks (E2E-1), and the reader's context
   is **budget-capped and points-first** (UX decision 3): extracted points
   render in rank order, raw chunks backfill the remaining
   `context_token_cap` tokens. Reports session-level recall@k (fraction of
   `answer_session_ids` in top-k), turn/evidence recall@k (extracted points
   only — the D5 denominator split), chunk-evidence recall@k (the raw-chunk
   containment view), and the exact context handed to the reader
   (`ret["context_points"]`; `context_tokens ==
   _estimate_tokens(render_context(context_points))`). Recall is measured
   over the **isolated per-question corpus** (each question's haystack in
   its own fresh graph), NOT the official full-corpus indexing — stated
   explicitly in the report's methodology so numbers are never misread as
   paper-comparable recall.

   **Optional R6 post-fusion stage (cross-encoder + MMR, off by default):**
   with `--rerank`, `hybrid_search` fetches the rerank pool (default 40 —
   the effective applied pool is `max(pool, max(k))`), `rerank.py` scores
   every (query, chunk) pair with the pinned cross-encoder (sigmoid → (0,1))
   and selects up to `top_k` via greedy MMR (`λ·rel − (1−λ)·max_sim`;
   `sim = (1+cos)/2` over the stored point `embedding` with a per-pair
   Jaccard fallback on missing/NaN/dimension-mismatched embeddings — scale-
   consistent so the λ tradeoff is not distorted), capped per-session
   (E2E-10: ≤ 1–2 chunks per session; empty `session_id` hits are exempt).
   The per-question result carries `rerank_pass` (applied / degrade_reason /
   pool_size / pool_recall@k / moved / dropped / selected_count /
   max_session_chunks) + `rerank_latency_ms`; the leg-mix `rerank` bucket
   counts the selection-loss (dropped), so `legs + dropped == pool_size`.
   Degraded (scorer-unavailable) questions fall back to the **baseline
   20/60 pool** truncated to `top_k` — never a 40-pool stand-in.
3. **Reader** (`reader.py`) — LLM answering from the top-k context via the
   repo's `OpenAICompatModel` provider wiring; `--mock` = deterministic
   evidence-turn reader. The context follows the official gen.py shape:
   a `Current Date: {question_date}` header + a per-session date annotation
   on every retrieved chunk (temporal-reasoning questions are structurally
   unanswerable without the dates), and the API call disables JSON mode
   (`response_format=None`) with `max_tokens=500` — the official call shape.
4. **Judge** (`judge.py`) — the official `get_anscheck_prompt` templates
   (verbatim from LongMemEval `evaluate_qa.py`: contains-answer, temporal
   off-by-one exemption, knowledge-update updated-answer, preference rubric,
   abstention), label = `'yes' in response.lower()`; default
   `gpt-4o-2024-08-06` at temperature 0. The judge API call replicates
   `evaluate_qa.py` exactly: a single user message (no system message),
   `n=1, temperature=0, max_tokens=10`, no `response_format` (JSON mode
   would deviate from the official protocol). `--mock` = containment/
   abstention keyword judge.
5. **Report** (`report.py`) — overall + task-averaged accuracy, the five
   paper categories (Information Extraction, Multi-Session Reasoning,
   Temporal Reasoning, Knowledge Updates, Abstention) + six raw types,
   retrieval recall@k (incl. paper-aligned `_paper@k` keys over non-_abs
   questions, M7), context-token means, latency (retrieval/reader/judge/
   **ingest** per question, mean/p50/p95 — M7 isolates the write-path
   cost), the **integrity block** (valid / invalid_rate / per-question
   error census — printed BEFORE the score), **leg-mix / pool-size /
   evidence written-·retrieved aggregates** (M7), and the methodology
   provenance (dataset id, split, reader/judge models, judge rule,
   extraction approach, k values, token estimator, git sha, **python
   version, workers, dataset fingerprint, the dataset recall-semantics
   audit record**, run timestamp).

## Report contract (M7 #1527)

The report is self-explanatory: every run prints and persists

- **`integrity`** — `valid` (invalid_rate ≤ threshold), `n_attempted` /
  `n_valid` / `n_invalid`, `invalid_rate` (invalid = failed question OR
  completed question with ingest errors), `error_census` (site-prefixed
  P2-aligned error classes: `reader:retries_exhausted`, `judge:fatal`, …),
  `checks` (python guard, dataset audited, audit present, fingerprint
  matched, census computed). Printed BEFORE the score.
- **`leg_mix`** — per-leg `match_source` counts over the top_k context the
  reader saw (embedded → `tfidf`; real → `rrf`; never empty).
- **`pool_size`** — live per-question graph point count (mean/p50/p95) =
  the retrieval-pool denominator.
- **`evidence`** — `evidence_written` (ingest's evidence turns/points) vs
  `evidence_retrieved@k` (the turn_recall numerator), with vacuity over
  **evidence-bearing questions only** (`evidence_written > 0`;
  evidence-absent abstentions are counted separately, never dragged into
  the denominator).
- **`methodology.dataset_semantics_audit`** — the dataset recall-semantics
  audit (E2E-3 Precondition 2): coverage/consistency/roles/abstentions
  census + recorded paper divergences + verdict. **Publication gate (no
  opt-out):** `build_report` raises without it, and a `not-trusted`
  verdict serializes every recall key to `null` — no `turn_recall` /
  `evidence_recall` number is published until the dataset is re-audited.
  Paper-aligned `retrieval.*_paper@k` keys (non-_abs only) are reported
  alongside the legacy keys.

Per-question outcomes (the Layer-1 payload) carry `valid`, `error_classes`,
`leg_mix`, `leg_mix@k`, `pool_size`, `evidence_written`,
`evidence_retrieved@k`, `ingest_latency_ms`.

On a rerank run the outcomes additionally carry `rerank_pass`
(applied / degrade_reason / pool_size / pool_recall@k / moved / dropped /
selected_count / max_session_chunks) and `rerank_latency_ms`, and the
report's `rerank` block records the effective config (enabled / model /
lambda_ / per_session_cap / pool_size / model_load_ms / prewarmed) + the
aggregates (applied_fraction / degraded_n / sample_reasons / mean_moved /
mean_dropped / mean_selected_count / **max_session_chunks_max** — the
E2E-10 cap assertion — / pool_recall_mean@k). Baseline reports carry **zero**
rerank keys.

## Full run prerequisites

- The dataset (~tens of MB; auto-downloaded to the cache dir — needs
  network) and
- provider keys (reader: e.g. `OPENROUTER_API_KEY`; official judge:
  `OPENAI_API_KEY`).

The full 500-question run is `@pytest.mark.slow` (never in CI); the
committed mini fixture + `--mock` exercises the entire pipeline offline in
CI (`tests/test_longmem_runner.py`).

## R6 follow-up run (epic #1509 run protocol step 9)

The R6 layer is **measured post-baseline** — the V3 baseline report must
exist first, and the follow-up compares **shared qids** against it (never
unpaired aggregates). Protocol for the follow-up:

1. **Fresh checkpoint** — the R6 arm never resumes a V3-baseline
   checkpoint: `run.py` records the effective rerank config in the
   checkpoint fingerprint and refuses any config-mismatched resume
   (baseline↔rerank, or a pool change with rerank off).
2. **Pre-warm the model once** before the run (the runner pre-warms at
   start — `model_load_ms` recorded — but the first-ever load needs a
   network download into the HF cache, so pre-warm the env, e.g.
   `uv run python -c "from sentence_transformers import CrossEncoder; "
   "CrossEncoder('cross-encoder/ms-marco-MiniLM-L6-v2')"`).
3. **Baseline env must be clean** — `TORTOISE_LME_RERANK` UNSET (or the
   run's effective config recorded) so the baseline cannot silently become
   a rerank-on run.
4. **Latency comparability** — the R6 arm's `retrieval_latency_ms`
   includes `rerank_ms`; the delta vs baseline must subtract
   `rerank_latency_ms` (reported separately) or state the confound.

## Granularity sweep (run protocol step 2, R1 #1540)

The 3-point micro-test that SELECTS the granularity knob before the pilot:

```bash
# offline smoke (deterministic ingest implied by --mock):
python -m tools.longmem_eval.sweep_granularity --data tests/fixtures/longmemeval_mini.json \
    --limit 5 --mock

# real v2 sweep (needs provider keys / --extractor-model):
python -m tools.longmem_eval.sweep_granularity --split s --limit 20
```

Runs `chunk_turns ∈ {1, 2, 4}` with all other knobs fixed, prints a
comparison table (evidence / chunk-evidence / session / turn recall@10,
context_tokens_mean, context_point_count_mean) and selects the winner per
D2: **v2 mode** maximizes `evidence_recall@10` subject to
`context_tokens_mean ≤ context_token_cap`, tie-break → smaller `chunk_turns`;
the deterministic cell is a context-token/underfill view only. The chosen
value feeds the pilot and the 500-Q run; the report's methodology records it
(`chunk_turns` in the methodology).

## Reader pinning (M5, #1525)

The reader model + prompt are **pinned constants for the run**: the code
default (`READER_MODEL` in `tools/longmem_eval/reader.py`) is
`openrouter:deepseek/deepseek-v4-flash` — the model the V2 runs actually
used — so a default-config run is a pinned run. The prompt constants
(`_SYSTEM_PROMPT`, `_TYPE_FRAGMENTS`) live in the same module. Every report
records the reader's resolved identity + the verbatim prompt in its
methodology (`reader_model_spec`, `reader_provider`, `reader_pinned`,
`reader_system_prompt`, `reader_type_fragments`) — cross-cell/cross-run
reader drift is visible, never silent.

Run cells must **not** override `TORTOISE_LME_READER_MODEL` (or set it
exactly to `READER_MODEL`); an override is recorded (`reader_pinned=false`)
and warned on stderr. Verify after each cell:

```bash
jq '.methodology | {reader_model_spec, reader_pinned, reader_system_prompt, reader_type_fragments}' <report>
```

shows `reader_pinned: true` and identical values across the three cell
reports (pilot → 500-Q → confirmation).

## Temporal/recency (R5, #1544)

Temporal-reasoning (TR) questions get a distinct retrieval path, default-ON
for the TR category and completely inert for every other question type
(non-TR path is byte-identical to pre-R5 — baseline isolation, M8):

* **Events in the TR pool** — the retrieval pool is the point + event
  union (E2E-4's "no point-only filter"): dated `:Event` nodes
  (eventKind `lmeHaystackSession`, one per dated haystack session,
  `startedAt == haystack_dates[si]`, `sessionId == dataset sid`) join the
  point hits, merged by RRF score with a deterministic id tiebreak. The
  v2 leg's payload events (core:occurrence etc.) ride the same union with
  `startedAt` from their payload/session date (E1 #1533 dependency —
  landed).
* **Recency date weight in RRF** — the engine's `rrf_fusion` caller
  (`tortoise_fts_query`) accepts an optional recency multiplier
  (`recency_field` / `recency_boost`): a rank-based percentile (newest
  → 1.0, oldest → 0.0, undated → 0.0 — parsed via the existing
  `_created_sort_key`, mixed ISO/epoch safe) multiplies each doc's RRF
  score by `(1 + boost × factor)`. Default-off → byte-identical for every
  pre-R5 caller. On the eval graph points carry `createdAt := session_date`
  (sentinel `1970-01-01T00:00:00Z` for undated sessions — deterministic-
  oldest, never the server default now), so the weight ranks by session
  date.
* **TR-constraint detection → time-window filter** — `detect_time_constraint`
  classifies the TR question text: `interval` ("between … and …", ISO or
  Month-day with the question's year) and `recency` ("N days/weeks/months
  ago", "last N …") produce a hard window filter on the annotated
  session_dates BEFORE truncation — session_recall@k measures the
  in-window pool. `ordering` ("how many days", "how long", "when did",
  bare "ago" with no bound) applies no filter — the question needs the
  full dated set. Defensive rule: when the filter would empty the dated
  pool, the unfiltered pool is kept (the reader is never starved into
  abstention), recorded per question as `tr_window_fallback`.
* **Time-ascending rendering** — the TR reader context renders dated hits
  in ascending session_date order (dated first, undated last; stable
  within a date). Recall metrics keep retrieval order — they measure
  retrieval, not rendering.
* **TR top_k cap (20→12)** — TR context items are capped at `tr_top_k`
  (the transcript-flood control; R1's per-session chunk cap is the
  complementary flood control).

Knobs (recorded in the report methodology — same provenance as `top_k` /
`ks`):

| Flag | Default | Meaning |
|---|---|---|
| `--tr-top-k` | 12 | TR context item cap (20→12) |
| `--tr-date-weight` | 0.5 | RRF recency multiplier strength (0.0 off) |
| `--no-tr-events` | off | exclude the events timeline from the TR pool |

Per-question outcomes carry `tr_constraint` (the detected kind, TR only)
and `tr_window_fallback` (whether the window filter fell back). The run
protocol step-2/6 knob sweeps should cover `tr_date_weight ∈ {0.0, 0.5,
1.0}` and `tr_top_k ∈ {8, 12, 16}` on the TR subset.

> ⛔ **E1 dependency note**: the v2 leg's payload-event dating
> (`startedAt` on core:occurrence events) requires the E1 (#1533)
> `session_date` kwarg on `extract_session_v2` — landed; without it, v2
> events keep the server default and the dated timeline surface is
> exercised via the deterministic leg's `lmeHaystackSession` events only.
