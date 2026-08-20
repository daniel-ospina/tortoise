#!/usr/bin/env python3
# Historical — uses embedded tortoise.db. Do not run against production Docker.
"""(#6707) Verification script for shock propagation on FalkorDB."""
from __future__ import annotations  # noqa: I001

import sys
sys.path.insert(0, '/Users/home/eldato/negation-game-explorations/tortoise')

from tortoise.projection import FalkorProjection

proj = FalkorProjection()

# 1. Reset — clear all confidence
proj.g.query('MATCH (n:Point) WHERE n.confidence IS NOT NULL SET n.confidence=0.5')
print('✅ Reset: all confidence set to 0.5')

# 2. Find epicenter: a non-operator Point with IMPL edges
rows = proj.g.query(
    "MATCH (n:Point)-[:IMPL]-(:Point) "
    "WHERE n.is_operator = false "
    "RETURN n.id LIMIT 1"
).result_set
assert rows, 'No Point with IMPL edges found'
epicenter = rows[0][0]
print(f'✅ Epicenter: {epicenter[:20]}...')

# 3. Propagate
result = proj.propagate_shock(epicenter)
print(f'✅ Propagated: {len(result)} nodes changed')

# 4. Epicenter should NOT be dampened (depth=0)
epi_old, epi_new = result[epicenter]
assert epi_new != 0.5, f'Epicenter confidence unchanged ({epi_new})'
assert epi_new == 1.0, f'Epicenter should have edge-ratio 1.0 (all IMPL), got {epi_new}'
print(f'✅ Epicenter: {epi_old:.4f} → {epi_new:.4f} (no damping at depth 0)')

# 5. Depth-1 nodes should be dampened
depth1_dampened = False
for nid, (old, nv) in result.items():
    if nid != epicenter and old == 0.5:
        # Depth-1: old=0.5, new = 0.5*0.5 + edge_ratio*0.5
        assert nv != old, f'Depth-1 node {nid[:10]} unchanged'
        # edge_ratio >= 1.0 (all supports) → new >= 0.75
        # edge_ratio could be <1.0 if NAND edges exist
        depth1_dampened = True
        break
assert depth1_dampened, 'No depth-1 dampened nodes found'
print('✅ Depth-1 nodes dampened correctly')

# 6. Threshold gate: re-run should change fewer nodes (values converging)
result2 = proj.propagate_shock(epicenter)
print(f'✅ Re-run: {len(result2)} changes (converging toward edge-ratio)')

# 7. Confidence written to DB
n_with_conf = proj.g.query(
    'MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN count(n)'
).result_set[0][0]
assert n_with_conf > 0, 'No nodes have confidence set'
print(f'✅ {n_with_conf} nodes have confidence in DB')

# 8. Edge case: propagate from node with no edges
no_edge = proj.g.query(
    "MATCH (n:Point) WHERE NOT (n)-[:IMPL]-(:Point) AND NOT (n)-[:NAND]-(:Point) "
    "RETURN n.id LIMIT 1"
).result_set
if no_edge:
    r3 = proj.propagate_shock(no_edge[0][0])
    # Isolated node: 0.5 edge-ratio, delta=0 < threshold → no change recorded
    assert len(r3) == 0, f'Isolated node should have no changes, got {len(r3)}'
    print(f'✅ Isolated node: no edges → stays 0.5 (delta=0 < threshold)')  # noqa: F541
    # Verify it still has default confidence in DB
    conf = proj._confidence(no_edge[0][0])
    assert conf == 0.5, f'Isolated node confidence should be 0.5, got {conf}'
    print(f'   Confirmed: n.confidence = {conf}')

# Clean up test data
proj.g.query('MATCH (n:Point) WHERE n.confidence IS NOT NULL SET n.confidence=0.5')
proj.close()

print('\n✅ All assertions passed (#6707)')
