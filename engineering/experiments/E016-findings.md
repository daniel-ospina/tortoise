# E016 Findings — Known Operators vs Raw Memory

## What We Tested

6 harness versions attempting to discriminate Tortoise (structured graph) from Control (same evidence, different presentation). All used **pre-extracted propositions** with known NAND operators — no extraction noise.

| Version | Design | Tortoise | Control | Discriminated? |
|---------|--------|----------|---------|----------------|
| v1 | Dev approval (correct=NO), 8 chunks + NANDs | 100% | 100% | ❌ |
| v2 | CFO projection (correct=YES), shorter | 100% | 100% | ❌ |
| v3 | 12 chunks, operators Tortoise-only | 100%* | 100%* | ❌ |
| v4 | **Memory prosthesis** (Control sees only current chunk) | 100% | 75% | ✅ |
| v5 | Prior-decisions anchoring | 100% | 100% | ❌ |
| v6 | Simulated agent opinions | 100% | 100% | ❌ |

*v3 had parser issues initially; after fix, both arms hit 100%.

## Root Cause

**DeepSeek integrates 12 clean propositions perfectly regardless of presentation format.**
- Total evidence: ~700 tokens (half a page of text)
- Each proposition: pre-extracted, labeled, self-contained
- Nothing to organize — the claims arrive already organized
- The graph's value chain (extraction → operator detection → organization) was only tested at step 3

## What v4 Actually Tested

v4 discriminated because it tested a different question: "Does the graph serve as a memory prosthesis?" (Control had 0 history, Tortoise had the graph). It did NOT test "Does the graph organize raw data better than raw text?"

## Key Insight

> **We tested the graph's organization step in isolation, but the graph's real value is compression: turning noisy raw text into structured claims that the agent can query.** Clean propositions handed to the agent need no organizing — the experiment should start from raw text where extraction load is real.

## Files

- Harness code: `tortoise/experiments/E016/harness_v1.py` through `harness_v6.py`
- Preregistration: `tortoise/experiments/E016/preregistration.md`
- Validation results: `tortoise/experiments/E016/validate_results_v4.json` through `v6.json`
