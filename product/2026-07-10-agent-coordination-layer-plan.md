# Epic Plan: Agent Coordination Layer

**Created:** 2026-07-10
**Status:** Planning (Substep 4: Data Model)
**Epic pipeline:** Align ✅ → Research ✅ → Scope ✅ → Plan (in progress)
**Complexity:** Architecture: Very High | Ontology: Medium | UX: Low | Accessibility: Low

---

## Pre-Plan Research

### Agent Boundary Patterns
Three-tier boundary system (Always/Never/Ask) is the industry standard for agent specs (Addy Osmani, 2025). Our "boundary: MUST NOT" maps to the "Never" tier. "Always" and "Ask" tiers deferred to Phase 2 when agent autonomy increases.

### Coordinator State Machine Patterns
Hybrid detection (graph cycle + timeout) is the production pattern. Our approach mirrors this: stuck detection = timeout-based, deadlock breaker = graph-based "all stuck" detection. Industry standard: `lock_timeout` at 2-3× average execution time, escalation via log + notification.

### Slack Single-Bot Multi-Agent Identity
`username` field in `chat.postMessage` API provides dynamic identity per message. Phase 1: single Slack app with one bot token, routing all agents internally. Each agent role gets a distinct `username` override — non-spoofable, 1 app slot used.

### Health Endpoint Patterns
Distinct `/health/live` (process liveness) and `/health/ready` (dependency check) prevent false crash detection. Bridge `/status` endpoint should follow this pattern. SIGTERM handling: keep returning healthy during graceful shutdown to avoid false positives.

### GitHub Projects v2 API
Two-step mutation: `addProjectV2ItemById` → `updateProjectV2ItemFieldValue`. Custom fields must be pre-created in UI. Field IDs obtained via query. This is the mechanism for Kanban auto-population.

---

## UX Design Decisions

**SKIPPED** — UX rating is Low. No new UI surfaces. Agents communicate via existing Slack patterns. Kanban is existing GitHub Projects.

---

## 3. Scope

**In scope:** S1-S10 (see scope document)
**Out of scope:** O1-O13
**E2E tests:** 19 high-level test cases

---

## 1. User Journeys

### Agent State Model

Flat state enumeration (no compound/substates):

```
idle ──→ working ──→ idle (completion)
idle ──→ working ──→ blocked ──→ working (unblocked)
idle ──→ working ──→ paused ──→ working (gate approved)
                                     ──→ idle (gate rejected)
idle ──→ working ──→ crashed (Pi process dies)

Note: `paused` = agent is actively in a session, paused and awaiting human input (e.g., PR review, plan approval gate, scope sign-off). Initiatives awaiting human approval BEFORE any agent spawns use `pending_approval` (initiative-level, not agent state). When an initiative is approved, the coordinator @mentions the relevant agent, which spawns fresh (not from `paused`).
```

**Crash override rule:** Crash detection overrides all other states. If an agent crashes, all its in-flight issues are marked unassigned, pending stuck-detection timers are cancelled, and the coordinator's reassignment logic takes precedence over stuck-detection escalation.

**Component separation:** Bridge responsibilities are split into:
- **SlackRouter:** @mention routing, channel posting, message attribution
- **HealthMonitor:** Agent process health polling, writes `/status`, sole writer of session state
- **Coordinator:** Reads GitHub + `/status`, triggers escalations, does NOT spawn agents — it posts Slack messages; agents spawn independently when their Slack-event trigger fires on coordinator messages

### Personas

| Persona | Role | Slack presence | Goals |
|---------|------|---------------|-------|
| **H1: Team Human (Overseer)** | Resp: watches team channel, receives escalations, approves plans, kills stuck projects | Watches `#team-*` channel, responds to @channel/@mentions | Know what agents are doing without micromanaging. Only intervene when necessary. |
| **A1: Coordinator Agent** | Daemon. One per team. Reads GitHub + `/status`. Posts Slack, escalates. Does NOT spawn agents. | Posts as `@coordinator` via single Agent app | Detect stuck work, crashed agents, empty queues. |
| **A2: Strategist Template** | Trigger-based. Reusable template — each team instantiates their own strategist. | Posts as `@strategist` via single Agent app | Generate initiatives aligned with team strategy. |
| **A3: Product Manager Template** | Trigger-based. Reusable template. Team-specific instance. | Posts as `@pm` via single Agent app | Scope product initiatives into ready-to-implement issues. |
| **A4: CMO Template** | Trigger-based. Reusable template. Team-specific instance. | Posts as `@cmo` via single Agent app | Create growth plans and content briefs. |
| **A5: Product Implementer Template** | Trigger-based. Reusable template. Team-specific instance. | Posts as `@implementer` via single Agent app | Implement issues, pass review, ship. |
| **A6: Growth Implementer Template** | Trigger-based. Reusable template. Team-specific instance. | Posts as `@growth` via single Agent app | Execute content pipeline, pass review, publish. |

### Journey Maps

#### UJ-1: Human Watches Team Work (Happy Path)

**Entry:** Human opens `#team-app` Slack channel in the morning.
**Path:**
1. Human sees thread from last night: Strategist posted "📋 Proposed initiative: Expand to `domain:product`"
2. PM agent spawned (triggered by @mention from coordinator) → scoped → created 3 child issues → @mentioned Product Implementer
3. Product Implementer spawned → executed issue #456 → opened PR → @mentioned human for review
4. Human reviews PR, approves, merges
5. Implementer posts "✅ #456 merged" in thread
6. Coordinator's next cycle: no stuck work found, logs silence
7. Human can follow the entire thread chronologically without intervention

**Exit:** Human has full visibility of all agent decisions and work progress.

**Edge cases:**
- Human is AFK for 8h → coordinator's cron cycle runs, stuck detection fires after 4h of no activity
- Human returns → full thread history available, no state lost
- Multiple humans in channel → all see same messages

#### UJ-2: Human Receives Stuck Work Escalation

**Entry:** Product Implementer started issue #789 but got blocked (dependency not merged).
**Path:**
1. Implementer posts "⏸️ Blocked: waiting for PR #123 to merge" → state transitions to `blocked`
2. No activity on #789 for STUCK_DETECTION_SECONDS (4h)
3. Coordinator's cron cycle detects #789 has no activity >4h, state = `blocked` (not `paused`) → pings
4. Coordinator posts in `#team-app`: "⚠️ Stuck: #789 (No activity for 4h) — @implementer status?"
5. Still no resolution → 2nd ping at T+8h, 3rd ping at T+12h
6. After 3 failed pings → @channel escalation: "🚨 #789 stuck >16h"
7. Human sees @channel notification → investigates → posts: "PR #123 merged" → state → `working`
8. Implementer resumes work

**Crash override (precedence over stuck detection):** If the implementer crashes mid-blocked-state:
- HealthMonitor detects crash → marks state `crashed` in /status
- All pending stuck-detection timers for this agent's issues are cancelled
- Coordinator's next cycle detects `crashed` → reassigns #789 to next idle implementer (see UJ-3)

**Deadlock strategy trigger timing:** DEADLOCK_SECONDS (24h+) > 3×STUCK_DETECTION_SECONDS (12h+). Strategist deadlock breaker only fires if ALL issues stuck >DEADLOCK_SECONDS AND no human has posted in the team channel since the last @channel escalation.

**Exit:** Human intervened after automated escalation, work resumed.

**Edge cases:**
- Issue is in `paused` state → coordinator excludes from stuck detection entirely
- Multiple stuck issues → coordinator posts one message listing all
- Coordinator crash mid-escalation → restart resets ping counter (cold restart, O11), but stuck detection re-detects within STUCK_DETECTION_SECONDS
- Terminal condition: after @channel at T+16h, re-escalate every 24h. After 7 days with no human response → auto-close as stale with notice

#### UJ-3: Agent Receives Work via @Mention Dispatch

**Entry:** PM agent finishes scoping → creates 3 child issues → needs Product Implementer.
**Path:**
1. PM posts in `#team-app` thread: "@implementer Please implement #456, #457, #458 — scoped and ready"
2. SlackRouter detects `@implementer` mention → routes to Implementer agent trigger
3. Implementer spawns (if `idle`) or queues (if `working`/`blocked`)
4. Implementer picks #456 → executes via issue-workflow → posts progress in thread
5. If implementer crashes mid-execution:
   - HealthMonitor detects crash → marks state `crashed` in /status
   - Coordinator's next cycle detects crashed → reassigns #456-#458 to next idle implementer
   - **Crash override rule:** pending stuck-detection timers for this agent's issues cancelled

**Exit:** Agent received work via @mention trigger, executed, or crash → reassigned.

**Edge cases:**
- Agent is `working`/`blocked` → new @mention queued, picked up after current issue completes
- Agent is `idle` → immediate spawn
- All implementers crashed AND no idle of same domain → coordinator escalates to human: "All [domain] implementers crashed, #456-#458 unassigned" (no dead-letter to Strategist — single Strategist handles initiative generation, not task reassignment)

#### UJ-4: Strategist → Domain Strategist → Implementer Chain

**Entry:** Team has no active work. Coordinator detects empty queue.
**Path:**
1. Coordinator posts: "@strategist: No active work — generate initiatives."
2. Strategist's Slack-event trigger fires → spawns → researches market → files 2 epics tagged `domain:product` and `domain:growth`
3. Coordinator's next cycle detects new epics → posts: "📋 New initiative: `domain:product` — @pm scope this"
4. PM's mention trigger fires → spawns → scopes → creates child issues → posts: "@implementer #456"
5. Product Implementer's mention trigger fires → spawns → executes (UJ-3)
6. Simultaneously: coordinator posts: "📋 New initiative: `domain:growth` — @cmo plan this"
7. CMO's mention trigger fires → spawns → creates plan → posts: "@growth content brief for [topic]"
8. Growth Implementer's mention trigger fires → spawns → executes

