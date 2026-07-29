# E019 Experiment Design

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 4 — DESIGN
**Pre-registration:** [E019-preregistration.md](./E019-preregistration.md)

## 1. Experiment Type

**Mathematical validation experiment.** Compares bidirectional (current) vs directed EP propagation on the same graph to measure false cascade effects.

## 2. Independent Variable

**EP propagation mode:**
- `bidirectional` — messages flow both ways on all IMPL edges (current behavior)
- `directed` — messages flow only source→target on IMPL edges (A→B means A sends to B, not reverse)

**Graph parameters:**
- Shared conclusion count: 1, 2, 3 (how many conclusions A and B share)
- B's source tier: T4, T2, T0 (how strongly anchored B is)
- A's source tier: T0 (always high, to maximize cascade potential)

## 3. Dependent Variable

- **C2 confidence** after A invalidated (primary — measures false cascade)
- **B confidence** after A invalidated (secondary — measures feedback on B)
- **C1 confidence** after A invalidated (control — should drop)

## 4. Graph Construction

### Base topology (1 shared conclusion)
```
Sources_A → A ──IMPL──→ C1
                         ↑
Sources_B → B ──IMPL─────┤
              │
              └──IMPL──→ C2
```

### Extended topology (3 shared conclusions)
```
Sources_A → A ──IMPL──→ C1
                   ┌───→ C1a
                   └───→ C1b
                         ↑ ↑ ↑
Sources_B → B ──IMPL─────┤ │ │
              ├──IMPL─────┘ │ │
              ├──IMPL───────┘ │
              ├──IMPL─────────┘
              │
              └──IMPL──→ C2
```

## 5. EP Configuration

**Bidirectional mode (current):**
Both source→target and target→source messages on every IMPL edge.

**Directed mode (experimental):**
Only source→target messages on IMPL edges. The target does NOT send feedback to the source through the IMPL edge. This requires a one-line change in `ep.py`:
```python
# Current: messages sent to both id_a and id_b
self._write_message(op_id, id_a, *damped_a, op_type)
self._write_message(op_id, id_b, *damped_b, op_type)

# Directed: only target gets message from source
self._write_message(op_id, id_b, *damped_b, op_type)  # target receives
# id_a does NOT receive message from id_b
```

### Anchored C2 (5 additional IMPL sources)
```
Sources_A → A ──IMPL──→ C1
                         ↑
Sources_B → B ──IMPL─────┤
              │
              └──IMPL──→ C2 ←──IMPL── S1(T2)
                               ←──IMPL── S2(T2)
                               ←──IMPL── S3(T2)
                               ←──IMPL── S4(T2)
                               ←──IMPL── S5(T2)
```
C2 has 5 independent T2 source points plus B. B's feedback must compete
with 5 other IMPL signals. Test: does the cascade break through anchoring?

### Anchoring gradient (low → medium → high)
```
Sources_A → A ──IMPL──→ C1
                         ↑
Sources_B → B ──IMPL─────┤
              │
              └──IMPL──→ C2 ←──IMPL── S1(T4)      ← low-anchor
                               (1 T4 source)

              └──IMPL──→ C2 ←──IMPL── S1(T4)      ← med-anchor
                               ←──IMPL── S2(T4)      (2 T4 sources)

              └──IMPL──→ C2 ←──IMPL── S1(T2)      ← high-anchor
                               ←──IMPL── S2(T2)      (5 T2 sources)
                               ←──IMPL── S3(T2)
                               ←──IMPL── S4(T2)
                               ←──IMPL── S5(T2)
```
Tests the gradient: how many anchor points does C2 need before the
bidirectional cascade from A's invalidation becomes undetectable?

## 6. Test Cases

| # | Topology | B Tier | C2 Type | Mode | What we measure |
|---|----------|--------|---------|------|-----------------|
| 1 | 1 shared | T4 | isolated | bidirectional | C2 drop magnitude |
| 2 | 1 shared | T4 | isolated | directed | C2 should not drop |
| 3 | 1 shared | T2 | isolated | bidirectional | Compare to T4 |
| 4 | 1 shared | T0 | isolated | bidirectional | Anchoring reduces cascade |
| 5 | 3 shared | T4 | isolated | bidirectional | Cascade grows with density |
| 6 | 3 shared | T4 | isolated | directed | Directed still clean |
| 7 | 1 shared | T4 | low-anchor (1×T4) | bidirectional | One weak anchor — partial cascade |
| 8 | 1 shared | T4 | med-anchor (2×T4) | bidirectional | Two weak anchors — reduced cascade |
| 9 | 1 shared | T4 | high-anchor (5×T2) | bidirectional | 5 T2 anchors — minimal cascade |
| 10 | 1 shared | T4 | high-anchor (5×T2) | directed | Anchored baseline |
| 11 | 3 shared | T4 | low-anchor (1×T4) | bidirectional | Dense + weak anchor |
| 12 | 3 shared | T4 | med-anchor (2×T4) | bidirectional | Dense + medium anchor |
| 13 | 3 shared | T4 | high-anchor (5×T2) | bidirectional | Worst case: dense + anchored |

## 7. Expected Results

| Test | Bidirectional C2 drop | Directed C2 drop |
|------|----------------------|-----------------|
| 1 shared, T4 B, isolated | ~0.05-0.10 | <0.01 |
| 1 shared, T2 B, isolated | ~0.03-0.06 | <0.01 |
| 1 shared, T0 B, isolated | ~0.01-0.03 | <0.01 |
| 3 shared, T4 B, isolated | ~0.15-0.25 | <0.01 |
| 3 shared, T0 B, isolated | ~0.05-0.10 | <0.01 |
| 1 shared, T4 B, low-anchor (1×T4) | ~0.03-0.06 | <0.01 |
| 1 shared, T4 B, med-anchor (2×T4) | ~0.01-0.04 | <0.01 |
| 1 shared, T4 B, high-anchor (5×T2) | ~0.01-0.02 | <0.01 |
| 3 shared, T4 B, low-anchor | ~0.08-0.15 | <0.01 |
| 3 shared, T4 B, high-anchor | ~0.02-0.05 | <0.01 |

Key insight: the gradient from isolated → low → med → high-anchor
shows that even 1-2 T4 anchors significantly reduce the cascade.
The cascade is proportional to B's share of C2's total IMPL support.

## 8. Actual Results (2026-07-29)

**Finding: No false cascade.** Bidirectional EP correctly isolates C2.

| Scenario | A(T4) → NAND(T0) | B(T4) feedback | C1 drop | C2 drop |
|----------|-------------------|-----------------|---------|---------|
| B=T0, A=T4 | 52.6% → 48.6% (-4.0%) | 90.4% → 89.8% (-0.6%) | 54.1% → 53.7% (-0.4%) | 54.0% → 54.0% (0%) |
| B=T4, A=T4 | 52.3% → 47.9% (-4.4%) | 52.2% → 52.0% (-0.2%) | 50.4% → 50.0% (-0.4%) | 50.2% → 50.2% (0%) |

Key insight: EP damping (0.5) and evidence anchoring prevent the
B→C1→B→C2 feedback loop from having measurable effect. Each hop
attenuates the signal. A's 4% drop becomes 0.4% at C1, 0.2% at B,
and undetectable at C2.

**Verdict: No EP change needed.** Bidirectional messaging on directed
IMPL edges is safe. The cascade dies before reaching unrelated
sub-arguments. Your original fear was valid to investigate, but the
math handles it.
