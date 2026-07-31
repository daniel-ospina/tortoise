# Tortoise Architecture: Three-Layer Memory Model

**Date:** 2026-07-31
**Status:** Vision
**Team:** epistemic-team

## Overview

Tortoise is not just an argumentation engine. It is an organization's **memory system** with three distinct layers that work together. Expansion packs are not "optional schemas" — they are the **primary interface** for customers. They define what state the customer tracks, and everything else connects to that state.

## The Three Layers

### 1. State Layer (Deterministic — Facts)

This is the customer's organizational reality. Defined by the core ontology + expansion packs. It answers: "What is the current state of the organization?"

**Examples:**
- What teams exist? What roles do they have?
- What products do we offer? What features do they have?
- What campaigns are running? On what channels?
- What customer segments exist? What are their attributes?

The state layer is **deterministic** — these are facts, not beliefs. A team either exists or it doesn't. A campaign is either active or finished. The state changes through external events (see episodic layer), not through argumentation.

**How it's populated:** Customers document their state through agents, extraction from conversations, and manual configuration. Expansion packs provide the schema — the kinds and relationships that define what state can be tracked.

### 2. Episodic Layer (Deterministic — Events)

External data sources that connect to the state layer and update it. This is the "what happened" layer.

**Examples:**
- GitHub issues: "Bug #452 was closed" → updates product state
- Task management: "Sprint 14 completed" → updates team state
- Meta Ads: "Campaign #7 finished its run" → updates campaign lifecycle
- Calendar: "Q3 planning meeting happened" → updates event log

The episodic layer is **deterministic** — events are facts. They carry timestamps, provenance, and connect to state entities via `aboutObject` / `aboutSubject` edges. The extraction pipeline (epic #70) ingests episodic data and automatically updates the state layer.

**How it connects to state:** An episodic event doesn't argue about whether a campaign finished — it OBSERVES that it finished. The event connects to the campaign entity and triggers a lifecycle transition. This is deterministic, not probabilistic.

### 3. Epistemic Layer (Probabilistic — Beliefs)

The Tortoise EP graph. Arguments, evidence, confidence scores. This is the "what do we believe?" layer.

**Examples:**
- "We believe customer segment X is declining because..."
- "Strategy Y is preferred over Z with 75% confidence"
- "Competitor analysis suggests market shift toward W"
- "Three independent sources converge on insight V"

The epistemic layer is **probabilistic** — these are beliefs backed by evidence, not facts. They carry confidence scores, source credibility tiers, mitigations, and NAND contradictions. The EP engine propagates belief through the graph.

**How it connects to state:** Epistemic claims connect to state entities. "Customer segment X is declining" connects to the segment entity in the marketing pack. The CONFIDENCE from the epistemic layer enriches the state — the state says "this segment exists," and the epistemic layer says "and we are 75% confident it's declining."

## How They Work Together

```
EPISODIC LAYER                  STATE LAYER                   EPISTEMIC LAYER
(deterministic)                 (deterministic)               (probabilistic)
                                                              
GitHub issue → updates →    Product state              ← enriched by →  Arguments, evidence
Task complete → updates →   Team state                 ← enriched by →  Confidence scores
Meta campaign → updates →   Marketing state            ← enriched by →  Belief about segments
Calendar event → updates →  Event log                  ← enriched by →  Decision rationale
                                                              
OBSERVED FACTS                ORGANIZATIONAL REALITY         REASONED BELIEFS
"this happened"               "this is our state"            "we think this is true"
```

The state layer is the **anchor.** Episodic events update it with facts. Epistemic reasoning enriches it with beliefs. The customer's primary interface is the state — they customize their ontology (expansion packs), populate it with their organizational reality, and then the epistemic layer adds reasoning on top.

## What This Means for Expansion Packs

Expansion packs ARE the state layer schema. They define:
- What kinds of entities the customer tracks (objectKinds)
- What relationships exist between them
- What lifecycle states entities can be in
- What valid values properties can take

When a customer integrates Tortoise, they customize their packs. "We have marketing campaigns, sales channels, customer segments" → those are kinds in the marketing pack. The extraction pipeline populates these from conversations and documents. The epistemic layer then reasons about them.

Packs are not "optional add-ons." They are the **primary interface** — the vocabulary through which the customer describes their world.

## Starter Packs

The core ontology provides cross-domain primitives (Subject, Object, Action, Point, Event, Document). Expansion packs provide domain-specific state:

| Pack | What it tracks | Example kinds |
|------|---------------|---------------|
| **Product strategy** | Products, features, competitors, markets | Product, Feature, Competitor, Market, Customer |
| **Marketing** | Campaigns, channels, content, audiences | Campaign, Channel, Content, Audience, Brand |
| **Development** | Code, services, deployments, infrastructure | Service, API, Database, Deployment, Tool |
| **Organization design** | Teams, roles, workflows (already in core) | Team, Role, Workflow, Skill |

## Practical Impact

1. **Extraction MUST support custom ontologies.** Without it, customers can't define their own state. The extraction pipeline (#70) is blocked on expansion pack architecture (#123).

2. **The state layer is the moat.** Every custom kind a customer creates is data that lives in Tortoise. Switching costs grow with state depth.

3. **The epistemic layer is the differentiator.** Competitors can store state. Only Tortoise can REASON about state — confidence, contradiction, evidence quality, belief propagation through the graph.

4. **Governance becomes critical at Team tier.** When multiple people share state, you need RBAC (who can modify the schema?), kind lifecycle (who approves new kinds?), and audit (who changed what?).
