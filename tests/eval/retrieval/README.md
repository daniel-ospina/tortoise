# Tortoise Retrieval Quality Eval (issue #1144)

The in-house retrieval eval — the quality arm of the ongoing retrieval
optimization loop (speed arm: `benchmarks/` #316). Measures retrieval
QUALITY (not latency) per strategy — FTS / vector / structural / TF-IDF —
and fused RRF, over graded 3-level relevance judgments, with paired 90%
bootstrap CIs and the pre-registered SHIP/WARN/BLOCK gate.

Design locked in the issue (comment 2026-08-17): 150 queries (100
synthetic-oracle + 50 authored over the real internal graph domain), metrics
nDCG@10 (headline) + P@5 + R@10 + MRR, per-strategy AND fused, paired 90%
bootstrap CIs, two cross-vendor LLM judges (temp 0.0, κ ≥ 0.60 via
tools/kappa.py) + owner adjudication (≥85% acceptance, ≥10% sample) for the
authored set. n=100 matches the pre-registered power math (detect ≥5% P@10
delta).

## The oracle (why the old corpus couldn't measure anything)

`benchmarks/synthetic_corpus.py` generates the #316 corpus by drawing every
point from ONE shared token pool — any point containing query tokens
matches, so P@K ≈ 1.0 for every strategy and no quality signal exists. The
new **latent-topic oracle layer** fixes this:

- TOKENS is partitioned into 24 topics (sequential chunks — thematically
  coherent, mirroring real docs).
- Each seeded point gets a HIDDEN topic; content draws only from the topic
  vocab, embeddings cluster around the topic centroid
  (`ORACLE_EMBED_ALPHA = 0.75`). The topic is never written to the graph.
- Each topic boundary has a BRIDGE token shared with the next topic —
  controlled distractors: near-topic points token-match queries but grade 1.
- Relevance is DERIVED deterministically: grade 2 = target-topic points,
  grade 1 = NEAR (bridge-sharing) topics, grade 0 = everything else. No
  judges, no human labels, fully repeatable from the seed.

Tiers (50/30/20): easy = query tokens all in the target core (near-perfect
retrieval expected); medium = one bridge token (mild ambiguity); hard =
"ambiguous near-miss" text dominated by a NEAR topic (latent intent vs
surface vocabulary mismatch — the case reranking #317 targets).

## Query sets

`tests/eval/retrieval/queries/`:
- `oracle_queries.json` — 100 oracle queries (53 from the #316 query mix +
  47 new). q052/q055/q056 are excluded (zero token overlap with the corpus
  → no oracle target; they stay #316 latency probes).
- `authored_queries.json` — 50 queries over the real internal graph domain
  (anchor slice). Relevance is subjective → LLM judges + owner adjudication.
  No logs required.

## Quickstart (embedded smoke)

```bash
# embedded — fast, NOT prod-parity (no FTS/HNSW; FTS degrades, vector runs
# brute-force, synthetic semantic query vectors stand in for the embedding
# model). FTS/structural columns are environment artifacts here.
python -m tests.eval.retrieval.run \
    --db /tmp/tortoise-eval.db --corpus-size 2000 --out /tmp/report.json \
    --pool-out /tmp/pool.json
```

## Full run (authoritative — Docker FalkorDB >= 4.x)

```bash
# 1. Docker FalkorDB (HNSW + FTS indexes auto-created at boot).
# 2. Optionally the embeddings extra so query vectors encode natively:
#       pip install -e '.[embeddings]'
python -m tests.eval.retrieval.run --db docker://falkor:6379
```

## The gate (every optimization cycle)

```bash
# baseline = the committed "where we stand" run
python -m tests.eval.retrieval.run --db /tmp/new.db \
    --baseline tests/eval/retrieval/baseline/baseline-embedded-2026-08-17.json
```

Gate (pre-registered): **SHIP** iff the paired 90% CI on ΔnDCG@10 (points,
vs baseline, paired on identical query ids) does not exclude −2; **WARN**
−2…−4; **BLOCK** < −4 or a significant P@5 drop (paired CI below 0). The
loop ships a change only when speed improved (the #316 arm) AND this gate
does not block.

## Judges + adjudication (authored set)

```bash
# 1. judge the emitted pool with two cross-vendor models (temp 0.0)
python -m tests.eval.retrieval.judge run --pool /tmp/pool.json \
    --model-a deepseek-v4-pro-noreason --model-b qwen3.8-max \
    --out-dir /tmp/judges
#    → judgeA.json / judgeB.json / agreement.json (kappa + gate)

# 2. owner adjudication on disagreements (>= 85% acceptance, >= 10% sample)
python -m tests.eval.retrieval.judge adjudicate \
    /tmp/judges/judgeA.json /tmp/judges/judgeB.json \
    --pool /tmp/pool.json --emit-template /tmp/rulings.json
#    → fill owner_ruling in /tmp/rulings.json
python -m tests.eval.retrieval.judge adjudicate \
    /tmp/judges/judgeA.json /tmp/judges/judgeB.json \
    --apply-rulings /tmp/rulings.json --out /tmp/labels.json

# 3. rerun the eval with merged labels → authored metrics
python -m tests.eval.retrieval.run --db /tmp/tortoise-eval.db \
    --judge-labels /tmp/labels.json
```

Oracle labels are deterministic — the synthetic core never needs judges.

## Tests

```bash
TORTOISE_DB_URI= python3.12 -m pytest tests/eval/retrieval/ -q
```

Unit tests cover the metric math (nDCG/P@5/R@10/MRR with hand-computed
values), oracle determinism + the property that the oracle makes strategies
distinguishable (P@K < 1.0 for at least one strategy), bootstrap CI
correctness + the gate bands, and the judge harness (mock models).
