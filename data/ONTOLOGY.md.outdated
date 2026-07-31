---
title: "El Dato — Canonical Ontology"
type: data
domain: data
status: seedling
tags: []
summary: ""
created: 
updated: 
---
> ⚠️ **FORKED** from  (2026-07-07).
> The org-design ONTOLOGY.md is the canonical document for how El Dato operates
> (teams, roles, workflows, domains). This fork isolates entity classes relevant to
> the epistemic graph product — Points, Sources, Operators, Events, Documents — and
> strips organisational scaffolding that is not foundational to the product layer.
> Alignment between the two documents must be maintained when entity definitions change.




# El Dato — Canonical Ontology

> **Version:** 1.0.0
> **Status:** 🔒 CANONICAL CONTRACT — ratified 2026-06-30
> **Authority:** Single source of truth for how El Dato is organized. All agents, skills, and systems reference this document for entity definitions, relationships, and domain structure.
> **Based on:** Memory architecture research, ontology-wiki reconciliation, W3C ORG, OASIS, Dublin Core, PROV-O, Agile/DevOps standards

---

## 1. Organizational Model

### 1.1 Teams (Vertical — Product-Owning)

| Team | Slug | Owns | State | Example Roles (aspirational — not implemented) |
|------|------|------|-------|------------------------------------------------|
| **El Dato App Team** | `app` | Consumer marketplace, business dashboard, deals, scanner, referrals+loyalty, eldato.com.mx, newsletter | active | Product Strategist, Full-Stack Dev, Content Strategist, Growth Hacker |
| **El Dato Outreach Team** | `outreach` | B2B sales, WhatsApp outreach, CRM, owner discovery | active | Sales Operator, CRM Manager, Content Strategist |
| **DMer App Team** | `dmer-app` | IG daemon, desktop app, automation | active | Desktop Dev, Growth Hacker |
| **Organisation Design Team** | `org-design` | Memory architecture, ontology, agent infrastructure, domains, roles, skills, workflows — cross-domain governance | active | Platform Architect, Org Designer |

> **State values:** `active` (current), `sunset` (product ended, knowledge preserved), `superseded` (merged into another team).

> **Role implementation status:** Roles are NOT yet implemented. The examples above are aspirational. Roles will be defined in `operations/roles/` (see §9.2 for template structure). Roles operate as continuous/trigger/cron-based loops within the Levers of Control framework, scoped by product and governed by delegation boundaries.

### 1.2 Domains (Horizontal — Expertise-Compounding)

| # | Domain | Slug | Focus | Wiki | Why Separate |
|---|---|---|---|---|---|
| 1 | **Product & Services** | `product` | Product specs, strategy, competitive analysis, value proposition, pricing strategy | `docs/01_product/wiki/` | What we build and why. Includes Strategy (competitive analysis, market positioning — formerly 09_strategy). |
| 2 | **Data** | `data` | Ontology, canonical schemas, data models | `docs/02_data/wiki/` | How we model reality |
| 3 | **Engineering** | `engineering` | Dev, auth, DB, deployment, platform, security, ADRs | `docs/04_platform/wiki/` | How we build and run it. Absorbs former Platform & Security and Architecture domains. Wiki path retains 04_platform/ for backward compat. |
| 4 | **UX** | `ux` | Design specs, journey maps, interaction patterns, design tokens, component catalog (design system) | `docs/07_ux/wiki/` | How users experience it — owns the design system |
| 5 | **Growth** | `growth` | Marketing, SEO, content, search, CRM, analytics, sales, customer support, external relationships | `docs/05_growth/wiki/` | How we acquire and retain. Includes Content, Sales, Customer Support, and External Relationships (proposals, partnership docs) as sub-functions. |
| 6 | **Operations** | `operations` | CI/CD, migrations, agent ops, skills pipeline, monitoring, cost management | `docs/06_operations/wiki/` | How we operate. Technical operations only: CI/CD, migrations, monitoring, cost management. Skills pipeline, L&D, agent roles, hiring moved to domain 9 (Capability) per the split. |
| 7 | **Legal & Compliance** | `legal` | Legal research, compliance, data protection, regulatory, terms | `docs/10_legal/wiki/` | Legal and regulatory landscape |
| 8 | **Finance & Accounting** | `finance-accounting` | Billing, spend analysis, forecasting, financial reporting | `docs/11_finance/wiki/` | How we manage money. Standard business taxonomies (APQC PCF category 8.0) treat this as separate from Operations. |
| 9 | **Org Development & Capability** | `capability` | Workflows, skills, L&D, agent roles, hiring (future), team culture, Identity (Mission, Vision, Values) | `docs/12_capability/wiki/` | How we grow as an organization. Includes Identity ("who we are") — cross-cutting org documents owned by this domain. |

