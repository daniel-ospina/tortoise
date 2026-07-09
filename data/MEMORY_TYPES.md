---
title: "El Dato — Memory Types Taxonomy v2.0"
type: data
domain: data
status: canonical
tags: [memory, taxonomy, canonical]
summary: "Canonical taxonomy of organisational memory types. Implementation-agnostic — for architecture see epistemic-team."
created: 2026-07-01
updated: 2026-07-09
---

# Memory Types Taxonomy v2.0

> **Version:** 2.0.0
> **Status:** 🔒 CANONICAL — ratified 2026-07-01, revised 2026-07-09
> **What this is:** The taxonomy of memory types an organisation needs. Defines WHAT each type stores and answers, not HOW it's implemented.
> **What this is NOT:** An implementation guide. For architecture, see `epistemic-team/`.

---

## The five types

| Memory | Stores | Answers | Example |
|---|---|---|---|
| **Semantic** | Facts, concepts, knowledge independent of time | What do we know? | "Our ICP is restaurant owners." |
| **Episodic** | Events, decisions, experiences with temporal context | What happened? | "On July 1st we decided to pause paid acquisition." |
| **Epistemic** | Claims, evidence, confidence, assumptions, competing hypotheses | Why do we believe this? | "Organic outperforms paid because 4 experiments support, 1 partially contradicts." |
| **Procedural** | Skills, workflows, execution knowledge | How do we do this? | "To launch a campaign: create audience → generate creatives → publish." |
| **Working** | Temporary context, active reasoning | What am I working on right now? | "Comparing CAC across channels for Q3 recommendation." |

---

## 1. Semantic Memory

Everything the organisation knows, independent of when it was learned. The company's encyclopedia.

**Stores:** Documentation, wiki, ontology, architecture, product specs, customer personas, research, strategy, playbooks.

**Answers:** What do we know? What is our product? What is our architecture?

---

## 2. Episodic Memory

Everything the organisation has experienced, tied to a point in time. The company's diary.

**Stores:** Conversations, decisions, meetings, experiments, customer interviews, agent actions, outcomes.

**Answers:** What happened? What have we tried? Why was this decision made?

---

## 3. Epistemic Memory

What the organisation believes and why. Every belief connected to evidence, open to challenge.

**Stores:** Claims, evidence, counter-evidence, assumptions, competing hypotheses, decision rationale.

**Answers:** Why do we believe this? How confident are we? What evidence contradicts it? Should we update?

This is the layer that lets agents reason rather than just retrieve — the difference between "here are relevant documents" and "here is what we should believe, with justification."

---

## 4. Procedural Memory

How work gets done. Execution knowledge, not business knowledge.

**Stores:** SOPs, workflows, prompt templates, coding standards, agent skills, tool usage.

**Answers:** How do we execute this? Which workflow? Which tools?

---

## 5. Working Memory

The agent's current cognitive state. Exists only while solving a task.

**Stores:** Current objective, retrieved documents, intermediate reasoning, tool outputs.

**Answers:** What am I solving? What have I already retrieved? What remains unresolved?

---

## Putting them together

```
Semantic  — "What do we know?"
Episodic  — "What happened?"
Epistemic — "What should we believe?"
Procedural— "How should we act?"
Working   — "What should I do next?"
```

Epistemic sits at the center: semantic provides knowledge, episodic provides experience, epistemic integrates both into justified beliefs. Procedural executes. Working coordinates.

---

## Implementation

For current architecture, see `docs/teams/epistemic-team/`. The v1 implementation (LightRAG + MemPalace KG) is historical at `docs/teams/organisation-design-team/operations/2026-07-01-memory-types-implementation-v1.md`.
