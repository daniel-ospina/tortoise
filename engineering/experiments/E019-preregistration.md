# E019 Pre-Registration

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 3 — HYPOTHESIZE ⛔

## Experiment Question
Does bidirectional EP messaging on directed IMPL edges cause false cascades where an invalidated sub-argument incorrectly reduces confidence in unrelated sub-arguments?

## Graph Scenario

```
Before NAND:
  A(90%) ──IMPL──→ C1(85%)
                       ↑
  B(80%) ──IMPL───────┤
    │
    └──IMPL──→ C2(75%)

After NAND on A:
  Question: does C2 drop disproportionately?
```

- A and B both support C1 via IMPL
- B also supports C2 via IMPL (independent conclusion)
- A gets NAND contradiction (A's claim is invalidated)
- C1 should drop (lost A's support)
- B should be minimally affected (B's evidence is independent)
- C2 should be unaffected (A has no path to C2)

## Formal Hypotheses

### H1: Bidirectional EP causes measurable false cascade
In bidirectional EP, C2's confidence drops by more than 5% after A is invalidated, despite A having no direct or indirect IMPL path to C2. The drop comes entirely from B→C1 feedback reducing B's confidence, which then reduces B→C2.

**Prediction:** conf_bidirectional(C2_after) < conf_bidirectional(C2_before) - 0.05

### H2: Directed EP eliminates the false cascade
In directed-only EP (messages only flow forward along IMPL edges), C2's confidence is unchanged after A is invalidated. A's NAND affects C1 through A→C1. C1's drop does NOT feed back to B through B→C1 because the edge is directed B→C1 only.

**Prediction:** |conf_directed(C2_after) - conf_directed(C2_before)| < 0.01

### H3: Cascade magnitude depends on shared conclusion count
As more shared conclusions are added between A and B, the bidirectional cascade grows.

**Prediction:** cascade(3_shared) > cascade(1_shared)

### H4: B's intrinsic evidence anchors B against feedback
Increasing B's source credibility (T4→T0) reduces the magnitude of the bidirectional cascade.

**Prediction:** cascade(T0_B) < cascade(T4_B)

### H5: C2's own connections anchor C2 against the cascade
When C2 has 5 independent T2 IMPL sources (anchored), the bidirectional cascade is
invisible (< 0.03). When C2 has only B (isolated), the full cascade propagates.

**Prediction:** cascade(anchored_C2) < cascade(isolated_C2)

## Falsification Criteria
- H1 is falsified if bidirectional C2 drop < 0.02 (no measurable cascade)
- H2 is falsified if directed C2 drop > 0.01 (directed still has cascade)
- H3 is falsified if cascade doesn't grow with shared conclusions
- H4 is falsified if source strength has no effect on cascade

## Metrics
- **Primary:** C2 confidence mean after A invalidated, compared to baseline
- **Secondary:** B confidence after A invalidated (how much does feedback reduce B?)
- **Tolerances:** δ = 1e-4, ε = 0.02

## Independence
This experiment requires modifying the EP engine to support a directed-only mode (messages only flow source→target on IMPL edges). The code change is isolated to the `_send_messages` / `_update_factor` function.
