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
| `TORTOISE_LME_READER_MODEL` | reader model spec `<provider>:<model>` or bare | `openrouter:deepseek/deepseek-chat` |
| `TORTOISE_LME_JUDGE_MODEL` | judge model spec | `openai:gpt-4o-2024-08-06` (the official judge model) |
| `TORTOISE_LME_CACHE_DIR` | dataset cache dir (outside the repo) | `~/.cache/tortoise-longmemeval` |

CLI flags: `--split s|m|oracle`, `--limit N`, `--data <local json/jsonl>`,
`--k 5,10,20`, `--top-k 20`, `--mock`, `--output <report.json>`,
`--cache-dir`, `--no-download`, `--work-dir`,
`--checkpoint <state.json>` (partial-results checkpoint + resume),
`--max-retries N` (per-question LLM-call retries with exponential backoff;
questions that still fail are recorded in `report['failures']` and the run
continues — one transient error never aborts the 500-Q run).

## Dataset

Official **`xiaowu0162/longmemeval-cleaned`** (the canonical cleaned
replacement for the deprecated `xiaowu0162/longmemeval`) — fetched on demand
via urllib into the cache dir, **never committed** (tens of MB). A local
path via `--data` skips the download. Split S = `longmemeval_s_cleaned.json`
(~40 sessions ≈ 115k tokens of history per question).

## Pipeline (per question, isolated fresh graph)

1. **Ingest** (`tools/longmem_eval/ingest.py`) — deterministic, no LLM keys:
   `:Session` nodes, episodic turn Points (`pointKind=event`, `[role] text`,
   `has_answer` stamped on evidence turns), and one raw verbatim
   transcript Point per session (`pointKind=session-transcript` — the "index
   raw text too" leg that mitigates the competitor-RAG verbatim-recall edge).
   Idempotent re-runs (MERGE + existence guards).
2. **Retrieve** (`retrieve.py`) — production hybrid search
   (`TortoiseSDK.tortoise_fts_query`: RRF fusion of FTS + vector +
   structural with the TF-IDF degradation fallback; embedded/CI degrades
   automatically, Docker/HNSW uses the full stack). Reports session-level
   recall@k (fraction of `answer_session_ids` in top-k) and turn-level
   recall@k (`has_answer` turns), plus the context handed to the reader.
   Recall is measured over the **isolated per-question corpus** (each
   question's haystack in its own fresh graph), NOT the official full-corpus
   indexing — stated explicitly in the report's methodology so numbers are
   never misread as paper-comparable recall.
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
