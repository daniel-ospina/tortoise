---
name: tortoise-audit
description: Audit Tortoise graph wiring quality — structural checks via CLI, semantic checks via agent review. Use before high-stakes decisions or when EP confidence seems off.
type: capability
domain: capability
status: live
doc_status: live
subjects.team: epistemic-team
created: 2026-07-22
updated: 2026-07-22
allowed-tools: read write edit bash grep find web_search web_fetch todo_write task, mcp__tortoise__tortoise_query, mcp__tortoise__tortoise_get_point, mcp__tortoise__tortoise_get_operator, mcp__tortoise__tortoise_annotate_operator, mcp__tortoise__tortoise_mitigate_operator, mcp__tortoise__tortoise_create_point
---

> This skill MUST be read in full — not skimmed.

# tortoise:audit

Audit the Tortoise epistemic graph for wiring quality. Two passes: structural (fast, automated) and semantic (agent-driven, reads content).

## Pre-Flight Checklist (Before Any Graph Write)

Complete ALL items before creating or modifying any graph entity:

- [ ] Read target operator/claim content via tortoise_get_point BEFORE deciding what to create
- [ ] NAND vs Mitigation: does this contradict the claim (→ NAND operator), or weaken the connection (→ mitigation point)?
- [ ] Mitigation confidence: use 0.10-0.35 for edge weakness, 0.0 for diagnostic observations
- [ ] Mitigation scope: connect to specific affected operators, never batch-connect entire contexts
- [ ] After creating: verify entity exists via read-back query (tortoise_get_point)
- [ ] After superseding operators: delete IMPL/NAND/INPUT edges from superseded operators
- [ ] After any batch operation: run docker exec falkordb redis-cli BGSAVE

## When to Run

- Before making a high-stakes decision using EP confidence scores
- After filing many new points (every ~50 points)
- When EP confidence seems off or contradictory

---

## Pass 1: Structural Audit (CLI)

Run `tortoise audit` or `tortoise audit --context <pattern>`. This checks mechanical issues:

| Check | What it finds | Fix |
|-------|---------------|-----|
| `missing_sourceKind` | Operator without source credibility tier | Set sourceKind (T0-T4). Default T4. |
| `missing_sourceDate` | Graded evidence without date | Set sourceDate (ISO format) |
| `superseded_no_edge` | Superseded point without :SUPERSEDES | Create edge to replacement |
| `superseded_active_edges` | Superseded point still connected | Clean up edges |

Fix all structural issues before Pass 2.

---

## Pass 2: Semantic Audit (Agent-Driven)

The CLI's semantic checks are keyword heuristics — they can produce false positives. **The real audit happens here.** Dispatch agent sub-tasks to read the actual content of IMPL edges and decide.

### Step 1: Mitigation Gaps

For each context being audited, dispatch an agent to review IMPL edges:

```
Review ALL IMPL edges in <context>. For each edge:
1. Read the SOURCE content and the TARGET content
2. Decide: should a mitigation point exist between them?

A mitigation is needed when:
- The source is uncertain/risky but the target is a strong claim
- The connection is weaker than IMPL implies
- The source is T3/T4 evidence supporting a decision-level claim
- Two claims seem to support each other but there's a nuance

For each edge needing mitigation, CREATE the mitigation point:
- Write the mitigation content explaining what makes this specific connection weaker
- Set confidence to the degree of edge weakness (see Mitigation Mechanics above for ranges)
- Connect via :mitigates edge to the operator ONLY (not to all operators in the context)
- If the gap is systemic (applies to many operators identically), create ONE mitigation at low confidence (0.10-0.20) and connect to all affected operators
- For diagnostic observations about patterns (not edge-specific), set confidence: 0.0 for documentation-only
```

### Step 2: Under-Researched Decisions

For each decision point, check if it has counter-arguments:

```
For each decision/claim/hypothesis point in <context>:
- Does it have at least one NAND edge (counter-argument)?
- Does it have at least one mitigation point?
- If neither: flag as under-researched. Add a NAND edge with a counter-argument, or add a mitigation point explaining what's uncertain.
```

