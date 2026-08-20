---
title: "MULTI-SESSION REASONING — Tortoise v2 vs Baseline LongMemEval Full-Run Report"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# MULTI-SESSION REASONING — Tortoise v2 vs Baseline LongMemEval Full-Run Report

**Measurement:** LongMemEval-S (split=s, `xiaowu0162/longmemeval-cleaned`, 500 questions)
**Runs:** baseline det = `/tmp/lme-det-full-report.json` (2026-08-18, git 4a66fb7, n=485, 15 network failures) · v2 = `/tmp/lme-v2-full-report.json` (2026-08-20, git 75cd281, n=496, 4 failures), checkpoint `/tmp/lme-v2-full.json`
**Category:** Multi-Session Reasoning (paper category; `question_type=multi-session`, `_abs` → Abstention)
**Reported delta: +2.53pp (49.54% n=109 → 52.07% n=121)** — the "+2.6pp win"

---

## ⚠️ Executive verdict: the +2.6pp win is REAL in the numbers, but it does NOT validate the v2 extractor

**The v2 5-stage pipeline (S1 story → S2 map → S3 search → S4 gaps → S5 embed) contributed NOTHING to the MSR category in this run. The win is a raw-transcript + reader-context artifact, and the S1 "narrative-first preserves cross-session arc" hypothesis is untested.**

Three evidence chains:

### 1. Extraction failed for every multi-session question
From the run's authoritative checkpoint (`/tmp/lme-v2-full.json` ingest stats):
- **133/133 multi-session questions have 0 extracted points, 0 entities, 0 events, 0 operators.** The graph for every MSR question contains ONLY the raw verbatim transcript blobs (`lme:{qid}:s{si}:raw`, one per session).
- Mean **142 ingest errors per MSR question**, of which mean **47.2 are `"no embed list produced (S2/S4 empty) — nothing to embed"` — one per session**. Error kinds across the run: 21,356 `S1 chunk failed` + 21,343 `N/M S1 chunks failed` + 21,957 `no embed list` + 1,087 `S2 failed` + 1,037 `S4 failed` — all `ConnectionError: HTTPSConnectionPool(host='api.deepseek.com') Read timed out`.
- Only **52/496 questions have any extracted points — all single-session-user** (first in run order). Extraction health decays monotonically with run position: rate-limit exhaustion, not content. Even the healthy questions are partially failed (70 errors each, 19/45 sessions with no embed list).

### 2. The flip delta on strictly shared questions is +1 net (10 wins − 9 losses / 109)
- `False→True` (v2 wins): **10** · `True→False` (v2 loses): **9** · both-true 45 · both-false 45.
- That is **+0.9pp** of the headline. The remaining **~+1.7pp comes from 12 qids the baseline run couldn't execute** (all 12 are in the baseline's network-failure list; v2 got 8/12 correct = 66.7% > category mean 52%). That chunk of the "win" is **run reliability, not a v2 capability** (baseline n_failed=15 vs v2 n_failed=4).

### 3. Retrieval quality did not improve — the reader got 4.6× more context instead
| Metric (MSR category) | Baseline | v2 |
|---|---|---|
| session_recall@20 | 0.863 | 0.858 (≈parity) |
| evidence_recall@20 | **0.687** | **0.000** |
| turn_recall@20 | 0.687 | **0.000** |
| context_tokens (mean) | 8,204 | **37,687 (4.6×)** |

`evidence_recall@k` is **0.0 at every k for every v2 question** — a broken metric, not a broken retrieval: the raw transcript blobs are never marked `has_answer` (only payload points get overlap-marked at ≥0.4 token overlap, and MSR has no payload points). Baseline marks verbatim turns → 0.687. So v2's *measured* evidence delivery is zero while its accuracy went UP — the reader is answering from **37.7k tokens of whole-session verbatim text** (≈ top-20 blobs ≈ most of the 115k-token history) vs baseline's 8.2k of turn snippets. **Bigger context at read time, not better extraction, is the mechanism.** This aligns with LongMemEval's own finding that *round-level* granularity beats session-level for MSR — v2 accidentally won by giving the reader more raw evidence per unit, not by smarter units.

