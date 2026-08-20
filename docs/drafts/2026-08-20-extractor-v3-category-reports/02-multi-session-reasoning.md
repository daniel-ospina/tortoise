---
title: "Multi-Session Reasoning category — v2 extractor LongMemEval full-run (2026-08-19)"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# Multi-Session Reasoning category — v2 extractor LongMemEval full-run (2026-08-19)

**Verdict: the +2.6pp win (49.5% → 52.1%) is REAL in the numbers but does NOT validate the v2 extractor — it's a context-density + run-reliability artifact. The narrative-first hypothesis is untested until S3 cross-session reads + evidence marking are exercised.**

## Evidence
1. **Extraction failed for every MSR question:** 133/133 multi-session questions had 0 extracted points (the graph held only raw transcript blobs; S1/S2/S4 ConnectionError timeouts). Only 52/496 questions extracted anything (all single-session-user).
2. **The shared-question delta is +1 net flip** (10 wins − 9 losses on 109 shared qids). The rest of the headline is run-reliability (12 MSR qids the baseline couldn't run — network failures — v2 got 8/12).
3. **Retrieval didn't improve — the reader got 4.6× more context** (8.2k → 37.7k tokens; whole-session verbatim blobs). evidence_recall@k = 0.0 everywhere.

## Wins/losses
- Wins (10 flips): all cross-session AGGREGATION questions (total charity $5,850, 5 fitness classes/wk, 8 camping days) — answered from dense raw context, NOT the S1 narrative.
- Losses (9 flips): reader precision failures — miscounts ("1 day" vs 3 days) and hedging under the bloated context.

## Competitor mechanisms
- LongMemEval paper: **round-level granularity > session-level** for MSR.
- Mem0: two-phase extract → compare-with-existing → ADD/UPDATE/DELETE/NOOP consolidation; multi-signal scoring (semantic + BM25 + entity boost).
- Graphiti: one temporal graph, episodes→entities with MENTIONS/NEXT_EPISODE edges, valid_at/invalid_at windows, raw episodes retained, hybrid BM25+cosine+BFS+cross-encoder.
- xMemory/T-Mem/HiMem/CPP: set-level diverse retrieval for multi-fact queries; context presentation (evidence highlighting) as an independent accuracy driver.

## Recommendations
1. **P0 — re-measure with a healthy rate profile + fix has_answer marking on blobs** — the category is currently unmeasurable.
2. **Keep raw-transcript retention** (the actual win source); move granularity to rounds.
3. **Activate S3 real-backend search** so sessions link into ONE cross-session graph (the design doc's §3 intent — degraded in this run).
4. **Add cross-session consolidation (S5.5)** — Mem0's 4-way / Graphiti's validity windows.
5. **Reader multi-session aggregation instruction** (mirror the TEMPORAL_REASONING_INSTRUCTIONS fragment that fixed TR in #1366).