### Step 3: Isolated Points

For each point with no edges, connect it or flag it:

```
For each isolated point (no IMPL/NAND edges):
- Is this point meant to be connected? Connect it.
- Is this point reference-only? Mark it as such.
- Is this point obsolete? Mark it superseded.
```

---

## Mitigation Mechanics (Critical)

Mitigations are **not just documentation** — they actively reduce EP operator weight.

From `tortoise/weights.py`:
```python
# Mitigation: if a mitigation point targets this operator, reduce weight
mit_rows = g.query(
    "MATCH (m:Point {op_type: 'mitigation'})-[:mitigates]->(o:Point {id: $id}) "
    "RETURN coalesce(m.confidence, 0.5)", ...
).result_set
if mit_rows:
    max_mit = min(max(r[0] for r in mit_rows), 1.0)
    w *= max(0.0, 1.0 - max_mit)
```

**An operator's EP weight is multiplied by `(1 - mitigation.confidence)`.**
A mitigation at 0.30 → operator runs at 70% weight. A mitigation at 0.95 → operator runs at 5% weight.

### Rules for Creating Mitigations

1. **Connect sparingly.** A mitigation only goes on operators where the IMPL edge is **genuinely weaker than standard IMPL weight**. Not on every operator in a context.

2. **Confidence = edge weakness.** 0.20 means "this edge is 20% weaker than it claims." 0.50 means "this edge is half as strong as it claims." NOT "I am 50% confident in this diagnosis."

3. **Diagnostic observations are documentation-only.** If a mitigation describes a systemic pattern (e.g., "all operators lack sourceKind") rather than operator-specific weakness, set `confidence: 0.0`. This documents the issue without reducing EP weight.

4. **Never batch-connect at high confidence.** Connecting a 0.95 mitigation to hundreds of operators nukes EP propagation across the graph.

5. **Appropriate confidence ranges:**
   - 0.10–0.20: Minor weakness (unmeasured but well-reasoned, plausible assumption)
   - 0.20–0.35: Clear gap (paper calculation vs benchmark, self-assessed vs independent)
   - 0.35–0.50: Significant gap (contradictory assumptions, known missing factors)
   - 0.50+: Major gap (the connection is more wrong than right) — rare, prefer NAND

### Mitigation vs NAND

| | NAND | Mitigation |
|---|------|------------|
| Mechanic | Operator with `:NAND` edges | Point with `:mitigates` edges |
| EP effect | Reduces target claim confidence | Reduces operator edge weight |
| When to use | "A contradicts B" — the claim is wrong | "A supports B less than IMPL implies" — the connection is weak |
| Example | "GPU availability contradicts CPU-only preference" | "Memory calculations are paper estimates, not benchmarks" |

---

## Process (Not Heuristics)

Do NOT build keyword heuristics upfront. The process is:
1. Give IMPL edges to an agent
2. Agent reads content
3. Agent decides if mitigation is needed
4. Agent creates it

Over many iterations, patterns will emerge. THEN add heuristics to the CLI. But start with the process.


## Quality Gate

After Pass 2, re-run `tortoise_compute_confidence` on audited contexts. EP scores should reflect the improved wiring.

## Post-Audit Verification

After completing Pass 1 and Pass 2, run these checks to confirm fixes:

```bash
# 1. Structural health
tortoise audit --context <ctx>

# 2. Remaining weaknesses
tortoise weaknesses --context <ctx>

# 3. Verify no superseded operators have active edges
docker exec falkordb redis-cli -a '<password>' --no-auth-warning GRAPH.QUERY tortoise "MATCH (op:Point {status:'superseded'})-[r:IMPL|NAND|INPUT]->() RETURN count(r)"

# 4. Verify all operator content uses standard format
docker exec falkordb redis-cli -a '<password>' --no-auth-warning GRAPH.QUERY tortoise "MATCH (op:Point {is_operator:true, op_type:'IMPL'}) WHERE (op.status='live' OR op.status IS NULL) AND NOT op.content CONTAINS 'IMPL(' RETURN count(op)"
# Expected: 0
```
