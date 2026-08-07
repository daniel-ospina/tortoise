---
title: "Epic Scope — Hosted Onboarding Journey (#235)"
type: engineering
domain: platform
doc_status: draft
subjects.team: organisation-design-team
created: 2026-08-07
---

# Epic Scope — Hosted Onboarding Journey (#235)

**Date:** 2026-08-07
**Status:** draft (awaiting human approval)
**Inputs:** Align Decision (PROCEED, Phase 1 only) + Research Brief (15 assumptions, 6 sections)

---

## 1. Scope Boundaries

### In Scope (Phase 1 — Design)

1. **Research document** — Mined self-hosted onboarding flow (@ `__main__.py:_cmd_onboard`, `_cmd_setup`, `_cmd_init`), mapped hosted equivalents, identified gaps. (✅ Complete — `01-research-brief.md`)

2. **One-artifact trigger design** — Decision on what the user copies/pastes to start onboarding. Prototype: MCP config block + onboarding prompt block, presented as a single block on the welcome page. Validate across Claude Code, Codex, Cursor.

3. **Yes/no question set design** — Finalize the questions (GitHub connect, index, sessions, demo, team-teaser, verification): 5 yes/no (Q1–Q5) + 1 free-text org prompt (Q1a) + final Verification step (Q6). Q5 (team) is a "coming soon" teaser only — no tool, no endpoint. Define what each "yes" executes and what each "no" skips.

4. **Welcome page flow design** — Wireframe/prototype the updated welcome page that presents the one-artifact and tracks onboarding progress. Includes post-onboarding state ("You're all set! Here's what Tortoise remembers").

5. **Agent-side onboarding prompt design** — Write the markdown prompt block that the agent follows to execute the yes/no flow. Includes: conversation script, MCP tool calls, error handling, and verification steps.

6. **API gap analysis** — Document all new API endpoints needed (key provisioning, GitHub OAuth callback, indexing trigger, demo graph, onboarding state). NOT building them — just enumerating.

7. **Funnel analytics design** — Define the events to track (signup → paste config → first yes → first memory → retention). NOT implementing — just defining the schema.

### In Scope (Phase 2 — Build, gated)

> Phase 2 is gated on user signal (≥5 signups/week or committed acquisition push). These are listed for boundary clarity but are NOT part of this scoping session's deliverables.

- Self-service key provisioning endpoint
- GitHub OAuth app + connect flow
- Background issue/PR indexing via GitHub API
- Demo graph verification (the demo is auto-seeded at signup via the existing tenant-provision edge function → `/internal/demo`, sentinel-idempotent; the flow verifies and shows it — it does not create/overwrite)
- Onboarding state tracking (API)
- Welcome page v2 (dynamic, with onboarding progress)
- Agent onboarding prompt deployment
- Funnel analytics implementation

### Out of Scope

| Item | Reason | Defer to |
|------|--------|----------|
| Model selection UI/questions | Hosted doesn't expose model choices | N/A (self-hosted only) |
| Docker/FalkorDB setup | Hosted manages infrastructure | N/A |
| Role-based memory_filter config (`tortoise setup`) | Hosted simplifies to one default config | Future epic (advanced users) |
| Harness-specific setup instructions (Pi/Claude/Codex/Cursor) | One artifact should work across all | Future epic (harness-specific optimizations) |
| OAuth-based MCP authentication | Bearer `tt_` keys sufficient for v1 | Future epic (security hardening) |
| Progressive tool disclosure (hiding the full tool surface) | Onboarding guides first 3-5 tools, but all tools remain accessible | Future epic (tool namespaces/discovery) |
| Multi-team onboarding | Single-team flow only for v1 | Future epic (team management) |
| In-app dashboard onboarding wizard | Welcome page is the surface for v1 | Future epic (dashboard UX) |
| White-glove onboarding automation | White-glove is manual for early users, not automated | Future epic (CRM/onboarding automation) |
| Pricing/payment flow | No paid tier exists yet | Future epic (monetization) |

### Boundary Rationale

**The guiding principle:** Phase 1 produces DESIGN artifacts only — no code, no endpoints, no deployed changes. This is a design-heavy epic. The research brief already validates that the self-hosted flow maps to hosted, that the 6-question set is plausible, and that the one-artifact needs prototyping. Phase 1 delivers the blueprint; Phase 2 builds it (and Phase 2 is gated on user signal).

**What makes Phase 1 "done":** A complete design package that a developer can pick up and implement. Includes: research brief, trigger design, question set, welcome page wireframe, agent prompt, API gap list, analytics schema.

**Why Phase 2 is gated:** Research found LOW confidence on user volume (A3), paste-trigger reliability (A1/A2), and question-set fitness (A14). Building before validating these assumptions risks building the wrong thing. The gate ensures we have ≥5 real users to test against before committing engineering effort.

---

## 2. Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | **medium** | The yes/no flow + one-artifact trigger + welcome page redesign involve multiple interaction surfaces. Agent-driven workflow UX is novel (no established patterns). However, the flow is linear (5 yes/no + 1 free-text org + 1 Verification step) and the design is bounded to one page + one prompt. Prototype complexity is low; polish complexity is medium. |
| Architecture | **high** | Phase 2 build touches: API (key provisioning, indexing, demo endpoints), MCP server (new onboarding tool), GitHub OAuth (external integration), welcome page (frontend), agent prompt (distributed config), analytics pipeline, and onboarding state tracking (new schema). Multiple external dependencies (GitHub API rate limits, OAuth app registration). Phase 1 design is low-architecture (documents only). |
| Ontology | **low** | No new entity classes needed. Onboarding state may add an `onboarding_status` property on Team or User entities, but no new graph node types. Existing Point, Operator, Team, and APIKey entities cover the domain. |
| Accessibility | **low** | Welcome page is static HTML (already accessible). Agent prompt is plain text. No complex UI components, no forms beyond OAuth redirect. MCP tools are text-in/text-out. |

