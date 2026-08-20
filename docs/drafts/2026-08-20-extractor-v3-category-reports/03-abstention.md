---
title: "Abstention category — v2 extractor LongMemEval full-run (2026-08-19)"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# Abstention category — v2 extractor LongMemEval full-run (2026-08-19)

**Verdict: +11.9pp (71.4% → 83.3%) — the win is real but the causal story is the OPPOSITE of the stated hypothesis. The v2 extractor did NOT run on a single abstention question (all 30 had 100+ ingest errors, S1 chunk failed: HTTPError 402 Payment Required). The win is a reader/context artifact, not extraction.**

## What actually drove the win
1. **Reader model swap** — deepseek-chat → deepseek-v4-flash (proven by a flip on IDENTICAL context).
2. **Reader prompt hardening** — the v2-era prompt ("only say you do not know when the context genuinely lacks the information" + temporal fragment) tells the reader HOW to abstain correctly.
3. **Full-session verbatim context** — with extraction dead, retrieval returned 20 full transcripts instead of small turn fragments; full-session context lets the reader VERIFY ABSENCE ("no mention of Tom becoming a parent") instead of committing to a decoy fragment.

## Where we do well / not
- **Well:** absence verification with full sessions (all 4 flip-wins are clean "I do not know. The context contains no information about X"); reader abstention quality.
- **Not well:** decoy-commit persists (4 residual losses both runs — the reader commits to near-miss facts when salient); the commit-bias prompt backfires on partial knowledge (1 regression — suppressed the needed abstention clause); the win is fragile (2 of +5 correct answers were baseline network failures).

## Competitor scan
- **Mem0: actively forbids abstention** ("If no relevant information is found… provide a general response") — they'd score ~0 on _abs questions.
- **Graphiti:** no reader-side abstention; closest is extraction-side "if evidence insufficient, omit the claim" + contradiction handling.
- **Letta:** no abstention mechanism.
- **WHITESPACE:** nobody has a reader-side abstention/uncertainty mechanism. Tortoise already has the ingredients (NAND = truth attack, MITIGATES = relevance attack, supersession markers, confidence/c_cal) — an abstention-cue path from graph structure → reader is a defensible differentiator.

## Recommendations
1. **P0 — re-run with extraction actually succeeding** (budget/retry/failover) — the +11.9pp is currently un-attributable to the extractor.
2. **P1 — pin the reader model + prompt across A/B runs** (two confounds currently).
3. **P1 — add an explicit abstention clause for partial knowledge:** "state what IS present AND explicitly state the asked info is absent" (the phrasing that won the regression). Soften "do not hedge" for abstention questions.
4. **P2 — surface structural abstention signals to the reader** (NAND/supersession/MITIGATES → "this fact is contradicted/replaced" cue).
5. **P2 — evidence-sufficiency / absence-verification signal:** "no exact match for X found in N sessions searched" when the target entity/relation has no retrieved point.
6. **KEEP:** full-session raw transcripts in the pool, the reader model upgrade, the hardened prompt's abstention criterion. Consider a calibration threshold (selective prediction — points carry confidence/c_cal; abstain below a bar). Nobody in the competitor set has this.
