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
- Write the mitigation content in your own words based on what you read
- Set confidence based on how clear the gap is
- Connect via :mitigates edge to the operator
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

## Process (Not Heuristics)

Do NOT build keyword heuristics upfront. The process is:
1. Give IMPL edges to an agent
2. Agent reads content
3. Agent decides if mitigation is needed
4. Agent creates it

Over many iterations, patterns will emerge. THEN add heuristics to the CLI. But start with the process.

---

## Quality Gate

After Pass 2, re-run `tortoise_compute_confidence` on audited contexts. EP scores should reflect the improved wiring.
