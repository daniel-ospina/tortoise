---
title: "Retrieval product inversion — parity notes (WIRED)"
type: engineering
domain: platform
doc_status: live
created: 2026-08-29
subjects.team: epistemic-team
aboutObjects: tortoise-retrieval, tortoise-longmem-eval
aboutSubjects: tortoise-retrieval-quality, tortoise-retrieval-parity
---

# Retrieval Product Inversion — Parity Notes (WIRED)

**Date:** 2026-08-29 · **Branch:** `fix/invert-retrieval-to-product` · **Head:** main
**Scope:** the retrieval-quality logic that lived eval-only in the LongMemEval
harness (`tools/longmem_eval/retrieve.py` / `errors.py`) is now a PRODUCT
feature (`tortoise/retrieval.py` / `tortoise/retry.py`); the harness is a thin
caller. This resolves the audit's retrieval PARTIAL items (docs/audit/
2026-08-29-product-cohesion.md, §3) and gaps G2/G7/G8-primitive.

## What moved (the inversion contract)

| Capability | Eval-only before | Product home (after) | Product default | Eval role now |
|---|---|---|---|---|
| Pool depth 120 (#1947, G2) | `DEFAULT_POOL_SIZE = 120` + resolution in `retrieve.py` | `tortoise/retrieval.py::resolve_pool_size` + `DEFAULT_POOL_SIZE`; `sdk.tortoise_fts_query` BAKES the floor at 120 (was env-only opt-in, default `limit*2`=20 at limit=10) | 120 | passes `TORTOISE_LME_POOL_SIZE` through the product resolution; `max(ks)` horizon floor |
| Rank-interleaved context (C1 #1745, G7) | `_assemble_context` / `render_context` / `_render_block` / `_validity_marker` / `_estimate_tokens` / `_dedup_pool` / `_is_raw_chunk` in `retrieve.py` | `tortoise/retrieval.py::assemble_context` / `render_context` / `dedup_pool` (+ `DEFAULT_CONTEXT_ITEM_CAP`=40, `DEFAULT_CONTEXT_TOKEN_CAP`=8000, `DEFAULT_MAX_CHUNKS_PER_SESSION`=3) | budget+item-capped, rank-interleaved, C5 cap 3 | re-exports + passes `context_item_cap` / TR `tr_top_k` |
| Evidence-mark boost (C2 #1745 / #1945) | `_apply_evidence_boost` + `_rank_delta` + `DEFAULT_EVIDENCE_BOOST_*` in `retrieve.py` | `tortoise/retrieval.py::apply_evidence_boost` (position-ceiling rank promotion; `mark_for` provider injectable; stored-`has_answer` → source-class fallback) | OFF (fail-safe, #1745); answer_string x2.0 / verbatim x1.5 / source x1.15 | injects its dataset-derived marks (`evidence.mark_for_question` — read-time recompute incl. the gold-answer mark (d)) + knob resolution (`TORTOISE_LME_EVIDENCE_BOOST*`) |
| Retry predicate (#1806, G8-primitive) | `retryable_transient` / `call_with_predicate` / `WriteStageRetriesExhausted` in `errors.py` | `tortoise/retry.py` (bounded jittered retry, transport-only predicate) | module shipped; **SDK write-path wiring is a FOLLOW-UP** | re-exports unchanged; keeps eval run knobs (`INGEST_WRITE_RETRIES` etc.) |

## In-code parity notes

The PRODUCT-PARITY NOTE blocks in `tools/longmem_eval/` are updated for the
wired items: the pool/context/boost notes in `retrieve.py` and the retry
note in `errors.py` now read **WIRED — shipped to product in this PR** and
point at the product home. The remaining eval-only notes (eval retrieval
budget 1500 ms, reader_surface@k metric, graph-integrity gate, reader/A1
two-phase commit, MMR rerank, extraction-health gate, resume gate) are
unchanged — those are measurement-budget, metric, gate, or reader-surface
items the audit still classifies eval-only.

## Thin-caller proof

`grep -n "_assemble_context\|_apply_evidence_boost\|DEFAULT_POOL_SIZE" tortoise/retrieval.py`
→ the product definitions. In `tools/longmem_eval/retrieve.py` the same grep
finds only the re-export imports (`from tortoise.retrieval import
apply_evidence_boost as _apply_evidence_boost, assemble_context as
_assemble_context, ...`) and call sites — no `def` of the moved logic. The
eval's existing tests pass with the same behavioral assertions; new product
tests live in `tests/test_retrieval.py`.

## Follow-ups (NOT this PR)

- **SDK write-path retry wiring (audit G8):** `tortoise/retry.py` is the
  reusable bounded-retry primitive; wiring `call_with_predicate` into
  `TortoiseSDK._post_commit` / capture/commit graph writes is a separate
  change (the product's write robustness today remains idempotent-MERGE +
  `client_commit_id` replay + server-side dedup).
- **Product-side consumption of the new capabilities:** `resolve_pool_size`
  / `DEFAULT_POOL_SIZE` are invoked by product code today
  (`tortoise_fts_query` bakes the floor). `assemble_context` /
  `render_context` / `dedup_pool` / `apply_evidence_boost` are
  product-owned modules that the EVAL invokes (and any MCP/SDK consumer
  can call); wiring them into a product answer/context surface is the
  reader/answer-surface decision (audit G1, biggest gap).
- Reader/answer surface (audit G1) and the remaining eval-only gates/metrics
  stay tracked under the cohesion audit.