**Note:** Coordinator NEVER spawns agents directly. It posts Slack messages. Each agent's Slack-event trigger independently detects @mentions and spawns. This maintains the "trigger-based" persona for all non-coordinator agents.

**Exit:** Full chain from empty queue to deliverable. No human intervention needed.

**Edge cases:**
- Strategist generates low-quality initiative → PM's scoping review gate catches it
- All issues are stuck but not deadlocked → coordinator does NOT trigger Strategist
- ALL issues stuck >DEADLOCK_SECONDS, no human posted since @channel → strategist fired anyway (deadlock breaker)

#### UJ-5: Human Views Kanban Board

**Entry:** Human wants team status at a glance. Opens GitHub Projects.
**Path:**
1. Human navigates to `github.com/eldato-io/eldato/projects` → selects `#team-app` board
2. Board: To Do / In Progress / Review / Done
3. Cards = GitHub issues, auto-populated on issue events
4. Filter by Role → shows PM + Implementer issues
5. Filter by Domain → shows CMO + Growth Implementer issues
6. Click card → opens GitHub issue with linked Slack thread

**Exit:** At-a-glance status without reading Slack threads.

**Edge cases:**
- Issue not yet on board → < 30s lag on GitHub workflow trigger
- Custom field missing → shows "No Role" / "No Domain"
- Empty board → agents have no work (Strategist should fire next cycle)

#### UJ-6: Human Kills Stuck Project

**Entry:** Human reviews Kanban, sees initiative with 3 stuck issues, all blocked >1 week. Decides the initiative is no longer worth pursuing.
**Path:**
1. Human posts in `#team-app`: "@coordinator kill initiative 'Expand to new categories' — not worth pursuing"
2. Coordinator detects @mention → queries GitHub for all issues under that initiative (epic + child issues)
3. Coordinator closes all related issues with comment: "💀 Killed by human: initiative no longer pursued"
4. Coordinator posts summary: "💀 Initiative killed: 'Expand to new categories' — 3 issues closed, 0 in flight"
5. If any issue was in `working`/`blocked` state with an active agent session → coordinator @mentions the agent in thread: "@implementer Issue #789 killed — stop work" → agent detects and transitions to `idle`
6. Coordinator checks for open PRs linked to killed issues → posts: "1 open PR remains from killed initiative — @human review or close?"
7. Kanban reflects closed issues (move to Done column)

**Exit:** Human proactively terminated stale work, agents cleaned up, Kanban updated.

**Edge cases:**
- No matching initiative found → coordinator posts: "No initiative matching [name] found — check spelling?"
- Initiative partially completed (some child issues merged) → coordinator only closes OPEN issues, merged ones stay

#### UJ-7: Human Approves/Rejects Plan Before Work Begins

**Entry:** Strategist generated a new initiative. Human wants to gate it before agents start scoping.
**Path (approve):**
1. Strategist posts: "📋 Proposed initiative: [name] — awaiting human approval" (initiative: `pending_approval` — no agent in `paused`; Strategist completed and is `idle`)
2. Human reads the initiative in thread → reacts with ✅ or comments "Approved, proceed"
3. Coordinator cron cycle detects approval (emoji reaction or "Approved" keyword in thread) → posts: "@pm scope [initiative]" AND "@strategist Initiative [name] approved — file the epic"
4. Strategist fires on @mention → files the epic as a GitHub issue
5. PM fires on @mention → begins scoping

**Path (reject):**
1. Strategist posts proposal
2. Human comments: "Rejected — [reason]"
3. Coordinator cycle detects rejection ("Rejected" keyword) → posts: "❌ Initiative rejected: [reason]" AND "@strategist Initiative [name] rejected — closing"
4. Strategist fires on @mention → closes epic with rejected label → returns to `idle`

**Exit:** Human gates initiative before agent hours are spent on it.

**Edge cases:**
- Human doesn't respond for 24h → coordinator pings human directly (not @channel): "Awaiting approval on [initiative]"
- 72h no response → @channel escalation
- 168h (1 week) no response → auto-close as stale with notice
- Human approves but then changes mind → must explicitly kill (UJ-6) — approval is a one-way gate
- Coordinator detects human response via cron cycle scanning thread for "Approved"/❌ or ✅ reaction (emoji detection)

---

## 2. Workflows

### Coordinator Execution Model

The coordinator runs as a **long-running daemon process** (not cron). It uses the loop enforcer's `loop_type: continuous` with sleep-based pacing. Heartbeat file is written at cycle start with a lock file (`.coordinator.lock`) using `flock` to prevent concurrent instances. Heartbeat is refreshed every HEARTBEAT_INTERVAL_SECONDS via a separate timer thread.

### Coordinator State Schema

All coordinator state is persisted in a single JSON file (`coordinator-state.json`):

```json
{
  "issues": {
    "<issue_number>": {
      "ping_stage": 0,
      "last_pinged_at": "2026-07-10T14:00:00Z",
      "last_activity_at": "2026-07-10T13:55:00Z",
      "assigned_agent": "implementer-product",
      "state": "stuck"
    }
  },
  "dispatched_epics": ["6210", "6211"],
  "pending_approvals": [
    {
      "initiative_id": "epic-123",
      "title": "Expand to new categories",
      "proposed_by": "strategist",
      "proposed_at": "2026-07-10T14:00:00Z",
      "status": "pending"
    }
  ],
  "orphaned_issues": [
    {
      "issue_number": "789",
      "domains": ["product"],
      "orphaned_at": "2026-07-10T15:00:00Z",
      "status": "reassignment_failed"
    }
  ],
  "message_queue": {
    "implementer-product": [
      {"message": "@implementer #789", "queued_at": "2026-07-10T14:05:00Z", "from": "pm"}
    ]
  },
  "config": {
    "github_owner": "eldato-io",
    "github_repo": "eldato",
    "subjects_yaml_path": "operations/subjects/"
  }
}
```

**First-run bootstrap:** If coordinator-state.json doesn't exist, initialize with empty schema: `{"issues": {}, "dispatched_epics": [], "pending_approvals": [], "orphaned_issues": [], "message_queue": {}, "config": {...}}`.

**Single source of truth:** Agent state lives in bridge `/status` ONLY. The `/status` response includes: `{agent_role, state, last_activity_iso, diagnostic{}, announced_epics: [123, 456]}`. The coordinator-state.json is the coordinator's persistence layer (issues, approvals, queues, config) — NOT a copy of agent state. Agents NEVER write to coordinator-state.json. Strategist writes `announced_epics` to `/status` (via bridge endpoint).

### Activity Detection Efficiency

Instead of 3N API calls per cycle, the coordinator uses:
1. **GitHub bulk query:** GET `/repos/{owner}/{repo}/issues?state=open&labels=in_progress&since={last_cycle_start}&sort=updated&direction=asc` — returns all issues updated since last cycle. Issues NOT in response have no GitHub activity.
2. **Slack batch query:** `conversations.history` with `oldest` filter for the team channel, scanning for agent-authored messages since last cycle. One API call per team, not per issue.
3. **Clock alignment:** All times compared against coordinator's monotonic clock with STUCK_DETECTION_FUDGE_SECONDS (+30s) to absorb NTP drift.

Target: <5 API calls per cycle regardless of issue count.

### WF-1: Coordinator Loop

**Execution:** Continuous daemon, CRON_INTERVAL_SECONDS (default 300s) between cycles.

**Flow:**
1. Acquire file lock (`.coordinator.lock`) via `flock` — abort if lock held (another instance running)
2. Write heartbeat file at cycle START
3. Load coordinator-state.json from disk
4. Query GitHub Issues API (bulk `since` filter)
5. Query bridge GET `/status`
6. Query Slack `conversations.history` (team channel, since last cycle)
7. Merge activity data: any issue with a GitHub update OR Slack message by its assignee → mark `last_activity_at` = now
8. For each tracked issue:
   a. If agent state == `crashed` in /status → trigger reassignment (WF-3)
   b. If issue state == `paused` → skip
   c. If now - last_activity_at > STUCK_DETECTION_SECONDS → trigger escalation (WF-2)
9. Check for unannounced epics with domain tags → post dispatch (WF-4 step 6)
10. If no active issues → trigger Strategist activation (WF-4)
11. Fast-forward escalation for pending_approval items past their thresholds (WF-7)
12. Write updated coordinator-state.json (atomic: temp file → rename)
13. Sleep CRON_INTERVAL_SECONDS, repeat

**Failure modes:**
- GitHub API down → log warning, skip cycle, retain state
- Bridge /status down → retain last known agent states from state file, skip stuck detection
- State file corrupted → delete, start fresh (acceptable: max one-cycle duplicate ping)
- Lock held → abort (another instance protecting)

### WF-2: Stuck Detection & Escalation

**Trigger:** Coordinator detects issue with now - last_activity_at > STUCK_DETECTION_SECONDS, agent state ≠ `paused` or `crashed`

**Flow:**
1. Read issue's `ping_stage` from coordinator-state.json
2. If ping_stage = 0: post "⚠️ Stuck: #{issue} — @{agent} status?" → set ping_stage = 1, last_pinged_at = now
3. If ping_stage = 1 AND now - last_pinged_at > STUCK_DETECTION_SECONDS: post 2nd ping → ping_stage = 2
4. If ping_stage = 2 AND now - last_pinged_at > STUCK_DETECTION_SECONDS: post @channel "🚨 #{issue} stuck >16h" → ping_stage = 3
5. If ping_stage = 3 AND now - last_pinged_at > ESCALATION_SECONDS AND no human response detected → auto-close as stale with notice
6. **Activity-first check:** Before escalating, always check latest activity (WF-1 step 7) — if activity arrived since last cycle, reset ping_stage to 0 instead of escalating
7. If agent responds or activity resumes at any stage → reset ping_stage to 0, update last_activity_at

**Crash override:** If agent crashes → cancel all tracking for this agent's issues (remove from state file), transition to WF-3.

### WF-3: Crash Detection & Reassignment

**Trigger:** HealthMonitor detects agent process death (3 consecutive health check failures → state `crashed`)

