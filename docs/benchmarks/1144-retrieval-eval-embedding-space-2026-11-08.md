---
title: "#1144 — the retrieval eval's two embedding spaces, and the false BLOCK it produced"
type: runbook
domain: benchmarks
doc_status: live
created: 2026-11-08
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# #1144 — the retrieval eval's two embedding spaces

**Status:** fixed in the eval runner. No product behaviour changed; the eval is
the loop's yardstick, and the yardstick was measuring its own instrument.

**Evidence:** `tests/eval/retrieval/baseline/baseline-embedded-model-2026-11-08.json`
(the new model-space baseline) and `tests/eval/retrieval/README.md` §"One
embedding space, always".

---

## 1. The finding

`#1144` owns the ongoing retrieval optimization loop, whose gate is
*"measure → optimize one bottleneck → verify speed improved AND quality didn't
regress"*. The quality half is `tests/eval/retrieval/`. Measured 2026-11-08 on
`main`'s own tree (`e66450bb36`), **no edit applied**, canonical dev env
(`uv sync --extra embeddings --extra parity`), embedded lane, corpus 2000,
seed 42:

| strategy | nDCG@10 | P@5 | R@10 | MRR |
|---|---|---|---|---|
| fts | 0.800 | 0.882 | 0.094 | 0.803 |
| **vector** | **0.108** | **0.170** | **0.009** | **0.098** |
| structural | 0.005 | 0.006 | 0.000 | 0.009 |
| tfidf | 0.828 | 0.918 | 0.097 | 0.820 |
| **fused** | **0.462** | **0.526** | **0.054** | **0.587** |

```text
gate vs baseline: BLOCK
  ΔnDCG@10 90% CI [-42.1, -32.5] points   (block band is < -4)
  ΔP@5     90% CI [-44.0, -35.4] points
```

The committed baseline (`baseline-embedded-2026-08-17.json`) reads vector
0.855 / fused 0.835. So **the loop's gate told every future cycle that main had
regressed by 37 nDCG points** — a verdict no cycle could ever clear, on code
nobody had touched. That is a gate that cannot pass, and its BLOCK band is
indistinguishable from a real one.

## 2. Root cause — two spaces, one arm

The seeded corpus carries the oracle's **synthetic** topic-centroid embeddings
(`benchmarks/synthetic_corpus.py`: `_vector(rng)` clustered around a per-topic
centroid, `ORACLE_EMBED_ALPHA = 0.75`). Nothing in the repository ever embeds
the corpus with a real model.

`run.py::_query_vecs` nevertheless chose the QUERY side's space from ambient
model availability:

```python
use_model = EmbeddingModel.get() is not None   # ← the whole decision
```

`EmbeddingModel.get()` is not `None` exactly when the `embeddings` extra is
installed — which is the canonical dev environment, and the very extra
`AGENTS.md` tells lanes to install. So the vector arm compared **bge text
vectors against centroid vectors**: same dimension, unrelated spaces, cosine
similarity ≈ noise. The fused RRF leg then ranked largely on that noise and
collapsed with it.

`_query_vecs`'s own docstring said *"Embedded mode: synthetic … Docker mode:
the real embedding model"*, and a report note asserted *"Query vectors on
embedded are SYNTHETIC … because no embedding model is loaded"* — both false
in the canonical env. The prose described the intent; the code read the
environment.

This is `#2678`'s defect class on the sibling arm. That fix pinned the `tfidf`
arm to ONE scorer because *"the arm must be the same scorer in every
environment, or the label lies"*; the `vector` arm had the same hole, and the
`vector` arm is the one the fused line is built on.

## 3. The fix — the space is decided from the corpus

`_corpus_embedding_space()` resolves ONE space per run, before anything is
measured, and never from ambient availability:

| condition | space | what happens |
|---|---|---|
| a model is loaded, this run seeded the synthetic corpus | `model` | `_reembed_corpus()` re-encodes every seeded point with that same model (production write shape: clear-then-set guard + `vecf32`); queries encode with it too |
| no model | `oracle-synthetic` | the documented degraded fallback on BOTH sides |
| `--no-seed-corpus` (any environment) | `unknown` | the vectors already in the graph were not written by this run (an earlier eval seed's centroids and product embeddings are indistinguishable); no space can be claimed, and a loaded model does not launder one |

Labels are untouched: the oracle grades from HIDDEN topics and per-topic live
ids, never from embeddings, so re-encoding cannot move a relevance judgment.
`provenance.corpus_embedding_space` and `provenance.corpus_reembedded` state
which space a report measured, and the corpus fingerprint (`embeddings_hash`)
is taken AFTER the re-embed — a fingerprint taken before it reads identically
in both spaces, so it could not detect the change it exists to detect.

`_gate_vs_baseline` refuses a cross-space comparison with a new verdict,
`INCOMPARABLE`, naming the remedy (commit a run of unmodified code in this
space) instead of reporting a regression the change did not cause. A
baseline's space comes from its own provenance; when it declares none, it is
read from `embedding_model` as a record of what that run DID: absent or
`unavailable` → `oracle-synthetic` (no embedder, so both sides came from the
oracle), and a real model id → `unknown`, because a run that predates this fix
re-embedded nothing and therefore measured the MIXED corpus-synthetic /
query-model arm — `unknown` makes the gate refuse rather than certify the
instrument change as a ~+70-point improvement.

## 4. After — and what the number now means

Same box, same lane, same corpus/seed, model space:

| strategy | nDCG@10 | P@5 | R@10 | MRR |
|---|---|---|---|---|
| fts | 0.814 | 0.886 | 0.096 | 0.814 |
| vector | 0.826 | 0.884 | 0.099 | 0.825 |
| structural | 0.005 | 0.006 | 0.000 | 0.009 |
| tfidf | 0.828 | 0.918 | 0.097 | 0.820 |
| fused | 0.817 | 0.888 | 0.096 | 0.841 |

`gate vs baseline`: **INCOMPARABLE** (this run is `model`; the schema-1
baseline is `oracle-synthetic`) — the honest answer, and the reason
`baseline-embedded-model-2026-11-08.json` is committed alongside it. Gating a
baseline against itself returns SHIP in both spaces (checked), so the wiring
is exercised rather than merely present.

**The capability this buys.** The old vector arm was structurally blind to an
embedder change: both sides of the cosine came from the oracle, so `#1349`'s
swap — the single largest lever in the loop's queue — could not move this
number at all. Real bge over the topic-vocab corpus scores 0.826 against the
centroid arm's 0.855: close enough to be a sanity check on the oracle's
geometry, and now sensitive to the thing the loop actually changes.

## 5. Residuals, stated rather than hidden

- **The `fts` arm varies run to run.** Four runs of identical inputs at
  corpus 2000 measured nDCG@10 0.800 / 0.806 / 0.809 / 0.814. The gate's band
  (±2 points) absorbs it, but a single run is not a fixed point — the FTS
  column is tie-order sensitive on the embedded engine, and it is already
  declared an environment artifact there.
- **Embedded is not prod-parity.** No HNSW; the vector arm runs a brute-force
  scan. Docker FalkorDB stays authoritative for a decision-grade number; the
  space rule is the same in both lanes.
- **The model-space baseline is one run.** It is a yardstick, not a paper
  claim; regenerate it in the space and lane you will gate in.
- **The authored 50-query set is still `labels_pending`** (judges +
  owner adjudication, `judge.py`). Unchanged by this fix; the oracle core is
  what produces every number above.

## 6. Related, not fixed here

`tests/eval/retrieval/test_run.py::test_model_flag_injects_before_query_vecs_and_records_provenance`
times out (600 s, inside `torch.nn.functional.linear`, after
`Loading weights 199/199`) on a starved host — **reproduced on the pristine
`e66450bb36` tree with the original test file**, so it is not this change.
Same shape as `#10045` (a single model call stalling past the timeout while
the runner is starved); evidence added there.
