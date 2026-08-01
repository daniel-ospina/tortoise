# Tortoise EP Source Credibility Validation — Experiment Design

**Status:** Design (pre-implementation)
**Created:** 2026-07-29
**Epic:** #TODO — Source credibility log-scale aggregation
**Goal:** Validate that planned log-scale source credibility aggregation produces correct,
monotonic, anti-Sybil EP belief propagation across linear, loopy, and contradictory graphs.

---

## 1. Mathematical Framework

### 1.1 Source Credibility Tiers

Source nodes (`kind=Source`) carry a `credibilityTier` property (T0–T4). Each tier maps
to a Beta evidence prior on the Point the source is extracted from.

| Tier | Name       | Beta Prior  | Mean (α/(α+β)) | Pseudo-count (pc = α−1) |
|------|-----------|-------------|-----------------|--------------------------|
| T0   | Gold       | Beta(10, 1) | 0.9091          | 9.0                      |
| T1   | High       | Beta(5, 1)  | 0.8333          | 4.0                      |
| T2   | Medium     | Beta(3, 1)  | 0.7500          | 2.0                      |
| T3   | Low        | Beta(2, 1)  | 0.6667          | 1.0                      |
| T4   | Unverified | Beta(1.1, 1)| 0.5238          | 0.1                      |

**No-source baseline:** Beta(1, 1) → mean = 0.5000, pc = 0.

### 1.2 Log-Scale Aggregation (Planned — Not Yet Implemented)

**Current behavior** (as of 2026-07-29, `_apply_source_inheritance` in `sdk.py`):
highest-tier-wins — only the best single source is used.

**Planned behavior:** When N same-tier sources support the same Point via
`extractedFrom` edges, aggregate them with log-scale dampening:

```
effective_pc = base_pc × log₂(N + 1)
effective_prior = Beta(1 + effective_pc_pos, 1 + effective_pc_neg)
```

| N sources | log₂(N+1) | T4 eff. pc | T4 mean  | T0 eff. pc | T0 mean  |
|-----------|-----------|------------|----------|------------|----------|
| 1         | 1.000     | 0.100      | 0.5238   | 9.000      | 0.9091   |
| 2         | 1.585     | 0.159      | 0.5368   | 14.265     | 0.9345   |
| 3         | 2.000     | 0.200      | 0.5455   | 18.000     | 0.9474   |
| 4         | 2.322     | 0.232      | 0.5519   | 20.898     | 0.9543   |
| 5         | 2.585     | 0.259      | 0.5571   | 23.265     | 0.9588   |
| 10        | 3.459     | 0.346      | 0.5737   | 31.131     | 0.9689   |
| 100       | 6.658     | 0.666      | 0.6249   | 59.922     | 0.9836   |
| 1000      | 9.966     | 0.997      | 0.6663   | 89.694     | 0.9890   |

**Key anti-Sybil property:** Even 1000 T4 sources (pc ≈ 0.997) barely exceeds a single
T3 source (pc = 1.0). Quantity cannot substitute for quality.

### 1.3 Multi-Tier Aggregation

When multiple tiers are present:

```
total_pc_pos = Σ_tier (base_pc_pos_tier × log₂(N_tier + 1))
total_pc_neg = Σ_tier (base_pc_neg_tier × log₂(N_tier + 1))
prior = Beta(1 + total_pc_pos, 1 + total_pc_neg)
```

For NAND sources (contradictory), the pseudo-count contributes to `total_pc_neg`.

### 1.4 EP Propagation Model

- **IMPL edges** (bidirectional in EP): belief flows both ways along `A-[IMPL]->B`.
  The factor Φ(c_A, c_B) = 1 − |c_A − c_B| pulls beliefs toward each other.
- **NAND edges**: Φ(c_A, c_B) = |c_A − (1−c_B)| pushes beliefs apart.
- **Evidence priors** are set via `set_point_baseline(point_id, α, β)` and injected
  as natural-parameter offsets before message accumulation.
- **EP convergence** uses damped message passing with Gauss-Jacobi quadrature (n=8),
  tolerance 1e-3, max 50 iterations.

### 1.5 Graph Topology

```
Source(T) ──[extractedFrom]──> Point ──[IMPL/NAND]──> Claim
```

Sources provide evidence for Points. Points propagate belief to Claims via operators.
Claims (and Points) can also be connected to each other via IMPL/NAND operators,
forming arbitrary factor graphs.

---

## 2. Graph Scenarios

### Scenario A: Linear Chain
```
Source(s) ──[extractedFrom]──> Point_A ──[IMPL]──> Claim_B
```
- Sources only on Point_A. Claim_B receives belief only via the IMPL edge.
- Tests: belief propagation along a single edge, attenuation.

### Scenario B: Loopy Cluster (Single-Source)
```
Point_A ──[IMPL]──> Point_B
   ^                  |
   |                  v
Point_C <──[IMPL]───
```
- Sources only on Point_A. All three nodes form a directed cycle.
- Tests: EP convergence with feedback loops, no inflation explosion.

### Scenario C: Loopy Cluster (Multi-Source)
```
Point_A ──[IMPL]──> Point_B
   ^                  |
   |                  v
Point_C <──[IMPL]───
```
- Sources on Point_A AND Point_B. Tests: multi-source reinforcement in loopy graphs.

---

## 3. Orthogonal Situations — Predictions & Assertions

### Naming conventions for assertions

- `conf(X)` = EP confidence mean for node X after convergence
- `δ = 1e-4`: exact-match tolerance (direct prior computation)
- `ε = 0.02`: EP convergence tolerance (message-passing approximation)
- `ε_loop = 0.03`: loopy graph tolerance (feedback artifacts)

---

### Situation 1: No Source → Add Single T4

**Goal:** Verify that any source (even weakest T4) increases confidence above baseline.

**Setup:** Scenario A. Point_A baseline → add 1×T4 → re-run EP.

**Predicted values:**
| State              | Prior on A      | conf(A)      |
|--------------------|-----------------|--------------|
| No source          | Beta(1, 1)      | 0.5000       |
| 1×T4               | Beta(1.1, 1)    | 0.5238       |
| Δ                  | —               | +0.0238      |

