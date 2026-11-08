# W7-a 500-Q rows for `comparison-systems.md` (issue #2106 merge handoff)

> **From:** #2105 (W7-a sealed run). **To:** #2106 (W7-b publication).
> **Merge instruction:** paste the Tortoise rows below into
> `docs/benchmarks/comparison-systems.md` §3.1 (replacing the PENDING
> 500-Q row), update the §0 publication-status table + §5 receipts index,
> and log the fill in the §7 errata log as an annotated update (never a
> silent edit). All values are derived from the committed runner report +
> receipt in `docs/benchmarks/longmemeval-500q/` (this commit); the
> receipt is machine-built + validated (`tools/longmem_eval/w7_publish.py`).
> **Sequencing caveat from the epic §2.6 ordering note still applies** to
> the W4-gate evidence (see the doc's §0) — this fill covers the 500-Q row
> only.

## Tortoise rows (§3.1 — replace the PENDING 500-Q row)

```markdown
| Tortoise | graph (FalkorDB) + hybrid retrieval (FTS + vector + structural RRF) over the question's history, ingested into a typed memory graph by the **`v2` production extractor** (`--ingest-mode v2 --activate-beliefs`, the #8486 protocol's step-5 mode): statement points, IMPL/NAND operators minted from the question's history, plus turn-granular raw chunks — so there IS a **paid LLM extraction leg** in this run, and **EP is exercised** on the operators it mints, for `questions_with_ep` of `questions` per the report's per-question disclosure bar (a `partial` posture is the expected state, and the number must not be described as belief-activated as a whole). The `deterministic` raw-content mode (turn points + raw chunks, no operators) is the **superseded historical variant** documented in run-report §3 and cannot carry the belief layer | LongMemEval 500-Q (official `xiaowu0162/longmemeval-cleaned` set, sealed answer keys, judge pin derived from the judge that graded — NEVER pre-filled, see `run-report.md` §1.1) | `recall_all@5` (OFFICIAL — `all(doc in recalled_docs for doc in correct_docs)`, non-_abs) | PENDING (run not executed at machinery commit) | Run report `docs/benchmarks/longmemeval-500q/run-report.md` + receipt `w7a-500q.receipt.json`: commit `HEAD`, corpus `sha256:d6f21ea9…3a442` (verified vs the official split digest), judge pin `unpinned:<model>` — DERIVED from the judge that graded, so it CANNOT be pre-filled: under the #8486 ruling (2026-10-27, `run-report.md` §1.1) the default judge is `deepseek-v4-flash`, whose receipt records `unpinned:deepseek-v4-flash` + `inconclusive` by design. The `longmemeval-official-anscheck-gpt4o-2024-08-06-v1` pin is minted ONLY for a run the OFFICIAL judge (`openai:gpt-4o-2024-08-06`, verbatim `evaluate_qa.py` prompt) graded — pasting it for a DeepSeek-graded run is a false provenance claim (R12), rubric hash `82d07b0de05daa48`, `dataset_audit.py` divergence record in the report methodology. **Mechanism-honest labels:** `v2` LLM-extraction ingest (paid extractor; operators present; EP exercised per question — `posture.activation.questions_with_ep` of `questions`); reader = pinned default deepseek-v4-flash (QA accuracy is reader-bound, reported separately from retrieval); any-hit R@5 = PENDING (LABELED VARIANT, NOT the official number); per-question-fraction variants labeled, never mixed. The run itself is PENDING (provider-rotation + ~2-day/$10-40 scale; see run-report §3) — `v2` is this run's mechanism, not a future one. |
```

## §5 receipts-index row

```markdown
| W7 500-Q `recall_all@5` (`v2` ingest, belief layer armed) | `docs/benchmarks/longmemeval-500q/w7a-500q.report.json` | `docs/benchmarks/longmemeval-500q/w7a-500q.receipt.json` | `HEAD` | `sha256:d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` | `unpinned:<model>` — copied from the receipt, never pre-filled; `longmemeval-official-anscheck-gpt4o-2024-08-06-v1` only if the OFFICIAL judge graded it (`run-report.md` §1.1) |
```

## §0 publication-status update

The Tortoise 500-Q row's **PENDING** status is resolved by this fill
(receipt committed by #2105); the W3-b why-layer row stays PENDING. Per the
doc's sequencing caveat, the W4-gate evidence (post-fix-wave survival pass
on the frozen corpus) remains a named pending handoff — no row claims it.
