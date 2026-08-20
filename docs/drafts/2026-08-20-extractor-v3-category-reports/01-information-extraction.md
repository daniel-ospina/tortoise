---
title: "Information Extraction category — v2 extractor LongMemEval full-run (2026-08-19)"
type: log
domain: capability
doc_status: draft
created: 2026-08-20
subjects.team: epistemic-team
---

# Information Extraction category — v2 extractor LongMemEval full-run (2026-08-19)

**Verdict: LOSS (−7.9pp, n=153 matched IE questions) — but the dominant root cause is extraction-infra failure, not extraction design quality.**

## Key numbers
- Baseline 0.850 → v2 0.771. Net: 15 losses / 3 wins / 20 both-fail / 115 both-pass.
- Mean reader-context tokens: 7.3k (baseline) → 32.4k (v2) — **4.4× bloat** (6.6× on losses).
- Evidence recall@20: 0.71–0.80 → **0.00** (all 153 questions) — collapse.
- Session recall@5: 1.00 → 0.85 (0.60 on losses).

## Root cause: extraction never ran for most sessions (infra, not design)
- 5,195/7,528 sessions (69%) hit `S1 failed: ConnectionError … Read timed out` (uncapped S1 calls).
- 5,807 sessions (77%) produced ZERO extraction (no S2 → "no embed list produced").
- Only 1,721 sessions (22.9%) produced content. Median IE question wrote 0 points; 154/155 questions had 0 evidence-marked points.
- A live probe of a 12-turn session extracted cleanly (236s, no errors) and DID capture the fact ("the user has a CS degree from UCLA") — the design works when the call succeeds.

## The three failure mechanisms
1. **Extraction sparsity (infra):** 77% of sessions contributed nothing.
2. **Retrieval misses on paraphrase:** extracted points are paraphrase; the lexical retriever never matches them ("CS degree from UCLA" point vs "Where did I complete my Bachelor's degree in Computer Science?" query — only {degree} token overlap). Evidence-marking (≥0.4 overlap vs the whole answer turn) structurally can't mark short paraphrase points → evR=0.
3. **Reader failures on bloated context:** 11/15 losses had the answer session retrieved (sessR5=1.0) yet the reader abstained or hallucinated under 30-50k-token contexts. Preference subtype worst (8/15 losses; single-session-preference 0.43→0.23).

## Wins prove the design (when extraction succeeds)
- `58ef2f1c`: v2 answered "volunteered on Valentine's Day" (baseline said "back in February") — correct.
- `75f70248`: extracted cat-shedding/HEPA points → correct "Yes, it's possible."

## Recommendations (ranked)
1. **P0 — make extraction actually run at scale:** bound S1/S2 generation (max_tokens caps — the uncapped narrative drives timeouts), per-call retry/backoff (reuse `_call_with_backoff`), provider failover. Collapse the 4-call pipeline to ≤2 LLM calls/session (merge S2+S4; S4 only when S3 returns matches).
2. **P1 — give extracted facts a real retrieval surface:** fact-augmented key expansion (each point emits 2-4 search keys — entity names, synonyms, question phrasings — per the LongMemEval paper's own recommendation); entity-centric retrieval boost (points carry about_entities/slots); atomic fact granularity (Mem0's template: short entity-anchored facts).
3. **P2 — fix evidence marking:** mark against the answer TEXT or use bidirectional containment; the ≥0.4-vs-whole-turn overlap can't mark paraphrase points.
4. **P3 — extraction discipline from competitors:** speaker attribution (USER assertions = facts; assistant suggestions ≠ user facts — Mem0's hardest rule); fact-level dedup at write; preference extraction as entity+value slots; ADD-only + contradiction coexistence (store both, don't collapse — we have NAND/MITIGATES).
5. **P4 — reader context budget:** cap rendered context + dedupe near-identical chunks.

## S1/S2 prompt recommendations (from the analysis)
- S1: "When the USER states a personal fact (education, purchase, membership, recipe, preference), preserve it as a short atomic sentence — do NOT fold it into narrative." "Distinguish who asserted a claim." "Extract preferences as entity+qualifier+value, not episodes." "Bound the narrative: ~1 paragraph per 10 turns."
- S2: "Emit points as single-claim atomic statements ≤15 words, entity-anchored, present tense." Extend OUTPUT_CONTRACT with `search_keys` (2-4) + `source_role` ("user"|"assistant"). Inline dedup against S3 + emitted points. Preference/opinion exemption from the value filter.
- Structural: collapse the 4-call pipeline to ≤2 calls/session.
