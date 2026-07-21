---
name: tortoise-graph-reasoning
description: Teaches proper Tortoise graph usage — creating operators (IMPL/NAND), annotating edges, evaluating veracity vs implication, mitigating edge strength, and using NAND constraints. Use when creating operators, evaluating claims, or reasoning about evidence.
type: capability
domain: capability
status: live
doc_status: live
subjects.team: epistemic-team
created: 2026-07-21
allowed-tools: read write edit bash grep find web_search web_fetch todo_write task, mcp__tortoise__tortoise_create_point, mcp__tortoise__tortoise_create_operator, mcp__tortoise__tortoise_query, mcp__tortoise__tortoise_get_point, mcp__tortoise__tortoise_check_structure, mcp__tortoise__tortoise_compute_confidence, mcp__tortoise__tortoise_get_confidence, mcp__tortoise__tortoise_annotate_operator, mcp__tortoise__tortoise_get_operator, mcp__tortoise__tortoise_mitigate_operator, mcp__tortoise__tortoise_analyze
---

> ⛔ **This skill MUST be read in full — not skimmed.** Formal quality gates depend on its workflow.
> Skipping steps silently bypasses epistemic quality checks. Missing gates = unreliable belief graph.

# tortoise:graph-reasoning

Teaches agents how to use the Tortoise epistemic graph properly — not just creating Points and edges, but reasoning about what they mean. The graph has formal power (operators-as-points, NAND constraints, structured annotation) that naive usage leaves on the table.

## When to Use

- Any time you create an IMPL or NAND operator between Points.
- When evaluating whether a claim is true vs whether it strongly implies another.
- When you need to modulate edge strength (this evidence supports the claim, but weakly).
- When reasoning about contradictions (NAND = logical tension, not a weighted vote).

## Steps

### 1. Kinds Are Roles, Not Tags

The existing `pointKind` vocabulary covers everything. Don't invent new kinds.

| When you want to say... | Use `pointKind` | Provenance |
|--------------------------|-----------------|------------|
| "This is a factual claim" | `statement` | Add `extractedFrom` for source |
| "This is an observation" | `observation` | Source via `tortoise_create_point(props={extractedFrom: "..."})` |
| "This is a theory" | `hypothesis` | Same — provenance chain anchors it |
| "This is a decision" | `decision` | Document the rationale in content |

**Evidence is a role, not a kind.** A `statement` becomes evidence when it has an IMPL edge to another claim. The relationship defines it. Use `tortoise_create_operator("IMPL", evidence_id, [claim_id])` to encode the evidential relationship.

### 2. Operators Are Points

Every IMPL/NAND edge you create with `tortoise_create_operator` is itself a Point. It has its own ID, its own confidence, and can be:

- **Annotated** via `tortoise_annotate_operator` — add structured dimensions (bias, precision, consistency, directness)
- **Attacked** — create a NAND edge pointing at the operator: "This relevance claim is wrong"
- **Mitigated** — use `tortoise_mitigate_operator` to weaken the edge without denying source or target

After creating an operator, always annotate it:

```
tortoise_annotate_operator(id=<operator_id>, bias=0.1, precision=0.8, consistency=0.7, directness=0.9)
```

**Annotation dimensions:**
- **bias** (0-1): How much hidden stake beyond stated position? 0 = purely epistemic, 1 = highly motivated.
- **precision** (0-1): How narrow/well-defined is the relevance claim? 0 = vague hand-wave, 1 = precise logical link.
- **consistency** (0-1): How stable across contexts? 0 = only holds in narrow conditions, 1 = holds universally.
- **directness** (0-1): How directly does source bear on target? 0 = requires many inferential steps, 1 = immediate entailment.

### 3. Veracity vs Implication — Evaluate Both

These are independent dimensions. Don't conflate them.

| Question | Where to look | Tool |
|----------|--------------|------|
| Is this claim TRUE? | Point confidence | `tortoise_get_confidence(claim_id)` |
| How strongly does A IMPLY B? | Operator confidence + annotations | `tortoise_get_operator(operator_id)` |

A point can be FALSE but the IMPL edge is still logically valid. "If A were true, it would strongly support B" is a true statement about implication, even if A is false. The operator's job is to encode implication strength; the point's job is to encode truth.

**Before reasoning about any claim:**
1. Get the claim's confidence: `tortoise_get_confidence(claim_id)`
2. Get each operator connecting it: `tortoise_get_operator(id)` (returns dimensions + confidence)
3. Evaluate the claim's veracity AND each operator's implication strength independently

### 4. Mitigation — Weaken Without Denying

Mitigation says: "Yes, A is true. Yes, A implies B. But here's why the connection is weaker than it looks."

Use `tortoise_mitigate_operator` when:
- Evidence is low-quality (small sample, confounded, noisy)
- Relevance holds only in specific conditions not met here
- The implication chain has a known gap
- The operator's precision or directness is low (annotate first, mitigate if still too strong)

```
tortoise_mitigate_operator(id=<operator_id>, reason="Sample size n=12, underpowered", strength=0.3)
```

**strength semantics:** 0 = fully neutralized (edge contributes nothing), 1 = fully intact (edge at full weight). Default 0.5.

Mitigation is **idempotent** — calling it twice on the same operator updates the existing mitigation, doesn't create a duplicate.

### 5. NAND Constraint — Logical Tension

`¬(A ∧ B ∧ R)` — you cannot have A true, B true, AND the relevance operator R true simultaneously.

| If... | Then... |
|-------|---------|
| B is true AND R is true | A must be false (B refutes A) |
| A is true AND R is true | B must be false (A survives B's challenge) |
| R is false | A and B can both be true (no relevance = no tension) |

NAND is **not** a weighted vote against. It's logical coupling. When you create a NAND operator, you're asserting that A and B are structurally incompatible through R.

**Before creating a NAND:** check if a mitigation operator would be more appropriate. NAND = they CANNOT both be true. Mitigation = the connection is WEAKER than it appears. These are different.

### 6. Verify — Quality Gate

After filing points and operators, run verification:

```
tortoise_check_structure()       # Gate 0→4 chain integrity
tortoise_analyze("are there unannotated IMPL edges?")  # Annotation coverage
tortoise_compute_confidence()    # Propagate belief scores
```

If `tortoise_check_structure` returns violations, fix them before filing more points. If `tortoise_analyze` finds unannotated operators, annotate them.

## Quality Gates

- **G1 (Static):** Every IMPL/NAND operator created during this session must be annotated via `tortoise_annotate_operator`. Unannotated operators carry invisible weights.
- **G2 (Semantic):** At least one adversarial query ran — use `tortoise_analyze` with disconfirming framing ("what contradicts this claim?") before concluding.

## Error Handling

- If `tortoise_annotate_operator` returns `{"error": "..."}`, check: (a) the ID is an operator, (b) all dimensions are 0-1, (c) the operator exists.
- If `tortoise_mitigate_operator` returns an error, verify the target ID is an operator Point. Non-operators cannot be mitigated.
- If `tortoise_check_structure` returns violations, fix before filing more. Chain integrity errors compound.

> Continue following the workflow as mandated by this skill. Do not skip steps.
