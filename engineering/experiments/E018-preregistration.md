# E018 Pre-Registration

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 3 — HYPOTHESIZE ⛔

## Experiment Question
Do Beta priors with log-scale aggregation produce correct, monotonic, anti-Sybil EP belief propagation across linear, loopy, and contradictory graphs?

## Formal Hypotheses

### H1: Source positivity
Any source tier (T0-T4) increases confidence compared to no-source baseline (Beta(1,1) → mean 50%).

**Prediction:** After EP convergence, all tiered claims have mean > 0.5000. Monotonic: mean(T0) > mean(T1) > mean(T2) > mean(T3) > mean(T4) > 0.5000.

### H2: Log-scale cumulative aggregation
Multiple same-tier sources increase confidence with diminishing returns. 10 T4 < 1 T2.

**Prediction:** confidence(10×T4) < confidence(1×T2). confidence(100×T4) < confidence(10×T4) due to log curve flattening.

### H3: Anti-Sybil robustness  
1,000,000 T4 sources must NOT overpower 2 T0 sources.

**Prediction:** confidence(2×T0) > confidence(1000×T4). log₂(1,000,001) ≈ 20, effective T4 pc ≈ 2.0, while 2×T0 pc ≈ 14.2.

### H4: Non-dilution
Adding any tier source to existing higher-tier sources never reduces confidence.

**Prediction:** confidence(5×T0) ≤ confidence(5×T0 + 1×T4). Adding T4 to 5 gold sources must not decrease the mean.

### H5: Graph propagation
Source credibility propagates through IMPL edges bidirectionally in both linear and loopy graphs.

**Prediction:** In Scenario A (chain), B's confidence > 0.5000 when A has sources. In Scenarios B/C (loopy), all cluster points have confidence > 0.5000.

## Falsification Criteria
Each hypothesis is falsified if:
- H1: Any tier produces mean ≤ 0.5000
- H2: confidence(10×T4) ≥ confidence(1×T2) OR confidence(100×T4) ≥ confidence(1×T4)
- H3: confidence(1000×T4) ≥ confidence(2×T0)
- H4: confidence(5×T0 + 1×T4) < confidence(5×T0)
- H5: B's confidence ≤ 0.5000 in Scenario A, or any cluster point ≤ 0.5000 in B/C

## Metrics
- **Primary:** EP-computed confidence mean (α/(α+β)) for claim points
- **Tolerances:** δ = 1e-4 for exact math, ε = 0.02 for EP convergence, ε_loop = 0.03 for loopy feedback

## Pre-Registered Test Cases
40 tests across 14 classes in `tests/test_ep_sources.py`. All test assertions are pre-registered here — no changes to assertions after running.