**Assertions:**
```python
# S1.1: T4 increases confidence above baseline
assert conf_A_with_T4 > conf_A_baseline

# S1.2: Increase is measurable (≈ 0.024)
assert 0.015 < (conf_A_with_T4 - conf_A_baseline) < 0.035

# S1.3: A stays well above uniform
assert conf_A_with_T4 > 0.51

# S1.4: Downstream Claim_B should not decrease (propagation)
assert conf_B_with_T4 >= conf_B_baseline - ε
```

---

### Situation 2: No Source → Add T3/T2/T1/T0 (Proportional Increase)

**Goal:** Verify monotonic increase with source tier. Higher tier → higher confidence.

**Setup:** Scenario A. Test each tier independently (fresh graph per tier).

**Predicted values:**
| Source | Prior          | conf(A) pred. |
|--------|----------------|---------------|
| None   | Beta(1, 1)     | 0.5000        |
| 1×T4   | Beta(1.1, 1)   | 0.5238        |
| 1×T3   | Beta(2, 1)     | 0.6667        |
| 1×T2   | Beta(3, 1)     | 0.7500        |
| 1×T1   | Beta(5, 1)     | 0.8333        |
| 1×T0   | Beta(10, 1)    | 0.9091        |

**Assertions:**
```python
confs = [conf_none, conf_T4, conf_T3, conf_T2, conf_T1, conf_T0]

# S2.1: Strict monotonic ordering
for i in range(len(confs) - 1):
    assert confs[i] < confs[i+1], \
        f"Tier ordering violated at index {i}: {confs[i]} >= {confs[i+1]}"

# S2.2: T4 > baseline with meaningful margin
assert conf_T4 - conf_none > 0.01

# S2.3: T0 substantially exceeds T4
assert conf_T0 - conf_T4 > 0.30

# S2.4: T0 near expected value
assert abs(conf_T0 - 0.9091) < ε

# S2.5: All confidences in valid range
for c in confs:
    assert 0.0 < c < 1.0
```

---

### Situation 3: 1→2→3→10 T4 Sources (Cumulative, Diminishing Returns)

**Goal:** Verify log-scale aggregation produces strictly diminishing returns.

**Setup:** Scenario A. Add T4 sources incrementally to Point_A.

**Predicted values:**
| N  | log₂(N+1) | eff. pc  | mean(A) pred. | Δ from N−1 |
|----|-----------|----------|---------------|------------|
| 1  | 1.000     | 0.100    | 0.5238        | —          |
| 2  | 1.585     | 0.159    | 0.5368        | +0.0130    |
| 3  | 2.000     | 0.200    | 0.5455        | +0.0087    |
| 4  | 2.322     | 0.232    | 0.5519        | +0.0064    |
| 5  | 2.585     | 0.259    | 0.5571        | +0.0052    |
| 10 | 3.459     | 0.346    | 0.5737        | —          |

**Assertions:**
```python
# S3.1: Monotonic increase
assert conf_10 > conf_5 > conf_4 > conf_3 > conf_2 > conf_1

# S3.2: Diminishing returns — early additions give more than late
delta_1_to_2 = conf_2 - conf_1
delta_3_to_4 = conf_4 - conf_3
delta_9_to_10 = conf_10 - conf_9
assert delta_1_to_2 > delta_3_to_4
assert delta_1_to_2 > delta_9_to_10 * 1.5

# S3.3: 10 T4 still below T3 level (pc=1)
assert conf_10 < conf_1xT3  # from Situation 2

# S3.4: 10 T4 within range of predicted mean
assert abs(conf_10 - 0.5737) < ε

# S3.5: N=10 conf matches predicted within EP tolerance
assert 0.55 < conf_10 < 0.60
```

---

### Situation 4: 10 T4 vs 1 T2 (Anti-Sybil Validation)

**Goal:** Verify log-scale aggregation prevents low-tier source flooding from
matching higher-tier credibility.

**Setup:** Scenario A. Two independent graphs:
- Graph X: 10×T4 sources on Point_A → compute conf(A)
- Graph Y: 1×T2 source on Point_A → compute conf(A)

**Predicted values:**
| Case       | eff. pc | mean(A) pred. |
|------------|---------|---------------|
| 10×T4      | 0.346   | 0.5737        |
| 1×T2       | 2.000   | 0.7500        |
| Difference | —       | 0.1763        |

**Assertions:**
```python
# S4.1: Quality beats quantity — 1×T2 > 10×T4
assert conf_1xT2 > conf_10xT4

# S4.2: Substantial gap (> 0.10 in confidence)
assert conf_1xT2 - conf_10xT4 > 0.10

# S4.3: 10 T4 < 1 T3 (even weaker quality tier still beats 10 unverified)
assert conf_10xT4 < conf_1xT3

# S4.4: 100 T4 still < 1 T2 (extreme anti-Sybil)
# 100×T4: pc = 0.1 × 6.658 = 0.666, mean ≈ 0.625
assert conf_100xT4 < conf_1xT2

# S4.5: 1000 T4 roughly equals 1 T3 (boundary case)
# 1000×T4: pc = 0.1 × 9.966 = 0.997 ≈ 1.0 (T3 pc)
# Should be close but NOT exceed T3 by large margin
assert abs(conf_1000xT4 - conf_1xT3) < 0.05
```

---

### Situation 5: 2 Gold + Add T4 (Ceiling Effect)

**Goal:** Verify that adding a low-tier source to strong existing evidence
produces negligible change (ceiling/saturation).

**Setup:** Scenario A.
1. 2×T0 sources on Point_A → run EP → measure
2. Add 1×T4 → run EP → measure
3. Compare

**Predicted values:**
| State     | eff. pc      | mean(A)  |
|-----------|--------------|----------|
| 2×T0      | 9×1.585=14.27| 0.9345   |
| 2×T0+1×T4 | 14.27+0.1=14.37| 0.9349  |
| Δ         | —            | +0.0004  |

