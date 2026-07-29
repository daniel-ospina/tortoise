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

## 6. Test Cases

| # | Topology | B Tier | Mode | What we measure |
|---|----------|--------|------|-----------------|
| 1 | 1 shared | T4 | bidirectional | C2 drop magnitude |
| 2 | 1 shared | T4 | directed | C2 should not drop |
| 3 | 1 shared | T2 | bidirectional | Compare to T4 |
| 4 | 1 shared | T0 | bidirectional | Anchoring reduces cascade |
| 5 | 3 shared | T4 | bidirectional | Cascade grows with density |
| 6 | 3 shared | T4 | directed | Directed still clean |

## 7. Expected Results

| Test | Bidirectional C2 drop | Directed C2 drop |
|------|----------------------|-----------------|
| 1 shared, T4 B | ~0.05-0.10 | <0.01 |
| 1 shared, T2 B | ~0.03-0.06 | <0.01 |
| 1 shared, T0 B | ~0.01-0.03 | <0.01 |
| 3 shared, T4 B | ~0.15-0.25 | <0.01 |
| 3 shared, T0 B | ~0.05-0.10 | <0.01 |

## 8. Success Criteria
- H1: Bidirectional C2 drop > 0.05 for T4 B (false cascade confirmed)
- H2: Directed C2 drop < 0.01 (cascade eliminated)
- H3: 3-shared drop > 1-shared drop (density effect)
- H4: T0 B drop < T4 B drop (anchoring effect)