> **Prefixes:** 03 and 09 vacated (03_search and 09_strategy deprecated — content merged into Growth and Product & Services respectively).

> **Engineering scope:** Absorbs former Platform & Security and Architecture domains. Tech strategy, platform specs, security, ADRs — one engineering bucket per small-team convention (5 core documentation categories).

> **Operations scope:** Includes both technical operations (CI/CD, migrations, monitoring) and organizational capability (workflows, skills, L&D, agent roles, hiring). These were one domain at current scale — now split: domain 6 (Operations) = technical ops; domain 9 (Capability) = org capability.

> **Growth scope:** Includes Content, Sales, Customer Support, and External Relationships as sub-functions. No separate domains needed at current scale.

> **Org Development & Capability scope:** Includes Identity (Mission, Vision, Values — "who we are as an organization"). These are cross-cutting org documents referenced by all domains but owned by this domain.



### 1.3 Domains — Cross-Cutting Taxonomy

Domains are a **tagging system**, not a hierarchy. Multiple domains can be associated with a piece of work or an object. The primary organizational structure is subjects (teams, roles).

| Domain | Tags | Governed By |
|--------|-----------|-------------|
| **Product & Services** | Product specs, Features, strategy, competitive analysis | El Dato App (domain objects), Organisation Design (cross-domain: ontology, skills registry) |
| **Data** | Ontology, canonical schemas, data models | Organisation Design |
| **Engineering** | Dev, auth, DB, deployment, platform, security, ADRs | El Dato App (domain objects), DMer App (daemon code), Organisation Design (cross-domain: ADRs) |
| **UX** | Design specs, journey maps, component catalog | Organisation Design (cross-domain: design system) |
| **Growth** | Marketing, SEO, content, CRM, sales, support | El Dato Outreach (domain objects), Organisation Design (cross-domain: skills registry) |
| **Operations** | CI/CD, migrations, agent ops, workflows, skills, L&D | Organisation Design (cross-domain infrastructure) |
| **Legal & Compliance** | Legal research, compliance, data protection, regulatory | Organisation Design |
| **Finance & Accounting** | Billing, spend analysis, forecasting, financial reporting | Organisation Design |
| **Org Development & Capability** | Workflows, skills, agent roles, hiring, Identity | Organisation Design |

**How domains relate to the four categories:**
- **Work** happens in a **team/role** (subject), acts ON an **object** (product, brief, etc.), and is **tagged** with one or more domains
- **Objects** may be tagged with multiple domains (a Skill can be domain UX and domain growth)
- **Subjects** may specialize in domains but belong to teams — domain is not an organizational home
- **Organisation Design Team** governs cross-domain objects: the ontology, WIKI_SCHEMA, skills registry, role registry, workflow registry — the domain system itself
- The primary structure is **subjects** (Organization → Team → Role → Agent). Domains are secondary — a tagging system, not an org chart.

### 1.4 The Matrix

Subjects (teams, via their roles) govern objects. Work happens in a team, acts on objects, and is tagged with domains. Domains are a tagging system — they don't govern anything.

| Team | Owns (objects) | Cross-Team Tools & Processes |
|------|---------------|---------------------------|
| **El Dato App Team** | eldato.com.mx, scanner, deals, referrals+loyalty, newsletter | — |
| **El Dato Outreach Team** | WhatsApp outreach, CRM, owner discovery | — |
| **DMer App Team** | IG daemon, desktop app | — |
| **Organisation Design Team** | ONTOLOGY.md, WIKI_SCHEMA.md, skills/role/workflow registries, agent infrastructure, memory architecture, domain taxonomy | Skills pipeline, loop enforcer, `/research` command, wiki/ structure |

**Collaboration:** Multiple teams can collaborate on work. Domains tag the work — they don't own it. Organisation Design Team builds tools and processes that other teams use.

## 2. Entity Model — Four Categories

Entities fall into four ontological categories. These are not hierarchical levels — they are different kinds of things with different lifecycle, ownership, and relationships.