**Robustness verdict: NOT robust as a v2 validation.** The number will not reproduce as an extractor win; it reproduces only as a "retain raw transcripts, give the reader the whole history" effect. Any MSR claim for the S1 narrative-first architecture requires a healthy re-run.

---

## Where we do well / where we don't (evidence)

### Wins (10 False→True flips) — all cross-session AGGREGATION questions
Every flipped win is a "how many / how much total / which most" question requiring summation or comparison across 2–5 evidence sessions — exactly what a dense whole-history context enables the reader to do itself:

| qid | span | question | answer |
|---|---|---|---|
| gpt4_5501fe77 | 3 | most-followed platform over past month | TikTok |
| 129d1232 | 3 | total raised across all charity events | $5,850 |
| 2788b940 | 4 | fitness classes per typical week | 5 |
| 9d25d4e0 | 3 | jewelry acquired in last 2 months | 3 |
| b5ef892d | 3 | camping days in the US this year | 8 |
| gpt4_e05b82a6 | 4 | rollercoaster rides Jul–Oct | 10 |
| c4a1ceb8 | 4 | citrus types in cocktail recipes | 3 |
| 61f8c8f8 | 2 | 5K time vs previous year | 10 min faster |
| 4f54b7c9 | 2 | antiques inherited from family | 5 |
| 681a1674 | 2 | Marvel movies re-watched | 2 |

All had session_recall@20 = 1.0 in v2 (evidence sessions present) with the answer embedded in a retrieved verbatim session blob. **These are the narrative-first dream questions — and they were answered WITHOUT the narrative** (S1 produced nothing).

### Losses (9 True→False flips) — reader precision failures, not retrieval failures
- **Miscounts despite full context:** `10d9b85a` "1 day" vs 3 days (April workshops); `2318644b` "$270 more" hedge; `6d550036` project-count confused with "leading a team of five engineers".
- **Hedging with evidence in context (ses@20=1.0):** `ef66a6e5` "I do not know" (2 sports), `aae3761f` "don't have that information" (15h driving), `27016adc` (10% renovation), `b3c15d39` (5-day shutter), `099778bb` (20% women leadership — "cannot be determined").
- Failure mode = **reader arithmetic/aggregation discipline**, not retrieval. Baseline answered these from 8.2k tokens of marked turns; v2 lost them at 37.7k — the extra context is noisy for precision-count questions.

### The category is mostly unchanged
45 both-true / 45 both-false on shared qids. session_recall parity (0.858 vs 0.863) means retrieval coverage neither gained nor lost. The entire observable delta sits in (a) the +1 net flip and (b) 8/12 correct on baseline-unrunnable qids.

---

## Proposed improvements (research + competitor mechanisms, ranked)

Ranked by leverage × measurability for the MSR category:

1. **P0 — Re-measure with a healthy rate profile before touching anything.** MSR is currently a measurement of raw-blob context, not the extractor. Bound concurrency (the run showed 429/read-timeout decay), cap backoff, checkpoint-resume aggressively. Also **fix evidence marking**: stamp `has_answer=true` on a raw transcript blob when any contained turn is an answer turn (and on payload points at a lower paraphrase-tolerant overlap, e.g. cosine > entity-set overlap). Until evidence_recall@k is meaningful, every v2 category claim is unverifiable.

2. **Round-level storage, not session-level (LongMemEval paper, confirmed by this run).** The paper: "round is the best granularity for storing interactive history." v2's session blobs won accidentally via density; baseline's turn points lost via sparsity. **Store per-round verbatim chunks + per-session compiled story** — retrieval then returns fine-grained evidence at high density (both worlds).

