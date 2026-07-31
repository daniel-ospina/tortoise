# Tortoise Architecture: 5-Layer Memory Model

**Date:** 2026-07-31
**Status:** Vision
**Team:** epistemic-team

## Overview

Tortoise is not just an argumentation engine. It is an organization's **memory system** with five layers that work together. Expansion packs are not "optional schemas" — they are the **primary interface** for customers. They define what state the customer tracks, and everything else connects to that state.

Of the five layers, Tortoise directly owns and builds three: State, Episodic, and Epistemic. The Procedural layer is acknowledged and will receive metadata support later. The Working Memory layer is explicitly out of scope — it belongs to the organization's agent orchestration layer.

## The Layers

### 1. State Layer (Deterministic — Objects)

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
- Project management: "Sprint 14 completed" → updates sprint state
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

### 4. Procedural Layer (Processes & Workflows) — Acknowledged, Not Built Yet

**Tortoise ownership:** Acknowledged. Metadata and indexing planned for later. Not built now.

This is where business processes, workflows, and standard operating procedures live. It answers: "How do we do things?"

**Examples:**
- Sales pipeline stages and transitions
- Content publishing workflow
- Bug triage process
- Hiring pipeline steps
- Customer onboarding checklist

**How it connects to state:** A process connects to the state entities it operates on. The "Content Publishing Workflow" connects to Content objects in the marketing pack. The "Bug Triage Process" connects to Product objects in the development pack. When an episodic event fires ("task moved to Done"), it triggers a state transition defined by the procedural layer.

**What Tortoise does now:** The procedural layer exists as a concept. Processes can be documented as objects with `objectKind: workflow` or `objectKind: process`. They connect to state entities via `aboutObject` edges. No workflow engine, no automation, no process enforcement — just the knowledge that processes exist and relate to state.

**What Tortoise will do later:** Metadata indexing so processes are searchable. Lifecycle tracking so the state layer can reference "this entity is currently in step 3 of the Bug Triage Process." Not a workflow engine — just enough structure to connect process knowledge to organizational state.

### 5. Working Memory Layer (Agent Context & In-Progress Work) — Explicitly Out of Scope

**Tortoise ownership:** None. This layer belongs to the organization's agent orchestration and data teams.

This is where agents operate. It answers: "What is being worked on right now?"

**Examples:**
- Which agent is executing which workflow step?
- What's in the agent's current context window?
- Which tasks are in progress vs queued?
- Session state, conversation history, immediate priorities

**How it connects to Tortoise:** Agents in the working memory layer reference Tortoise entities. An agent executing a customer support workflow queries the state layer for the customer's segment, the epistemic layer for confidence about churn risk, and the procedural layer for the next step in the resolution process. Tortoise provides the memory — the agent provides the action.

**Why it's out of scope:** Working memory is fast, ephemeral, and agent-specific. It's about "right now" — not about persistent organizational knowledge. The organization's data team handles this layer. Tortoise's role is to be the persistent memory that agents query and update, not to track which agent is doing what.

**What Tortoise does:** Tortoise entities can be referenced from the working memory layer via their IDs. Agents can point to state objects, epistemic claims, or episodic events. But Tortoise itself does not index, store, or manage working memory state.

## How They Work Together

```
LAYER 5: WORKING MEMORY (agent context, in-progress work)
         │ references Tortoise entities via ID
         │ OUTSIDE TORTOISE DOMAIN — org data team handles this
         ▼
LAYER 4: PROCEDURAL (processes, workflows)
         │ defines how state transitions happen
         │ acknowledged now, metadata/indexing later
         ▼
LAYER 2: EPISODIC ──updates──→ LAYER 1: STATE ←──enriched by── LAYER 3: EPISTEMIC
(deterministic events)         (deterministic objects)         (probabilistic beliefs)

GitHub issue → updates →    Product state              ← enriched by →  Arguments, evidence
Task complete → updates →   Team state                 ← enriched by →  Confidence scores
Meta campaign → updates →   Marketing state            ← enriched by →  Belief about segments
Calendar event → updates →  Event log                  ← enriched by →  Decision rationale

OBSERVED FACTS                ORGANIZATIONAL REALITY         REASONED BELIEFS
"this happened"               "this is our state"            "we think this is true"
```

The state layer is the **anchor.** Episodic events update it with facts. Epistemic reasoning enriches it with beliefs. Procedural knowledge defines how it changes. Working memory acts on it. The customer's primary interface is the state — they customize their ontology (expansion packs), populate it with their organizational reality, and then the epistemic layer adds reasoning on top.

## Tortoise Scope Summary

| Layer | Built by Tortoise? | When |
|-------|-------------------|------|
| 1. State | ✅ Yes | Now |
| 2. Episodic | ✅ Yes | Now (extraction pipeline #70) |
| 3. Epistemic | ✅ Yes | Now (EP engine, tortoise-decide) |
| 4. Procedural | ⏳ Acknowledged | Metadata/indexing later. Connect to state now. |
| 5. Working Memory | ❌ Out of scope | Org data team. Tortoise entities referenced by ID. |

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

2. **Ontology is the moat.** Every custom kind a customer creates is data that lives in Tortoise. Switching costs grow with ontology customization and data depth.

3. **The epistemic layer is the differentiator.** Competitors can store state and events. Only Tortoise can REASON about state — confidence, contradiction, evidence quality, belief propagation through the graph.

4. **Governance becomes critical at Team tier.** When multiple people share state, you need authorization (who can modify the schema?), kind lifecycle (who approves new kinds?), and audit (who changed what?). This means having a flexible system (RBAC, ReBAC, and ABAC compatible and other frameworks if needed) that can be slotted into enterprise customers too.