| Category | What | Lifecycle | Owned By | Entities |
|----------|------|-----------|----------|----------|
| **Subjects** | Who acts, owns, decides | Ongoing (persist until org change) | Organisation Design | Organization, Team, Role, Agent, Membership |
| **Objects** | What persists — knowledge, products, data | Ongoing (created once, updated) | Domain teams + Organisation Design (cross-domain) | Product, Feature, Competitor, Customer, User, ResearchBrief, Brief, Plan, Proof, Decision, Evidence, Document, Claim, Skill, Workflow |
| **Actions** | What subjects do — from small tasks to strategic initiatives. Scale and type are orthogonal properties. | Temporary (start → end) | Executing subject, accountableTo → Team (via Role) | Epic, Project, Task, Research, Scope, Planning, Implement, Verify, Decompose, Delegate, Loop |
| **Tools & Processes** | What subjects use to act — from deterministic code tools to semi-autonomous workflows | Ongoing (maintained, versioned) | Organisation Design Team (cross-team) + Domain teams (team-specific) | Workflow (process orchestrating skills), Skill (capability), Tool (deterministic code/MCP) |

### 2.1 Subjects

| # | Entity | Standard | Definition |
|---|--------|----------|------------|
| 1 | **Organization** | W3C ORG | El Dato as a whole. |
| 2 | **Team** | W3C ORG | Vertical product-owning unit. |
| 3 | **Role** | W3C ORG | Organizational function. |
| 4 | **Agent** | OASIS Agent | Human or AI entity. Performs Actions, holds Roles. |
| 5 | **Membership** | W3C ORG | Agent → Team via Role. Duration, contract. |

### 2.2 Objects — Products & Market

| # | Entity | Standard | Definition |
|---|--------|----------|------------|
| 11 | **Product** | Enterprise | What El Dato sells. |
| 12 | **Customer** | Enterprise | Business that pays. Lifecycle: Prospect → Lead → Active → Churned. These are states, not separate entity classes. |
| 13 | **Competitor** | Enterprise | Organization or product we compete against. |
| 14 | **User** | Consumer | Uses product, doesn't pay. |
| 15 | **Feature** | Agile | Product capability. Owned by Team. Persists after built. Building it is Work. |
| 31 | **Supplier** | Enterprise | External service, dependency, or integration partner. Libraries, APIs, MCP servers, SaaS tools. |

### 2.3 Objects — Knowledge, Products & Decisions

| # | Entity | Standard | Definition |
|---|--------|----------|------------|
| 16 | **Document** | Dublin Core | Any markdown file. |
| 17 | **Decision** | Context Graph | Binding choice with rationale, evidence, date. |
| 18 | **Evidence** | PROV-O | Source document, data point, or observation. |
| 19 | **Claim** | Epistemic Graph | Assertion with confidence, source, support/contradict edges. |

### 2.4 Objects — Work Stage Outputs

| # | Entity | Standard | Definition |
|---|--------|----------|------------|
| 20 | **RawData** | Dublin Core | Immutable source data. Lives in source systems (Supabase, HubSpot, GSC, Stripe) with immutable copies in `raw/` for research traceability. Input to Research. |
| 21 | **ResearchBrief** | Dublin Core | Structured synthesis: findings, confidence, contradictions. Output of Research. |
| 22 | **Brief** | Dublin Core | Guiding question + O/I/T + requirements + constraints. Output of Scope. |
| 23 | **Plan** | Dublin Core | Trade-offs, approach, architecture. Output of Plan. |
| 24 | **Proof** | PROV-O | Outcome of verifying: issues found, issues fixed, verdict, evidence, confidence. The output object of Verify work.

### 2.5 Tools & Processes

| # | Entity | Standard | Definition |
|---|--------|----------|------------|
| 25 | **Workflow** | OASIS Process | Orchestrated sequence of Skills to achieve a goal. Both Object (the definition) and Tool (when executed). |
| 26 | **Skill** | Skills Ontology | Executable capability. Defined in `operations/skills/`. Can belong to multiple Workflows and Roles. |
| 27 | **Tool** | MCP/CLI | Deterministic code capability: MCP servers, CLI tools, APIs. Distinct from Tools & Processes category which includes semi-autonomous Workflows and Skills. |

#### 2.5.1 Skill Types (MECE Taxonomy)