---

## 3. High-Level E2E Test Cases

> Written BEFORE user journeys. These define what must be true at the system level, not how to achieve it.

### E2E-1: New user signup → receive API key
**Given:** Tortoise hosted platform is live at api.premiselabs.co
**When:** A new user completes signup (email or GitHub OAuth)
**Then:** The system provisions an API key with prefix `tt_` scoped to a default team
**And:** The user is redirected to a welcome page that displays the key and the one-artifact block
**And:** The key can authenticate against `GET /mcp` returning transport metadata

### E2E-2: Paste one-artifact → agent connects
**Given:** User has API key from E2E-1 and is on the welcome page
**When:** User copies the one-artifact block (MCP config + onboarding prompt) and pastes it into their agent (Claude Code, Codex, or Cursor)
**Then:** The agent successfully connects to api.premiselabs.co/mcp using the Bearer key
**And:** The agent acknowledges it has Tortoise tools (lists at least `tortoise_health`, `tortoise_create_point`)
**And:** The agent begins executing the onboarding prompt (asks the first yes/no question)

### E2E-3: Yes/no flow → GitHub connected
**Given:** Agent is executing the onboarding prompt (E2E-2 state)
**When:** User answers "yes" to "Connect GitHub?" and provides their GitHub org/username
**Then:** The system initiates GitHub OAuth and returns an auth_url
**And:** The user authorizes in the browser and confirms in chat; the agent **awaits authorization** by polling the GitHub status endpoint (3-min timeout) before moving to the next question
**And:** On successful authorization, the system records `github_connected: true` in onboarding state
**And:** The agent confirms: "GitHub connected — found N repositories."
**And:** If authorization times out, the agent records `github_connected: false, github_error: "oauth not completed"` and continues with the remaining questions

### E2E-4: Yes/no flow → indexing → first memory written
**Given:** GitHub is connected (E2E-3 state)
**When:** User answers "yes" to "Index your GitHub issues and PRs into memory?"
**Then:** The system begins background indexing of issues/PRs from authorized repos
**And:** Within the session, at least one Point is created from indexed content
**And:** The agent can call `tortoise_query(kind="observation")` and see indexed content

### E2E-5: Yes/no flow → demo graph shown
**Given:** Agent is executing the onboarding prompt, at least one data source connected
**When:** User answers "yes" to "See the demo graph?"
**Then:** The agent verifies the signup-seeded demo graph (respecting the `_demo_sentinel`; backfills only if missing — never deletes-and-overwrites)
**And:** The agent calls `tortoise_summarize_structure()` and reports graph statistics (N points, M operators, incl. supports/contradicts/mitigates)
**And:** The agent explains what the demo graph shows ("You have 3 decisions connected by evidence...")

### E2E-6: Yes/no flow → session recording enabled
**Given:** Agent is executing the onboarding prompt
**When:** User answers "yes" to "Record agent sessions automatically?"
**Then:** The system enables session recording for the team
**And:** The agent confirms: "Session recording enabled — future conversations will be filed as memory."
**And:** The onboarding state records `session_recording: true`

### E2E-7: Onboarding complete → memory digest shown
**Given:** All yes/no questions answered (user may have said "no" to some)
**When:** The agent reaches the final Verification step of the onboarding prompt
**Then:** The agent calls `tortoise_health` and `tortoise_context` (MCP tool wrapping `GET /v1/context`)
**And:** A memory digest is displayed showing what Tortoise now remembers (with a first-memory welcome Point auto-created if the graph is empty)
**And:** The agent reports: "Onboarding complete. Tortoise remembers [summary]."
**And:** Funnel event `onboarding_complete` is tracked with elapsed time < 5 min — fired server-side once, when `tortoise_onboarding_complete` sets `completed_at`

### E2E-8: User says "no" to everything → minimal setup
**Given:** Agent is executing the onboarding prompt
**When:** User answers "no" to all 5 yes/no questions
**Then:** The agent still verifies the connection (`tortoise_health` returns OK)
**And:** Every answer is recorded via `tortoise_onboarding_answer` (no answers are never silently dropped)
**And:** The agent reports: "Tortoise is connected. You can create your first memory with tortoise_create_point()." (a welcome Point is auto-created as the first memory on empty graphs)
**And:** Onboarding state records all steps as `false`/`skipped` with per-question answers
**And:** The user can still use all HTTP-visible MCP tools (64 on the streamable-http surface; 4 privilege-bound tools remain excluded)

---

## 4. Human Approval Gate

## Epic Scope Ready for Review

**Scope:** Phase 1 — Design only (7 design artifacts). Phase 2 — Build (8 components, gated on ≥5 signups/week). Out of scope: model selection, Docker setup, role config, OAuth, tool disclosure, multi-team, dashboard wizard, pricing.

**E2E test cases:** 8 drafted (E2E-1 through E2E-8), covering: signup→key, paste→connect, GitHub connect, indexing→first memory, demo graph, session recording, completion→digest, and "no to everything" minimal path.

**Complexity:** UX=medium, Architecture=high (Phase 2), Ontology=low, Accessibility=low

Review the scope boundaries and E2E test cases.
Reply "proceed" to continue to detailed planning, or give feedback.
