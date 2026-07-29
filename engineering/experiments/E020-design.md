# E020 Experiment Design

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 4 — DESIGN
**Pre-registration:** [E020-preregistration.md](./E020-preregistration.md)

## 1. Experiment Type
Mathematical validation — measures IMPL edge transmission strength and evaluates whether phi_impl is too conservative.

## 2. Independent Variables
- IMPL phi function strength (current vs stronger variant)
- Chain length (1, 2, 3 hops)
- Source count on root (1, 2, 5)
- Topology (chain, mutual IMPL loop)

## 3. Dependent Variable
B and C confidence after EP convergence. Measured at each hop.

## 4. Test Cases

| # | Topology | Sources | Phi | What we measure |
|---|----------|---------|-----|-----------------|
| 1 | A→B (1 hop) | 1×T0 on A | current | B's confidence |
| 2 | A→B→C (2 hops) | 1×T0 on A | current | B, C confidence |
| 3 | A→B→C→D (3 hops) | 1×T0 on A | current | Chain attenuation |
| 4 | A→B (1 hop) | 2×T0 on A | current | Multiple sources |
| 5 | A→B (1 hop) | 5×T0 on A | current | Source accumulation |
| 6 | A↔B (mutual) | 1×T0 each | current | Loopy amplification |
| 7 | A→B (1 hop) | 1×T0 on A | stronger | Compare phi strength |
| 8 | A→B→C (2 hops) | 1×T0 on A | stronger | Multi-hop with stronger phi |

## 5. Expected vs Current

| Test | Current (approx) | Should be closer to |
|------|------------------|---------------------|
| 1 T0 → B | B ≈ 54% | B > 70% |
| 1 T0 → B → C | C ≈ 50.4% | C > 60% |
| 2 T0 → B | B ≈ ? | B > 80% |
| A↔B both T0 | A,B ≈ ? | > single-source |

## 6. Phi Investigation
Read and analyze `phi_impl` in `tortoise/ep.py`. Determine what parameters control transmission strength. The current transmission of ~5% per hop is extremely weak — a gold source implying a claim should carry more weight.