Every **Skill** (#26) falls into exactly one of three structural types, classified by invocation pattern:

| Type | Definition | Invokes other skills? | In `available_skills`? | Example |
|------|-----------|----------------------|------------------------|---------|
| **Workflow** | Routes through phases, invokes other skills in sequence. Can be top-level or nested. | Yes | Yes (top-level), No (nested) | `epic-workflow`, `_shared/plan/` |
| **Bounded** | A single stage in a Workflow's pipeline. Not independently invocable — only meaningful in context. | No | No | `_shared/align/`, `_shared/scope/` |
| **Modular** | Standalone skill, independently invocable, reusable across any context. | No | Yes | `research`, `code-review` |

**Why MECE:** Every skill is exactly one type. Nestedness is a property of Workflows (top-level vs nested), not a separate type.

**Continuity Directive Rules:**

| Type | Continuity |
|------|-----------|
| **Workflow** (top-level) | "as mandated by this skill" |
| **Workflow** (nested) | "as mandated by the parent workflow skill" |
| **Bounded** | "as mandated by the workflow skill" |
| **Modular** | None |

**Full Classification (current skills):**

| Skill | Type | Notes |
|-------|------|-------|
| `epic-workflow` | Workflow (top-level) | Routes epic through 6 stages |
| `project-workflow` | Workflow (top-level) | Routes project through 6 stages |
| `task-workflow` | Workflow (top-level) | Routes task through 6 stages (lightweight) |
| `_shared/plan/` | Workflow (nested) | Orchestrates 8 substeps, bounded to parent |
| `_shared/align/` | Bounded | Stage 1 of planning pipeline |
| `_shared/research/` | Bounded | Stage 2 — wraps research capability |
| `_shared/scope/` | Bounded | Stage 3 — scope + high-level E2E |
| `_shared/decompose/` | Bounded | Stage 5 — MECE + wiring + verification |
| `_shared/verify/` | Bounded | Stage 6 — pre + post deploy checks |
| `research` | Modular | Standalone research, invocable anywhere |
| `issue-creation` | Modular | Standalone issue creation |
| `code-review` | Modular | Standalone review |

**Sources:** Agent Harness (Stanford) — Orchestrator/Worker/Judge; AWS Step Functions — Task vs Flow; LangChain Skills — Skill/Tool/Progressive Disclosure; CrewAI — Planner/Researcher/Reviewer/Executor. Our taxonomy is structural (how invoked), not functional (what domain).

### 2.6 Actions

Everything a Subject does is an Action. Scale and type are orthogonal properties — not separate entity classes.

| # | Action | Volume | Definition |
|---|--------|-------|------------|
| 28 | **Epic** | Strategic volume | Decomposes into Projects. Tracked as GitHub Issue with `epic` label. Parent of child issues. |
| 29 | **Project** | Tactical volume | Decomposes into Tasks. Tracked as GitHub Issue with child/parent links. |
| 30 | **Task** | Atomic volume | Smallest unit. assignedTo → Agent. Tracked as GitHub Issue. |
| 32 | **Research** | Any scale | Gather and synthesize data. Produces ResearchBrief. |
| 33 | **Scope** | Any scale | Define O/I/T, requirements, constraints. Produces Brief. |
| 34 | **Planning** | Any scale | Design approach, architecture, interfaces, E2E tests. Produces Plan. |
| 35 | **Implement** | Any scale | Execute. Produces closed Tasks, gotchas. |
| 36 | **Verify** | Any scale | Confirm correctness. Produces Proof. |
| 37 | **Decompose** | Cross-cutting | Break action into child issues. Pipeline stage 5 at epic/project levels (MECE + wiring + verification). At task level: no-op. |
| 38 | **Delegate** | Cross-cutting | Assign action to Subject with RACI. |
| 39 | **Loop** | Cross-cutting | Verification wrapper. Completion/cron/trigger/continuous. |

**Tracking:** All Actions (Epic, Project, Task) are tracked as GitHub Issues. Parent/child relationships are expressed via issue references (`**Epic:** #N` in body, child issue lists).

**How Actions compose:**
- An Epic may contain Research, Scope, Planning actions and multiple Projects
- A Project decomposes into Tasks (Research tasks, Implement tasks, Verify tasks)
- Decompose and Delegate are cross-cutting — apply to any Action at any scale
- A Loop wraps any Action with verification cycles



### 2.7 Systems of Record

Every entity class has a canonical system of record — the authoritative database where instances are stored, queried, and managed.

> **Filing principle:** Object type is a frontmatter field queried by Dataview. Physical location follows Subject + Actionability + Volatility (see `docs/teams/organisation-design-team/operations/filing-matrix.md`). Do not create folders named after entity types. The paths below are systems of record (where canonical data lives), NOT filing instructions for documents.

| Entity Class | System of Record | Access |
|-------------|-----------------|--------|
| Epic (#28), Project (#29), Task (#30) | **GitHub Issues** | `gh issue`, GitHub API |
| Customer (#12) | **HubSpot CRM** | `mcp__seo-intelligence__hubspot_*` |
| Skill (#26) | **`operations/skills/`** | Filesystem — SKILL.md files |
| Document (#16) | **`docs/`** | Filesystem — markdown files. Filed by Subject + Actionability, not by entity type. |
| Decision (#17) | **`docs/teams/<team>/decisions/`** or **`docs/08_architecture-decisions/`** (cross-team) | Filesystem — ADR markdown files |
| Evidence (#18) | **MemPalace KG** | `mempalace_kg_*` tools |
| Claim (#19) | **MemPalace KG** | `mempalace_kg_*` tools (future: epistemic graph edges) |
| ResearchBrief (#21), Brief (#22), Plan (#23), Proof (#24) | **`docs/teams/<team>/`** (filed by Subject) | Filesystem. Object type is frontmatter — Dataview finds all ResearchBriefs regardless of location. |
| Competitor (#13) | **`docs/01_product/competitive/`** | Filesystem — markdown profiles |
| Product (#11) | **`docs/teams/eldato-app-team/data/ONTOLOGY_SPEC_v4.0.md`** | Filesystem + MemPalace KG |
| Organization (#1), Team (#2), Role (#3) | **`docs/teams/organisation-design-team/data/ONTOLOGY.md`** | Filesystem — canonical contract |
| Agent (#4) | **Runtime** | Pi, Claude Code, Hermes — not persisted |
| User (#14) | **Supabase** (`profiles` table) | `mcp__supabase__execute_sql` |
| RawData (#20) | **Source system** + **`raw/`** (immutable copies) | Source APIs + Filesystem |
| Feature (#15) | **`docs/01_product/`** | Filesystem — product specs |
| Workflow (#25) | **`operations/workflows/`** | Filesystem — registry |
| Supplier (#31) | **`docs/01_product/integrations/`** + `package.json` | Filesystem |
| Tool (#27) | **MCP servers / npm / pip** | Runtime |

**Rule:** Agents query the system of record. Object type is frontmatter, not folder. Cross-references are links, not copies. See `docs/teams/organisation-design-team/operations/filing-matrix.md` for physical filing destinations.

## 3. Work Vocabulary

| Stage | Verb | Produces | Definition |
|-------|------|----------|------------|
| **Align** | align | Decision (#17) | Validate strategic fit. Should this enter the pipeline? Adversarial check + Eisenhower matrix + profit alignment. Produces go/no-go with rationale. |
| **Research** | research | ResearchBrief (#21) | Gather and synthesize data on a topic. |
| **Scope** | scope | Brief (#22) | Define O/I/T, requirements, constraints. Produces high-level E2E test cases. |
| **Plan** | plan / strategize | Plan (#23) | Design approach, architecture, interfaces, E2E tests. Does NOT decompose — that's a separate stage. |
| **Decompose** | decompose | Features (#15) + Tasks (#30) | Break plan into child issues. MECE verification + wiring + per-issue review. Runs at epic/project levels. At task level: no-op (single issue, nothing to decompose). |
| **Implement** | implement | Implementation (closed Tasks, gotchas) | Execute. At epic/project: runs inside child issues, not as a pipeline stage. At task level: pipeline stage 5 (replaces Decompose). |
| **Verify** | verify | Proof (#24) | Confirm correctness. Test + fix + retest loop. The /loop command wraps this with verification cycles. Loop is the wrapper; Proof is the output. |



### 3.1 Subject Attribution

Every Action MUST be attributed to a Subject. Subjects are matryoshka: Organization contains Teams, Teams contain Roles. Attribution to a Role implies attribution to its Team and Organization. Governance is at the Role level; association flows upward.

| Entity | Required Attribution | Enforcement |
|--------|--------------------|-------------|
| **Epic** (#28) | `accountableTo` → Team | Epic body: `**Team:** <team-name>` |
| **Project** (#29) | `accountableTo` → Team (via Role junction) | Inherited from parent Epic or explicit |
| **Loop** | `accountableTo` → Team (inherited from parent Epic or explicit) | Manifest scope field |
| **Feature** (#15) | `accountableTo` → Team (inherited from parent Epic) | Issue labels |
| **Task** (#30) | `assignedTo` → Agent (via delegation) | Issue assignee |

**Rationale:** Without team attribution, work items float without ownership.

### 3.2 Workflow & Object Statuses

#### Workflow Statuses (Action Lifecycle)

Used for epics, projects, tasks, loops — anything that moves through a pipeline.

| Status | Meaning | Example |
|--------|---------|----------|
| `pending` | Not yet started | Epic created, not picked up |
| `in-progress` | Actively working | Pipeline running |
| `paused` | Intentionally halted, will resume | Human gate, session boundary, waiting for upstream |
| `blocked` | Cannot proceed, needs intervention | Dependency failed, unfixable P0, deadlock |
| `completed` | Finished successfully | All gates passed, issues created |
| `failed` | Tried, broke | Pipeline error, unrecoverable |
| `dropped` | Intentionally abandoned | Align NO-GO, human rejection |

**paused vs blocked:** `paused` = positive state — process is suspended but ready (will resume when condition met). `blocked` = negative state — process cannot move without external intervention.

#### Object Statuses (Document Lifecycle)

Used for frontmatter `doc_status` field — any persistent artifact.

| Status | Meaning |
|--------|----------|
| `draft` | In development, not live |
| `live` | Actively in use — do not break |
| `superseded` | Replaced by a newer version |
| `deprecated` | Will be removed — do not rely on |
| `broken` | Live but malfunctioning |
| `archived` | Preserved, not active |

> **Source issue:** #5896. Controlled vocabulary at `docs/teams/organisation-design-team/data/controlled_vocabulary.md`.

### Cross-Cutting Skills

| Skill | Applies To | Definition |
|-------|-----------|------------|
| **Decomposition** | Any work item | Break into child items. Epic→Features, Feature→Tasks. |
| **Delegation** | Any work item | Assign to Role/Agent with RACI. |

---

## 4. Memory Levels (Fractal M0-M2)

| Level | Scope | Storage |
|-------|-------|---------|
| **M0** | Task learnings | `MEMORY.md`, `wiki/gotchas.md`, plan doc learnings |
| **M1** | Domain knowledge | `wiki/synthesis.md`, `wiki/patterns.md`, domain docs |
| **M2** | Cross-domain | MemPalace KG, ADRs, strategy docs |

---

## 5. Research Pipeline (R0-R5)

| Layer | Name | Status |
|-------|------|--------|
| **R0** | Query Planning | ✅ Research skill protocol |
| **R1** | Sourcing (multi-source) | ⚠️ Perplexity + yt-dlp. Exa/Brave P1 add-ons documented |
| **R2** | Cross-Reference (confidence tiers) | ✅ Not quantity filtering |
| **R3** | Synthesis | ✅ Agent synthesis + LightRAG (P1 documented) |
| **R4** | Verification | ✅ Manual verifier (pre-loop-enforcer bridge) |
| **R5** | Output (wiki/ + KG filing) | ✅ `/research` command + 7-step filing contract |

---

## 6. Faceted Taxonomy

| Axis | Prefix | Levels |
|------|--------|--------|
| Memory Depth | M | M0-M2 |
| Research Pipeline | R | R0-R5 |
| Verification Rigor | V | V1-V4 |
| Business Maturity | P | P1-P4 |
| Cynefin Domain | — | Simple, Complicated, Complex, Chaotic |

---

## 7. Key Relationships

| Relationship | From → To | Standard |
|-------------|----------|----------|
| `subOrganizationOf` | Team → Organization | W3C ORG |
| `hasMember` | Team → Agent | W3C ORG |
| `holdsRole` | Agent → Role | W3C ORG |
| `hasWorkflow` | Role → Workflow | OASIS |
| `orchestrates` | Workflow → Skill | OASIS |
| `canExecute` | Role → Skill | Skills Ontology |
| `performs` | Agent → Task | OASIS |
| `produces` | Verify → Proof | Actions |
| `wraps` | Loop → Action | Loop Enforcer |
| `decomposesInto` | Action → Action | Actions |
| `accountableTo` | Epic/Loop → Team | RACI |
| `accountableTo` | WorkItem → Role | RACI |
| `responsibleFor` | Role → WorkItem | RACI |
| `supports` / `contradicts` | Claim → Claim | Epistemic Graph |
| `hasEvidence` | Claim → Evidence | PROV-O |
| `delegatesTo` | Action → Subject | RACI |
| `produces` | Action → Object | Actions |
| `wraps` | Loop → Action | Loop Enforcer |
| `contains` | Action (parent) → Action (child) | Actions |
| `authoredBy` | Document → Agent | Dublin Core |

---

## 8. Cross-References

- **Memory Architecture:** `docs/teams/organisation-design-team/operations/2026-06-28-memory-architecture.md`
- **WIKI_SCHEMA:** `docs/teams/organisation-design-team/operations/WIKI_SCHEMA.md`
- **Reconciliation:** `docs/teams/eldato-app-team/product/2026-06-30-ontology-wiki-reconciliation.md`
- **AGENTS.md:** Memory Contracts, gate rules
- **00_index.md:** §14 Memory Architecture
- **Work Order:** `docs/teams/organisation-design-team/operations/2026-06-29-work-order.md`
- **ONTOLOGY_SPEC:** `docs/teams/eldato-app-team/data/ONTOLOGY_SPEC_v4.0.md` (data model)


---


## 9. Role, Workflow & Skill Architecture

### 9.1 What a Role IS

A Role is an organizational function with defined responsibilities, goals, and authority. Roles can be held by humans, AI agents, or both — operating semi-autonomously within governance boundaries defined by the Levers of Control framework.

**Role characteristics:**
- **Continuous/trigger/cron-based** — roles run as persistent loops (e.g., Growth Hacker monitors analytics daily) or on-demand (e.g., Sales Operator triggered by new lead)
- **Scoped by product** — a role operates within a Team's product scope (e.g., "Growth Hacker for El Dato App" vs "Growth Hacker for DMer App")
- **Delegation-governed** — roles operate within delegation boundaries (open/closed) defined by the Levers of Control framework (belief, boundary, diagnostic, interactive controls). The loop enforcer's P3 culture drift detection + 4-knob control valve provide the governance layer.

### 9.2 Subjects Registry (Operations Layer)

Subjects (Teams and Roles) are NOT defined in the ontology. Like skills (`operations/skills/` — 83 SKILL.md files (including 3 deprecated)), subjects have their own registry:

```
operations/subjects/
├── eldato-app-team.yaml        # Team = container, roles nested inside
├── eldato-outreach-team.yaml
├── dmer-app-team.yaml
└── organisation-design-team.yaml
```

**Why team files contain roles inline:** A role belongs to a team — nesting them in the team file preserves the container relationship without cross-references. One file to read for "what subjects exist right now."

**Team file structure:**
```yaml
team:
  slug: eldato-app-team              # from ONTOLOGY.md §1.1
  name: El Dato App Team
  leads_to: organisation-design-team # recursion up — parent team
  escalation: platform-architect     # role to notify on caps/human gates

roles:
  product-strategist:
    # ── Required ──
    held_by: human                   # human | pi | claude-code | vacant
    loop_type: continuous            # completion | cron | trigger | continuous
    delegation: open                 # open | closed
    reports_to: null                 # role slug within same team (optional)
    # ── Identity (optional) ──
    status: active                   # proposed | active | archived | deprecated
    created: "2026-07-01"            # ISO 8601 date (quoted to prevent YAML date coercion)
    # ── Tagging (optional) ──
    domains: [product]               # canonical slugs from ONTOLOGY.md §1.2
    # ── Governance (optional, from ONTOLOGY.md §9.4) ──
    belief: ""                       # role purpose + mission
    boundary: ""                     # what the role MUST NOT do
    diagnostic: []                   # measurable KPI names
    interactive: ""                  # when oversight is required
```

Full field reference: `operations/subjects/_schema.md`.

**Why subjects are separate from ontology:** The ontology defines WHAT a Team and Role ARE (entity classes, relationships, standards). The subjects registry defines WHICH Teams and Roles EXIST. Same pattern as skills — the ontology has Skill, Team, and Role entity classes; `operations/skills/` has 83 SKILL.md files (including 3 deprecated); `operations/subjects/` has the actual team and role instances.

### 9.3 Workflow Registry (Aspirational)

Workflows are aspirational — no consumer exists yet. When needed, they will follow the same pattern as subjects and skills:

```
operations/workflows/
├── _template.md
├── editorial-plan.md
├── seo-audit.md
└── ...
```

**Workflow template structure:**
```yaml
---
workflow_id: editorial-plan
name: Editorial Plan
type: per-role              # per-role | cross-role
authorized_roles: [content-strategist]
skills:
  - content-research
  - editorial-content-writer
  - seo-meta-generator
  - content-humanizer
stages: [align, research, scope, plan, decompose, verify]
delegation: closed           # closed (follow this plan) | open (agent decides sequencing)
---
```

### 9.4 Levers of Control → Delegation Alignment

The Levers of Control framework (Simons, 1995) governs delegation boundaries:

| LoC Lever | Maps To | Implementation |
|-----------|---------|---------------|
| **Belief Systems** | Role purpose + mission | `belief` field in role registry |
| **Boundary Systems** | Delegation limits | `boundary` field — what the role MUST NOT do |
| **Diagnostic Control** | KPIs + metrics | `diagnostic` field — measurable outcomes |
| **Interactive Control** | Review gates (human or AI agent) | `interactive` field — when oversight is required. May be human, AI agent, or both. |

**Delegation types (OASIS):**
- **Open delegation** — agent decides HOW to achieve the goal. Governance: boundary + diagnostic controls.
- **Closed delegation** — agent follows a predefined plan (workflow). Governance: all 4 LoC levers apply.

The loop enforcer's culture drift detection (P3) monitors whether open-delegation roles are drifting outside their belief/boundary systems over time.

### 9.5 Example Workflows (Aspirational — Not Canonical)

The following workflows are aspirational examples. Actual workflows are defined in `operations/workflows/`. This table illustrates the pattern — it does NOT represent implemented roles.

| Workflow | Skills (in order) | Type |
|----------|-------------------|------|
| Editorial Plan | content-research → editorial-content-writer → seo-meta-generator → content-humanizer | per-role |
| SEO Audit | content-research → on-page-seo-auditor → technical-seo-checker → seo-content-checklist | cross-role |
| Feature Implementation | issue-scoping → writing-plans → executing-plans → test-design → code-review → commit-workflow | per-role |
| Bug Fix | systematic-debugging → implement-issue → verification-before-completion → commit-workflow | cross-role |

### 9.6 Entity Classes → Wiki Tags

| # | Entity Class | Tag Slug | Page Type |
|---|-------------|----------|-----------|
| 1 | Organization | `entity-class/organization` | entity |
| 2 | Team | `entity-class/team` | entity |
| 3 | Role | `entity-class/role` | entity |
| 4 | Agent | `entity-class/agent` | entity |
| 5 | Membership | `entity-class/membership` | concept |
| 25 | Workflow | `entity-class/workflow` | entity |
| 26 | Skill | `entity-class/skill` | entity |
| 28 | Epic | `entity-class/epic` | entity |
| 15 | Feature | `entity-class/feature` | entity |
| 29 | Project | `entity-class/project` | entity |
| 30 | Task | `entity-class/task` | entity |
| 11 | Product | `entity-class/product` | entity |
| 12 | Customer | `entity-class/customer` | entity |
| 13 | Competitor | `entity-class/competitor` | entity |
| 14 | User | `entity-class/user` | entity |
| 31 | Supplier | `entity-class/supplier` | entity |
| 17 | Decision | `entity-class/decision` | entity |
| 19 | Claim | `entity-class/claim` | entity |
| 18 | Evidence | `entity-class/evidence` | concept |
| 16 | Document | `entity-class/document` | concept |
| 21 | ResearchBrief | `entity-class/research-brief` | concept |
| 22 | Brief | `entity-class/brief` | concept |
| 23 | Plan | `entity-class/plan` | concept |
| 24 | Proof | `entity-class/proof` | concept |
| 20 | RawData | `entity-class/raw-data` | concept |
| 27 | Tool | `entity-class/tool` | entity |

### 9.7 Memory Levels → Entity Classes

| Level | Entity Classes Stored Here |
|-------|--------------------------|
| **M0** (Task) | Task (#30), Proof (#24) — gotchas, MEMORY.md entries |
| **M1** (Domain) | ResearchBrief (#21), Brief (#22), Plan (#23), Skill (#26), Workflow (#25), Product (#11), Competitor (#13), Document (#16) |
| **M2** (Cross-domain) | Organization (#1), Team (#2), Role (#3), Agent (#4), Membership (#5), Epic (#28), Feature (#15), Customer (#12), User (#14), Decision (#17), Claim (#19), Evidence (#18) |