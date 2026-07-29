# E020 Pre-Registration

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 3 — HYPOTHESIZE ⛔

## Experiment Question
Is the IMPL edge transmission (phi_impl function) too conservative? When A strongly implies B and B has no other evidence, should B's confidence be closer to A's?

## Current Behavior
```
A(T0, 91%) ──IMPL──→ B(no source) ──IMPL──→ C(no source)
A: 90.6%   B: 53.9%   C: 50.4%
```

B only gains ~4% from A's 91%. Each IMPL hop transmits only ~5% of the source's confidence above baseline. A single IMPL hop from a gold source barely moves a dependent claim.

## Formal Hypotheses

### H1: IMPL edge transmission should be stronger
A T0 source at 91% implying B should push B above 70%, not barely above 53%.

**Prediction:** With current phi_impl, B < 55%. A modified phi_impl should produce B > 65%.

### H2: Multi-hop chains should preserve meaningful signal
In a 3-hop chain A→B→C, C should receive a detectable signal from A, not just 0.4%.

**Prediction:** Current: C ≈ 50.4%. A stronger phi_impl: C > 55%.

### H3: Multiple IMPL sources should accumulate
Two T0 sources both implying B should push B higher than one source.

**Prediction:** 2×T0 → B > 80%.

### H4: Loopy mutual implication should amplify
In A↔B (mutual IMPL), both with T0 sources, confidence should be higher than the single-source chain case.

**Prediction:** A↔B with both T0 > A→B with one T0.

## Falsification Criteria
- H1 is falsified if B < 65% even with stronger phi_impl
- H2 is falsified if C < 55% in 3-hop chain with stronger phi
- H3 is falsified if 2×T0 doesn't beat 1×T0
- H4 is falsified if mutual IMPL doesn't amplify

## Metrics
- **Primary:** B and C confidence after EP convergence
- **Tolerances:** δ = 1e-4, ε = 0.02

## What We'd Test
1. Current phi_impl (baseline) — measure actual transmission
2. Stronger phi_impl — increase the coupling between source and target
3. Compare across chain lengths, source counts, and topologies
