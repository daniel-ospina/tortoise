# Memory Filter Schema v1.0

> **Status:** Spec — defines the `memory_filter` field for agent manifests.
> **Canonical for:** `tortoise.agent_provisioning.validate_role()`
> **Based on:** Tortoise Decide analysis (2026-07-20, 151 Points)

## Purpose

The `memory_filter` field extends agent manifests with a **memory scope** — what this agent should remember. No other agent framework (Claude Code, Cursor, Pi, LangGraph, CrewAI) defines memory scope as a first-class manifest field. This is Tortoise's differentiator.

**It is a FLOOR, not a CEILING.** Agents can always call `tortoise_search()` to get more. The filter prevents noise, not access.

## Schema

```yaml
memory_filter:
  episodic:        # optional — session/event memory
    last_n_sessions: <int, 0-50, default 3>
    filter_by_epic: <bool, default true>
  epistemic:       # optional — claims/evidence memory
    min_confidence: <float, 0.0-1.0, default 0.5>
    max_age_days: <int, 1-365, default 30>
    include_kinds: <list[str], default all>
      - decision
      - observation
      - hypothesis
  semantic:        # optional — facts/knowledge memory
    include_decisions: <bool, default true>
    include_plans: <bool, default false>
  procedural:      # optional — skill/workflow memory
    include_workflows: <bool, default true>
  working:         # optional — active context memory
    include_active_epics: <bool, default true>
```

## Default Behavior

When `memory_filter` is **absent or empty** (`{}`): no filtering — the agent gets all memory types with default parameters.

When a **memory type is absent**: that type uses its defaults (as listed above).

## Validation Rules

| Rule | Error Message |
|------|---------------|
| `memory_filter` must be a dict if present | `memory_filter must be a dict` |
| Unknown top-level keys rejected | `Unknown memory_filter key: '<key>'. Valid: episodic, epistemic, semantic, procedural, working` |
| `episodic.last_n_sessions` must be int 0-50 | `episodic.last_n_sessions must be an integer 0-50` |
| `episodic.filter_by_epic` must be bool | `episodic.filter_by_epic must be a boolean` |
| `epistemic.min_confidence` must be float 0.0-1.0 | `epistemic.min_confidence must be a float 0.0-1.0` |
| `epistemic.max_age_days` must be int 1-365 | `epistemic.max_age_days must be an integer 1-365` |
| `epistemic.include_kinds` must be list of valid kinds | `epistemic.include_kinds contains unknown kind: '<kind>'. Valid: decision, observation, hypothesis, statement, plan, goal, vision, strategy, workflow, useCase, userJourney, requirement, target, milestone, incident` |
| `semantic.include_decisions` must be bool | `semantic.include_decisions must be a boolean` |
| `semantic.include_plans` must be bool | `semantic.include_plans must be a boolean` |
| `procedural.include_workflows` must be bool | `procedural.include_workflows must be a boolean` |
| `working.include_active_epics` must be bool | `working.include_active_epics must be a boolean` |

## Example Manifests

### Developer (issue implementation)

```yaml
---
team: app
role: developer
capabilities:
  tools: [read, edit, bash, grep]
  mcp: [supabase, tortoise]
  skills: [issue-scoping, writing-plans, executing-plans]
  memory_filter:
    episodic:
      last_n_sessions: 3
      filter_by_epic: true
    epistemic:
      min_confidence: 0.5
      max_age_days: 30
      include_kinds: [decision, observation]
    semantic:
      include_decisions: true
      include_plans: false
    working:
      include_active_epics: true
---
```

### Researcher (investigation)

```yaml
---
team: org-design
role: researcher
capabilities:
  tools: [read, web_search, web_fetch]
  mcp: [tortoise]
  skills: [research, tortoise-decide]
  memory_filter:
    epistemic:
      min_confidence: 0.3
      max_age_days: 90
      include_kinds: [hypothesis, observation, statement]
    semantic:
      include_decisions: false
      include_plans: false
---
```

### Strategist (decision-making)

```yaml
---
team: org-design
role: strategist
capabilities:
  tools: [read, web_search, web_fetch]
  mcp: [tortoise]
  skills: [define-strategy, tortoise-decide]
  memory_filter:
    epistemic:
      min_confidence: 0.5
      max_age_days: 60
      include_kinds: [decision, hypothesis, strategy, vision]
    semantic:
      include_decisions: true
      include_plans: true
    working:
      include_active_epics: true
---
```

## Known Valid Kinds

These are the registered `pointKind` values in Tortoise's domain loader:

`checkpoint-item`, `decision`, `diary`, `goal`, `hypothesis`, `incident`, `issue`, `jobToBeDone`, `meeting`, `milestone`, `observation`, `plan`, `requirement`, `session`, `statement`, `strategy`, `target`, `useCase`, `userJourney`, `vision`, `workflow`