**Flow:**
1. HealthMonitor writes `state: "crashed"` to `/status` for that agent_role
2. Coordinator's next cycle detects `crashed` in /status
3. Remove all tracking entries for this agent's issues from coordinator-state.json
4. Query GitHub: `GET /search/issues?q=assignee:{crashed_agent_login}+state:open`
5. Load subjects YAML to determine the crashed agent's `domain` field
6. Query /status for agents with same domain AND state = `idle`
7. If idle implementer found → reassign each issue via PATCH `/repos/{owner}/{repo}/issues/{n}` → `assignees: [new_agent_login]`
   - **Rate limiting:** Batch at 10 issues per API window (GitHub secondary rate limit ~90/min). Use exponential backoff on 429.
   - **Partial failure:** Track reassigned vs failed in state file. Retry failed in next cycle.
8. If no idle implementer of same domain:
   - Post: "🚨 All {domain} implementers crashed — #{issues} remain unassigned. @human intervention needed."
   - Add to `orphaned_issues` array with `issue_number`, `domain`, `orphaned_at: now`, `status: reassignment_failed` for human triage
   - Coordinator retries reassignment in subsequent cycles (if new implementer becomes available)
9. Clear `message_queue[crashed_agent_role]` — reinject queued messages into replacement agent's queue
9. Post crash notice: "⚡ @{agent} crashed — work reassigned to @{new_agent} (#{issues})"

### WF-4: Strategist Activation

**Trigger:** Coordinator detects no open issues with activity within STUCK_DETECTION_SECONDS (or deadlock breaker condition)

**Flow:**
1. Coordinator posts: "@strategist No active work — generate initiatives."
2. Strategist's mention trigger fires → spawns Pi session
3. Strategist researches market → **files epics on GitHub FIRST** (before coordinator dispatch), generates issues with domain labels
4. **Strategist writes created issue numbers to bridge `/status` `announced_epics` field** (avoids API eventual consistency race — the coordinator reads /status, not GitHub API, to discover new epics)
5. Strategist posts summary in thread with links to created issues
6. Each initiative enters `pending_approval` in coordinator-state.json
7. Coordinator's next cycle reads `announced_epics` from `/status` → filters against `dispatched_epics` in state file to skip already-dispatched epics → for each new epic number:
   a. For `domain:product`: posts "📋 New initiative: [name] (#{n}) — @pm scope this" AND "@strategist Initiative #{n} approved — epic filed"
   b. For `domain:growth`: posts "📋 New initiative: [name] (#{n}) — @cmo plan this" AND "@strategist Initiative #{n} approved — epic filed"
   c. Marks epic as dispatched in state file
8. Strategist fires on @mention → confirms epic status → returns to `idle`
9. PM/CMO fire on @mention → spawn with concrete issue reference

**Batch protection:** If strategist files 10+ epics, coordinator posts ONE summary message with all initiatives in a bullet list. Individual dispatches follow in the same thread.

**Gate exception:** If human gate is ACTIVE, coordinator sets status to `pending` in `pending_approvals` and waits (WF-7) instead of dispatching.

### WF-5: Agent @Mention Dispatch

**Trigger:** Any message in team Slack channel containing `@agent_role`

**Flow:**
1. SlackRouter detects `@agent_role` mention in message text
2. Look up agent config from subjects YAML: `trigger_on: mention`, `skills[]`, `boundary[]`, `domain`
3. Query bridge `/status` for agent state:
   - `idle` → spawn new Pi session (step 4)
   - `working` or `blocked` → enqueue message in coordinator-state.json `message_queue[agent_role]`
   - `crashed` → post "@{agent} is unavailable (crashed)" — do NOT enqueue
   - `paused` → enqueue (agent handles after gate resolves)
4. Spawned session receives: message context (thread, issue refs), agent config (skills[], boundary[], domain)
5. Agent writes `state: working` to /status at session start
6. Agent executes task using skills from its template
7. Agent posts progress + result in thread
8. **On completion:** Agent writes `state: idle` to /status as FINAL action before Pi session exit
9. **Queue processing (coordinator-driven):** Agent does NOT touch coordinator-state.json. Agent signals completion via `/status` (`state: idle`). Coordinator's next cycle reads `state: idle`, dequeues next message from `message_queue[agent_role]`, and posts to agent @mention to trigger new session. This avoids agents mutating coordinator state directly.

**Queue management:** Max 20 messages per agent_role. Overflow → oldest message dropped, sender notified. Messages from same user within 5-min window → deduplicated.

**HealthMonitor:** Detects Pi process exit (natural or crash). If process exits with `state: working` → marks `crashed` (WF-3). If exits from `idle` → no action.

### WF-6: Kanban Auto-Population

**Trigger:** GitHub issue event (opened, assigned, labeled, closed) or PR event (opened, merged)

**Flow:**
1. GitHub webhook or workflow triggers on issue/PR event
2. Query Projects v2 GraphQL API:
   - `addProjectV2ItemById` → add issue to team board
   - `updateProjectV2ItemFieldValue` → set Role and Domain custom fields
3. Role derived from issue assignee → maps to agent_role in subjects YAML
4. Domain derived from issue label (`domain:product`, `domain:growth`)
5. Board columns auto-advance:
   - Issue opened → To Do
   - Issue assigned + in_progress label → In Progress
   - PR opened → Review
   - PR merged / Issue closed → Done
6. Custom fields auto-set: Role = "Product" or "Growth" based on domain label

**Lag:** < 30s from event to board update (GitHub workflow trigger latency)

### WF-7: Human Gate (Approve/Reject Initiative)

**Trigger:** Strategist posts initiative proposal → coordinator adds to `initiative_registry` in state file

**Flow:**
1. Strategist posts: "📋 Proposed initiative: [name] (#{n}) — awaiting human approval"
2. Coordinator's next cycle detects new epic, adds to `initiative_registry` with `status: pending, proposed_at: now`
3. If human gate is ACTIVE (configurable per team):
   a. Coordinator does NOT dispatch to PM/CMO
   b. Waits for human response in thread (checks on each cycle)
4. On human response detection (cycle scans for "Approved" / ✅ / "Rejected" / ❌):
   a. **Approved:** coordinator posts dispatch (WF-4 step 6), sets `status: approved`
   b. **Rejected:** coordinator posts "@strategist Initiative #{n} rejected — closing" → sets `status: rejected`
5. **Escalation (wall-clock based, survives coordinator restart):**
   - On each cycle, check `now - proposed_at` for each pending item:
   - >24h → ping human: "Awaiting approval on #{n} [name]"
   - >72h → @channel escalation
   - >168h (1 week) → auto-close as stale, mark `status: stale_closed`
   - **Fast-forward on restart:** Load all pending items, apply any overdue escalations immediately
6. After resolution, remove from `initiative_registry`

**Gate deactivation:** If human gate is INACTIVE, skip step 2-5 — coordinator dispatches immediately.

### WF-8: Human Kills Project

**Trigger:** Human posts "@coordinator kill initiative [issue_number]" or "@coordinator kill #{n}"

**Important:** Initiatives are referenced by GitHub issue number, not by name. Command format: `@coordinator kill initiative #123` or `@coordinator kill #123`.

