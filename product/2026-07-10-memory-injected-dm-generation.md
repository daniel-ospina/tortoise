---
team: epistemic-team
domain: engineering
entity_type: ResearchBrief
doc_status: live
epic: eldato#5201
created: 2026-07-10
updated: 2026-07-10
product: cross-team
---

# Memory-Injected DM Generation — Research Synthesis

**Date:** 2026-07-10
**Domain:** engineering
**Confidence:** High

epic: eldato#5201
created: 2026-07-10
updated: 2026-07-10
product: cross-team
---

| Claim | Sources | Confidence |
|-------|---------|------------|
| Argument trees as decision trees for persuasion optimization | 2 academic + internal | High |
| GraphRAG dual-channel for evidence retrieval | Microsoft + industry + internal | High |
| Prompt-based memory triples for personalization | 3 practitioner + internal | High |
| AI feedback loop with DPO for self-improvement | 2 practitioner + academic | High |
| Epistemic graph as evidence engine for DM generation | Internal Phase 1 | ⚠️ hypothesis |

## What We Have Internally

**Epistemic Graph — Phase 1 (shipped):**
- Claim model: kind (policy/evidence/value), confidence (-1 to 1), status (draft/live/superseded)
- Operator model: supports, contradicts, derivesFrom, supersedes, assumes
- Taxonomy: liveness, grounding, track record
- Belief propagation + strawman detection + DebateCV
- Built on Graphiti + FalkorDB

**Phase 2 design (pending):** boundary conditions, conflict-driven deep search, 5-state lifecycle

## External Findings

### 1. Argument Tree Persuasion

Core insight: persuasion is about *sequencing* claims optimally, not listing them.

An epistemic graph models the recipient's beliefs. Argument trees are decision trees — each node is "which claim maximizes my chance of persuasion?" SAT solvers scale to hundreds of arguments.

For DMs: hook with most specific claim first, back with evidence, close with social proof. The graph's confidence scores + boundary conditions naturally produce this ordering.

### 2. GraphRAG for Evidence Retrieval

Dual-channel: vector + graph traversal. Multi-hop reasoning across entities. Provenance links every claim to source.

The epistemic graph IS a GraphRAG backend. Query: "highest-confidence claims about [category] in [city] with evidence from [N] profiles."

### 3. Prompt-Based Memory Injection (No Fine-Tuning)

Store outcomes as `<prompt, outcome, context>` triplets. At generation time, inject best/worst performers as few-shot examples:

```
✅ "Hola [name], vi que [business] está en [area]. Trabajamos con [similar] — 
   les ayudamos a conseguir 30+ clientes nuevos/mes. ¿Te interesa saber cómo?"
❌ "Hey there! We noticed your amazing business and wanted to reach out about our incredible platform..."
```

### 4. AI Feedback Loop

Two maturity levels:
- **Prompt injection** (now): zero cost, corpus grows with use
- **DPO fine-tuning** (later): needs 500+ pairs, contextualizes when prompt windows overflow

### 5. Short-Form Persuasion Structure

| Step | DM equivalent |
|------|---------------|
| Hook | "Vi que [Business] está en [Area]" |
| Evidence | "Trabajamos con [Similar] — consiguieron [Result]" |
| CTA | "¿Te interesa? Responde 'sí'" |

## Architecture

```
1. PROFILE RESOLVE → category, city, language
2. EVIDENCE RETRIEVE → epistemic graph: claims + confidence + sources
3. FEEDBACK RETRIEVE → best 3 messages that got follows, worst 2 that got called slop
4. PROMPT ASSEMBLE → claims + examples + anti-examples + profile
5. GENERATE → SEND
6. LOG OUTCOME → feeds back into step 3
```

## What to Build

| Phase | What | Lines | Depends on |
|-------|------|-------|------------|
| 1 (now) | `feedback.ts` — outcome logging + retrieval | ~80 | Supabase |
| 2 (later) | `evidence.ts` — epistemic graph client | ~60 | Stream D API |
| 3 (future) | DPO fine-tuning | — | 500+ pairs |

**Key insight:** The epistemic graph answers "what should I SAY?" (evidence). The feedback loop answers "how should I say it?" (tone). Both are needed; together they solve "AI slop."