**Assertions:**
```python
delta = conf_with_T4 - conf_2xT0

# S5.1: Non-negative (no regression)
assert delta >= 0

# S5.2: Negligible increase (< 0.005)
assert delta < 0.005
```

---

### Situation 6: 5 Gold + Add T4 (No Regression)

**Goal:** Verify monotonicity — adding evidence NEVER decreases confidence.

**Setup:** Scenario A.
1. 5×T0 sources on Point_A → run EP
2. Add 1×T4 → run EP
3. Assert conf does NOT decrease

**Predicted values:**
| State     | eff. pc      | mean(A)  |
|-----------|--------------|----------|
| 5×T0      | 9×2.585=23.27| 0.9588   |
| 5×T0+1×T4| 23.37        | 0.9590   |

**Assertions:**
```python
# S6.1: Strict non-regression (with floating-point tolerance)
assert conf_with_T4 >= conf_5xT0 - δ

# S6.2: Increase is negligible (ceiling)
assert (conf_with_T4 - conf_5xT0) < 0.005
```

---

### Situation 7: Add T4 → Remove T4 (Idempotency)

**Goal:** Verify that removing a source returns confidence to its prior state.
The system must be deterministic and source-removal must be reversible.

**Setup:** Scenario A.
1. Baseline: 1×T2 source on Point_A → run EP → conf_baseline
2. Add 1×T4 source → run EP → conf_with_T4
3. Remove the T4 source → run EP → conf_removed
4. Compare conf_removed vs conf_baseline

**Assertions:**
```python
# S7.1: Return to baseline within EP tolerance
assert abs(conf_removed - conf_baseline) < ε

# S7.2: Relative error < 1%
relative_error = abs(conf_removed - conf_baseline) / max(conf_baseline, 0.01)
assert relative_error < 0.01

# S7.3: Intermediate step did increase (sanity)
assert conf_with_T4 > conf_baseline
```

---

### Situation 8: 1 Gold + 1 NAND Source (Contradictory Signals)

**Goal:** Verify that NAND edges correctly reduce confidence from contradictory sources.

**Setup:** Scenario A extended with NAND:
- Source_T0 →[extractedFrom]→ Point_A (positive, pc_pos=9)
- Source_T4 →[extractedFrom]→ Point_A_NAND →[NAND]→ Point_A (contradictory, pc_neg=0.1)

The NAND source contributes negative pseudo-count to Point_A.

**Sub-case 8a: T4 NAND (weak contradiction)**
| State            | pc_pos | pc_neg | Prior           | mean   |
|------------------|--------|--------|-----------------|--------|
| T0 alone         | 9.0    | 0      | Beta(10, 1)     | 0.9091 |
| T4 NAND only     | 0      | 0.1    | Beta(1, 1.1)    | 0.4762 |
| T0 + T4 NAND     | 9.0    | 0.1    | Beta(10, 1.1)   | 0.9009 |
| Δ from T0 alone  | —      | —      | —               | −0.0082|

**Sub-case 8b: T0 NAND (equal-tier contradiction)**
| State            | pc_pos | pc_neg | Prior           | mean   |
|------------------|--------|--------|-----------------|--------|
| T0 + T0 NAND     | 9.0    | 9.0    | Beta(10, 10)    | 0.5000 |

**Assertions:**
```python
# S8.1: NAND reduces confidence (T4 NAND, sub-case 8a)
assert conf_T0_alone > conf_T0_plus_T4_NAND

# S8.2: Reduction is bounded — T0 still dominates weak T4 NAND
assert conf_T0_plus_T4_NAND > 0.85

# S8.3: Drop is proportional to NAND strength
drop_weak = conf_T0_alone - conf_T0_plus_T4_NAND
assert 0.005 < drop_weak < 0.02

# S8.4: Equal-tier contradiction returns to near-baseline (sub-case 8b)
assert abs(conf_T0_plus_T0_NAND - 0.50) < ε_loop

# S8.5: T4 NAND alone is below baseline (contradiction without support)
assert conf_T4_NAND_alone < 0.50
```

---

### Situation 9: T4 Source + Mitigate Edge (Drops but Stays Above No-Source)

**Goal:** Verify edge mitigation correctly attenuates source credibility without
going below the no-information baseline.

**Setup:** Scenario A with mitigated extractedFrom edge.
1. No source baseline
2. 1×T4 source (unmitigated)
3. 1×T4 source with edge mitigation at 0.5 strength
4. 1×T4 source with edge fully neutralized (mitigation 0.0)

**Predicted values** (mitigation linearly scales effective pc):
| State               | eff. pc | mean(A) |
|---------------------|---------|---------|
| No source           | 0.000   | 0.5000  |
| T4 (unmitigated)    | 0.100   | 0.5238  |
| T4 (mitigation 0.5) | 0.050   | 0.5119  |
| T4 (mitigation 0.0) | 0.000   | 0.5000  |

**Assertions:**
```python
# S9.1: Mitigation reduces confidence
assert conf_unmitigated > conf_mitigated_50

# S9.2: Mitigated stays above baseline (no negative info)
assert conf_mitigated_50 > conf_baseline - ε

# S9.3: Full neutralization returns to baseline
assert abs(conf_mitigated_0 - conf_baseline) < ε

# S9.4: Effect is proportional
drop_50 = conf_unmitigated - conf_mitigated_50
drop_100 = conf_unmitigated - conf_mitigated_0
assert drop_50 > 0  # mitigation has effect
assert drop_100 > drop_50  # stronger mitigation = bigger drop
```

---

### Situation 10: Chain — Source on A, Check B's Response

**Goal:** Verify belief propagation along IMPL edges with correct direction
preservation and attenuation.

**Setup:** Scenario A. Source on Point_A. Measure both Point_A and Claim_B.

**Sub-case 10a: T0 source on A**
| Node | Expected behavior |
|------|-------------------|
| A    | ≈ 0.909 (directly sourced) |
| B    | 0.50 < conf(B) < 0.909 (attenuated from A) |

**Sub-case 10b: T4 source on A**
| Node | Expected behavior |
|------|-------------------|
| A    | ≈ 0.524 (weak source) |
| B    | 0.50 < conf(B) < 0.524 (weakly influenced) |

