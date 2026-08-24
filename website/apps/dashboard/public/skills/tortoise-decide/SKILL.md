---
name: tortoise-decide
description: Make a decision with the Tortoise graph — refine the decision, research options/criteria/findings, wire IMPL/NAND/mitigation edges, and rank options by EP confidence. Invoke this skill when the user needs to decide between options and the graph should hold the reasoning.
domain: capability
type: Workflow
status: live
tags: [tortoise, graph, decision, epistemology, ep-confidence]
summary: "The core decision workflow on the Tortoise graph — options/criteria/findings → IMPL/NAND/mitigation edges → EP confidence ranking. Tool-based (MCP) so it runs against hosted tenants; graph-scripts/decide.py is the self-host variant."
created: 2026-08-23
updated: 2026-08-23
allowed-tools: read write bash grep
---

> ⛔ **Read this skill in full before running a decision comparison.** The graph's reasoning (the IMPL/NAND/mitigation structure) is the deliverable — the ranking is the read-out.

# tortoise-decide

Make a decision **on the graph**: the options, the criteria, the findings, the
reasons (edges), and the resulting confidence ranking all live in Tortoise —
so the decision is auditable, comparable across time, and re-runnable as new
evidence lands.

## The 7-step workflow (authoritative)

1. **Refine the decision with the user.** What exactly is being decided? Write
   it as a short domain label (e.g. `2026-Q3-db-migration`). Get the options
   right before anything else — the user owns the option set.
2. **Research options and criteria.** What are the candidate options? What
   criteria will they be judged on? What findings (evidence) are relevant?
   (For deep research, pair with the `research` skill; new findings land on
   the graph and connect to the decision.)
3. **Check with the user:** list the criteria (for value) + the options (for
   completeness) — confirm before wiring edges.
4. **Connect criteria → options.** For each criterion that supports or
   undermines an option, create an operator edge:
   - `crit:1 -[IMPL]-> opt:a` — the criterion argues FOR the option
   - `crit:1 -[NAND]-> opt:b` — the criterion argues AGAINST the option
5. **Research IMPL/NAND MITIGATIONS to the option–criterion connections.**
   Sometimes a finding NANDs an option itself ("out of stock") — but mostly
   findings **mitigate the operator** (the criterion is TRUE but matters
   LESS). Express fit on the operator, never NAND the option for a bad fit.
6. **Optional: sub-mitigations.** A mitigation can itself carry an operator
   on the edge → mitigations of mitigations (nested relevance).
7. **Options can also IMPL/NAND among themselves** — two go well together
   (IMPL), three are mutually exclusive (NAND) with those two.

Then run the EP computation and present the **ranked options with their
confidence** — plus the *why*: the top reasons (edges) that moved each option.

## Execution (tool-based — hosted tenants)

Use the Tortoise MCP tools (never a local FalkorDB):

1. **Create the nodes** — `tortoise_create_point` per point (dedup by
   content): options → `pointKind: option`, criteria → `criterion`,
   findings → `evidence`; keep ids stable (`opt:a`, `crit:1`, `finding:1`).
2. **Wire the edges** — `tortoise_create_operator` for each IMPL/NAND;
   mitigation strength in `[0.10, 0.50]` for relevance edges.
3. **Compute** — `tortoise_compute_confidence` (EP belief propagation on the
   decision subgraph) → the per-option confidence.
4. **Sanity** — `tortoise_check_structure` before presenting (the graph must
   be structurally sound: no orphan operators, edges well-formed).

### Output contract

```
Decision: <label>
Options ranked by EP confidence:
  1. opt:a  — <confidence> — top reasons: <edges>
  2. opt:b  — <confidence> — top reasons: <edges>
Mitigations applied: <n> (relevance-adjusted operators)
```

## Self-host variant

`graph-scripts/decide.py` runs the same model locally against a FalkorDB
(`--db` URI / `TORTOISE_DB_URI`): `--options/--criteria/--findings/--edges/
--truth-edges/--relevance-edges` (JSON) or `--input file.json|yaml` with the
full decision definition. Same input shape as the tool-based flow. Hosted
tenants use the MCP tools above.

## Anti-patterns (the corruption classes)

- ⛔ **Never pass a bare `aboutObject`/`context` as a point prop** — it's a
  node property, non-canonical + invisible to traversal. Wire edges
  explicitly (ID-based).
- ⛔ **Never NAND an option/criterion for bad fit** — express fit on the
  operator (truth vs relevance).
- ⛔ **Never let operator resolution fall back to stub nodes** — every edge
  source/target must be a real created point id.
- ⛔ **Don't store decisions as first-class Points** — the graph says "this
  state is based on these reasons"; the decision dimension is the
  decision-as-Event timeline.
