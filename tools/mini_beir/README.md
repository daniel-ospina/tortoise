# mini-BEIR research harness (#1349 Task 6)

Independent retrieval-quality signal across four BEIR datasets — **a research
surface ONLY, NOT a gate**. It feeds the #1349 multi-winner tiebreak and the
long-term monitoring baseline, and is deliberately **not** part of the
pre-registered gate rule (that is LongMemEval-S turn_recall@10 + nDCG@10).

```
python -m tools.mini_beir.run --model minilm                       # control
python -m tools.mini_beir.run --model arctic-s --query-prompt query
python -m tools.mini_beir.run --model arctic-xs --limit 10         # smoke
python -m tools.mini_beir.run --data-dir /path/to/beir-dir         # offline/local
```

Metrics: **nDCG@10 + R@10**, binary relevance (qrels score > 0, BEIR
convention). nDCG uses the in-repo binary-gain definition (DCG = Σ
rel_i/log₂(i+2); IDCG = all-relevant-first capped at k) — identical to the
LongMemEval gate arm, so the two surfaces stay directly comparable.

## ⚠️ Read this before using the numbers

1. **MS MARCO scores are NOT leaderboard-comparable.** The harness evaluates
   the qrels-constrained top-1000 dev queries against a **deterministic
   100k-passage sample** of the full 8.8M corpus (seeded reservoir, seed
   1349 — same sample every run). Full-corpus BEIR MS MARCO leaderboard
   numbers (nDCG@10 ≈ 0.35-0.40 for strong models) are **not** reproducible
   from a 100k subset — a 100k sample is a much easier retrieval surface.
   Treat MS MARCO here as **internal ranking signal only**.
2. **MS MARCO is in-domain contamination for arctic and bge** — both are
   trained on MS MARCO. Its mini-BEIR scores are a sanity floor/ceiling
   check, **not** a discriminator. The **OOD datasets (NFCorpus / SciFact /
   FiQA) carry the selection read** — weight them accordingly. The runner
   prints this warning on every MS MARCO evaluation and the results JSON
   records the note per dataset.
3. NFCorpus / SciFact / FiQA run on the **full corpus** (3,633 / 5,183 /
   57,638 passages) with the full qrels-constrained test sets (323 / 300 /
   648 queries) — these are comparable with published BEIR numbers at the
   standard binary-relevance nDCG@10 convention, though the identical-metric
   requirement only holds within this harness.

## Datasets & sources

Raw BEIR data (jsonl/tsv in zips) via **stdlib urllib only** — no parquet,
no `datasets`/`beir` packages, zero new top-level dependencies (mirrors
`tools/longmem_eval/dataset.py`). Official BEIR distribution (UKP
TU-Darmstadt):

| dataset | source | corpus | queries |
|---|---|---|---|
| MS MARCO | `https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/msmarco.zip` | 8.8M (100k sampled) | top-1000 dev |
| NFCorpus | `https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nfcorpus.zip` | 3,633 | 323 |
| SciFact | `https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip` | 5,183 | 300 |
| FiQA | `https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/fiqa.zip` | 57,638 | 648 |

Corpus documents are encoded as `title + " " + text` (standard BEIR
composition). Queries and documents encode through the injected model
(`tools.embedder_probe.inject_model`, T1) — the singleton IS the candidate;
`--query-prompt` threads via the model's default prompt, matching the
LongMemEval arm.

## Integrity: digest-pinned, never a partial corpus

- Downloads are **atomic** (temp `.part` + rename) and the zip is
  CRC-validated before promotion; extraction is per-file atomic.
- **nfcorpus / scifact / fiqa sha256s are pinned in code** (verified
  2026-08-17 against the official UKP zips).
- **msmarco's sha256s are recorded at first verified download** in a sidecar
  (`<cache>/msmarco.digest.json`) — the 1.1GB zip makes an author-time pin
  impractical; every later run verifies the cache against the recorded
  digest (sha256/size/fetched-date also recorded in the results JSON).
- A corrupt/truncated cache **re-downloads or raises a clear error**
  (`DatasetDigestError` / `--no-download`) — never a silently-partial corpus.
- Empty and 1-passage corpora produce **defined output** (nDCG@10 = 0,
  R@10 = 0), never a crash.

## Budgets (2GB-VM class, CPU)

One-time download per dataset into the cache
(`TORTOISE_MINI_BEIR_CACHE_DIR` or `~/.cache/tortoise-mini-beir`):

| dataset | download | disk (extracted) |
|---|---|---|
| msmarco | ~1.1GB | ~1.2GB (corpus.jsonl) |
| nfcorpus | ~2.4MB | ~6MB |
| scifact | ~2.8MB | ~4MB |
| fiqa | ~18MB | ~35MB |

Encode (CPU, MiniLM-class): nfcorpus+scifact+fiqa ≈ 66k passages ≈
2-4 min; msmarco 100k-passage sample ≈ 5-10 min; +1000 query encodes ≈ 1
min. Peak RAM ≈ 154MB for the 100k×384-dim float32 doc matrix + ~90MB model
+ batching — comfortably inside 2GB. The full 8.8M-passage MS MARCO corpus
is never materialized (streaming seeded-reservoir sample, one I/O pass).
Runtime budget on the benchmark box: ~15-25 min per model-config for all
four datasets.

## Output

One results JSON per model in the output dir (`tools/mini_beir/results/` by
default, committed per model for the burn): per-dataset nDCG@10/R@10 means +
per-query detail + corpus sampling metadata + dataset digests (sha256/size/
date) + probe state + methodology note. See `_methodology()` in run.py for
the exact metric definitions.

## Research surface, not a gate

These numbers are advisory: they feed the multi-winner tiebreak and the
monitoring baseline only. The swap decision is made by `gate_1349.py` on
the LongMemEval-S co-primary metrics — mini-BEIR is never a pass/fail gate.
