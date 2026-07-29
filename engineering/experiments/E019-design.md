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

## 6. Test Cases

| # | Topology | B Tier | C2 Type | Mode | What we measure |
|---|----------|--------|---------|------|-----------------|
| 1 | 1 shared | T4 | isolated | bidirectional | C2 drop magnitude |
| 2 | 1 shared | T4 | isolated | directed | C2 should not drop |
| 3 | 1 shared | T2 | isolated | bidirectional | Compare to T4 |
| 4 | 1 shared | T0 | isolated | bidirectional | Anchoring reduces cascade |
| 5 | 3 shared | T4 | isolated | bidirectional | Cascade grows with density |
| 6 | 3 shared | T4 | isolated | directed | Directed still clean |
| 7 | 1 shared | T4 | anchored | bidirectional | C2 with 5 other IMPL sources |
| 8 | 1 shared | T4 | anchored | directed | Anchored C2 baseline |
| 9 | 3 shared | T4 | anchored | bidirectional | Worst case: dense + anchored |

## 7. Expected Results

| Test | Bidirectional C2 drop | Directed C2 drop |
|------|----------------------|-----------------|
| 1 shared, T4 B, isolated | ~0.05-0.10 | <0.01 |
| 1 shared, T2 B, isolated | ~0.03-0.06 | <0.01 |
| 1 shared, T0 B, isolated | ~0.01-0.03 | <0.01 |
| 3 shared, T4 B, isolated | ~0.15-0.25 | <0.01 |
| 3 shared, T0 B, isolated | ~0.05-0.10 | <0.01 |
| 1 shared, T4 B, anchored | ~0.01-0.03 | <0.01 |
| 3 shared, T4 B, anchored | ~0.03-0.08 | <0.01 |

Key insight: anchoring C2 (5 T2 sources) drops the cascade from
0.05-0.10 to 0.01-0.03. The bidirectional feedback path B→C1→B→C2
competes with C2's 5 other IMPL signals.

## 8. Success Criteria
- H1: Bidirectional C2 drop > 0.05 for T4 B (false cascade confirmed)
- H2: Directed C2 drop < 0.01 (cascade eliminated)
- H3: 3-shared drop > 1-shared drop (density effect)
- H4: T0 B drop < T4 B drop (anchoring effect)
