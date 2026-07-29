---
title: "how-to-use-tortoise"
type: skill
domain: capability
status: seedling
tags: []
summary: ""
created: 2026-07-24
updated: 2026-07-24
---


> This skill MUST be read in full — not skimmed.

# how-to-use-tortoise

Teaches agents how to use the Tortoise epistemic graph properly — veracity vs implication, mitigation, NAND constraints, source credibility.

## Progressive Disclosure

| Tier | Graph Size | Content |
|------|-----------|---------|
| Tier 1 | <100 Points | Veracity vs implication, proper kind tags, IMPL basics |
| Tier 2 | 100-1000 Points | NAND constraints, mitigation operators, operators-as-points |
| Tier 3 | >1000 Points | Liveness, grounding, signal vs price, cascading invalidation |

## Tier 1 — Basic Graph Usage

### Kinds Are Roles, Not Tags
Use existing pointKind: statement, observation, hypothesis, decision. Evidence is a role — a statement becomes evidence via IMPL edge.

### Veracity vs Implication
Independent dimensions. Point confidence = truth. Operator weight = implication strength. Get both: tortoise_get_confidence(id) + tortoise_get_operator(id).

## Tier 2 — Operators, NAND, Mitigation

### Operators Are Points
Every IMPL/NAND edge is a Point with its own confidence. Can be attacked (NAND) or mitigated.

### Annotation: Source->Point only
Annotate operators where external evidence supports a claim. Use sourceKind (T0-T4) and sourceDate.
Skip annotation for Point->Point logical connections.

### Source Credibility Tiers
Assign sourceKind to every evidence point. Default T4 if ungraded.

| Tier | Weight | Examples |
|------|--------|----------|
| T0 Gold | 1.0 | Meta-analyses, systematic reviews |
| T1 High | 0.8 | Peer-reviewed, multiple independent sources |
| T2 Medium | 0.6 | Case studies, expert consensus |
| T3 Low | 0.4 | Anecdotal, single observations |
| T4 Unverified | 0.2 | Blog, social media, **ungraded default** |

Time decay is computed automatically by the EP engine from sourceDate. Set sourceDate on evidence points.
EP weights sources by: tier_weight × time_decay (logarithmic, 0.5 at 15 years).

### Mitigation — Weaken Without Denying (MANDATORY first pass)

tortoise_mitigate_operator(id, reason, confidence). weight = base × (1 - mitigation_confidence).

**Mitigation is the default. Most real-world connections aren't full-strength.** A source
might be biased, a study might have limited sample size, a claim might apply in some
contexts but not others. Mitigation encodes this nuance — it says "this connection
exists but it's weaker than it appears."

**Process — mitigation before NAND, always:**

1. **For every IMPL edge created:** ask "what weakens this?" File at least one mitigation.
   Common mitigations: source bias (low tier), small sample, outdated data, conflicting
   context, indirect relevance, single-source risk.

2. **After mitigations are filed:** check if any claim is logically contradicted (not
   just weakened). Only then use NAND.

3. **Why this order matters:** a mitigated edge still contributes to the graph — it's
   a signal, just a weaker one. A NAND edge removes the signal entirely. Mitigation
   preserves nuance; NAND erases it. Default to preserve.

### Decision Table: Mitigate vs NAND vs Annotate

| Situation | Use | Why |
|-----------|-----|-----|
| Connection is weaker than it seems but still valid | Mitigate | Preserves signal, encodes uncertainty |
| Both endpoints are valid but the link is overstated | Mitigate | Weakens without denying |
| Source is credible but old | Mitigate | Time decay is a form of mitigation |
| Claim is logically false (contradiction, not just weak) | NAND | Only when mitigation can't capture the issue |
| Source is credible but has known bias | Annotate + Mitigate | Bias is annotation; strength reduction is mitigation |

### Examples

```
// Mitigation: study has small sample size (n=50)
tortoise_mitigate_operator(
  id="edge-123",
  reason="Study has small sample size (n=50) — findings may not generalize",
  confidence=0.3  // 30% strength reduction
)

// Mitigation: source is credible but 5 years old
tortoise_mitigate_operator(
  id="edge-456",
  reason="Published 2021 — technology landscape has changed significantly",
  confidence=0.4  // 40% strength reduction
)

// Mitigation: single-source risk
tortoise_mitigate_operator(
  id="edge-789",
  reason="Only one source supports this — needs corroboration",
  confidence=0.5  // 50% strength reduction
)
```

### Supersession — Replace, Don't Decay
When a new source completely replaces an old one:
- Create :SUPERSEDES edge from old source to new source
- Mark old source status: superseded
- EP automatically skips superseded points (non-live)
- Partial replacement: supersede only the specific contradicted claims

Supersession removes. Mitigation weakens. NAND contradicts. Different mechanisms.

### NAND Constraint — Step AFTER mitigation
Logical tension, not a weighted vote. Only use NAND after mitigations are filed and
the claim is still logically contradicted. If mitigation could capture the issue,
use mitigation. NAND is for when the connection is fundamentally wrong, not just weak.

**Process:** Mitigations → then NAND if still needed. Never NAND-first.

## Tier 3 — Advanced
Liveness, grounding, signal vs price. Source credibility as foundation.

## Quality Gates
- G1: Source->Point operators must have sourceKind set (default T4). Point->Point skip.
- G2: At least one adversarial query before concluding.
- G3: Superseded sources must have :SUPERSEDES edge to replacement. Lifecycle status must be superseded.
