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

> **Judge via OpenRouter (no OpenAI key needed).** The official judge is
> OpenAI's `gpt-4o-2024-08-06` — the model id is what makes it official
> (external comparability), and the SAME model is served by other
> configured providers. With only `OPENROUTER_API_KEY` set, run the sealed
> benchmark with:
> `export TORTOISE_LME_JUDGE_MODEL='openrouter:openai/gpt-4o-2024-08-06'`
> (provider resolution order: openrouter → deepseek → openai → gemini).
| `TORTOISE_LME_CACHE_DIR` | dataset cache dir (outside the repo) | `~/.cache/tortoise-longmemeval` |
| `TORTOISE_LME_CHUNK_TURNS` | turns per raw-chunk window (R1 #1540) | `2` (the run protocol step-2 sweep selects the pilot/500-Q value) |
| `TORTOISE_LME_CONTEXT_CAP` | reader context token budget — rank-interleaved (C1 #1745: points + chunks in true RRF order, the R1 points-first tiering deliberately reversed) bounded by the item cap | `8000` |
| `TORTOISE_LME_MAX_CHUNKS_PER_SESSION` | per-session raw-chunk cap in the retrieval pool (E2E-1 session dedup; C5 #1745 raised 2→3 so the evidence chunk is not capped out) | `3` |
| `TORTOISE_LME_CONTEXT_ITEMS` | reader context ITEM cap (C1 #1745 — the token budget rarely binds at ~114 tok/item, so the item cap bounds reader flood; TR questions keep the pinned `--tr-top-k` item cap) | `40` |
| `TORTOISE_LME_EVIDENCE_BOOST` | C2 evidence-mark rank boost gate (OFF by default in code — ON only for the re-validation run; only `1/true/yes/on` enables; explicit `--evidence-boost`/`--no-evidence-boost` beat it) | unset = OFF |
| `TORTOISE_LME_EVIDENCE_BOOST_VERBATIM` | verbatim/raw-chunk mark rank-offset multiplier (C2 #1745) | `1.5` |
| `TORTOISE_LME_EVIDENCE_BOOST_SOURCE` | source-session-only mark rank-offset multiplier (C2 #1745) | `1.15` |
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
`--chunk-turns N`, `--context-cap N`, `--max-chunks-per-session N`,
`--context-items N` (C1 #1745), `--evidence-boost`/`--no-evidence-boost`
`--evidence-boost-verbatim F` `--evidence-boost-source F` (C2 #1745)
(R1/R1C knobs — env-first, CLI overrides, all validated ≥ 1; the C2 boost
is OFF by default in code, ON only for the re-validation run),
`--integrity-threshold F` / `--integrity-justification <text>` (M7 #1527 +
#1747 census-class-aware: override the `integrity.valid` RATE criterion —
max allowed `invalid_rate` over questions with recoverable-class signals
(parse_error/truncated/truncated_parse_error/partial_parse/transient_*/s1_chunk_summary
census classes, reader/judge/ingest:retries_exhausted eval failures); hard-failure
questions (fatal_*/ingest/unknown census classes, non-census error strings
with an empty census, permanent eval failures, malformed inputs — present
non-bool `valid` / non-iterable or non-str `error_classes`) VETO at any
threshold — no override admits them; the override is recorded with its
justification, and a *violated* override still yields `valid=false`. NOTE:
the CLI default stays 0.0 (strict), but the run-protocol step-5 500-Q
baseline injects the justified default `JUSTIFIED_BASELINE_THRESHOLD`
(0.02) — see `run_protocol.py`; an operator override suppresses the
injected baseline justification (the recorded reason never claims the 0.02
baseline for a non-baseline threshold). NOTE (#1747 round-17): the
step-5 injection is SCOPED to step 5 — the step-8 (1k benchmark) and
step-9 (R6/E6 follow-up, a full 500-Q run) build commands carry the
strict 0.0 CLI default, so the owner must pass `--integrity-threshold F`
with an `--integrity-justification` for those runs (the #1747 failure
mode — `valid=true` unreachable at scale because any recoverable-class
blip pushes `invalid_rate > 0` — would otherwise silently recur on the
follow-up's ~24k session extractions; the step-5 pattern applies).

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

## Parse-error robustness (#1746) — census, readout, probe, JSON-mode parity

**Error census vocabulary (D1, extractor side — every class appends an
`errors` string AND bumps `error_census[class]`, 1:1 enforced):**

| Class | Meaning |
|---|---|
| `parse_error` | S2/S4 final parse failure, first parse-failing attempt `finish_reason != "length"` (sloppiness/contamination) |
| `truncated_parse_error` | S2/S4 final parse failure, first parse-failing attempt `finish_reason == "length"` (truncation) |
| `partial_parse` | schema-validated partial-accept applied (truncated tail dropped — the embed list is INCOMPLETE; `valid=false`) |
| `empty_embed_list` | no embed list produced (S2/S4 empty) |
| `s5_failed` | S5 (embed execution) exception |
| `entity_resolution_failed` | entity-resolution exception |
| `s1_chunk_summary` | the "N/M S1 chunks failed" summary line (one bump per summary event) |

The invariant `len(errors) == sum(error_census.values())` holds structurally
(criterion 2) — every `errors.append` site pairs exactly one census class.

**Warning-only telemetry (D7 — NEVER error strings, NEVER in
`error_census`, `valid` unaffected):** `llm_calls` / `llm_retries` /
`llm_truncated` / `recovery` ride each outcome and the Layer-1 projection.
The report's `integrity.truncated_valid_qids` lists every question with
`llm_truncated > 0` AND no error classes — the silent-truncation success
class is now RECORDED (criterion 3 structural: no UNRECORDED truncation
with `valid=true`). `recovery` counts the parse-ladder's
`sanitize` / `sanitize_insufficient` / `repair` events.

**Recovery ladder (D4, extractor side):** S2/S4 output is parsed through a
bounded ladder — canonical `_parse_json` → string-aware sanitize (raw C0
control chars inside strings; H2) → bounded repair (≤8 closer appends +
bounded missing-comma rules, schema-gated; H3/H2 intersection) →
schema-validated partial-accept (longest valid prefix ≥ 1 non-empty embed
section; H3) → error-informed re-prompt. A first parse-failing attempt with
`finish_reason == "length"` SKIPS the deterministic same-prompt retry;
stop-class failures get ONE error-informed re-prompt carrying the bounded
parse-error block.

**JSON-mode parity (D6):** `DeepSeekDirectModel.complete` now sends
`response_format: {"type": "json_object"}` when `TORTOISE_JSON_MODE=1`
(the default, read at call time) AND the prompt requests JSON ("json"
present in the prompt text, case-insensitive — #1782) — mirroring
`OpenRouterModel`; the pilot's direct route previously ran WITHOUT JSON mode
(H1, the untested lever). DeepSeek returns HTTP 400 if the mode is set but
the prompt lacks the text "json", so non-JSON calls (the preflight
probe/ping) omit the mode. `TORTOISE_JSON_MODE=0` is the documented escape.
JSON mode does NOT fix truncation (it breaks at `max_tokens`) — the
recovery ladder is the truncation pairing; no cap raise in #1746.

**Pre-flight probe (D6):** before trusting JSON mode in a run, test the
provider for pennies:

```bash
python tools/longmem_eval/probe_json_mode.py --n 10 \
  [--model deepseek/deepseek-v4-flash] [--out /tmp/probe.json] [--dry-run]
```

The verdict JSON (`verdict` ∈ {honored, ignored, rejected, inconclusive} +
per-mode malformed-rate/finish_reason blocks + `mode_delta`) lands in the
closing-run record (Task 5 of the #1746 plan). The closing-run record
should also cross-tabulate `recovery["repair"]` with `llm_truncated > 0`
per question, so truncation recovered as a content-complete repair is
visible alongside the `truncated_valid_qids` list. `rejected` (any HTTP
400/404) → abort the run pre-flight or re-run with `TORTOISE_JSON_MODE=0`;
`inconclusive` → re-probe at `--n 20`; `honored`/`ignored` → proceed with
the verdict noted. The probe exercises the PILOT's path (the same
`build_extractor_model` resolution the eval run uses).

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
   is **budget-capped and rank-interleaved** (C1 #1745, replacing R1's
   points-first UX decision 3): extracted points and raw turn-granular
   chunks render in true RRF rank order, bounded by the token budget
   and the `context_item_cap`. Reports session-level recall@k (fraction of
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

- **`integrity`** — `valid` (#1747 census-class-aware:
  `valid = (n_hard_invalid == 0) AND (n_excluded_hard == 0) AND
  (invalid_rate ≤ threshold) AND (outcome-derived attempted set non-
  empty whenever any entry was excluded or dropped — a fully
  excluded/dropped run never certifies; failures do not count as
  attempts for this guard)` — fatal_*/ingest/unknown/non-census-error-string
  (empty-census)/permanent-eval-failure questions, and malformed inputs
  (present non-bool `valid`, non-iterable, non-str, or falsy-but-present
  NON-CONTAINER `error_classes` — 0 / "" / False / a PRESENT null;
  empty dict/list are the legitimate no-census shapes and grade clean)
  fail closed to hard and veto at any threshold; an
  EXCLUDED outcome (shape-broken dict, or a breaker_open vector-arm drop)
  still vetoes when it carries a hard census class — malformed shapes
  cannot launder a fatal class out of the gate (`n_excluded_hard`), and a
  run whose entire outcome set was excluded OR dropped (e.g. a vector-arm
  outage that breaker-tripped every question) never certifies valid (a
  truly
  empty report stays vacuously valid)),
  `n_attempted` / `n_valid` / `n_invalid`, `invalid_rate` (invalid = a
  failed question OR a completed question with error-class/extraction-error
  signals; recoverable classes — parse_error/truncated/
  truncated_parse_error/partial_parse/transient_*/s1_chunk_summary, plus
  reader/judge/ingest:retries_exhausted eval failures — are rate-limited, not
  vetoed), the #1747 breakdown `n_hard_invalid` /
  `n_recoverable_invalid` / `recoverable_invalid_rate` /
  `n_excluded_hard`,
  `n_excluded` (entries dropped by the entry shape filter — malformed
  checkpoint JSON in outcomes OR failures; the denominator shrink is
  observable, never silent),
  `error_census`
  (site-prefixed P2-aligned error classes: `reader:retries_exhausted`,
  `judge:fatal`, …), `error_census_malformed` (non-int counts recorded
  verbatim per class; non-str legacy flat-list junk under the
  `<legacy-list>` sentinel key; a PRESENT malformed top-level
  `error_classes` shape under `<malformed-top-level>` — no malformed
  evidence vanishes at any level), `criterion` (the applied gate rule,
  human-readable), `checks`
  (python guard, dataset audited, audit present, fingerprint matched,
  census computed). The integrity block prints BEFORE the score; the
  additive breakdown fields (`n_hard_invalid` / `n_excluded` / `criterion`
  / …) ride the persisted report JSON. NOTE: a failure entry WITHOUT an
  `error_class` (e.g. the full-context cell producer) grades hard —
  fail-closed — until the #1746 lane wires site-prefixed classes.
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

## LLM token usage + cost (#2185)

Every real LLM call in the eval pipeline (reader, judge, extractor_v2) is
metered through an additive `usage_sink` seam on the chat transports
(`OpenRouterModel` / `DeepSeekDirectModel` / `VeniceModel` /
`OpenAICompatModel` / the eval's `OfficialJudgeModel` — all no-op by
default) and collected per question by the harness collector
(`tools/longmem_eval/usage.py`). Mock runs call no LLM → **zero usage
keys** anywhere (reports stay byte-identical to pre-#2185, pinned).

**Per-question outcomes** (completed questions only) conditionally carry
`llm_usage` — an envelope bucketed by `(stage, provider, model)` with
`by_stage` + a flat `total` (`prompt_tokens` / `completion_tokens` /
`calls`); the projection emits the key ONLY when present (the `rerank_pass`
conditional pattern — never a null). Cache/reasoning detail keys ride the
per-lane buckets when the provider reports them.

**The report's conditional top-level `usage` block** appears iff ≥1
completed outcome carries `llm_usage` OR overhead rows exist (breaker-open
/failed-question / preflight spend). Schema:

- `per_question` — `{qid: {prompt_tokens, completion_tokens, calls,
  cost_usd, priced, estimated}}` over the completed outcomes;
- `totals` — Σ per-question + overhead tokens/calls;
- `overhead` — ONLY when rows exist: breaker-open drops, failed questions,
  preflight/keyless spend (tokens/calls/cost + per-lane `lanes`). Never
double-counted: per-question usage lives on the outcomes; everything else
rides the drained collector envelope (replicas on failure entries are the
kill-9-safe durability copy, folded shortfall-only on resume — A4);
- `cost` — `usd` run total, `priced`, `estimated`, and the per-data-point
  USD over the evidence-bearing WITH-usage subset
  (`evidence_written > 0` and envelope present):
  `data_point_usd` / `data_point_n` / `data_point_priced`;
- `coverage` — `evidence_bearing` / `with_usage` counts; the `partial`
  marker is emitted when `0 < with_usage < evidence_bearing` (a mixed
  report — e.g. resumed pre-#2185 outcomes — is disclosed, never silent);
- `priced` — False when ANY lane was unpriced (unknown model OR a
  `usage_present=False` lane), with the offending lanes listed in the
  cost breakdown — loud, never a silent $0;
- `pricing` — snapshot: `map_version` / `git_sha` / `verified_on` (also
  recorded in `methodology.usage_pricing`).

**Pricing map** (`tools/longmem_eval/costing.py`): versioned
per-(provider, model) USD-per-1M table (`PRICING_MAP_VERSION`), computed
at report time from the RAW envelopes (outcome `llm_usage` is never
mutated — a map correction can reprice any future report from the same
raw tokens, but an already-published report's block is a fixed snapshot →
COGS corrections are FORWARD-ONLY for published artifacts). Provenance
discipline per entry: `source` (URL/page) + `verified_on` + `estimated`
(True = single-source or cross-check-conflicting — surfaced in the
breakdown, never silently asserted). Cache-hit input tokens price at the
reduced `cache_read_per_1m` rate ONLY where the map entry carries a
verified rate; otherwise full-priced with a `cache_discount_unpriced`
flag. Out-of-map lanes never crash and never guess: `priced=False` with a
reason.

**Out-of-coverage producers**: producers that run a real LLM OUTSIDE the
runner pipeline (e.g. `tools/longmem_eval/full_context.py`
`--limit` cells, the product spot-check `tools/ask_spotcheck.py` judge
lane) do not register a collector — their reports carry no `usage` block
until a collector registers them. Only the `run_main`/`run_evaluation`
pipeline records usage today; retrieval-only runs never call the reader/
judge → no usage keys.

**Bounded-loss windows** (round-2 code-review, PR #2250 — same status as
the documented post-drain late-fire bound above):

- A kill-9 between a SUCCESS question's usage drain and the trailing
  checkpoint write loses that envelope (no replica exists on the success
  path); on resume the question re-runs and re-bills, so the report counts
  the re-run's spend only. Window is the drain→save gap under flock.
- Failure/breaker-open spend is protected by the A4 replica (kill-9-safe
  failure-entry usage); the replica is the CUMULATIVE qid envelope
  (payload + drained rows), so a `--retry-failed` re-attempt that burns
  fewer tokens than the persisted payload still folds its exact un-saved
  delta on the next resume.

```bash
# offline smoke (--mock mocks reader+judge only — the extractor is real,
# so the post-#2185 smoke report GAINS exactly one top-level key: "usage")
uv run python -m tools.longmem_eval.run_protocol smoke --mock
```

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
