---
name: tortoise-audit
description: Audit Tortoise graph wiring quality — find missing sourceKind, supersession gaps, mitigation opportunities, and edge type errors. Use before making high-stakes decisions or when EP confidence seems off.
type: capability
domain: capability
status: live
doc_status: live
subjects.team: epistemic-team
created: 2026-07-22
allowed-tools: read write edit bash grep find web_search web_fetch todo_write task, mcp__tortoise__tortoise_query, mcp__tortoise__tortoise_get_point, mcp__tortoise__tortoise_get_operator, mcp__tortoise__tortoise_annotate_operator, mcp__tortoise__tortoise_mitigate_operator, mcp__tortoise__tortoise_create_point
---

> This skill MUST be read in full — not skimmed.

# tortoise:audit

Audit the Tortoise epistemic graph for wiring quality. Identifies poorly-annotated operators, missing source tiers, supersession gaps, and mitigation opportunities.

## When to Run

- Before making a high-stakes decision using EP confidence scores
- After filing many new points (every ~50 points)
- When EP confidence seems off or contradictory
- Before sharing the graph with others

## The Audit Checklist

Run `tortoise audit` or `tortoise audit --context <pattern>`. For each issue found:

### missing_sourceKind (medium)
The operator connects to evidence without a source credibility tier.

**Fix:** Set sourceKind on the evidence point. Default is T4 (unverified).
```
tortoise_annotate_operator(id, sourceKind="T1")
```
Tiers: T0 (meta-analysis, 1.0) → T1 (peer-reviewed, 0.8) → T2 (case study, 0.6) → T3 (anecdotal, 0.4) → T4 (unverified, 0.2, default).

### missing_sourceDate (low)
Graded evidence has no date. Time decay cannot be computed.

**Fix:** Set sourceDate on the evidence point (ISO format).
```
MATCH (n:Point {id: $id}) SET n.sourceDate = '2024-06-15'
```

### superseded_no_edge (high)
A point marked superseded has no :SUPERSEDES edge to its replacement.

**Fix:** Create the edge to the replacement source.
```
MATCH (old:Point {id: $old_id}), (new:Point {id: $new_id})
CREATE (old)-[:SUPERSEDES]->(new)
```

### superseded_active_edges (medium)
A superseded point still has active operators connected. EP skips these but they clutter the graph.

**Fix:** Remove edges from superseded points, or mark operators as superseded too.

### impl_instead_of_nand (high)
An IMPL edge connects to a point that semantically contradicts. Should be NAND.

**Fix:** If the points genuinely contradict, use NAND. If the connection is just weaker than it seems, use mitigation instead.
```
tortoise_create_operator('NAND', source_id, [target_id])
```

### mitigation_recommended (medium)
A low-relevance operator could benefit from a mitigation point to strengthen the connection.

**Fix:** Create a mitigation point explaining why this connection matters despite being weak.
```
tortoise_mitigate_operator(operator_id, "Despite weak surface connection, this evidence is relevant because...", confidence=0.7)
```

## Priority

Fix high-severity issues first (superseded_no_edge, impl_instead_of_nand), then medium (missing_sourceKind, mitigation_recommended), then low (missing_sourceDate). Run audit again after fixes to confirm issues resolved.

## Quality Gate

After fixing issues, EP confidence scores should be re-evaluated. Run `tortoise_compute_confidence` on the affected context if confidence scores haven't updated.