**Sub-case 10c: No source (baseline)**
| Node | Expected behavior |
|------|-------------------|
| A, B | Both ≈ 0.500 (uniform prior) |

**Assertions:**
```python
# S10.1: Direction preservation — B moves in same direction as A
delta_A_T0 = conf_A_T0 - conf_A_baseline
delta_B_T0 = conf_B_T0 - conf_B_baseline
assert delta_A_T0 > 0
assert delta_B_T0 > 0  # B follows A's direction

# S10.2: Attenuation — B's shift is smaller than A's
assert abs(delta_B_T0) < abs(delta_A_T0)

# S10.3: With T4, B barely moves (weak signal doesn't propagate far)
assert abs(conf_B_T4 - conf_B_baseline) < 0.05

# S10.4: With T0, B moves significantly
assert conf_B_T0 - conf_B_baseline > 0.02

# S10.5: Baseline: A and B both near 0.5
assert abs(conf_A_baseline - 0.50) < ε
assert abs(conf_B_baseline - 0.50) < ε
```

---

## 4. Log-Scale Validation Cases

These validate the mathematical correctness of `effective_pc = base_pc × log₂(N + 1)`.

### 4.1 Base Case: N=1
```
effective_pc = base_pc × log₂(2) = base_pc × 1.0 = base_pc
```
A single source has unchanged credibility. **Assert:** conf(1×T) == base_conf(T).

### 4.2 Growth Factor: N=3 vs N=4
- log₂(4) = 2.000, log₂(5) ≈ 2.322
- Ratio = 2.322/2.000 = 1.161
- The 4th source adds only 16% more credibility than the 3rd.
- **Assert:** (conf_4 − conf_3) / (conf_3 − conf_2) < 0.8

### 4.3 Scalability: N=10 vs N=100
- log₂(11) ≈ 3.459, log₂(101) ≈ 6.658
- Ratio = 1.925
- 10× more sources → less than 2× more credibility.
- **Assert:** (conf_100 − conf_baseline) / (conf_10 − conf_baseline) < 3.0

### 4.4 Extreme Anti-Sybil: 1000 T4 vs 1 T3
- 1000×T4: pc = 0.1 × 9.966 = 0.997
- 1×T3: pc = 1.0
- 1000 trash sources just barely reach 1 low-tier source.
- **Assert:** |conf_1000xT4 − conf_1xT3| < 0.05

### 4.5 T0 Saturation Curve
| N T0 | pc      | mean   | Δ from N−1 |
|------|---------|--------|------------|
| 1    | 9.00    | 0.9091 | —          |
| 2    | 14.27   | 0.9345 | +0.0254    |
| 5    | 23.27   | 0.9588 | (+0.0243 from 2) |
| 10   | 31.13   | 0.9689 | +0.0101    |
| 100  | 59.92   | 0.9836 | (+0.0147 from 10) |

- **Assert:** Δ(2→1) > Δ(10→5)  (diminishing marginal returns)
- **Assert:** Δ(10→5) > Δ(100→10)/10 (very diminishing at scale)

---

## 5. Multi-Scenario Cross-Validation

### 5.1 Scenario B: Loopy Cluster, Sources on A Only

**Setup:** Three Points (A, B, C) in a cycle: A→B→C→A (all IMPL). T0 source on A only.

**Predictions:**
- conf(A) ≈ 0.909 (directly sourced)
- conf(B) < conf(A) (attenuated by 1 hop)
- conf(C) < conf(A) (attenuated by 2 hops)
- All > 0.500 (positive signal propagates)
- Loop may create 2–5% inflation vs. equivalent linear chain due to feedback

**Assertions:**
```python
# SCB.1: Directly sourced node is highest
assert conf_A > conf_B
assert conf_A > conf_C

# SCB.2: All nodes get some positive signal
assert conf_A > 0.80
assert conf_B > 0.50
assert conf_C > 0.50

# SCB.3: B and C both elevated above baseline
assert conf_B > 0.55
assert conf_C > 0.52

# SCB.4: Loop doesn't explode — C not wildly inflated
assert conf_C < conf_A - 0.05

# SCB.5: Convergence — EP must converge, not diverge
assert ep_result["converged"] == True
```

### 5.2 Scenario C: Loopy Cluster, Sources on A AND B

**Setup:** Same cycle. T0 source on A, T1 source on B.

**Predictions:**
- conf(A) ≈ 0.909 (T0 directly, + some loop feedback from B)
- conf(B) ≈ 0.833+ (T1 directly, boosted by loop from A)
- conf(C) elevated by both A and B through the loop
- conf(C) in Scenario C > conf(C) in Scenario B (two sources > one)

**Assertions:**
```python
# SCC.1: Both sourced nodes at or above their tier level
assert conf_A >= 0.90
assert conf_B >= 0.83

# SCC.2: Two sources → C gets more signal than one-source scenario
# (Need to run Scenario B first for comparison)
assert conf_C_multi > conf_C_single + 0.02

# SCC.3: No contradiction between A and B (both IMPL)
assert conf_A > 0.80 and conf_B > 0.80

# SCC.4: EP converges
assert ep_result["converged"] == True
```

---

## 6. Test Implementation Structure

### File: `premise-labs/tests/test_ep_sources.py`

