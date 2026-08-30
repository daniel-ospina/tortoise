---
title: "Epic Research Brief — #1976: Agent-driven onboarding (shrink wizard, graph-held state, calm overview, ontology-precise seed)"
type: synthesis
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Epic Research Brief — Agent-driven onboarding (#1976)

> **Findings date:** 2026-08-29
> **Status:** draft
> **Research depth:** deep (epic scope) — consolidates TWO research rounds embedded in issue #1976 (2026-08-29), the superseded-epic briefs this epic inherits (#235 hosted onboarding, #529 harness onboarding, #1714 memory-capture), plus TWO targeted gap queries fired this session (W8 builder-catalog precedent; W5 graph-held-state pattern).
> **Dedup + provenance honesty note:** rounds 1-2 of the embedded research were **issue-drafting research never persisted to a source ledger** — the issue body names sources inline (Zylos, NN/g, CMU/NSPE, UserGuiding/ProductLed) with no retrievable citations, and `docs/research/` contains no files for the #1976 onboarding rounds. This brief's `## Raw Notes` is the **first formal persistence** of those rounds (retroactive stamp, not a contemporaneous ledger). The revalidation that HAS been done: the align gate's codebase fact-check (01-align.md baseline section — all codebase claims independently verified) and A0's transferability caveat on the third-party activation stats. **Scheduled revalidation (owned by epic-scope axis research, per research-protocol §0 — inherited claims are never a substitute for fresh queries):** the two load-bearing external claims — (a) "no comparable product uses a post-signup wizard" and (b) the 50%/28% activation/drop-off stats — will be re-queried at epic-scope's Axis Research step before scope converges, since the issue-drafting sources cannot be re-retrieved.

---

## 1. Strategy Context

### Market position (agent-tool onboarding, 2026-08-29 round 1)

- **No comparable product uses a post-signup wizard.** Agent-tool products (mem0, Letta, Claude Code, Devin, Cursor) front with a copy-paste setup command + first-value in the agent's first session. Principle: "onboarding educates the AI" (Zylos; code.claude.com `/init`).
- **Interactive/agent-mediated setup beats static UI:** 50% higher activation; AI-chat onboarding cuts drop-off 28% (UserGuiding/ProductLed stats via Zylos). *Transferability caveat registered in align A0 — these are third-party onboarding stats applied to this product class; medium confidence.*
- **Team-first is the tenant model:** the team/org is the graph's namespace (Slack model); org is the canonical tenant boundary (GitHub orgs, multi-tenant SaaS).
- **Infra products ask deployment/tenant, not intent** (Supabase/Postgres). GitHub deprecated user→org conversion — don't force a fork early or make users migrate.

### Business model implications (round 1 + align)

- **Billing deferred to a threshold** (PLG norm): free now, card at seat/usage/upgrade threshold. Never a wizard step.
- **The fork is a PRESENTATION fork, not a billing gate**: changes what's shown + nudged, never billing/deployment behavior. Nudge-not-force.
- **Launch-readiness framing** (align): pre-revenue; the metric is UX craft (walk-through + friction test + proud test), NOT invented numbers. W11 instruments the funnel (seed_complete + decide_complete) for future iteration, no threshold.

### Migration context (fact-checked in issue)

- **No existing orgs other than the owner's** (danielospina's). Migration collapses to a one-org special case: grandfathered `onboarding_complete`, anchors reconciled `person`→`naturalPerson`, OnboardingState node backfilled when the graph store ships.

## 2. UX Pattern Research

### Agent-driven setup (round 1 + this-session gap query)

