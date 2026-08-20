---
title: "Temporal Reasoning category — v2 extractor LongMemEval full-run (2026-08-19)"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# Temporal Reasoning category — v2 extractor LongMemEval full-run (2026-08-19)

**Verdict: parity (61.3% vs 61.1% headline) is a question-set artifact — the apples-to-apples delta is −0.8pp (17 wins / 18 losses / 88 ties on 123 shared qids). The story-arc narrative COMPRESSES temporal structure and hurts. The 62% floor is carried entirely by the retained raw-transcript leg + reader-side date annotations.**

## Three stacked causes
1. **S1 story compression destroys the temporal substrate.** The narrative keeps the durable belief and drops the episodic anchor (reproduced: S1 on the MoMA session produced "after a MoMA tour" — no dates, no elapsed days). TR questions are all "when" (elapsed days, ordering, recency). 11/18 losses are v2 abstentions where baseline answered from verbatim evidence.
2. **The extractor never sees session dates.** extract_session_v2 receives role/content turns only; the date exists only on the Session node + haystack_dates. S1/S2 cannot anchor events to dates → every extracted point is date-blind.
3. **Retrieval dilution + measurement collapse.** v2 puts 4.2× more tokens into the reader (39,950 vs 9,381); 13/18 losses are reader-losses under 40k-token contexts. Evidence recall@k = 0.00 (the ≥0.4-overlap marking is unreachable for paraphrased story content).

## Competitor mechanisms
- **Graphiti:** bi-temporal model — valid_at/invalid_at (fact time) + created_at/expired_at (system time); retrieval-time SearchFilters with date comparison operators.
- **Mem0:** **write-time date anchoring** — the extraction prompt injects "Today's date is {YYYY-MM-DD}" so relative expressions ("last weekend") become absolute at ingestion.
- **Letta:** wall-clock injected into the compiled system prompt.
- **Research (TimeRAG, TG-RAG, STAR-RAG):** semantic retrieval neglects temporal constraints; add timestamped relations + temporal query decomposition + time-scoped candidate filtering + recency signals (vector recency bias: pure similarity retrieves older semantically-similar facts).

## Recommendations (ranked)
1. **P0 — feed session dates into S1/S2 and require date anchors.** Pass session_date per chunk into run_s1; instruct: "anchor every event/decision/state-change to its session date ('visited MoMA on 2023-01-08')". Instruct S2/S4 to include the date in event content or a `when` slot. Zero new infrastructure; the mem0 write-time-anchoring lesson applied at the story layer.
2. **P0 — time-decay + temporal-bound retrieval.** Recency signal in the RRF ranking; detect temporal constraints in the question ("between…and…", "first", "before", "how many days") to bound retrieval + render hits time-ordered.
3. **P1 — bi-temporal validity on events/points** (Graphiti): valid_at/invalid_at + created_at/expired_at; render [valid …] markers into reader context (same pattern as the existing [SUPERSEDED BY…] markers).
4. **P1 — use the events timeline.** The design doc positions "Events=timeline" but the LME ingest writes events with no startedAt and retrieval is points-only — the timeline is inert.
5. **P1 — fix evidence marking:** mark by source-session attribution (point written from an evidence session) instead of ≥0.4 content overlap.
6. **P2 — backoff/retry in the extractor path** (reuse _call_with_backoff) to kill the ~3 errors/session.
7. **P2 — temporal-aware top-k:** for TR questions, prefer session-dated hits and cap context (20→12).

**Bottom line:** implement #1 (date-anchored S1/S2) first + re-run the TR subset — it converts extracted points from date-blind summaries into answerable evidence and should convert a large share of the 11 abstention losses. Treat TR as a reader+retrieval joint problem (13/18 losses are reader losses under context flooding).