```python
"""EP source credibility validation — log-scale aggregation experiments.

Tests the planned log-scale source aggregation model:
  effective_pc = base_pc × log₂(N + 1)
  effective_prior = Beta(1 + effective_pc_pos, 1 + effective_pc_neg)

Covers:
  - 10 orthogonal situations (Section 3 of experiment design doc)
  - 3 graph topologies: linear chain, loopy single-source, loopy dual-source
  - Log-scale formula validation
  - Cross-scenario comparisons
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK


# ═══════════════════════════════════════════════════════════════════
# Constants — Source Credibility Tiers
# ═══════════════════════════════════════════════════════════════════

TIER_MAP = {
    "T0": (10.0, 1.0),   # Gold:       pc=9.0, mean=0.9091
    "T1": (5.0, 1.0),    # High:       pc=4.0, mean=0.8333
    "T2": (3.0, 1.0),    # Medium:     pc=2.0, mean=0.7500
    "T3": (2.0, 1.0),    # Low:        pc=1.0, mean=0.6667
    "T4": (1.1, 1.0),    # Unverified: pc=0.1, mean=0.5238
}

TIER_PC = {tier: alpha - 1.0 for tier, (alpha, beta) in TIER_MAP.items()}

# Tolerance constants
DELTA = 1e-4       # Exact comparison (direct prior math)
EPSILON = 0.02     # EP convergence tolerance
EPSILON_LOOP = 0.03  # Loopy graph tolerance


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def log_aggregate_pc(base_pc: float, n_sources: int) -> float:
    """Compute log-scale aggregated pseudo-count.  Formula: base_pc × log₂(N+1)."""
    return base_pc * math.log2(n_sources + 1)


def log_aggregate_prior(tier: str, n_sources: int) -> tuple[float, float]:
    """Compute Beta(α, β) prior for N same-tier sources with log aggregation."""
    base_alpha, base_beta = TIER_MAP[tier]
    base_pc = base_alpha - 1.0
    effective_pc = log_aggregate_pc(base_pc, n_sources)
    return (1.0 + effective_pc, 1.0)


def log_aggregate_prior_mixed(
    pos_sources: dict[str, int],  # {tier: count}
    neg_sources: dict[str, int] | None = None,
) -> tuple[float, float]:
    """Compute Beta(α, β) prior for mixed-tier sources with log aggregation."""
    total_pc_pos = sum(
        log_aggregate_pc(TIER_PC[tier], count)
        for tier, count in pos_sources.items()
    )
    total_pc_neg = 0.0
    if neg_sources:
        total_pc_neg = sum(
            log_aggregate_pc(TIER_PC[tier], count)
            for tier, count in neg_sources.items()
        )
    return (1.0 + total_pc_pos, 1.0 + total_pc_neg)


def mean_from_beta(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta)


@contextmanager
def fresh_sdk():
    """Yield a TortoiseSDK with a fresh temp database."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ep_src_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        yield sdk
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def make_point(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    return sdk.create_point(kind, content)


def make_operator(sdk: TortoiseSDK, source_id: str, target_id: str,
                   op_type: str = "IMPL") -> dict:
    return sdk.create_operator(op_type, source_id, [target_id])


def set_source_evidence(sdk: TortoiseSDK, point_id: str, tier: str):
    """Set Beta prior on a Point to simulate a source of given tier."""
    alpha, beta = TIER_MAP[tier]
    sdk.set_point_baseline(point_id, alpha, beta)


def set_aggregated_evidence(
    sdk: TortoiseSDK,
    point_id: str,
    pos_sources: dict[str, int],
    neg_sources: dict[str, int] | None = None,
):
    """Set aggregated evidence from multiple sources on a Point."""
    alpha, beta = log_aggregate_prior_mixed(pos_sources, neg_sources)
    sdk.set_point_baseline(point_id, alpha, beta)


def run_ep(sdk: TortoiseSDK) -> dict:
    """Run EP and return {iterations, converged, confidences}."""
    return sdk.compute_confidence()


def get_conf(result: dict, node_id: str) -> float:
    return result["confidences"][node_id]["mean"]


# ═══════════════════════════════════════════════════════════════════
# Scenario Builders
# ═══════════════════════════════════════════════════════════════════

def build_scenario_a(sdk: TortoiseSDK) -> tuple[str, str]:
    """Linear chain: Point_A →[IMPL]→ Claim_B. Returns (a_id, b_id)."""
    a = make_point(sdk, "Point A: evidence aggregation point")
    b = make_point(sdk, "Claim B: conclusion")
    make_operator(sdk, a["id"], b["id"], "IMPL")
    return a["id"], b["id"]


def build_scenario_b(sdk: TortoiseSDK) -> tuple[str, str, str]:
    """Loopy cluster A→B→C→A. Returns (a_id, b_id, c_id)."""
    a = make_point(sdk, "Point A: loopy cluster")
    b = make_point(sdk, "Point B: loopy cluster")
    c = make_point(sdk, "Point C: loopy cluster")
    make_operator(sdk, a["id"], b["id"], "IMPL")
    make_operator(sdk, b["id"], c["id"], "IMPL")
    make_operator(sdk, c["id"], a["id"], "IMPL")
    return a["id"], b["id"], c["id"]


# ═══════════════════════════════════════════════════════════════════
# Unit Tests — Log-Scale Aggregation Math
# ═══════════════════════════════════════════════════════════════════

class TestLogAggregationMath:
    """Pure math: log-scale aggregation formulas (no EP needed)."""

    def test_base_case_n1(self):
        """N=1 → effective_pc = base_pc (no scaling)."""
        for tier, (alpha, _beta) in TIER_MAP.items():
            base_pc = alpha - 1.0
            effective = log_aggregate_pc(base_pc, 1)
            assert abs(effective - base_pc) < DELTA, \
                f"{tier}: effective_pc={effective} != base_pc={base_pc}"

    def test_diminishing_returns(self):
        """Each additional source adds less pseudo-count than the previous one."""
        base_pc = 1.0
        gains = []
        for n in range(1, 10):
            pc_n = log_aggregate_pc(base_pc, n)
            pc_next = log_aggregate_pc(base_pc, n + 1)
            gains.append(pc_next - pc_n)
        # Strictly diminishing
        for i in range(len(gains) - 1):
            assert gains[i] > gains[i + 1], \
                f"Gain at n={i+1} ({gains[i]}) not > gain at n={i+2} ({gains[i+1]})"

    def test_anti_sybil_extreme(self):
        """1000 T4 sources barely exceed 1 T3 source."""
        t4_pc_1000 = log_aggregate_pc(TIER_PC["T4"], 1000)
        t3_pc_1 = TIER_PC["T3"]
        # 1000 T4 ≈ 0.997, T3 = 1.0 — should be within 0.1
        assert abs(t4_pc_1000 - t3_pc_1) < 0.1

    def test_log_aggregate_prior_single(self):
        """log_aggregate_prior for N=1 returns base tier prior."""
        for tier, (alpha, beta) in TIER_MAP.items():
            a, b = log_aggregate_prior(tier, 1)
            assert abs(a - alpha) < DELTA
            assert abs(b - beta) < DELTA

    def test_prior_mean_monotonic_with_n(self):
        """As N increases, the prior mean increases monotonically."""
        for tier in TIER_MAP:
            prev_mean = 0.0
            for n in [1, 2, 3, 5, 10, 100]:
                a, b = log_aggregate_prior(tier, n)
                m = mean_from_beta(a, b)
                assert m > prev_mean, f"{tier} N={n}: mean {m} <= prev {prev_mean}"
                prev_mean = m

    def test_prior_never_exceeds_1(self):
        """No matter how many sources, mean stays below 1.0."""
        for tier in TIER_MAP:
            for n in [1, 10, 100, 1000, 10000]:
                a, b = log_aggregate_prior(tier, n)
                m = mean_from_beta(a, b)
                assert m < 1.0, f"{tier} N={n}: mean exceeded 1.0"

    def test_prior_above_baseline(self):
        """All tiers at N≥1 produce mean > 0.5."""
        for tier in TIER_MAP:
            for n in [1, 2, 10]:
                a, b = log_aggregate_prior(tier, n)
                m = mean_from_beta(a, b)
                assert m > 0.5, f"{tier} N={n}: mean {m} <= 0.5"

    def test_mixed_tier_aggregation(self):
        """Mixed tiers sum pseudo-counts correctly."""
        a, b = log_aggregate_prior_mixed(
            pos_sources={"T0": 1, "T4": 5},
        )
        expected_pc = TIER_PC["T0"] * math.log2(2) + TIER_PC["T4"] * math.log2(6)
        # T0 N=1: 9 * 1 = 9; T4 N=5: 0.1 * 2.585 = 0.259
        # Total: 9.259 → Beta(10.259, 1) → mean ≈ 0.911
        expected_alpha = 1.0 + expected_pc
        assert abs(a - expected_alpha) < DELTA
        assert abs(b - 1.0) < DELTA

    def test_nand_contribution(self):
        """NAND sources contribute to pc_neg (beta parameter)."""
        a, b = log_aggregate_prior_mixed(
            pos_sources={"T0": 1},
            neg_sources={"T4": 1},
        )
        # T0 pos: pc=9, T4 neg: pc=0.1
        # Beta(1+9, 1+0.1) = Beta(10, 1.1)
        assert abs(a - 10.0) < DELTA
        assert abs(b - 1.1) < DELTA
        mean = mean_from_beta(a, b)
        assert mean < mean_from_beta(10.0, 1.0)  # NAND reduces mean
        assert mean > 0.85  # Gold still dominates


# ═══════════════════════════════════════════════════════════════════
# Integration Tests — EP with Sources
# ═══════════════════════════════════════════════════════════════════

class TestSituation1_NoSourceToT4:
    """Situation 1: No source → add single T4."""

    def test_t4_increases_confidence(self):
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)

            # Baseline (no source)
            baseline = run_ep(sdk)
            conf_a_base = get_conf(baseline, a_id)
            conf_b_base = get_conf(baseline, b_id)

            # Clean up and rebuild with T4 source
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            with_t4 = run_ep(sdk)
            conf_a_t4 = get_conf(with_t4, a_id)
            conf_b_t4 = get_conf(with_t4, b_id)

            # S1.1: T4 increases above baseline
            assert conf_a_t4 > conf_a_base + 0.01
            # S1.2: Increase is in expected range (~0.024)
            delta = conf_a_t4 - conf_a_base
            assert 0.015 < delta < 0.035, f"Delta={delta:.4f} out of [0.015, 0.035]"
            # S1.3: A stays above 0.51
            assert conf_a_t4 > 0.51
            # S1.4: Downstream B does not decrease
            assert conf_b_t4 >= conf_b_base - EPSILON

    def test_t4_predicted_mean(self):
        """T4 prior mean should be close to 0.5238."""
        with fresh_sdk() as sdk:
            a_id, _b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            result = run_ep(sdk)
            conf = get_conf(result, a_id)
            assert abs(conf - 0.5238) < EPSILON


class TestSituation2_TierProportional:
    """Situation 2: No source → add T3/T2/T1/T0 — proportional increase."""

    def test_monotonic_tier_ordering(self):
        confs = {}
        for tier in ["T4", "T3", "T2", "T1", "T0"]:
            with fresh_sdk() as sdk:
                a_id, _b_id = build_scenario_a(sdk)
                set_source_evidence(sdk, a_id, tier)
                result = run_ep(sdk)
                confs[tier] = get_conf(result, a_id)

        # S2.1: Strict ordering T4 < T3 < T2 < T1 < T0
        ordered = ["T4", "T3", "T2", "T1", "T0"]
        for i in range(len(ordered) - 1):
            assert confs[ordered[i]] < confs[ordered[i + 1]], \
                f"{ordered[i]}: {confs[ordered[i]]:.4f} >= {ordered[i+1]}: {confs[ordered[i+1]]:.4f}"

    def test_t0_vs_t4_gap(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            conf_t4 = get_conf(run_ep(sdk), a_id)
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            conf_t0 = get_conf(run_ep(sdk), a_id)

        # S2.3: T0 substantially exceeds T4
        assert conf_t0 - conf_t4 > 0.30

    def test_t0_near_expected(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            conf = get_conf(run_ep(sdk), a_id)
            assert abs(conf - 0.9091) < EPSILON


class TestSituation3_CumulativeT4:
    """Situation 3: 1→2→3→10 T4 sources — cumulative with diminishing returns."""

    @pytest.mark.parametrize("n_sources,expected_mean", [
        (1, 0.5238),
        (2, 0.5368),
        (3, 0.5455),
        (5, 0.5571),
        (10, 0.5737),
    ])
    def test_cumulative_t4(self, n_sources, expected_mean):
        with fresh_sdk() as sdk:
            a_id, _b_id = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", n_sources)
            sdk.set_point_baseline(a_id, alpha, beta)
            result = run_ep(sdk)
            conf = get_conf(result, a_id)
            assert abs(conf - expected_mean) < EPSILON, \
                f"N={n_sources}: expected {expected_mean:.4f}, got {conf:.4f}"

    def test_diminishing_returns(self):
        """Each additional T4 gives less gain than the previous one."""
        gains = []
        prev_conf = None
        for n in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                alpha, beta = log_aggregate_prior("T4", n)
                sdk.set_point_baseline(a_id, alpha, beta)
                conf = get_conf(run_ep(sdk), a_id)
                if prev_conf is not None:
                    gains.append(conf - prev_conf)
                prev_conf = conf

        # Gains should be strictly decreasing
        for i in range(len(gains) - 1):
            assert gains[i] > gains[i + 1] - DELTA, \
                f"Gain {i}: {gains[i]:.6f} not > gain {i+1}: {gains[i+1]:.6f}"

    def test_10_t4_below_t3(self):
        """10 T4 sources should NOT reach T3 credibility."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 10)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_10t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T3")
            conf_1t3 = get_conf(run_ep(sdk), a_id)

        assert conf_10t4 < conf_1t3


class TestSituation4_AntiSybil:
    """Situation 4: 10 T4 vs 1 T2 — anti-Sybil validation."""

    def test_quality_beats_quantity(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 10)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_10t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T2")
            conf_1t2 = get_conf(run_ep(sdk), a_id)

        assert conf_1t2 > conf_10t4
        assert conf_1t2 - conf_10t4 > 0.10

    def test_100_t4_below_t2(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 100)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_100t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T2")
            conf_1t2 = get_conf(run_ep(sdk), a_id)

        assert conf_100t4 < conf_1t2

    def test_1000_t4_approx_t3(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T4", 1000)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_1000t4 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T3")
            conf_1t3 = get_conf(run_ep(sdk), a_id)

        assert abs(conf_1000t4 - conf_1t3) < 0.05


class TestSituation5_CeilingEffect:
    """Situation 5: 2 Gold + add T4 — tiny increase."""

    def test_ceiling_negligible_increase(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # 2 T0 sources
            alpha, beta = log_aggregate_prior("T0", 2)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_2t0 = get_conf(run_ep(sdk), a_id)

            # Add 1 T4
            alpha2, beta2 = log_aggregate_prior_mixed(
                pos_sources={"T0": 2, "T4": 1}
            )
            sdk.set_point_baseline(a_id, alpha2, beta2)
            conf_with_t4 = get_conf(run_ep(sdk), a_id)

        delta = conf_with_t4 - conf_2t0
        assert delta >= -DELTA  # no regression
        assert delta < 0.005    # negligible


class TestSituation6_NoRegression:
    """Situation 6: 5 Gold + add T4 — must NOT decrease."""

    def test_five_gold_plus_t4_no_regression(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior("T0", 5)
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_5t0 = get_conf(run_ep(sdk), a_id)

            alpha2, beta2 = log_aggregate_prior_mixed(
                pos_sources={"T0": 5, "T4": 1}
            )
            sdk.set_point_baseline(a_id, alpha2, beta2)
            conf_with_t4 = get_conf(run_ep(sdk), a_id)

        assert conf_with_t4 >= conf_5t0 - DELTA
        assert (conf_with_t4 - conf_5t0) < 0.005  # ceiling, negligible


class TestSituation7_Idempotency:
    """Situation 7: Add T4 → remove T4 → returns to baseline."""

    def test_add_remove_returns_to_baseline(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # Baseline: 1 T2
            set_source_evidence(sdk, a_id, "T2")
            conf_baseline = get_conf(run_ep(sdk), a_id)

            # Add T4
            alpha, beta = log_aggregate_prior_mixed(
                pos_sources={"T2": 1, "T4": 1}
            )
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_with_t4 = get_conf(run_ep(sdk), a_id)

            # Remove T4 (back to 1 T2)
            set_source_evidence(sdk, a_id, "T2")
            conf_removed = get_conf(run_ep(sdk), a_id)

        assert conf_with_t4 > conf_baseline      # T4 added something
        assert abs(conf_removed - conf_baseline) < EPSILON  # returns
        rel_err = abs(conf_removed - conf_baseline) / max(conf_baseline, 0.01)
        assert rel_err < 0.01


class TestSituation8_NANDContradiction:
    """Situation 8: 1 Gold + 1 NAND source — contradictory signals."""

    def test_weak_nand_reduces_confidence(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            conf_t0 = get_conf(run_ep(sdk), a_id)

        with fresh_sdk() as sdk:
            # T0 positive + T4 NAND (contributes to beta)
            a_id, _ = build_scenario_a(sdk)
            alpha, beta = log_aggregate_prior_mixed(
                pos_sources={"T0": 1},
                neg_sources={"T4": 1},
            )
            sdk.set_point_baseline(a_id, alpha, beta)
            conf_combined = get_conf(run_ep(sdk), a_id)

        assert conf_t0 > conf_combined         # NAND reduces
        assert conf_combined > 0.85            # Gold still dominates
        drop = conf_t0 - conf_combined
        assert 0.005 < drop < 0.02

    def test_equal_tier_contradiction_returns_to_baseline(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # T0 positive + T0 NAND = Beta(10, 10) → mean 0.5
            alpha, beta = log_aggregate_prior_mixed(
                pos_sources={"T0": 1},
                neg_sources={"T0": 1},
            )
            sdk.set_point_baseline(a_id, alpha, beta)
            conf = get_conf(run_ep(sdk), a_id)

        assert abs(conf - 0.50) < EPSILON


class TestSituation9_Mitigation:
    """Situation 9: T4 source + mitigate edge."""

    def test_mitigation_reduces_but_stays_above_baseline(self):
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            # Baseline
            conf_baseline = get_conf(run_ep(sdk), a_id)

            # Full T4
            set_source_evidence(sdk, a_id, "T4")
            conf_full = get_conf(run_ep(sdk), a_id)

            # Mitigated T4 (50% strength = pc × 0.5)
            mitigated_alpha = 1.0 + TIER_PC["T4"] * 0.5
            sdk.set_point_baseline(a_id, mitigated_alpha, 1.0)
            conf_mitigated = get_conf(run_ep(sdk), a_id)

            # Full neutralization
            sdk.set_point_baseline(a_id, 1.0, 1.0)
            conf_neutral = get_conf(run_ep(sdk), a_id)

        assert conf_full > conf_mitigated           # mitigation reduces
        assert conf_mitigated > conf_baseline - EPSILON  # stays above baseline
        assert abs(conf_neutral - conf_baseline) < EPSILON  # neutral = baseline


class TestSituation10_ChainPropagation:
    """Situation 10: Chain — source on A, check B's response."""

    def test_chain_attenuation(self):
        # Baseline
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            base = run_ep(sdk)
            conf_a_base = get_conf(base, a_id)
            conf_b_base = get_conf(base, b_id)

        # T0 on A
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_a_t0 = get_conf(result, a_id)
            conf_b_t0 = get_conf(result, b_id)

        # T4 on A
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T4")
            result = run_ep(sdk)
            conf_a_t4 = get_conf(result, a_id)
            conf_b_t4 = get_conf(result, b_id)

        # S10.1: Direction preservation (T0)
        assert conf_a_t0 > conf_a_base
        assert conf_b_t0 > conf_b_base

        # S10.2: Attenuation — B's shift < A's shift
        delta_a = conf_a_t0 - conf_a_base
        delta_b = conf_b_t0 - conf_b_base
        assert delta_b < delta_a

        # S10.3: T4 barely propagates
        assert abs(conf_b_t4 - conf_b_base) < 0.05

        # S10.4: T0 propagates significantly
        assert conf_b_t0 - conf_b_base > 0.02

        # S10.5: Baseline near uniform
        assert abs(conf_a_base - 0.50) < EPSILON
        assert abs(conf_b_base - 0.50) < EPSILON


# ═══════════════════════════════════════════════════════════════════
# Scenario B — Loopy Cluster, Single Source
# ═══════════════════════════════════════════════════════════════════

class TestScenarioB_LoopySingleSource:
    """Three-node loopy cluster (A→B→C→A), sources on A only."""

    def test_loop_converges(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            assert result["converged"] == True

    def test_source_node_highest(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_b = get_conf(result, b_id)
            conf_c = get_conf(result, c_id)

        assert conf_a > conf_b
        assert conf_a > conf_c
        assert conf_a > 0.80
        assert conf_b > 0.55
        assert conf_c > 0.52

    def test_loop_no_explosion(self):
        """Feedback loop should NOT cause unbounded confidence inflation."""
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_c = get_conf(result, c_id)

        # C is 2 hops from source — should NOT exceed A
        assert conf_c < conf_a - 0.05


# ═══════════════════════════════════════════════════════════════════
# Scenario C — Loopy Cluster, Dual Source
# ═══════════════════════════════════════════════════════════════════

class TestScenarioC_LoopyDualSource:
    """Three-node loopy cluster, sources on A AND B."""

    def test_dual_source_converges(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            set_source_evidence(sdk, b_id, "T1")
            result = run_ep(sdk)
            assert result["converged"] == True

    def test_both_sources_at_or_above_tier(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            set_source_evidence(sdk, b_id, "T1")
            result = run_ep(sdk)
            conf_a = get_conf(result, a_id)
            conf_b = get_conf(result, b_id)

        assert conf_a >= 0.90  # T0 level
        assert conf_b >= 0.83  # T1 level (possibly boosted by loop)

    def test_c_gets_more_signal_than_single_source(self):
        # Single source (Scenario B)
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            conf_c_single = get_conf(result, c_id)

        # Dual source (Scenario C)
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            set_source_evidence(sdk, a_id, "T0")
            set_source_evidence(sdk, b_id, "T1")
            result = run_ep(sdk)
            conf_c_dual = get_conf(result, c_id)

        assert conf_c_dual > conf_c_single + 0.02


# ═══════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_zero_sources_baseline(self):
        """No sources → uniform prior → confidence ≈ 0.5."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            result = run_ep(sdk)
            conf = get_conf(result, a_id)
            assert abs(conf - 0.50) < EPSILON

    def test_all_tiers_distinct(self):
        """All 5 tiers are measurably distinct."""
        confs = {}
        for tier in ["T4", "T3", "T2", "T1", "T0"]:
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                set_source_evidence(sdk, a_id, tier)
                confs[tier] = get_conf(run_ep(sdk), a_id)

        # Each pair is separated by at least 0.05
        ordered = ["T4", "T3", "T2", "T1", "T0"]
        for i in range(len(ordered) - 1):
            gap = confs[ordered[i+1]] - confs[ordered[i]]
            assert gap > 0.05, \
                f"Gap between {ordered[i]} and {ordered[i+1]}: {gap:.4f}"

    def test_convergence_under_50_iterations(self):
        """EP always converges within max_iter=50."""
        with fresh_sdk() as sdk:
            a_id, _ = build_scenario_a(sdk)
            set_source_evidence(sdk, a_id, "T0")
            result = run_ep(sdk)
            assert result["converged"] == True
            assert result["iterations"] <= 50

    def test_confidence_bounds(self):
        """All confidences remain in [0, 1]."""
        test_cases = [
            {"T4": 1}, {"T0": 1}, {"T4": 100}, {"T0": 10},
            {"T0": 1, "T4": 1},  # mixed positive
        ]
        for pos_sources in test_cases:
            with fresh_sdk() as sdk:
                a_id, _ = build_scenario_a(sdk)
                alpha, beta = log_aggregate_prior_mixed(pos_sources=pos_sources)
                sdk.set_point_baseline(a_id, alpha, beta)
                result = run_ep(sdk)
                conf = get_conf(result, a_id)
                assert 0.0 <= conf <= 1.0, \
                    f"conf={conf:.4f} out of bounds for sources={pos_sources}"