- Agent-driven state machines: **agents read persisted state, not chat history** (Google ADK). Human visibility needs a mirrored checklist UI (UserGuiding/OpenAI Hub).
- This session's gap query confirmed the pattern externally: agent-as-state-machine with **database-backed persistent state cursor** is the standard production pattern (channel.tel; LogRocket/n8n; LangChain checkpointers) — "the agent advances its own setup state" is a recognized design, not a novelty. The *graph-as-the-store* specific is Tortoise's own generalization of tenant-keyed architecture (internal rationale; no external product stores onboarding in a knowledge graph — noted as a design bet, mitigated by idempotent-write contracts #398 + versioning).

### Progressive disclosure (round 1)

- **Anything on the Overview is implicitly endorsed as important** (NN/g) — a toggle wall reads as "all of this matters now" → choice-overload abandonment. Overview = connection status + memory digest + next action, ZERO feature toggles.

### Active learning > passive (round 1)

- Active/interactive learning beats passive (CMU/NSPE). **Demo data misleads** (users mistake it for their own) → seed with the user's OWN data.

### Consent & capture (round 1 + #1714 brief)

- **Content capture (not telemetry) default-ON is defensible only with visible controls** (Claude Code auto-memory precedent); Windows Recall is the cautionary tale. Hosted capture raises the bar (M10: proof-of-control via OTP before mismatch-override fusion).
- #1714 brief (2026-08-25) established the **3-tier per-harness capture matrix**: T1 automatic (Pi extensions `session_shutdown`/`agent_end`; Claude Code hooks `SessionEnd`/`Stop` http), T2 post-hoc extractor (Codex JSONL, Cursor JSONL/SQLite — spike needed, Claude Desktop local files), T3 prompt-instructed (Claude Web — the fallback and minimum honest promise). Session-capture disclosure = a named human-visible checkpoint (builds on #1927's default-ON decision; W6 must not re-introduce a gate).

### Invite-accept UX (round 1, W7)

- Slack/Notion/Linear/GitHub flows; bastionary (email-match, 3 mismatch cases); Logto/Stytch (magic-link branch on registration status); drop-off research (2-3 steps, fusion).
- **Three-case email-match model**: match → one-click accept; mismatch same person → explicit 3-path choice (fuse / new account / accept-with-mismatch), NEVER silent; mismatch different person → error + sign-in as invitee.
- **Account fusion** (M10): auth identities (Supabase), memberships UNION, api_keys UNION, graph data stays per-org (no cross-org merge). Privilege-accumulation vector → OTP proof-of-control of the invitee email before the mismatch-override path.
- **Atomic new-user accept**: account + membership created atomically (one action, no "create then accept"); agent setup is an inline skippable first action INSIDE the team (Slack's single-name-field model).

### Onboarding trigger & re-entry (round 1, W9)

- Trigger boundary = **org creation** (or invite-accept), NOT signup. Signup is pre-onboarding (identity + email only, ≤1 screen; R2-8).
- Multi-org: fresh per-org state, compact checklist for product-literate users, never full re-onboarding. Settings re-entry idempotent (Stripe/Linear/Slack pattern — resume, never restart). **5 entry doors → ONE state machine** (AWS "single mechanism" guidance).

## 3. Workflow Pattern Research

### The 5 human steps (W1 — product-owner confirmed)

1. **Orientation** (overview-first): one screen "here's what's about to happen" — sets the scope, tells the user the agent does most of it.
2. **Create or join the Organization**: name REQUIRED with editable prefill (never silent username — the name is the memory's public namespace); OR accept an invite (invite-channel-driven). Org boundary = trigger boundary.
3. **Fork card** (once, per org, after org-create, BEFORE agent flow arms): "use it for your own agents" vs "build an application on top". Presentation fork; changes seed-step + capability-catalog presentation.
4. **Connect-consent handoff**: one universal setup command (or agent self-adjudicates harness); human's only act = run/paste the command. Consent = OAuth/API-key only; no feature consent.
5. **Post-connect**: agent takes over (install → seed → decide). Human steps done.

### The agent flow (W2/W3)

- Agent reads install instructions + **self-adjudicates its own harness** (ADK precedent): 4 self-installable (Claude Code, Cursor, Codex, Pi — `claude mcp add --transport http` / config write), 2 manual/teach-human (Claude Desktop, Claude Web). Handoff = a universal command, not a per-harness UI screen. (Open question b: validate with real harnesses.)
- **Seed = two Subjects** (ontology-corrected, B1): Organization (Subject, subclass `organization`) + User (Subject, subclass `naturalPerson`), linked `memberOf` (§3.6 canonical structural edge). NEVER Object/Statement. Anchor data: hosted → pull from API (JWT `user_id`/`email`/`app_metadata`; org from `teams.name`; email-prefix name derivation — `display_name` NOT exposed by `verify_session_jwt`, ask once if unusable); self-hosted → two prompts ("what's your name?" / "what's your organization called?"). Subject id = `name` (MERGE on `{name}`) — typo'd org name fragments the anchor.
- **Nudge → `tortoise-decide`** = the magic moment: one real decision (options → criteria → findings → IMPL/NAND wiring → EP ranking). The two-session aha: anchors filed (session 1) → graph defends a decision (session 2).

### Completion semantics (R2-4/B2, supersedes earlier)

- Terminal = **two Subjects filed + one `tortoise-decide` completed + first agent connected**. Completion = first successful task, never dismissal. First-member-invited = secondary non-blocking milestone.

### Per-harness install surface (W2 — #529 brief + this-session verification)

- 6 harnesses (harnesses.js `HARNESS_NAMES`): Claude Code, Claude Desktop, Claude Web, Codex, Cursor, Pi. No universal command exists today (per-harness copy blocks) — W2 builds it.
- #529 brief (2026-08-11) verified per-harness paste surfaces + config-file mechanics (`.cursor/mcp.json` literal-key caveat, CLAUDE.md/AGENTS.md instruction surfaces, Pi JSON + contained prompt).

## 4. Tech Stack Research

### Current onboarding surfaces (ground-truth, verified this session)

| Surface | Location | State |
|---------|----------|-------|
| Wizard (5-step, #1643) | Dashboard (website/apps/dashboard) | **This epic shrinks/archives** — harness chooser, skills primer, GitHub connect, STATE seed, done |
| Onboarding prompt (single source) | `tortoise/onboarding/AGENT_ONBOARDING.md` (deployed via #540) | W2's SKILL.md is its SUCCESSOR — archive old + deployed copy, never two live scripts (M8) |
| Harness copy blocks | `website/apps/dashboard/src/harnesses.js` (HARNESS_NAMES, per-harness config) | 6 harnesses; per-harness copy; no universal command |
| Onboarding state (current store) | Supabase `teams.onboarding_state` jsonb (`supabase_control.py` `team_onboarding_state`/`update_onboarding_state`) + `hosted_api.py` `_get/_update_onboarding_state` (registered-keys allowlist) | W5/W9 SUPERSEDE → graph-held `OnboardingState` node (endpoint stays as read surface) |
| Onboarding endpoints | `/v1/onboarding/state` + `tortoise_onboarding_*` MCP tools (tool_registry.py group "onboarding") | Keep read surface; change store |
| Invite infra | `/v1/invites`, `/v1/invites/info`, `/v1/invites/accept`, `/v1/invites/pending`, `/v1/invites/pending/{id}/accept`, `DELETE /v1/invites/pending/{id}` (hosted_api.py) | W7 BUILDS ON these (fusion, atomic accept, pending affordance) — do NOT re-create (M6) |
| Tool registry | `tortoise/tool_registry.py` (ToolAnnotations, curation groups) | W8 EXTENDS this for the pullable catalog (R2-9) |
| Telemetry | `tortoise/analytics.py` — `tenant_provisioned`, `api_key_created`, `first_api_call` only | W11 adds `onboarding_seed_complete` + `onboarding_decide_complete` (M9 — real gap, verified) |
| Self-hosted API | `tortoise/selfhost_api.py` — **zero onboarding code** (verified) | W12 owns the self-hosted onboarding path |
| Session capture | `POST /v1/sessions` (hosted), Pi reflect-hook + tortoise-capture extensions, T1/T2/T3 harness matrix (#1714) | W6 builds view/delete (`DELETE /v1/sessions/{id}`) — self-use path only |
| Auth (hosted) | `session_auth.py` `verify_session_jwt` → `{user_id, email, app_metadata}` (no display_name — verified) | W3 anchor data source |
| Graph ontology | `docs/ONTOLOGY.md` §3.6 (Subject subclasses, memberOf), §5 (controlled vocab) | W3 seed must be ontology-precise; `subjectKind` is free-string today (normalize in seed path, M5b) |

### Architecture patterns (W5)

- **Graph-held state**: `OnboardingState` node per org, initialized inside the org-create transaction (no lazy-init races), idempotent writes (#398 contract), small + serializable, versioned when the flow changes, agent derives current step from completed-step edges (not a mutable cursor). The product's own store holds its own onboarding state — strict generalization of tenant-keyed architecture.
- **Migration** (M5): existing orgs grandfathered (no re-onboarding); seed MERGEs `person`→`naturalPerson`; backfill node from legacy jsonb; existing-user migration is a one-org special case.

## 5. Assumptions Register

| Assumption | Confidence | Source | Validation Plan |
|-----------|-----------|--------|-----------------|
| **A0 — current wizard friction is real & representative** | medium | 1 observation + market pattern; stats transferability caveated | W11 events + walk-through reviews on first cohort; if low friction, rebuild was premature |
| "Agent knows its own harness" holds for 4 CLI harnesses; 2 manual fall to teach-human | medium | ADK; harnesses.js; open question (b) | Test with real harnesses (W2) |
| Graph is the right store for onboarding state | medium | ADK; agent-state-machine external pattern; #398 idempotent contracts | W5 thin vertical slice ships with W1/W2; idempotency regression tests |
| Fork is a presentation fork, never a billing gate | high | 2 consistency passes + research (infra products ask deployment not intent) | Walk-through review of fork card |
| Org boundary = trigger boundary | high | Multi-tenant SaaS norms; Slack/Linear/Stripe patterns | W9 E2E: org-create arms onboarding, signup doesn't |
| No existing orgs other than owner's → one-org migration | high | Fact-checked in issue | W5 backfill test on owner org |
| Team-name-required with editable prefill beats auto-derive | medium | Thin external evidence; user preference; open question (a) | Usability test in W1 walk-through |
| Universal command works across 6 harnesses | medium | Per-harness copy exists; no unified command today; open question (b/c) | W2 E2E across 4 self-install + 2 manual paths |
| Capture default-ON defensible with visible controls | high | Claude Code precedent; Windows Recall cautionary; #1927 merged | W6 disclosure walk-through (no re-gate) |
| Builder capability catalog presented once on the build path (pullable registry) | medium | Gap-query negative: no strong external precedent; extends tool_registry (R2-9) | W8 build-path walk-through + registry-endpoint E2E |
| Agent state-machine + DB-backed state cursor is standard | high | This-session external queries (channel.tel, LogRocket, LangChain) | n/a — pattern confirmed |

## Raw Notes

> Append-only evidence ledger (research-protocol §13). Entries timestamped + source-tagged.

- `[2026-08-29 — issue #1976 embedded research round 1]` Agent-tool products front with copy-paste setup commands; no post-signup wizards. Interactive setup: +50% activation, −28% drop-off (UserGuiding/ProductLed via Zylos). Content capture default-ON defensible with visible controls (Claude Code auto-memory; Windows Recall cautionary). Progressive disclosure (NN/g): Overview = implicit endorsement. Active learning > passive (CMU/NSPE); demo data misleads. Infra products ask deployment/tenant (Supabase/Postgres); GitHub user→org conversion deprecation. Team-first (Slack); billing deferred to threshold. Agent-driven state machines (Google ADK); mirrored checklist UI (UserGuiding/OpenAI Hub).
- `[2026-08-29 — issue #1976 embedded research round 2 / invite-accept]` Slack/Notion/Linear/GitHub invite flows; bastionary email-match 3-case model; Logto/Stytch registration-status branch; drop-off research (2-3 steps, fusion); fork = presentation fork (nudge-not-force); builder capability catalog (indexers+extractors); atomic account+membership; OTP proof-of-control for fusion (privilege-accumulation vector).
- `[2026-08-29 — gap query: builder capability catalog precedent]` Search returned no strong external precedent for presenting a one-time "here's what you can build with" module catalog in builder onboarding — nearest analogs are Supabase docs' always-available feature surface and register-once-then-expose connector models. **Conclusion: W8's catalog is a product-design decision, not a pattern to copy; the pullable registry (tool_registry extension) is the honest implementation.**
- `[2026-08-29 — gap query: graph-held onboarding state]` Confirmed externally: agent-as-state-machine with database-backed persistent state cursor is the standard production pattern (channel.tel "Your Agent Is Already a State Machine"; LogRocket/n8n deterministic state machines; LangChain checkpointer handoffs). The graph-as-store specific is Tortoise's generalization — no external product stores onboarding in a knowledge graph; noted as a design bet mitigated by #398 idempotency + versioning.
- `[2026-08-29 — codebase verification (align + this brief)]` 6 harnesses (harnesses.js HARNESS_NAMES) ✅; invite endpoints all exist (hosted_api.py) ✅; analytics.py has only 3 events (W11 gap real) ✅; tool_registry.py exists ✅; onboarding state is Supabase jsonb ✅; verify_session_jwt returns {user_id, email, app_metadata} only ✅; AGENT_ONBOARDING.md live single-source ✅; selfhost_api.py zero onboarding code (W12 gap real) ✅; teams + team_memberships tables exist ✅; wizard #1643 = 5-step in-dashboard ✅.
- `[2026-08-25 — #1714 brief: per-harness capture matrix]` T1 automatic (Pi extensions session_shutdown/agent_end; Claude Code hooks SessionEnd/Stop http), T2 post-hoc (Codex JSONL, Cursor JSONL/SQLite spike, Claude Desktop local), T3 instructed (Claude Web). Pi reflect-hook local leg live; hosted 2xx leg unproven at time of brief. GitHub ingestion lifecycle-aware gap (content-hash dedup only; supersede + status projection needed). Parity: GitHub connect/index hosted-only; corpus/transcript stdio-only.
- `[2026-08-11 — #529 brief: harness paste surfaces]` Per-harness config mechanics verified: Claude Code `claude mcp add` CLI; Codex shell export + CLI; Cursor `.cursor/mcp.json` literal-key caveat + `.cursor/rules/` instruction surface; Pi JSON + contained prompt; CLAUDE.md/AGENTS.md persistent instruction alternatives.
- `[2026-08-07 — #235 brief: hosted onboarding journey]` Market position: hosted platform = monetization path; onboarding = conversion lever; time-to-first-memory = churn determinant. MCP onboarding patterns; self-hosted→hosted mapping; yes/no question design; agent-side workflow execution.

<!-- research-path: docs/epics/2026-08-07-hosted-onboarding-235/01-research-brief.md + docs/epics/2026-08-07-harness-onboarding-529/02-research-brief.md + docs/research/1714-memory-capture-onboarding.md + issue #1976 body (2 rounds, 2026-08-29) -->