3. **Activate S3 real-backend search (the design's own §3 unlock).** The design doc's cross-session mechanism — the extractor reading existing memory before S4 gap review — **degraded to "treat everything as new"** in this run (`resolve_backend_mode() → embedded`; per-question isolated temp graphs). Cross-session continuity at WRITE time is still unimplemented. This is the biggest architectural gap for MSR: Graphiti answers cross-session questions because episodes are ingested into ONE temporal graph with `MENTIONS`/`NEXT_EPISODE` edges and validity windows.

4. **Mem0-style consolidation pass (ADD/UPDATE/DELETE/NOOP) across sessions (S5.5).** After embedding, diff new points against retrieved prior points and merge/update/retire duplicates. Mem0's two-phase (extract → compare-with-existing → 4-way decision) is the canonical fix for cross-session contradiction and duplication; Graphiti does the same via bi-temporal invalidation (`valid_at`/`invalid_at`). v2 already has supersession machinery (`derive_supersessions`, `[SUPERSEDED BY]` markers in the runner) — it must fire on cross-session repeats, which requires #3.

5. **Set-level, diverse retrieval for multi-fact queries (xMemory / Graphiti recipes).** MSR questions need evidence *sets* across sessions, but retrieval is top-k similarity (redundant spans, lost middle). Adopt Graphiti's `COMBINED_HYBRID_SEARCH_CROSS_ENCODER` (BM25 + cosine + BFS over edges/nodes/episodes with cross-encoder rerank) or xMemory's theme→semantic→episode top-down with expand-only-when-uncertainty-drops. For Tortoise: retrieve story points + event timeline + raw chunks, dedup by session, MMR-style diversity.

6. **CPP-style context presentation.** Independent paper finding: how context is *presented* drives multi-hop accuracy even at identical retrieval. Add per-segment "most informative sentence" highlighting + per-session date/status headers (the runner already has `[session N] (session date D)` + supersession markers).

7. **T-Mem write-time triggers.** Add associative trigger generation at S2 ("this claim matters when …") so cross-session arc questions reach stored points through non-surface-similar queries (the "seafood allergy → team dinner" case). Cheap: one prompt at write time, stored as indexable text.

8. **Reader-side aggregation scaffold.** Mirror the existing `TEMPORAL_REASONING_INSTRUCTIONS` fragment (issue #1366 fixed TR by prompting the reader) with a MULTI-SESSION fragment: "count distinct events across sessions; do not double-count; if numbers conflict across sessions, reconcile by date." The losses in §4 are precisely reader arithmetic failures.

---

## Concrete recommendations to AMPLIFY the win

1. **Keep and promote raw-transcript retention** — it is the actual source of the win. Never ship a pipeline where the extractor output replaces verbatim evidence. (Graphiti does this explicitly: `store_raw_episode_content=True`.)
2. **Re-run the full benchmark with (a) bounded concurrency/backoff, (b) blob-level `has_answer` marking.** Get the true v2 MSR number with narrative points actually present — the honest test of the +2.6pp.
3. **Ship S3-for-real as the MSR feature**: one shared graph per entity across sessions; S3 search → S4 gap review connects this session's story to prior sessions (design doc §3, currently dead in eval). This is the mechanism the design claims preserves the story arc across sessions — it has never run.
4. **Add cross-session consolidation (S5.5, Mem0 4-way / Graphiti validity-window)** so repeated facts update rather than fragment — the single biggest expected MSR amplifier once S3 exists.
5. **Move context granularity to rounds** (#2 above) and give the reader the multi-session aggregation instruction (#8) — both are days of work, directly attack the loss bucket.
6. **Fix the metric first**: evidence_recall@k=0.0 for all v2 questions means the harness currently cannot distinguish "retrieval works" from "retrieval broken." Amplification without measurement repair is guessing.

**Bottom line:** the +2.6pp is a real measured delta but an artifact of context density + run reliability, not of the 5-stage narrative-first extractor. The right move is not to celebrate the win — it's to fix the measurement (P0), activate S3 cross-session reads, and consolidate across sessions; only then is the narrative-first hypothesis actually tested and the win amplifiable.
