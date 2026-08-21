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

CLI flags: `--split s|m|oracle`, `--limit N`, `--data <local json/jsonl>`,
`--k 5,10,20`, `--top-k 20`, `--mock`, `--output <report.json>`,
`--cache-dir`, `--no-download`, `--work-dir`,
`--checkpoint <state.json>` (partial-results checkpoint + resume),
`--max-retries N` (per-question LLM-call retries with exponential backoff;
questions that still fail are recorded in `report['failures']` and the run
continues — one transient error never aborts the 500-Q run),
`--chunk-turns N`, `--context-cap N`, `--max-chunks-per-session N`
(R1 #1540 knobs — env-first, CLI overrides, all validated ≥ 1).

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
   retrieval recall@k, context-token means, latency (retrieval/reader/judge
   per question, mean/p50/p95), and the methodology provenance (dataset id,
   split, reader/judge models, judge rule, extraction approach, k values,
   token estimator, git sha, run timestamp).

## Full run prerequisites

- The dataset (~tens of MB; auto-downloaded to the cache dir — needs
  network) and
- provider keys (reader: e.g. `OPENROUTER_API_KEY`; official judge:
  `OPENAI_API_KEY`).

The full 500-question run is `@pytest.mark.slow` (never in CI); the
committed mini fixture + `--mock` exercises the entire pipeline offline in
CI (`tests/test_longmem_runner.py`).

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
