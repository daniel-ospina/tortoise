# Tortoise Retrieval Quality Eval (issue #1144)

The in-house retrieval eval — the quality arm of the ongoing retrieval
optimization loop (speed arm: `benchmarks/` #316). Measures retrieval
QUALITY (not latency) per strategy — FTS / vector / structural / TF-IDF —
and fused RRF, over graded 3-level relevance judgments, with paired 90%
bootstrap CIs and the pre-registered SHIP/WARN/BLOCK gate.

**The `tfidf` arm is the sparse leg, by construction (#2678).** It pins the
sklearn TF-IDF scorer (`force_sparse=True`), so it is deterministic and
embedder-independent. The default scorer resolves a cached bge embedder and
would silently turn the column into a second semantic arm — near-duplicating
`vector` in the authoritative docker run. The oracle's `tfidf` proofs and this
arm now measure the same scorer in every environment.

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
# embedded — fast, NOT prod-parity (no HNSW; vector runs brute-force).
# NOTE: the FULLTEXT index EXISTS on embedded (indexes.fts=true; FTS
# populated 40/100 queries in the committed baseline) — FTS/structural
# columns are environment artifacts here, not quality statements.
python -m tests.eval.retrieval.run \
    --db /tmp/tortoise-eval.db --corpus-size 2000 --out /tmp/report.json \
    --pool-out /tmp/pool.json
```

⚠️ That db path is FIXED, and the corpus is `MERGE`d, never cleared: re-running
at a SMALLER `--corpus-size` leaves the previous run's tail holding the vectors
it was written with, so the run is `unknown` (`provenance.corpus_points_unverified`
counts it) and the gate refuses. Use a fresh db path per smoke, or keep the
corpus size at or above what the db already holds.

## One embedding space, always

The vector arm is only a measurement if the query vectors and the corpus point
embeddings live in the SAME space. The seeded corpus ships the oracle's
**synthetic** topic-centroid embeddings (`benchmarks/synthetic_corpus.py`,
`ORACLE_EMBED_ALPHA`), and a `model`-space run RE-EMBEDS it (`_reembed_corpus`)
so both sides share one space.
So the space is decided from the CORPUS side, once per run
(`_corpus_embedding_space`), never from ambient model availability:

| environment | space | what runs |
|---|---|---|
| no embedder (`uv sync`, CI offline) | `oracle-synthetic` | queries = topic-centroid sums (documented degraded fallback) |
| embedder loaded (`uv sync --extra embeddings`) | `model` | the seeded corpus is RE-EMBEDDED with that model (`corpus_reembedded` in provenance), queries too |
| `--no-seed-corpus` (any environment) | `unknown` | the vectors already in the graph were not written by this run — an earlier eval seed's centroids, a corpus an earlier MODEL-space run re-embedded, and product embeddings are indistinguishable — so no space is claimed: the reported metrics are not a quality statement and the gate refuses (drop the flag to gate a cycle) |
| this run rewrote only PART of the graph's vectors (a smaller `--corpus-size` over an existing db, or product points on a shared docker graph) | `unknown` | seeding and the re-embed only touch the ids this run generated (`p-{i:07d}`, positional) and nothing deletes the rest, so a leftover tail keeps the space it was written in — while retrieval is graph-wide, so the arm's space IS the whole index's. `provenance.corpus_points_unverified` counts that population and the gate refuses |

`provenance.corpus_embedding_space` names which one a report used. **It is not
cosmetic.** Before this rule existed the query side silently took the ambient
embedder while the corpus kept centroid vectors, and in the canonical dev env
that produced vector nDCG@10 0.108 (vs 0.826 sound) and a **BLOCK verdict on
unmodified code** — see
`docs/benchmarks/1144-retrieval-eval-embedding-space-2026-11-08.md`.

The rule is ENFORCED, not documented: `_query_vecs()` requires `space` and has
no ambient default, so a caller cannot acquire the two-space arm by omission.
`task0_probe.py` (depth curve, k-sweep, ceiling — it prints fused-nDCG verdicts)
seeds its own corpus and therefore owns the same decision: it resolves the
space from that corpus, re-embeds when the space is the model's, reports
`corpus_embedding_space` / `corpus_reembedded` / `corpus_points_unverified` in
its JSON, and reads `unknown` when the index holds vectors it did not write.

## Full run (authoritative — Docker FalkorDB >= 4.x)

```bash
# 1. Docker FalkorDB (HNSW + FTS indexes auto-created at boot).
# 2. The embeddings extra is OPTIONAL and does not change what is measured:
#    with it, the corpus is re-embedded into the model's space (one space);
#    without it, both sides are the oracle's synthetic space. Either way the
#    report says which in provenance.corpus_embedding_space.
#       pip install -e '.[embeddings]'
#    Point it at a graph this run owns: any pre-existing point the run does NOT
#    rewrite (an earlier, larger corpus; product points on a shared instance)
#    makes the space `unknown` and the gate refuses — deliberately, since those
#    vectors' space cannot be established from here.
python -m tests.eval.retrieval.run --db docker://falkor:6379
```

## The gate (every optimization cycle)

```bash
# baseline = the committed "where we stand" run, in YOUR embedding space:
#   baseline-embedded-model-2026-11-08.json       → corpus_embedding_space: model
#   baseline-embedded-2026-08-17.json (schema 1)  → oracle-synthetic
# The space must MATCH or the gate returns INCOMPARABLE (it names which two it
# saw). Pair the model baseline with a host that has the embedder installed
# (`pip install -e '.[embeddings]'`); without it the run is oracle-synthetic
# and belongs against the schema-1 baseline.
python -m tests.eval.retrieval.run --db /tmp/new.db \
    --baseline tests/eval/retrieval/baseline/baseline-embedded-model-2026-11-08.json
```

Gate (pre-registered): **SHIP** iff the paired 90% CI on ΔnDCG@10 (points,
vs baseline, paired on identical query ids) does not exclude −2; **WARN**
−2…−4; **BLOCK** < −4 or a significant P@5 drop (paired CI below 0). The
loop ships a change only when speed improved (the #316 arm) AND this gate
returns **SHIP**.

⚠️ **`INCOMPARABLE` is NOT a pass.** It is the gate declining to make a
statement — the run and its baseline did not share one instrument (space, or
model id inside the `model` space), so no delta was computed and **no
quality evidence exists for this cycle**. A cycle that ends `INCOMPARABLE`
has measured nothing: re-baseline in the run's own space and re-run before
shipping anything. Reading it as "did not block, therefore ships" is the
same defect this gate exists to remove, mirrored — the pre-fix gate could
never pass, and this verdict could never fail.

**A cross-space comparison is refused, not scored.** A run and its baseline in
different embedding spaces differ by the INSTRUMENT, so `_gate_vs_baseline`
returns `INCOMPARABLE` (naming the remedy: commit a run of unmodified code in
that space) instead of a BLOCK. The same holds inside the `model` space for a
different model id: `--model <candidate> --baseline <a baseline measured with
another model>` is refused, because the delta would be the swap — gate an
embedder change by committing a baseline measured with the candidate on
unmodified code. A baseline's space comes from its own
provenance — and when it declares none, `embedding_model` records what that run
DID: absent/`unavailable` → `oracle-synthetic`; a real model id → `unknown`,
because a pre-fix run re-embedded nothing and measured the mixed
corpus-synthetic / query-model arm (so the gate refuses instead of scoring it).

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