**Flow:**
1. Coordinator detects @mention + "kill" keyword, extracts issue number
2. Query GitHub for the epic issue (#{n}) → extract all child issues via tasklist/sub-issue API
3. For each open issue:
   a. Close with comment: "💀 Killed by human: initiative no longer pursued"
   b. If issue has active agent (state = `working`/`blocked` in coordinator-state.json):
      - Coordinator @mentions agent: "@implementer Issue #{n} killed — stop work"
      - Agent detects @mention → transitions to `idle`
4. Check for open PRs linked to killed issues (GitHub search: `is:pr is:open linked:issue/#{n}`) → post: "N open PRs remain — @human review or close?"
5. Post summary: "💀 Initiative killed: [title] (#{n}) — N issues closed, M PRs remain"
6. Remove initiative from coordinator-state.json tracking (if present)
7. Kanban auto-updates on issue close events

---

## 4. Data Model

> Note: Substep 3 (Prototype) skipped — no GUI.

### 4.1 Subjects YAML Schema (Agent Roles)

> **Schema drift note:** The canonical subjects registry (`operations/subjects/_schema.md`) defines the field contract. This plan extends it with coordinator-specific fields (`trigger_on`, `cron_interval_seconds`, `heartbeat_interval_seconds`, `slack_bot_id`, `github_login`, `skills`). These are additive — existing fields (`held_by`, `loop_type`, `delegation`, `status`, `reports_to`, `domains`, `boundary`, `diagnostic`, `belief`, `interactive`) conform to canonical. `loop_type: continuous` is canonical (see `_schema.md` §2 — `completion|cron|trigger|continuous`).
> 
> **Team registration note:** The `epistemic-team` is not yet registered in ONTOLOGY.md §1.1. Agent infrastructure is owned by Organisation Design Team per §1.4. **Resolution:** #6210 (Slack Bridge) includes creating `docs/teams/epistemic-team/` directory structure. Team registration will be filed as a dependency pre-check before Phase 3 agent deployment.

### Role Template Model

**Roles are templates, not named agents.** One CMO template → instantiated per team: `epistemic-team-cmo`, `eldato-app-team-cmo`, `dmer-team-cmo`. All share the same skills, boundary, and role definition. Only team-specific config differs (Slack channel, GitHub project, human members).

**Registry:**
- `operations/subjects/templates/cmo.yaml` — canonical CMO definition (skills, boundary, domains, belief, interactive)
- `operations/subjects/epistemic-team.yaml` — team instance: references CMO template, overrides team-specific fields

**Phase 1:** Deploy templates for epistemic-team only. Validate the pattern.  
**Phase 2+:** Instantiate for eldato-app-team, dmer-team, and future teams.

**File:** `operations/subjects/{team-name}.yaml`

```yaml
# operations/subjects/eldato-app-team.yaml
team:
  slug: eldato-app-team
  name: "El Dato App Team"
  slack_channel: "#team-app"
  github_project_id: "PVT_kwDOA..."  # GitHub Projects v2 node ID
  interactive_control: active  # active | inactive (coordinator-specific extension: when human oversight gates agent dispatch)

roles:
  - role: coordinator
    held_by: pi
    delegation: open
    status: proposed
    reports_to: null
    loop_type: continuous
    cron_interval_seconds: 300
    heartbeat_interval_seconds: 60
    slack_bot_id: "U..."  # Per-agent Slack bot token (Phase 1; user has admin)
    github_login: "coordinator-app"
    domains: []  # coordinator is domain-agnostic
    trigger_on: null  # coordinator is not mention-triggered
    skills:
      - github-query
      - bridge-state-query
      - slack-posting
    boundary: |
      MUST NOT implement code
      MUST NOT review quality
      MUST NOT spawn agents directly
    belief: "Keep work flowing — detect stuck work, crashed agents, and empty queues. Never execute, only route."
    interactive: "Escalate to human when all implementers of a domain are crashed and issues remain unassigned."
    diagnostic:
      - issues_stuck_24h
      - avg_response_time_sec
      - cycles_completed

  - role: strategist
    held_by: pi
    delegation: open
    status: proposed
    reports_to: null
    loop_type: trigger
    trigger_on: mention
    slack_bot_id: null
    github_login: "strategist-app"
    domains: []  # single strategist for all domains
    skills:
      - research
      - epic-workflow
      - issue-creation
    boundary: |
      MUST NOT implement code
      MUST NOT close issues without human approval
      MUST NOT modify another agent's config
    belief: "When team is idle, generate fresh work aligned with strategy."
    interactive: "Proposed epics require human approval before agents are dispatched."
    diagnostic:
      - initiatives_generated
      - avg_research_time_sec

  - role: pm
    held_by: pi
    delegation: open
    status: proposed
    reports_to: null
    loop_type: trigger
    trigger_on: mention
    slack_bot_id: null
    github_login: "pm-app"
    domains: [product]
    skills:
      - issue-scoping
      - writing-plans
      - issue-creation
    boundary: |
      MUST NOT implement code
      MUST NOT deploy without review gates
      MUST NOT decide what to build (strategist scope)
    belief: "Turn product initiatives into scoped, ready-to-implement issues."
    interactive: "Scoped plans require human approval before implementer dispatch."
    diagnostic:
      - issues_scoped
      - avg_scoping_time_sec

  - role: cmo
    held_by: pi
    delegation: open
    status: proposed
    reports_to: null
    loop_type: trigger
    trigger_on: mention
    slack_bot_id: null
    github_login: "cmo-app"
    domains: [growth]
    skills:
      - content-strategy-agent
      - issue-creation
    boundary: |
      MUST NOT approve growth spend
      MUST NOT publish content without review
      MUST NOT implement growth tasks
    belief: "Turn growth initiatives into plans and content briefs."
    interactive: "Growth plans require human approval before implementation."
    diagnostic:
      - plans_created
      - avg_planning_time_sec

  - role: implementer-product
    held_by: pi
    delegation: open
    status: proposed
    reports_to: null
    loop_type: trigger
    trigger_on: mention
    slack_bot_id: null
    github_login: "implementer-product"
    domains: [product]
    skills:
      - issue-workflow
      - executing-plans
      - code-review
      - commit-workflow
      - test-routing
    boundary: |
      MUST NOT decide what to build
      MUST NOT deploy without review gates
      MUST NOT modify agent config
    belief: "Implement product issues, pass code review, ship."
    interactive: "PR reviews require human approval per code-review gate."
    diagnostic:
      - issues_completed
      - avg_implementation_time_sec
      - prs_merged

  - role: implementer-growth
    held_by: pi
    delegation: open
    status: proposed
    reports_to: null
    loop_type: trigger
    trigger_on: mention
    slack_bot_id: null
    github_login: "implementer-growth"
    domains: [growth]
    skills:
      - content-strategy-agent
      - content-research
      - editorial-content-writer
      - deal-content-writer
      - carousel-b2b-strategy
      - seo-content-checklist
    boundary: |
      MUST NOT decide growth strategy
      MUST NOT publish without review gates
      MUST NOT modify agent config
    belief: "Execute content pipeline, pass review, publish."
    interactive: "Content requires human review before publishing."
    diagnostic:
      - tasks_completed
      - avg_task_time_sec
```

**Field reference:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| role | string | Yes | Unique agent identifier within team |
| held_by | string | Yes | Runtime: "pi" (DeepSeek Pi agent) |
| delegation | enum | Yes | "open" (can delegate) or "closed" (delegation disabled) |
| status | enum | No | "proposed", "active", "archived", "deprecated" (default: "active") |
| reports_to | string | No | Role slug of parent role within team (null if top) |
| belief | string | No | Free text — role's purpose and mission |
| interactive | string | No | Free text — when human oversight is required |
| loop_type | enum | Yes | "completion", "cron", "trigger", "continuous" |
| trigger_on | enum | Conditional | "mention" (required for trigger loops) |
| cron_interval_seconds | int | Conditional | Interval for cron/continuous loops |
| heartbeat_interval_seconds | int | No | Separate heartbeat timer (continuous loops) |
| slack_bot_id | string|null | No | Slack bot user ID (Phase 2; null for Phase 1) |
| `github_login` | string | Yes | GitHub username for issue assignment (Phase 1: placeholders — real machine accounts created in #6210; Kanban #6222 runs dry-run until accounts exist) |
| domains | string[] | Yes | Canonical domain slugs (product, growth) |
| skills | string[] | Yes | Agent skills (maps to /skill:name) |
| boundary | string | Yes | Free text — what the role MUST NOT do |
| diagnostic | string[] | Yes | Observable metrics for stuck detection |

### 4.2 Bridge /status Response Schema

**Endpoint:** GET `/status`

```json
[
  {
    "agent_role": "implementer-product",
    "agent_state": "working",
    "health_state": "healthy",
    "last_activity_iso": "2026-07-10T14:30:00Z",
    "github_login": "implementer-product",
    "domains": ["product"],
    "diagnostic": {
      "issues_completed": 12,
      "avg_response_time_sec": 420
    },
    "announced_epics": [456, 457],
    "pid": 88370,
    "health_check_failures": 0
  }
]
```

**States:** agent_state: `idle`, `working`, `blocked`, `paused` | health_state: `healthy`, `crashed` (crashed overrides all)

**Writers:** HealthMonitor writes `health_state`, `pid`, `health_check_failures`. Agent Pi session writes `agent_state` (idle/working/blocked/paused), `last_activity_iso`, `diagnostic`. Strategist writes `announced_epics`.

**Precedence:** Readers check `health_state` first — if `crashed`, agent_state is disregarded entirely.

**Readers:** Coordinator (WF-1), SlackRouter (WF-5 dispatch decision)

### 4.3 Coordinator State Schema

**File:** `coordinator-state.json` (adjacent to coordinator process)

Schema defined in §2 Workflows. Key constraints:
- Coordinator is sole writer (atomic: temp file → rename)
- First-run: initialize empty schema
- Corrupted: delete, start fresh
- Agents NEVER write to this file

### 4.4 GitHub Projects v2 Custom Fields

**Pre-created in GitHub UI (not programmatically creatable):**

| Field Name | Type | Options |
|-----------|------|---------|
| Role | Single select | Product Manager, CMO, Product Implementer, Growth Implementer, Strategist |
| Domain | Single select | product, growth |

**Auto-population via workflow:**
- Role: derived from agent_role → Role mapping (subjects YAML `role` field or domain)
- Domain: derived from issue label `domain:product` or `domain:growth`

### 4.5 Entity Relationships

> Mapping note: `role` in subjects YAML is the role identifier slug (e.g., `pm`, `implementer-product`). GitHub Projects `Role` field uses display names (Product Manager, Product Implementer). The `role` field on GitHub issues is auto-populated from the subjects YAML `role` identifier via a mapping table defined at implementation time.

```
Team (1) ──has── (N) Role (domains, skills, boundary)
Role (1) ──held_by── (1) Agent (held_by: pi — runtime instance)
Coordinator (1) ──monitors── (N) Issue (GitHub, assignee, labels)
Coordinator (1) ──reads── (1) Bridge /status
Coordinator (1) ──owns── (1) coordinator-state.json
Strategist ──generates── (N) Epic (GitHub issue, domain tags; approval pending = Epic in state pending)
PM ──scopes── (N) Issue (child of epic, domains: [product])
CMO ──plans── (N) Plan (content-strategy-agent output, domains: [growth])
Implementer ──executes── (N) Issue (via skills)
HealthMonitor ──watches── (N) Agent process (pid, health_state via /status)
SlackRouter ──routes── (N) @mention → Agent
GitHub Projects ──tracks── (N) Issue (via GraphQL mutations)
```

---

## 5. Architecture

### 5.1 Component Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                        EL DATO SYSTEM                           │
│                                                                 │
│  ┌─────────────────┐     ┌─────────────────┐                   │
│  │  Slack Workspace │◄────│  Slack Bridge   │                   │
│  │  (team channels) │     │  - inbound.ts   │                   │
│  │                  │     │  - slack.ts     │                   │
│  │  @agent mentions │────►│  - channel-map  │                   │
│  │  agent posts     │     │  - state.ts     │                   │
│  │  human gates     │     │  + SlackRouter  │──► dispatch       │
│  └─────────────────┘     └────────┬────────┘    decision       │
│                                   │                             │
│  ┌─────────────────┐     ┌───────▼────────┐                   │
│  │  GitHub          │     │  Bridge        │                   │
│  │  - Issues        │◄────│  /status       │                   │
│  │  - Projects v2   │     │  endpoint      │◄── HealthMonitor  │
│  │  - Webhooks      │     │  (read+write)  │◄── Agent sessions │
│  └─────────────────┘     └───────┬────────┘◄── Strategist      │
│                                   │                             │
│  ┌───────────────────────────────▼───────────────────────────┐ │
│  │                    COORDINATOR (per team)                  │ │
│  │                                                            │ │
│  │  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐ │ │
│  │  │ Activity       │  │ Stuck          │  │ Crash         │ │ │
│  │  │ Detection      │  │ Detection      │  │ Detection     │ │ │
│  │  │ (slack+github) │  │ (>4h no activ) │  │ (health_state)│ │ │
│  │  └───────┬────────┘  └───────┬────────┘  └──────┬───────┘ │ │
│  │          │                   │                   │         │ │
│  │          └───────────────────┼───────────────────┘         │ │
│  │                              ▼                             │ │
│  │                    ┌────────────────┐                      │ │
│  │                    │  Dispatch      │                      │ │
│  │                    │  Decision      │                      │ │
│  │                    │  (rule-based)  │                      │ │
│  │                    └───────┬────────┘                      │ │
│  │                            │                               │ │
│  │              ┌─────────────┼─────────────┐                 │ │
│  │              ▼             ▼             ▼                 │ │
│  │         @strategist    @pm/@cmo    @implementer            │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  SPECIALIST AGENTS (per role, per team)                   │  │
│  │  - Triggered by @mention → Pi session spawn               │  │
│  │  - Write agent_state to /status on transition             │  │
│  │  - Read skills from subjects YAML template                │  │
│  │  - Execute skills in order (issue-workflow → commit)      │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Component Descriptions

#### Slack Bridge (Extended)
**Location:** `operations/slack-bridge/src/`
**Current state:** Exists for manual-slack usage. Needs 3 additions:
1. **Agent identity:** Per-agent Slack bots from Phase 1 (user has Slack admin). Each role gets a distinct bot identity.
2. **@mention routing (SlackRouter):** Detect @agent mentions → query /status for agent_state → spawn or enqueue
3. **Channel-level posting:** Ensure agents post in `#team-{slug}` channels (channel-map.ts already has channel routing)

**New component — SlackRouter:** Embedded in inbound.ts. On inbound message:
- Parse @mention target agent_role
- GET bridge `/status` to read target's agent_state
- If idle → spawn Pi session with subject template
- If working/blocked → enqueue in coordinator-state.json message_queue
- If paused → enqueue (agent handles after gate resolves)
- If crashed → post "@agent is down" + notify coordinator
- If /status unreachable → enqueue (safe path: delayed > duplicate spawn)

#### Coordinator Daemon
**Location:** New — `operations/coordinators/{team-slug}/`
**Runtime:** Continuous daemon, single instance per team, flock-locked. TypeScript (Node.ts) — consistent with existing Slack Bridge runtime. Invoked via systemd/launchd.
**Cycle interval:** 300s (configurable per team YAML)
**Inputs:** GitHub API, Slack API (activity), Bridge /status (agent states)
**Outputs:** Slack posts, coordinator-state.json writes, @mention dispatches
**Persistence:** `coordinator-state.json` — atomic writes (temp file → rename)
**Memory:** coordinator-state.json loaded at cycle start, written at cycle end

**Cycle phases (WF-1):**
1. Acquire flock → read state file → query /status
2. Activity detection (Slack + GitHub, per-service degradation)
3. Stuck detection (issues > STUCK_DETECTION_SECONDS with no activity)
4. Crash detection + reassignment (health_state == crashed)
5. Queue dispatch (agents with state == idle → pop from message_queue)
6. Strategist activation (no active work → @strategist)
7. Strategic deadlock breaker (stuck > DEADLOCK_SECONDS → @strategist)
8. Write state → release flock

#### HealthMonitor
**Location:** New — runs alongside coordinator OR as separate process
**Function:** Watches agent PIDs, writes health_state to /status
**Writes to /status:** `health_state` (healthy/crashed), `pid`, `health_check_failures`
**Crash detection:** PID not found OR health check timeout (3 consecutive failures)
**Sole writer of `health_state`** — no Agent Pi session writes to this field

#### Bridge /status Endpoint
**Location:** New — alongside Slack Bridge
**Function:** Single source of truth for agent runtime state
**Serves:** Coordinator (reads all states), SlackRouter (reads dispatch target)
**Schema:** See §4.2 (agent_role, agent_state, health_state, domains, diagnostic, announced_epics, pid, health_check_failures)
**Auth:** Phase 1 trusts localhost-only (same process boundary as Slack Bridge). Phase 2: Unix socket permissions or shared localhost secret per OWASP API7:2023.

#### Specialist Agents
**Runtime:** One-shot Pi sessions (spawned by @mention trigger; TypeScript via Slack Bridge, Node.ts runtime — consistent with existing stack)
**Template:** subjects YAML per role (skills[], boundary[], domains[])
**Lifecycle:** spawn → read issue context → execute skills → write agent_state → exit
**State transitions:** idle → working (on spawn), working → paused (on gate), working → idle (on completion)

### 5.3 Runtime Model

| Component | Process Type | Instances | Lock | Lifecycle |
|-----------|-------------|-----------|------|-----------|
| Coordinator | Continuous daemon | 1 per team | flock | Start on deploy, restart on crash |
| Slack Bridge | Persistent Node | 1 total | — | Start on deploy |
| HealthMonitor | Sidecar (or embedded in coordinator) | 1 per coordinator | — | Start/stop with coordinator |
| Strategist | One-shot Pi session | 0-N | — | Spawned by @mention or coordinator |
| PM/CMO/Implementers | One-shot Pi session | 0-N | — | Spawned by @mention |
| /status endpoint | HTTP (within bridge) | 1 total | — | Part of Slack Bridge |

### 5.4 Integration Surfaces

| From | To | Protocol | Data |
|------|----|----------|------|
| Coordinator | Slack API | HTTP POST (chat.postMessage) | Agent posts, pings, escalations |
| Coordinator | GitHub API | GraphQL/HTTP | Issue search, assignment, Projects v2 |
| Coordinator | Bridge /status | HTTP GET | All agent states + announced_epics |
| Coordinator | coordinator-state.json | Filesystem (atomic write) | Persistence checkpoint |
| SlackRouter | Bridge /status | HTTP GET | Target agent state for dispatch |
| HealthMonitor | Bridge /status | HTTP POST/PUT | health_state, pid, health_check_failures |
| Agent session | Bridge /status | HTTP POST/PUT | agent_state transitions, diagnostic |
| Strategist session | Bridge /status | HTTP POST/PUT | announced_epics |
| GitHub Webhooks | Kanban Updater | HTTP POST → GraphQL | Auto-populate Projects v2 on issue events |

### 5.5 Data Flow: End-to-End Example

```
1. GitHub webhook → new issue #123 (domain:product)
2. Coordinator cycle reads GitHub → detects new unassigned issue
3. Coordinator reads /status → PM is idle
4. Coordinator posts @pm "scope #123" in #team-app
5. SlackRouter receives @pm mention → queries /status → PM idle
6. SlackRouter spawns Pi session (pm template from subjects YAML)
7. PM session writes agent_state: working to /status
8. PM executes issue-scoping → writes plan to issue comment
9. PM posts "📋 #123 scoped — @implementer-product"
10. PM session exits → writes agent_state: idle to /status
11. Coordinator next cycle: #123 has plan, implementer-product is idle
12. Coordinator posts @implementer-product "implement #123"
13. SlackRouter spawns implementer → agent_state: working
14. Implementer executes issue-workflow → writes code, passes review
15. Implementer exits → agent_state: idle
16. GitHub webhook → issue #123 closed → Kanban auto-update
```

### 5.6 Failure Isolation

- **Coordinator crash:** flock auto-released; systemd/launchd restarts; no agent work in progress lost (agents are independent)
- **Slack API down:** Coordinator logs warning, skips slack activity detection; GitHub activity detection still runs
- **GitHub API down:** Coordinator skips GitHub-based checks; Slack activity detection still runs
- **Bridge /status down:** Coordinator uses last known agent_states from coordinator-state.json with staleness threshold (skip stuck detection + reassignment if last refresh > BRIDGE_STALENESS_SECONDS)
- **Agent crash:** HealthMonitor detects → health_state: crashed → Coordinator reassigns or escalates
- **State file corruption:** Delete and initialize empty → max one-cycle duplicate ping

### 5.7 Deployment

- **Coordinator daemon:** TypeScript, systemd/launchd unit, `operations/coordinators/{team-slug}/`
- **Slack Bridge + /status:** Existing Slack Bridge deployment, extended with /status endpoint
- **HealthMonitor:** Embedded in coordinator process (simplest deployment) OR separate sidecar
- **Specialists:** No persistent process — spawn on-demand via Pi CLI
- **Config:** subjects YAML per team, coordinator-state.json runtime persistent

### 5.8 Observability & Testing

**Observability:** Each coordinator cycle phase writes structured JSON log to stdout: `{"ts":"ISO","team":"eldato-app","cycle":N,"phase":"name","duration_ms":N,"ok":true}`. Coordinator-state.json includes `last_cycle_completed_iso` for external monitoring age-check. Health check: GET `/status` returns coordinator pid.

**Testability:** Integration tests use captured API fixtures (Slack event JSON, GitHub API responses, /status snapshots) fed to coordinator cycle phases. Phase 2: full e2e with test Slack workspace.

**Race condition (documented):** Near-simultaneous @mentions to same agent_role during a spawn window can produce duplicate Pi sessions. SlackRouter dedup is thread_ts-keyed; channel-level coordinator dispatches bypass this. Acceptable Phase 1 behavior — duplicate Pi sessions self-correct (second session sees work already claimed). Phase 2: add agent_role-keyed dedup in SlackRouter with 5s TTL.

---

## 6. Interfaces

### 6.1 Bridge /status

**Endpoint:** `GET /status` → `POST /status`  
**Auth:** Localhost-only (Phase 1)  

**GET /status**  
→ `200 OK`: `[{agent_role, agent_state, health_state, last_activity_iso, github_login, domains[], diagnostic{}, announced_epics[], pid, health_check_failures}]`  
→ `200 OK`: `[]` (no agents registered — first run)  
→ `503 Service Unavailable`: state unreadable

**POST /status**  
→ `200 OK`: full state object (updated fields merged)  
→ `400 Bad Request`: unknown `agent_role`  
→ `503 Service Unavailable`: state unwritable

**POST body schema:**
```json
{
  "agent_role": "implementer-product",          // required: who this update is for
  "agent_state": "working",                     // Agent Pi ONLY: idle|working|blocked|paused
  "last_activity_iso": "2026-07-10T14:30:00Z",  // Agent Pi ONLY: ISO 8601
  "diagnostic": {"issues_completed": 12},       // Agent Pi ONLY: optional key/value
  "announced_epics": [456, 457],                // Strategist ONLY: int[]
  "health_state": "healthy",                    // HealthMonitor ONLY: healthy|crashed
  "pid": 88370,                                 // HealthMonitor ONLY
  "health_check_failures": 0                    // HealthMonitor ONLY
}
```
**Partial updates:** Only send fields that changed. Unlisted fields preserved. Authorized writers per field table:

| Field | Writer |
|-------|--------|
| agent_state | Agent Pi session |
| last_activity_iso | Agent Pi session |
| diagnostic | Agent Pi session |
| announced_epics | Strategist Agent |
| health_state | HealthMonitor ONLY |
| pid | HealthMonitor ONLY |
| health_check_failures | HealthMonitor ONLY |

**Health state transitions:** health_state: healthy → crashed when health_check_failures >= 3. On agent process restart, HealthMonitor resets health_check_failures to 0, health_state to healthy. Crash is terminal until agent process restart — no auto-recovery in HealthMonitor.

**GET error states:** first-run (empty array = 200 OK), unreadable state (503), corrupt (503).

### 6.2 Slack Bridge (Extended)

**Extension 1 — Agent identity:** Single Slack app with one bot token, routing all agents internally via SlackRouter. Each agent role gets a distinct `username` override in `chat.postMessage` — visually distinct, non-spoofable, 1 app slot.

**Extension 2 — @mention routing (SlackRouter):**
- Parse `@<agent_role>` from inbound message text
- GET `/status` → read target `agent_state` + `health_state`
- `health_state == crashed` → post "⚠️ @agent_role is down"
- `agent_state == idle` → spawn Pi session (subjects YAML template for role)
- `agent_state == working|blocked|paused` → POST to `/coordinator/enqueue`
- `/status` unreachable → enqueue (safe default)

**Extension 3 — Channel posting:** Agents route posts to `#team-{slug}` (channel-map.ts extended with agent channel mapping from subjects YAML `team.slack_channel`).

### 6.3 Coordinator Enqueue

**Endpoint:** `POST /coordinator/enqueue`  
**Auth:** Localhost-only (Phase 1)  
**Body:**
```json
{
  "agent_role": "pm",           // required: subjects YAML role slug
  "message": "@pm scope #123",  // required: Slack message text to deliver
  "from": "coordinator"         // required: source identifier (coordinator|slackrouter)
}
```
**Responses:**  
→ `202 Accepted`: enqueued for next coordinator cycle  
→ `400 Bad Request`: unknown `agent_role`  
→ `429 Too Many Requests`: queue at capacity (20 max per agent_role)  
→ `503 Service Unavailable`: state file unwritable  
**Effect:** Appends to `coordinator-state.json` → `message_queue[agent_role]`. Queue cap: 20 messages per `agent_role` (oldest dropped with notice posted to channel).

### 6.4 Coordinator State File

**File:** `coordinator-state.json` (adjacent to coordinator process)  
**Schema:** See §2 coordinator state schema  
**Writes:** Atomic (write to `coordinator-state.json.tmp` → rename). ONLY the coordinator writes this file.  
**Polling:** No file watching — coordinator reloads at each cycle start.

### 6.5 GitHub Projects v2 Kanban

**Endpoint:** `POST /kanban/webhook` (in Slack Bridge)  
**Auth:** Verify `X-Hub-Signature-256` against configured webhook secret (GitHub standard)  
**Payload:** Standard GitHub webhook payload — consumes `action`, `issue{}`, `repository{}`, `sender{}`  
**Events:** `issues.opened`, `issues.labeled`, `issues.assigned`, `issues.closed`, `issues.reopened`  
**Responses:**  
→ `200 OK`: processed  
→ `202 Accepted`: queued for batch processing  
→ `400 Bad Request`: unsupported event type  
→ `503 Service Gateway Timeout`: GraphQL mutation failed (retry in next cycle)

**Effect:** GraphQL mutations to populate Projects v2:
- `addProjectV2ItemById` → add issue to project board
- `updateProjectV2ItemFieldValue` → set Role and Domain custom fields

**Role derivation chain:**
1. GitHub webhook provides `assignee.login` (GitHub username)
2. subjects YAML: match `github_login` → get `role` slug
3. Role slug → display name via mapping table
4. If `github_login` not found in any role → field left unset, coordinator logs warning

**Role mapping:**

| subjects YAML `role` | GitHub Projects `Role` display |
|---------------------|-------------------------------|
| `pm` | Product Manager |
| `cmo` | CMO |
| `implementer-product` | Product Implementer |
| `implementer-growth` | Growth Implementer |
| `strategist` | Strategist |

**Batch:** 5-second debounce window — collect issue events, batch into single GraphQL mutation (cap: 50 items). On HTTP 429: exponential backoff, split batch and retry.

### 6.6 Agent Spawn Contract

**Invocation:** `pi -p -s <subjects_yaml_path> --role <role_slug> --issue <number>`  
**Session lifecycle:**
1. Read subjects YAML → load skills[], boundary[], domains[]
2. POST `/status`: `{"agent_state": "working"}`
3. Execute skills in order (e.g., issue-scoping → writing-plans → commit-workflow)
4. On before-session-exit hook: POST `/status`: `{"agent_state": "idle"}`
5. Exit

**State transition contract:** Agent writes `agent_state` at: spawn (working), gate entry (paused), gate exit (working), completion (idle). HealthMonitor owns `health_state` — agent NEVER writes it.

### 6.7 Human Gate Contract

**Trigger:** `interactive_control: active` in team YAML → coordinator holds initiatives in `initiative_registry`

**Detection mechanism:** Coordinator-cycle-driven — scans `conversations.replies` for emoji reactions on initiative proposal messages. No separate webhook endpoint (avoids Slack Events API subscription complexity for Phase 1).

**Approval flow:**
1. Strategist posts initiative → coordinator adds to `initiative_registry` with `status: pending`
2. Coordinator's next cycle scans initiative message for emoji reactions:
   - ✅ present → `status: approved` → dispatches
   - ❌ present → `status: rejected` → posts "Initiative rejected"
   - No reaction → check age; if > 7 days (ESCALATION_SECONDS = 604800) → `status: stale_closed` → posts "📋 Initiative #{n} closed due to inactivity"
3. Human gates are checked EVERY cycle (not just once) — stale detection runs until resolution

**Kill command (`@coordinator kill initiative #123`):**
- Validates sender is human (check Slack user ID — agents cannot kill): rejects with "❌ Kill rejected — only humans can kill initiatives"
- Issue not found → "❌ Issue #123 not found — check number?"
- Already closed → "❌ #123 is already closed"
- No child issues → "💀 #123 closed (no child issues)"
- Queries GitHub for child issues → closes children + parent → posts "💀 Initiative #123 killed — N child issues closed"
- Partial failure (some children fail to close) → "⚠️ N/M child issues closed — remaining failed. Check manual." Track in coordinator-state.json for retry.

### 6.8 Health Check Contract

**Mechanism:** HealthMonitor polls agent PID via `kill(pid, 0)` every 5s (HEALTH_CHECK_INTERVAL_SECONDS).

**Crash threshold:** 3 consecutive failures → POST `/status`: `{"agent_role": "...", "health_state": "crashed", "health_check_failures": N}`

**Recovery:** On agent process restart, HealthMonitor detects new PID → POST `/status`: `{"agent_role": "...", "health_state": "healthy", "health_check_failures": 0, "pid": <new_pid>}`

**Crash vs agent_state:** If agent session crashes, HealthMonitor writes `health_state: crashed`. The final `agent_state` may remain `working`. Coordinator disregards `agent_state` when `health_state == crashed` (precedence rule: §4.2). No data corruption — accepted Phase 1 behavior.

**Alternate:** Phase 2: `/health` HTTP endpoint per agent process (finer-grained than PID polling).

### 6.9 Coordinator Config Contract

**Config source:** `operations/subjects/{team-slug}.yaml`

**Loading:** Read at startup + reload on SIGHUP (for live config updates without daemon restart).

**Validation:**
- File missing → log FATAL, exit 1 (no recovery)
- YAML parse error → log FATAL with error, exit 1
- `github_project_id` missing → log FATAL, exit 1 (Kanban depends)
- `agents[].github_login` missing → log FATAL, exit 1 (issue assignment depends)
- All other missing fields → documented defaults apply
- `team.slack_channel` missing → default to `#general` with warning

### 6.10 Agent Spawn Error States

**Errors on spawn (`pi -p -s <yaml> --role <slug> --issue <n>`):**
- subjects YAML not found → log error, exit 1
- role_slug not found in YAML → log error, post "⚠️ Unknown role: `<slug>`" to Slack, exit 1
- /status unreachable → retry 3× (1s backoff), then post warning and proceed (optimistic spawn: better to work than wait)
- Issue #n not found on GitHub → post "⚠️ Issue #N not found — check number?" to Slack, exit 1

**Slack context pass-through:** Agent receives `--channel <id> --thread <ts>` for posting progress updates back to the correct conversation. If omitted (non-Slack spawn for testing), agent posts to stdout instead.

---

## 7. Detailed E2E Tests

### 7.1 Complete Workflow — Assignee Gets Stuck, Coordinator Pings, Escalates, Human Shows Up

**Test data:** Issue #789 assigned to implementer-product, no activity 5h. Agent state: working.

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | System | now - last_activity > STUCK_DETECTION_SECONDS | |
| 2 | Coordinator | Cycle runs (WF-1 → stuck detection) | |
| 3 | Coordinator | Detects #789 stale, agent_state ≠ paused | #789 added to state file, ping_stage=1 |
| 4 | Coordinator | Posts: "@implementer-product — #789 has no activity in 5h. Status?" | |
| 5 | Implementer | Does not respond | |
| 6 | Coordinator | Next cycle: #789 still stale, ping_stage=1 | ping_stage → 2 |
| 7 | Coordinator | Posts: "@implementer-product — #789 still pending. Second check-in." | |
| 8 | Implementer | Still silent | |
| 9 | Coordinator | Next cycle: ping_stage=2 → 3 | |
| 10 | Coordinator | "🚨 @channel — #789 stuck {elapsed}h with no response. Human needed. (1/3)" | |
| 11 | No response | 24h passes | |
| 12 | Coordinator | "🚨 @channel — #789 still stuck. (2/3)" | |
| 13 | No response | 24h passes | |
| 14 | Coordinator | "🚨 @channel — #789 still stuck. (3/3)" | |
| 15 | No response | 7 days since first @channel | |
| 16 | Coordinator | Auto-close: "📋 #789 closed due to inactivity after 7 days" | Issue closed on GitHub |

### 7.2 Strategist Generates Initiatives When No Work Exists

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | Coordinator | No open issues with activity | |
| 2 | Coordinator | Posts: "@strategist No active work — generate initiatives" | |
| 3 | Strategist | @mention trigger fires → spawns Pi session | |
| 4 | Strategist | Creates 2 epics: #100 (product), #101 (growth) | POST /status: announced_epics=[100,101] |
| 5 | Strategist | Posts summary, exits (agent_state → idle) | |
| 6 | Coordinator | Next cycle reads /status → announced_epics=[100,101] | |
| 7 | Coordinator | Posts: "📋 #100 — @pm scope this" + "@strategist #100 approved" | |
| 8 | Coordinator | Posts: "📋 #101 — @cmo plan this" + "@strategist #101 approved" | |

### 7.3 Crashed Implementer — Reassignment to Idle Agent

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | HealthMonitor | implementer-product PID gone → health_state: crashed | /status updated |
| 2 | Coordinator | Cycle reads /status → health_state=crashed | |
| 3 | Coordinator | GET /search/issues?q=assignee:{login}+state:open | Returns [#789, #790] |
| 4 | Coordinator | /status → finds implementer-product-2 (agent_state: idle, same domains) | |
| 5 | Coordinator | PATCH #789, #790: assignees=[implementer-product-2] | Both reassigned |
| 6 | Coordinator | Posts: "⚠️ @implementer-product crashed — #789, #790 reassigned to @implementer-product-2" | |

### 7.4 Crashed Implementer — No Idle Agent (Human Escalation)

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1-3 | As above | Crashed, issues found | |
| 4 | Coordinator | /status: all product implementers working/blocked/crashed | |
| 5 | Coordinator | Adds to orphaned_issues[] with status: reassignment_failed | |
| 6 | Coordinator | Posts: "🚨 All product implementers crashed — #789,#790 unassigned. @human needed" | |

### 7.5 Human Gate — ✅ Approval

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | Strategist | Posts proposal for #102 (interactive_control: active) | |
| 2 | Coordinator | Adds #102 to initiative_registry: status=pending | Does NOT dispatch |
| 3 | Human | Reacts ✅ on proposal message | |
| 4 | Coordinator | Next cycle scans reactions → ✅ | status → approved |
| 5 | Coordinator | Dispatches: "@pm scope #102" | |

### 7.6 Human Gate — ❌ Rejection

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1-2 | As above | Proposal pending | |
| 3 | Human | Reacts ❌ | |
| 4 | Coordinator | Scans reactions → ❌ | status → rejected |
| 5 | Coordinator | Posts: "Initiative #102 rejected" | Does NOT dispatch |

### 7.7 Human Gate — Stale Auto-Close (7 Days)

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1-2 | As above | Proposal pending | |
| 3 | No response | 7 days pass (604800s) | |
| 4 | Coordinator | Age > ESCALATION_SECONDS | status → stale_closed |
| 5 | Coordinator | Posts: "📋 #102 closed due to inactivity" | |

### 7.8 Kanban Auto-Population

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | GitHub | Webhook: issues.opened for #103 (domain:product, assignee:pm) | |
| 2 | Kanban handler | addProjectV2ItemById → #103 on board | |
| 3 | Kanban handler | updateProjectV2ItemFieldValue → Role: PM, Domain: product | |

### 7.9 Kanban Batch Debounce

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1-3 | GitHub | 5 webhook events within 2s | |
| 4 | Kanban handler | 5s debounce collects all | |
| 5 | Kanban handler | Single GraphQL mutation with 5 addProjectV2ItemById ops | |

### 7.10 Coordinator Queue — Agent Completes, Gets Next

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | SlackRouter | @pm mention while PM is working | Enqueued in message_queue[pm] |
| 2 | PM | Completes → POST /status: agent_state: idle | |
| 3 | Coordinator | Next cycle: PM idle, message_queue[pm] not empty | Dequeues message |
| 4 | Coordinator | Posts @pm with next task | |

### 7.11 Coordinator Graceful Degradation — /status Down

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | Bridge | /status goes down | |
| 2 | Coordinator | GET /status → 503 | Logs warning |
| 3 | Coordinator | Agent states staleness > BRIDGE_STALENESS_SECONDS | Skips stuck detection + reassignment |
| 4 | Coordinator | Cycle continues with Slack + GitHub checks only | |
| 5 | Coordinator | Posts: "⚠️ Agent state monitor unavailable — stuck detection suspended" | |

### 7.12 Human Kills Initiative Mid-Flight

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | Human | "@coordinator kill initiative #102" | |
| 2 | Coordinator | Validates sender is human | ✅ |
| 3 | Coordinator | #102 has 3 child issues | |
| 4 | Coordinator | Closes children + parent | |
| 5 | Coordinator | Posts: "💀 #102 killed — 3 child issues closed" | |

### 7.13 Agent Cannot Kill Initiative

| # | Actor | Action | Expected Result |
|---|-------|--------|----------------|
| 1 | PM Agent | "@coordinator kill initiative #102" | |
| 2 | Coordinator | Sender is agent → reject | Posts: "❌ Kill rejected — only humans can kill" |

### 7.14 Routing: SlackRouter Parses @mention and Routes to Correct Agent

| Step | Actor | Action | Expected |
|------|-------|--------|----------|
| 1 | Human | Posts `@pm scope this` in #team-epistemic | SlackRouter receives event |
| 2 | SlackRouter | Parses `@pm` mention, maps to agent_role `pm` | Roles lookup matches subjects YAML |
| 3 | SlackRouter | Calls GET /status, finds PM idle | Returns agent_role=pm, state=idle |
| 4 | SlackRouter | Routes to PM agent spawn with issue context | PM spawns in #team-epistemic thread |
| 5 | SlackRouter | Posts attribution via single Agent app with `username: "PM"` | Message displays as from @pm |
| 6 | SlackRouter | Human posts `@nonexistent do something` | SlackRouter replies: "Unknown role: nonexistent" |

### 7.15 Specialist Framework: Loads Subjects YAML and Enforces Boundaries

| Step | Actor | Action | Expected |
|------|-------|--------|----------|
| 1 | Coordinator | Spawns PM agent | pi -p --role=pm --team=epistemic-team |
| 2 | Specialist FW | Reads `operations/subjects/epistemic-team.yaml` | Parses YAML, finds `roles: - role_slug: pm` |
| 3 | Specialist FW | Loads `templates/pm.yaml` | Skills, boundary, domains loaded |
| 4 | PM Agent | Attempts code implementation (task type: coding) | FW blocks: "Boundary violation: pm MUST NOT implement code" |
| 5 | PM Agent | Scopes an issue, writes plan doc | FW permits (in-scope: planning, issue-scoping) |
| 6 | Specialist FW | Agent session ends, /status updated to idle | state: idle, diagnostic: {result: completed} |

### 7.16 Channel Visibility: Messages Appear in Correct Team Channel

| Step | Actor | Action | Expected |
|------|-------|--------|----------|
| 1 | Coordinator | Detects stuck issue #789, posted by `implementer-product` | Message appears in #team-epistemic (NOT DM) |
| 2 | Human | Confirms message visible in #team-epistemic | Channel members see @implementer posted about #789 |
| 3 | Strategist | Generates initiative, posts summary | Message in #team-epistemic thread under strategist topic |
| 4 | Human | Checks DM history | No agent DM messages found (all in public channels) |

### 7.17-7.19: Additional Tests (Summarized)

**7.17 — Agent Spawn Error: YAML Not Found:** Agent spawn request with `team=no-such-team` → Specialist FW returns 404, SlackRouter posts "Team `no-such-team` has no subjects YAML." Agent never spawns.

**7.18 — Agent Spawn Error: /status Unreachable:** PM fires on @mention, SlackRouter calls GET /status → times out. Fallback: posts to channel "PM queue is full — will dispatch when /status recovers (~5 min)." Coordinator picks up in next cycle.

**7.19 — Coordinator Config Reload (SIGHUP):** Coordinator receives SIGHUP → re-reads subjects YAML, re-loads team config → new `interactive_control: active` flag takes effect without restart → next cycle holds initiatives in initiative_registry.

### 7.20-7.25: Prior Tests (Summarized)

| # | Test | Expected |
|---|------|----------|
| 14 | **PM Scopes → Plan Created:** PM spawns, reads #100, creates plan doc, exits | Plan doc exists, issue updated |
| 15 | **Implementer Executes → Code Ships:** Implementer spawns, reads plan, executes, PR merged | Code deployed, issue closed |
| 16 | **CMO Creates Growth Plan:** CMO spawns, reads #101, runs content-strategy-agent | Plan posted, issue updated |
| 17 | **Growth Implementer Executes Content:** Implementer spawns, writes, passes reviewers | Content published |
| 18 | **Concurrent Strategists (Idempotency):** Two @strategist mentions same cycle | Only one Pi session; second sees announced_epics already populated |
| 19 | **Coordinator Restart Recovery:** Process restart → reads state file → no duplicate work | Flock acquired, incomplete dispatches completed |

---

## 8. Coherence Review

### UX Design Decisions

| # | Decision Type | User Choice | Rationale |
|---|---------------|-------------|----------|
| 1 | Message surface | New events top-level, follow-ups in-thread | Channel stays scannable; escalations visible; ongoing chatter collapsed |
| 2 | Human review tagging | User @-tagged on human gates | Coordinator already posts in channel for gate events |
| 3 | Agent attribution | Single Slack app with per-role `username` override | 1 app slot (not 6). Free plan compatible. Each agent role has distinct display name. |

### 8.1 Cross-Substep Consistency

| Check | Status |
|-------|--------|
| Data Model ↔ Architecture: /status schema matches component writers | ✅ §4.2 per-field writer table; §5.2 uses same |
| User Journeys ↔ Workflows: Agent state model consistent | ✅ paused excluded from stuck detection both UJ-2 and WF-1 step 8b |
| Workflows ↔ Interfaces: All API calls have contracts in §6 | ✅ Coordinator reads /status (§6.1), posts via Slack (§6.2), enqueues (§6.3) |
| Data Model ↔ Interfaces: POST /status per-field auth matches §4.2 writers | ✅ |
| Scope E2Es ↔ Detailed E2Es: All 25 tests fleshed out | ✅ 19 detailed + 6 summary in §7 |
| Architecture ↔ Failure Isolation: All 6 failure modes mitigated | ✅ §5.6 |
| Ontology ↔ Plan: Canonical terms throughout | ✅ growth, paused, roles:, domains[] |

### 8.2 Decision Log

| Decision | Rationale |
|----------|-----------|
| Localhost-only auth (Phase 1) | Same host; no new attack surface |
| Daemon + flock (not cron) | Cron overlap; flock guarantees singleton |
| coordinator-state.json (not DB) | Zero new infra; atomic writes |
| /status (not MemPalace) | Memory-substrate agnostic; thin runtime proxy |
| Rule-based routing (not LLM) | Deterministic; avoids coordination failure category |
| Single Slack app, per-role username override | 1 app slot on free plan; visually distinct; non-spoofable |
| Coordinator sole writer of state file | Single-writer concurrency; agents use /status |
| HealthMonitor embedded in coordinator | Simplest deployment; no separate lifecycle |

### 8.3 Risk Checklist

| Risk | Prob | Mitigation | Status |
|------|------|------------|--------|
| A2: Fractal pairs fail (25%) | Low | Phase 1 validates pattern | ✅ |
| A7: Deterministic routing too rigid | Med | Phase 2 LLM routing if metrics regress | ⚠️ |
| A13: Coordinator SPOF | Med | systemd restart; flock auto-release; 5-min blast radius | ⚠️ |
| A14: Human availability > 4h (80%) | High | 24h escalation; 7-day auto-close; agents work autonomously | ⚠️ |
| GitHub API rate limiting | Low | 5-min cycle; 5s kanban debounce; 50-item cap | ✅ |

**Go/no-go (post-Phase 1):** Throughput ≥ baseline AND drop rate ≤ 50% baseline. BOTH must fail to pause.

---

## 9. Work Decomposition

### Issue Map

| # | Title | Tier | Covers (Plan §) |
|---|-------|------|-----------------|
| #6210 | Slack Bridge Agent Extensions | standard | §6.2, §5.2 (SlackRouter) |
| #6211 | Bridge /status Endpoint | standard | §4.2, §6.1 |
| #6212 | Specialist Agent Framework (subjects YAML + Spawn) | standard | §4.1, §6.6, §6.10 |
| #6213 | Coordinator Daemon Core (Cycle Loop + State File) | complex | §2, §5.2, §6.4, §6.9 |
| #6214 | Coordinator Stuck Detection + Escalation | standard | WF-1, §7.1 |
| #6215 | Coordinator Crash Recovery + HealthMonitor | standard | §5.2, §6.8, §7.3-7.4 |
| #6216 | Strategist Agent (Initiative Generation) | standard | §4.1, §7.2, §7.20 |
| #6217 | Product Manager Agent (Issue Scoping + Planning) | standard | §4.1, §7.20 |
| #6218 | Product Implementer Agent (Code Execution) | standard | §4.1, §7.21 |
| #6219 | CMO Agent (Growth Strategy + Planning) | standard | §4.1, §7.22 |
| #6220 | Growth Implementer Agent (Content Execution) | standard | §4.1, §7.23 |
| #6221 | Coordinator Human Gate + Kill Command | standard | §6.7, §7.5-7.7, §7.12-7.13 |
| #6222 | GitHub Projects v2 Kanban Auto-Population | standard | §4.4, §6.5, §7.8-7.9 |
| #6223 | Coordinator Queued Dispatch | standard | §6.3, §7.10 |
| #6224 | Integration Testing + E2E | complex | §7 (25 tests), §5.8 |

### Dependency Graph

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Infrastructure (parallel)                         │
│                                                             │
│  #6210 (Slack Bridge: headers + channel posting)           │
│  #6222 (Kanban)                                            │
│       │                                                     │
│       └─→ #6211 (/status endpoint)                          │
│               │                                             │
│               ├─→ #6212 (Specialist FW)                     │
│               └─→ #6213 (Coordinator Core)                  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ Phase 2: Coordinator Logic (parallel, after #6213)         │
│                                                             │
│  #6213 ─┬─→ #6214 (Stuck Detection)                        │
│         ├─→ #6215 (Crash Recovery)                         │
│         ├─→ #6221 (Human Gate + Kill)                      │
│         └─→ #6223 (Queued Dispatch)                        │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ Phase 3: Specialist Agents (parallel, after #6210+#6212)   │
│                                                             │
│  #6212 ─┬─→ #6216 (Strategist)                             │
│         ├─→ #6217 (PM)                                     │
│         ├─→ #6218 (Product Implementer)                    │
│         ├─→ #6219 (CMO)                                    │
│         └─→ #6220 (Growth Implementer)                     │
│                                                             │
│  #6210 (SlackRouter) → all Phase 3 agents                  │
│  (SlackRouter routes @mentions; agents need it for spawn)  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ Phase 4: Integration                                       │
│                                                             │
│  ALL (#6210-6223) ──→ #6224 (Integration Testing)          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Boundary clarifications:**
- **#6210 vs #6211:** #6210 delivers Slack Bridge foundations (single app with per-role username override, channel routing) and SlackRouter stub. #6211 delivers /status. After #6211 completes, SlackRouter queries GET /status for spawn-vs-enqueue decisions.
- **#6213 vs #6223:** #6213 delivers coordinator cycle loop with empty `message_queue` in state schema (stub phase). #6223 fills the stub with dequeue + overflow logic.

**Parallelism:** 6 parallel groups across 4 phases.

### MECE Verification

#### Mutually Exclusive
| Issue Pair | Overlap Risk | Verdict |
|-----------|-------------|---------|
| #6210 ↔ #6211 | Both modify Slack Bridge; separate concerns (routing vs state) | ✅ ME |
| #6213 ↔ #6214 | Coordinator core vs stuck detection; detection is a cycle phase | ✅ ME |
| #6213 ↔ #6215 | Core vs crash recovery; recovery is a cycle phase | ✅ ME |
| #6216 ↔ #6217 | Strategist generates; PM scopes — distinct roles | ✅ ME |
| #6217 ↔ #6218 | PM plans; implementer executes — distinct roles | ✅ ME |
| #6219 ↔ #6220 | CMO plans; implementer executes — distinct roles | ✅ ME |
| #6221 ↔ #6214 | Gate vs escalation; different triggers (human approval vs stuck detection) | ✅ ME |

#### Collectively Exhaustive
| Scope Item | Covered By | Status |
|-----------|-----------|--------|
| S1: Slack communication primitives | #6210 | ✅ |
| S2: Specialist agent framework | #6212 | ✅ |
| S3: Coordinator daemon | #6213, #6214, #6215, #6221, #6223 | ✅ |
| S4: Strategist agent | #6216 | ✅ |
| S5: Product Manager agent | #6217 | ✅ |
| S6: Product Implementer agent | #6218 | ✅ |
| S7: CMO agent | #6219 | ✅ |
| S8: Growth Implementer agent | #6220 | ✅ |
| S9: Transparency (public channels) | #6210 (single app, per-role identity) | ✅ |
| S10: Kanban board | #6222 | ✅ |
| E2E Test Coverage | #6224 | ✅ |

### MECE Verdict
**MECE CLEAN** — 15 issues, no overlaps, all 10 scope items covered + integration testing. Boundary clarifications added for #6210/#6211 and #6213/#6223.

### Wiring Check
- **Scope coverage:** 10/10 scope items have ≥1 issue ✅
- **Plan traceability:** 15/15 issues trace to plan sections ✅
- **Dependency acyclicity:** ✅
- **Parallelism:** 7 groups across 4 phases

### Dependency Soundness
- Graph is **acyclic** ✅
- Phase 1: #6210 + #6222 in parallel → #6211 → #6212 + #6213 in parallel
- Phase 2: 4 coordinator sub-issues in parallel (after #6213)
- Phase 3: 5 specialist agents in parallel (after #6212; SlackRouter from #6210 needed for spawn)
- Phase 4: Integration tests after all components
- **Constraints verified:** Strategist depends on Specialist FW only (not coordinator); downstream agents don't depend on Strategist; SlackRouter stub precedes /status


