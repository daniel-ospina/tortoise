---
title: "Implementation Plan — Epic #1976: Agent-driven onboarding"
type: decisions
domain: capability
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-29
aboutSubjects: tortoise
aboutObjects: tortoise
---

# Implementation Plan — Epic #1976: Agent-driven onboarding

> **Inputs:** align `01-align.md` (PROCEED; Rails 1/2 + named couplings) · research `02-research-brief.md` · scope `03-scope.md` (12 E2E) · test-design `04-test-design.md` (#1992, 17 surfaces) · UX decisions `05-ux-decisions.md`.
> **Authoritative decomposition source:** the W1-W12 list in the epic issue body (FINAL WORKSTREAM LIST section, R2-1/R2-2 amendment) + this plan's substeps below. Where this plan and the issue body differ, the issue's CONSISTENCY RESOLUTION ROUND 2 + this plan's decisions govern.

---

# 1. User Journeys

> Personas: **Founder/individual** (self-use, tech-savvy, wants agent setup to just work), **Builder** (builds an app on top, needs capability catalog), **Invitee** (joins an existing org via link), **Multi-org product-literate user** (creates a second org).

### J1 — First-run creator journey (self-use) — the north star
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| Entry ① | Signs up (identity + email only, ≤1 screen) | Pre-onboarding; light "create your org" nudge if no org created | Account exists, no org |
| Entry ② | Creates org; **name REQUIRED w/ editable prefill** | `POST /v1/teams` (#1877) + `teams` row + membership; **OnboardingState node initialized graph-side, same eager statement as TeamMeta** (W5); onboarding FIRES here | Org + onboarding state exist |
| Fork card | Picks "use for your own agents" | `fork: 'self'` persisted in onboarding state; seed-step presentation set | Fork recorded (once, per org) |
| Connect-consent | Runs ONE universal command (or pastes it) | Agent self-adjudicates harness (4 self-install / 2 teach-human); MCP connected; connection reported to state | Harness-connected checkpoint set |
| Agent setup | Watches/reads Setup guide card | Agent reads OnboardingState → files **Organization (Subject/organization)** + **User (Subject/naturalPerson)** linked `memberOf` from API data; asks only for gaps (never invents identity) | Two Subjects filed; `onboarding_seed_complete` (W11) |
| Magic moment | Answers a real decision (options→criteria→findings) | Agent runs `tortoise-decide`; IMPL/NAND wiring; EP ranking produced | `onboarding_decide_complete` (W11) |
| Done | Sees Overview = 3 elements, zero toggles | `onboarding_complete` set ONLY on two Subjects + one decide + agent connected; Setup guide collapses to status | Terminal state (self-fork gate) |

> **Fork-aware completion (P1 fix):** the completion gate is **fork-aware**. Self fork: two Subjects + one decide + agent connected (R2-4/B2). Build fork: **org-anchor Subject + agent connected + catalog presented once** (the user Subject is filed as part of seed but is NOT a gate component; the decide stays available but is not the completion gate — see J2). Multi-org compact (J5): **org-anchor (seed-lite) + harness-connected** ('first point' = the org-anchor itself — see WF-2; reduced gate). Each fork/org-type has a reachable terminal state so the Setup guide card can collapse; DE2E-12 asserts the gate per fork.

### J2 — Builder fork journey
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| Entry ② | Creates org, picks **"build an application on top"** | `fork: 'build'` persisted; capability catalog **presented once** (static placeholder until W8 endpoint lands — Rail 2 coupling) | Fork + catalog view recorded |
| Connect-consent | Same universal command | Same as J1 | Connected |
| Builder setup | Sees catalog of indexers+extractors; billing **nudged** (never forced) | Catalog from registry (W8, extends tool_registry); session recording NOT a toggle — listed as module | Builder knows what's buildable |
| Seed (build fork runs it too) | Provides name/gaps if asked | Agent files **Organization Subject** (always — the anchor the node tracks); **user Subject filed too (always fileable via ask-once)** | Two Subjects filed (seed phase exit — user Subject is NOT a completion-gate component for build) |
| Done (build) | Sees catalog + connected state | **Build-fork completion = org anchor Subject + connected + catalog presented once** — the decide nudge is NOT the build gate (it remains available on demand); user Subject filed as part of seed but is not a gate component for build | Terminal state (build-fork gate) |

### J3 — Invitee journey (existing user)
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| Entry ③ | Clicks invite link | Token validated; email match? (a) **match** → one-click Accept → in org; (b) **mismatch same person** → 3-path choice (fuse w/ OTP / new account / accept-with-mismatch **w/ OTP**); (c) **mismatch different person** → error + sign in as invitee | In org (or correct path) |
| Fusion path | Chooses fuse (default) | OTP proof-of-control of invitee email → UNION memberships/api_keys; graph data stays per-org | Fused identity |
| In-org | Agent setup = inline skippable first action | Per-org onboarding state (compact if product-literate); invitee setup writes **member_progress** (never fakes org-level completion) | Org-level steps met by creator-or-any-member |

> **State ownership (P1 fix):** the OnboardingState node is **org-scoped** (1:1 org). Org-level steps (team-named, harness-connected, seed filed, decide-completed) advance the org checklist; an invitee's inline setup writes per-member progress into the node's `member_progress` map — it does NOT fake org-level steps. Org completion can be satisfied by the creator or any member (first-write-wins, idempotent). An invitee completing a decide CAN advance the org's decide-completed step; the checklist records who did it.

### J4 — Invitee journey (new user)
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| Entry ③ | Clicks invite link | Landing pre-fills email | — |
| Signup+accept | Enters name + password | **Account + membership created ATOMICALLY** (one action, never "create then accept") | In org |
| In-org | Skips or runs agent setup | Agent setup inline, skippable | Onboarding state armed |

### J5 — Re-entry / multi-org journey
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| Entry ④ | Opens Settings → Setup guide (or Continue-setup card) | Reads OnboardingState node; **resumes, never restarts** (idempotent) | Next incomplete step |
| Entry ⑤ | Empty-state CTA (add first point / connect agent) | Same state machine; fires relevant step | Step completes |
| New org later | Creates org B (product-literate) | **Compact per-org checklist** (connect→invite→first point); never full re-onboarding; org A unaffected | Org B armed |

> **Compact-path completion (P1 fix):** org B's terminal state = **seed-lite (org-anchor Subject ALWAYS filed — the node's `onboards` edge needs it) + harness-connected**. No decide, no user-Subject required for product-literate orgs; "first point" = the org-anchor itself (WF-2). The node reaches `status: 'complete'` and the card collapses. This is a fork-agnostic reduced gate for subsequent orgs, distinct from the first-org self/build gates. **Fork inheritance (P2 fix):** org B inherits the user's fork choice from org A at creation (per-user default) with per-org override available — org B NEVER re-asks the fork card (keeps "once per org" true for the first org while avoiding re-asking on compact).

### J6 — Legacy-org migration journey (M5 — NEW, P1 fix)
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| Migration | (no user action — backfill at deploy) | `teams.onboarding_state` jsonb → OnboardingState node **idempotent backfill**; `onboarding_complete` stays (grandfathered); NO re-onboarding; `person`→`naturalPerson` normalize on seed | Node backfilled |
| First Settings open | Opens new Settings tab / Setup guide | **Fork defaults at READ time without persisting** (grandfathering preserved — no write until the user opts in); asks fork card once on first guide-open if never asked (R2-7) | Fork recorded ONLY on explicit opt-in |
| In-org | Continues normal use | Legacy operational keys (github_connected, session_recording, etc.) keep working from the jsonb operational store (see §4 DM-2 split) | No re-onboarding, no broken toggles |

### J7 — Self-hosted first-run journey (W12 — NEW, P2 fix)
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| Install | Runs self-hosted setup (`selfhost` SDK/API init) | **OnboardingState node inited at selfhost SDK/API init (the chosen trigger — ONE owned write point; idempotent no-op if present); `fork='self'` DEFAULT — fork card never surfaced on self-hosted (no dashboard surfaces)** (P2 fix) | Node exists, fork=self |
| Seed prompts | Answers "what's your name?" / "what's your organization called?" | Agent files Organization (Subject/organization) + User (Subject/naturalPerson) linked memberOf from the two prompts — NO Supabase | Two Subjects filed |
| Decide | Answers one real decision | `tortoise-decide` completes; no Supabase involved | Decide complete |
| Done | Uses the graph locally | completion per self-hosted gate (two Subjects + decide + connected); no invite/settings/telemetry surfaces (self-hosted has none) | Terminal state |

### J8 — Capture disclosure beat (W6 — self-use; P2 fix: folded into J1's post-setup phase)
| Phase | User action | Agent/system action | Exit state |
|---|---|---|---|
| First capture | Uses the agent normally | **First-capture in-conversation announcement** (one line, non-blocking) → capture-disclosed checkpoint written (node checkpoint, not card step) | Checkpoint set |
| Manage | Settings → Memory sources → Agent sessions → view/delete | DELETE /v1/sessions/{id} removes session + receipt; no re-gate (#1927) | Capture managed |

### Edge cases (all journeys)
- **Empty/error:** org-create 409 (duplicate name), invite token expired/invalid, OTP wrong/expired, graph unavailable at org-create (graph-side rollback — node never half-inits; reads: graph UP + node absent → defaults; graph DOWN → FLOW 'unavailable' marker, never fabricated defaults — P2 fix), API key missing at connect, member capacity / 1-free-team entitlement at accept (explicit error or upgrade path), consumed invite token re-click (idempotent "already in org").
- **Loading:** Setup guide card while agent mid-step (spinner + current-step indicator from graph), Overview digest before first point (empty state copy, no fabrication).
- **Multi-device:** state machine renders the SAME node from any agent/dashboard (graph is the store).

---

# 2. Workflows

### WF-1 — Agent install workflow (W2)
```
OnboardingState read (agent) → node exists? (else init per W12 — self-hosted trigger: init at selfhost SDK/API init, ONE owned write point, idempotent no-op if present) → fork known? (else surface fork card — W1 renders)
→ harness self-adjudication (6-harness table: Claude Code/Cursor/Codex/Pi = config write; Claude Desktop/Claude Web = teach-human)
→ universal command executed/pasted → connection verify (tortoise_health) → write harness-connected checkpoint (idempotent)
→ report back to dashboard (Setup guide card advances)
Failure modes: config write invalid (harness broken) → teach-human fallback; connection verify fails → retry with diagnostic, surface honest error.
```

### WF-2 — Seed workflow (W3)
```
Post-connect trigger → read node → anchor data: hosted → JWT (user_id/email/app_metadata) + teams.name + membership role;
  self-hosted → two prompts (name, org name)
→ gaps? (no display_name → ask preferred name once; name not email-derived → confirm)
→ COLLISION CHECK before MERGE: same-name Subject already exists and is NOT this org/user → ask for disambiguation
  (suffix/canonical key) — never silently merge distinct identities (P1 fix; see DM-3)
→ file Organization (Subject/organization) + User (Subject/naturalPerson) linked memberOf (MERGE on {name}, #452)
  + write org_subject_id/edge from OnboardingState node to the Organization Subject (link, not just 1:1 claim)
→ normalize existing person→naturalPerson (subjectKind free-string)
→ WRITE step edge first-points-filed (P2 fix — the state machine's step edges are the canonical source; seed
  writes this edge, team-named is written by org-create, harness-connected by WF-1, decide-completed below)
→ emit onboarding_seed_complete (W11, dedup on edge new-creation)
→ nudge ONE real tortoise-decide (self fork only; build fork keeps it available on demand)
→ on decide success: WRITE step edge decide-completed + emit onboarding_decide_complete (W11, dedup on
  edge new-creation); on failure: write last_decide_attempt: 'failed' (never decide-completed)
→ set onboarding_complete ONLY on the fork's gate (self: two Subjects + decide + connected;
  build: org-anchor Subject + connected + catalog-presented-once; compact: org-anchor Subject + harness-connected)
Failure modes: anchor merge collision (resolved via disambiguation), decide dismissed (no completion — guide stays),
  LLM provider missing (decide fails closed → honest surface + decide-attempted-failed recorded, distinct from
  dismissed; retry reachable — P1 fix).

COMPACT first-point semantics (P2 fix): compact org B's "first point" = the ORG-ANCHOR Subject itself (filed by
  seed-lite) — the gate is seed-lite (org anchor) + connect, and the "first point" language in J1/J5/WF-2/DE2E-12
  refers to that anchor. Simplify to: compact gate = org-anchor Subject + harness-connected. (If a real knowledge
  point is desired later, the J5 empty-state CTA is the named writer/trigger.)
```

### WF-3 — Invite-accept workflow (W7)
```
Invite link → token validate (per-token/IP rate caps, expiry — existing) → email match?
  (a) match → one-click Accept → membership provisioned (atomic) → in org
  (b) mismatch same person → 3-path choice → fuse (OTP verify) | new account | accept-with-mismatch (OTP verify — P1 fix: BOTH mismatch-override paths require proof-of-control)
  (c) mismatch different person → error → sign in as invitee
New user → prefill email → name+password → account + membership ATOMIC (provision/invitation_accept RPC,
  SECURITY DEFINER, per-IP + per-email rate caps, token single-use — abuse posture from /v1/register)
In-app: pending-invites affordance (team switcher, GitHub-style); admin resend/expire (Slack-style)
Failure modes: OTP wrong/expired; atomic accept partial (rollback); privilege accumulation (both mismatch-override
  paths OTP-gated); member capacity/1-free-team entitlement (accept fails with explicit capacity error or surfaced
  upgrade path — never silent); token reuse (consumed token → idempotent "already in org" state, never double-membership);
  403→3-path compat: existing clients asserting 403 on mismatch get an additive response contract (see I-6).
```

### WF-4 — Onboarding state machine (W5/W9)
```
5 doors (signup pre-onboarding / org-create / invite-accept / Settings Setup guide / empty-state CTA) → ONE state machine
State = OnboardingState graph node per org (1:1 org): {completed_steps[], fork, status, version, member_progress}
Canonical step list (SINGLE source of truth — P2 fix): team-named, harness-connected, first-points-filed,
  decide-completed, capture-disclosed, catalog-presented. (capture-disclosed + catalog-presented = node
  checkpoints — set by WF-5 first-capture / WF-6 first catalog render — NOT card-rendered steps; card
  renders team-named, harness-connected, seed filed, first decision)
Step derivation: current step = first incomplete in canonical order, derived from completed-step EDGES (canonical —
  the completed_steps[] array is a read projection for the response, NOT a second store — P2 fix, no dual representation)
Writes: idempotent keyed MERGE on {org_id, step_id} — first-write-wins, value-preserving no-op on re-write (idempotent replay;
  #452 name-MERGE; #398 create-only never-overwrite principle — NOT a 409-on-overwrite contract; 409 reserved for
  node-level conflicts only (concurrent init with a different org_id) and set-once property re-writes (fork);
  last_decide_attempt is last-write-wins per attempt (200 on change — retry must be recordable, P2 fix);
  checkpoint re-writes are 200 no-op — P2 fix, single error contract with I-1)
Node init: graph-side, in the SAME eager statement as TeamMeta (one Cypher transaction — graph-side atomicity;
  orphan handling: idempotent no-op reads returning defaults + sweep alongside _journal_append_product)
Dashboard mirror: Setup guide card renders the SAME state; completes → collapse to status (dismissible, re-open path)
Failure modes: concurrent init (graph-side atomic — one node), non-idempotent re-write (keyed MERGE no-op), cross-org bleed (per-org node).
```

### WF-5 — Capture disclosure workflow (W6, self-use path)
```
First capture → in-conversation announcement (one line, non-blocking) → WRITE capture-disclosed checkpoint
  (idempotent keyed MERGE — the node checkpoint, not a card step; P2 fix)
Settings → Memory sources → Agent sessions → view/delete → DELETE /v1/sessions/{session_id} → graph + capture-receipt cleanup
Authz: team-member (until W10 RBAC)
No re-gate: #1927 default-ON stands; off-switch stays quiet 409; disclosure is explicit, not a consent ceremony
Failure modes: delete orphans graph data (receipt not cleaned), delete during capture (race), member deletes another's session (authz).
```

### WF-6 — Builder catalog workflow (W8)
```
Build fork → catalog presented once (W1-rendered placeholder until W8 endpoint; WF-6/W8 marks
  catalog-presented step edge on first render — P2 fix: storage slot defined, build gate evaluable)
W8: extend tool_registry.py → pullable registry endpoint → dashboard-side catalog read
W8b: per-module note sweep — every extractor/indexer module docstring carries: "referenced in the builder capability catalog — update on add/rename"
Failure modes: registry stale (module added w/o note), endpoint 404/empty, catalog re-presents on every visit (once-only flag in state).
```

---

# 3. Prototype

> **Decision (from epic-plan §3):** GUI surfaces exist (wizard, Overview, Settings, fork card, Setup guide card, invite landing). Per the epic-plan skill's prototype review gate, the prototype for this epic is a **markdown/mermaid wireframe spec + state diagrams** (the epic spans dashboard + agent + graph; a single HTML prototype cannot represent the agent-side). Dashboard HTML prototypes are deferred to per-workstream child issues (W1/W4/W7/W8) at implementation time via `prototype-review`. This doc specifies the layout skeletons + state transitions the child prototypes must satisfy.

### P1 — Wizard (5 human steps, W1)
```
[Step 1 Orientation]  →  [Step 2 Create/Join Org]  →  [Step 3 Fork card]  →  [Step 4 Connect-consent]  →  [Step 5 Done]
  "here's what's about    name REQUIRED + prefill    self-use vs build      universal command     agent takes over;
  to happen: install →    OR accept invite          (presentation fork)    (copy/run or paste)    Overview + Setup guide
  connect → add org+you →  [LEGACY DEFER (P2 fix): at launch the join leg uses existing /v1/invites*
  make first decision"      quality — W7's fusion/pending polish is follow-on, NOT silently re-scoped]
```

### P2 — Overview (W4)
```
┌──────────────────────────────────────────────┐
│ Connection status   [agent: connected ✓]      │
│ Memory digest       [n objects · m statements]│
│ Next action         [Make your first decision →] │
└──────────────────────────────────────────────┘
ZERO feature toggles. Stat cards relocated to existing tabs.
States (P2 fix — all must be renderable): populated (above) · EMPTY pre-first-point
  (digest shows "No memories yet — your agent will file your first points"; next action =
  connect/seed CTA) · LOADING (skeleton, no fabricated digest) · ERROR (connection lost —
  status shows offline, honest retry).
```

### P3 — Settings tab (W4, owner)
```
Tabs: Overview | API Keys | Graphs | Members | Billing | Profile | Settings(new)
Settings:
  ├─ Memory sources: github_connected, github_indexed, github_docs_indexed, session_recording
  ├─ GitHub connect (moved from wizard)
  ├─ Setup guide (W9 — idempotent re-entry; W5 card mirrors same state)
  └─ Capture view/delete (W6 consumer)
```

### P4 — Fork card (W2/W1)
```
┌──────────────────────────────────────────────┐
│ How will you use Tortoise?                    │
│ [1] Use it for your own agents                │
│ [2] Build an application on top               │
│ (self-host/deploy for customers)              │
│ Nudge copy, NEVER billing gate. Once per org. │
└──────────────────────────────────────────────┘
Build branch → capability catalog (placeholder until W8).
```

### P5 — Setup guide card (W5/W9)
```
┌──────────────────────────────────────────────┐
│ Setup guide — 3 of 4 complete        [resume] │
│ ✓ Team named   ✓ Agent connected   ✓ Seed filed │
│ ○ First decision                             │
│ (renders the SAME OnboardingState node)      │
└──────────────────────────────────────────────┘
States (P2 fix — all must be renderable): MID-FLIGHT (above; current-step spinner while agent
  works) · LOADING (agent mid-step — spinner + current-step indicator from graph, no stale claims) ·
  COMPLETE (collapses to compact setup-status per ux decision #3) · DISMISSIBLE (re-open path in Settings) ·
  DEGRADED (graph down — FLOW status 'unavailable'; renders "status unavailable", NEVER a false
  "N of 4 complete" — P2 fix)
  Steps rendered: team-named, harness-connected, seed filed, first decision (card-rendered subset —
  capture-disclosed is a node checkpoint, not a card step).
```

### P6 — Invite landing (W7)
```
Existing user (match):  [Accept invitation] (one click)
Existing user (mismatch same person):  3-path choice card:
  [Fuse accounts (recommended — one login, merged history)]  ← OTP verify
  [Log out and accept with a new account]
  [Accept under current account (mismatch recorded)]          ← OTP verify TOO (P1 fix)
  Copy makes permission consequence visible (per-account).
New user: email pre-filled → name + password → [Accept & join] (atomic).
Mismatch different person: error → [Sign in as invitee].
States (P2 fix — all must be renderable): NOMINAL (above) · TOKEN EXPIRED/INVALID (clear error
  + request-new-invite path) · OTP ENTRY (6-digit input + resend + error on wrong/expired) ·
  CAPACITY (member-cap/1-free-team exceeded → explicit error or upgrade path) ·
  TOKEN CONSUMED (idempotent "you're already in this org" → in-org landing).
```

> **Agent-side interaction contract (P2 fix — the north star has a prototype contract):**
> - **Seed:** in-context copy — "I'm setting up Tortoise for **<org name>**. First, I'll file your organization and you as Subjects in the memory graph." → files → shows graph digest (n objects · m statements).
> - **Decide nudge (self fork):** in-context copy — "Now the magic part: let's make a real decision together. Give me an option pair and I'll rank them with evidence." → runs `tortoise-decide` → shows EP-ranked recommendation.
> - **Capture announcement:** one-line, non-blocking — "Heads up: I'll remember this session so you can recall it later. View/delete in Settings → Memory sources."
> These are the copy + render contracts the deferred child prototypes (W2/W3/W6) must satisfy via prototype-review.

### Design-system compliance
Dashboard components use the existing design tokens/components; fork card + Setup guide card reuse existing card/button patterns; no new design language. (Child prototypes verified via prototype-review at implementation.)

---

# 4. Data Model

### DM-1 — OnboardingState graph node (W5; graph holds FLOW state — operational keys stay in jsonb, see DM-2)
```
Node: OnboardingState  (per org, 1:1 with Organization Subject — linked, not just asserted; see below)
  properties:
    org_id: str            (teams.id — foreign key to legacy table during migration)
    fork: 'self' | 'build' (R2-7; asked once per org; legacy-org default at READ time, persisted only on explicit opt-in)
    status: 'active' | 'complete'
    version: int           (bumped when the flow changes — R2-7 storage key named explicitly)
    completed_steps: [step_id]   (READ PROJECTION of completed-step edges — not a second store; P2 fix)
    member_progress: {user_id: {steps[]}}  (invitee/inline setup — never fakes org-level steps; P1 fix)
  edges: completed-step edges (canonical) + onboards edge → Organization Subject (org_subject_id written at
    seed when the Subject's canonical id resolves; P2 fix — links the node to the anchor it tracks)
  canonical step list (single source): team-named, harness-connected, first-points-filed, decide-completed,
    capture-disclosed, catalog-presented. capture-disclosed + catalog-presented = node checkpoints (set by
    WF-5 first-capture announcement / WF-6 first catalog render), NOT card-rendered steps.
    ENFORCEMENT (P2 fix): the canonical list lives in ONE shared module (e.g. tortoise/onboarding/state.py)
    consumed by hosted_api + dashboard; a unit test rejects unknown step_ids and asserts card-subset ⊆ canonical.
  decide-attempt record (P2 fix): node property last_decide_attempt: 'failed' | 'dismissed' | null —
    single-scalar, LAST-WRITE-WINS per attempt (200 on change; retry must be recordable), success clears to null,
    EXCLUDED from the canonical step set (so step_id enforcement never rejects it); per I-1's per-key-type semantics
  constraints:
    - init graph-side, SAME eager statement as TeamMeta (one Cypher transaction — graph-side atomicity)
    - idempotent keyed MERGE on {org_id, step_id}: first-write-wins, value-preserving no-op on re-write
      (#452 name-MERGE; #398 = create-only never-overwrite PRINCIPLE, not a 409 contract; 409 only for
      node-level conflicts (concurrent init with different org_id) or set-once property re-write (fork))
    - small + serializable
  COMPACT ORGS (P1 fix): compact org B runs seed-lite — the org-anchor Subject is ALWAYS filed (the node's
    onboards edge needs it), user Subject optional; no decide. The node↔Subject 1:1 link holds for all org types.
```

### DM-2 — Store split (W5/M5 — P1 fix: graph holds FLOW state; jsonb keeps OPERATIONAL keys)
```
FLOW state → graph OnboardingState node: fork, status, version, completed_steps, member_progress
OPERATIONAL keys → stay in teams.onboarding_state jsonb (the current store): github_connected, github_indexed,
  github_docs_indexed, session_recording, github_index_cursor, github_legacy_backfill_done,
  session_capture_receipt_*, install_probe_* — these have LIVE consumers today (the session_recording gate on
  POST /v1/sessions; dashboard Settings/Memory sources; PATCH /v1/onboarding/state allowlist; /v1/onboarding/session-recording,
  /team, /github/connect). They are NOT migrated.
Read surface: GET /v1/onboarding/state MERGES both — KEEPS the {onboarding: {…}, email:} envelope (wire compat,
  I-1), graph flow-state inserted as new keys; legacy operational keys unchanged.
  GRAPH-DOWN RENDER (P2 fix): when the graph is unavailable, the merged GET returns an explicit
  status: 'unavailable' marker for FLOW keys (never fabricated defaults) — the Setup guide card renders a
  degraded "status unavailable" state, NOT a false "N of 4 complete" checklist.
Write surfaces: PATCH /v1/onboarding/state retargeted — allowlist filter re-hosted as graph-side enforcement for
  FLOW keys (fork, completed_steps, capture-disclosed, catalog-presented) + jsonb for operational keys;
  member_progress = POST-checkpoint-only FLOW key (user-scoped map-merge; NOT a PATCH allowlist entry — P2 fix, kept in I-1's checkpoint contract)
  PATCH writes of completed_steps translate entries into step-edge MERGEs (never stores the array — DM-1's
  "not a second store" rule; catalog-presented written dashboard-side via PATCH on first catalog render — P2 fix);
  underscore→hyphen translation and team_created strip PRESERVED. WRITE ORDER + DIVERGENCE (P2 fix): jsonb first,
  graph second; on graph write failure the orphan/divergence sweep reconciles flow keys from jsonb (fork, completion
  status); negative test: jsonb succeeds / graph fails → next read consistent, no lost fork/completed_steps.
  /v1/onboarding/session-recording, /team, /github/connect unchanged (jsonb).
Migration: backfill = copy FLOW-relevant legacy fields (fork default, completion status) into the node — idempotent,
  re-run no-op; operational keys stay put. Existing orgs grandfathered (onboarding_complete stays).
```

### DM-3 — Seed anchors (W3, ontology-precise — ONTOLOGY.md §3.6/§5)
```
Subject (organization):  name ← teams.name (MERGE key = {name} per #452); subclass organization
Subject (naturalPerson): name ← display_name (hosted, ask-once if email-derived) | prompt (self-hosted); subclass naturalPerson
Edge: memberOf (Subject → Subject, §3.6 canonical — membership in org)
COLLISION HANDLING (P1 fix): before MERGE, query for an existing Subject with the same {name} that is NOT
  this org/user (by org_id / user_id). On collision → ask the user for disambiguation (suffix or canonical key)
  — NEVER silently merge distinct identities. Name stays the display property; org_id/user_id/email are the
  stable identity refs for collision checks. Adds DE2E assertion for same-name collision (surface 7).
Normalization: existing subjectKind='person' → 'naturalPerson' in the seed path (subjectKind is free-string today — normalize, don't validate-block)
NEVER Object/Statement for anchors (B1).
```

### DM-4 — Invite/fusion data (W7, extends existing)
```
Invitations: existing (token, expiry, role, per-token/IP caps) — PRESERVED (M6); token single-use + capacity checks
Fusion merge table (M10):
  auth identities (Supabase): keep both → merge identity
  memberships: UNION across orgs
  api_keys: UNION (decided R2-11; revisit under W10 RBAC)
  graph data: stays per-org, NO cross-org merge
OTP proof-of-control: required on BOTH mismatch-override paths (fuse AND accept-with-mismatch) —
  privilege-accumulation vector closed on both (P1 fix)
Atomic new-user accept: SECURITY DEFINER RPC, conditional-update atomicity (provision_team pattern),
  per-IP + per-email rate caps, token single-use (abuse posture from /v1/register; P2 fix)
```

### DM-5 — Builder catalog (W8, extends tool_registry.py)
```
Registry entries gain: catalog reference note (module docstring/registry entry):
  "This module is referenced in the builder capability catalog (onboarding) — if you add
   or rename an extractor/indexer, update the catalog reference."
Inventory (W8b): session recorder, session extractor, document indexer, (future) document extractor.
```

### DM-6 — Telemetry events (W11)
```
Events: onboarding_seed_complete, onboarding_decide_complete
Emitters: hosted_api write paths + MCP tools + self-hosted where applicable
Dedup: STRUCTURAL (P2 fix) — emit the event only when the completed-step EDGE is NEWLY CREATED (first-write-wins
  keyed MERGE transition), which is exact-once by construction and survives restarts/multi-worker; the in-process
  set (analytics.py _first_api_call_seen pattern) is kept only as a fallback for emitters without graph access
Sinks: dashboard + server records
No threshold (R2-6 — funnel visibility only)
```
---

# 5. Architecture

### A-1 — Component topology (target state)
```
┌─ Dashboard (React) ─────────────────────────────┐
│ Overview (3 elems) · Settings tab (W4 owner) ·  │
│ Wizard (5 human steps) · Fork card · Setup guide│
│ card · Invite landing · Pending-invites afford. │
└──────┬─────────────────────────────────────────┘
       │ REST
┌──────▼─────────────────────────────────────────┐
│ hosted_api.py (FastAPI)                         │
│ /v1/onboarding/state (READ surface — store     │
│   changes to graph) · /v1/teams (org-create +  │
│   OnboardingState init in transaction) ·        │
│ /v1/invites* (PRESERVED, W7 extends) ·         │
│ DELETE /v1/sessions/{id} (W6) · telemetry emit │
│  (W11) · builder catalog endpoint (W8)          │
└──────┬─────────────────────────────────────────┘
       │ MCP
┌──────▼─────────────────────────────────────────┐
│ Agent (6 harnesses)                             │
│ install skill (W2 SKILL.md) · seed (W3) ·       │
│ decide nudge · capture disclosure (W6)          │
└──────┬─────────────────────────────────────────┘
       │ graph
┌──────▼─────────────────────────────────────────┐
│ FalkorDB graph                                 │
│ OnboardingState node · Organization+User        │
│ Subjects (memberOf) · decisions · sessions      │
└────────────────────────────────────────────────┘
Supabase control plane: teams / team_memberships / api_keys / invitations (identity + tenant facts)
Self-hosted (W12): selfhost_api.py + local FalkorDB — no Supabase dependency introduced
```

### A-2 — Key architectural decisions
1. **Store SPLIT (W5, P1 fix):** onboarding state splits — FLOW state (fork/status/version/completed_steps/member_progress) moves to the graph `OnboardingState` node; OPERATIONAL keys (github_*, session_recording, capture receipts, install probes) STAY in Supabase jsonb where their live consumers read them. `/v1/onboarding/state` merges both and KEEPS the existing envelope (wire compat). Rail 1: node plumbing ships with W1/W2; migration/events/mirror land after the agent flow works.
2. **Single-owner Settings (R2-10):** W4 builds the tab; W6/W9 consume — no parallel-merge conflicts.
3. **Agent as user (W2/W3):** agent reads/writes the graph directly (the product's own store holds its own state — generalization of tenant-keyed architecture); harness self-adjudication instead of chooser UI (chooser archived, not deleted).
4. **Invites preserved (M6):** W7 extends existing `/v1/invites*` — never re-creates accept mechanics; 403→3-path is an additive response contract, not a silent behavior change (P2 fix).
5. **Catalog via registry (R2-9):** W8 extends tool_registry.py — no new infra.
6. **No new services:** all changes within existing components (dashboard, hosted_api, agents, graph, Supabase).
7. **Graph-side atomicity (P1 fix):** org-create has NO cross-store (Supabase+FalkorDB) transaction today; the OnboardingState node is created in the SAME eager Cypher statement as TeamMeta (graph-side atomic), with read-side tolerance for orphan nodes (defaults on read + sweep).

### A-3 — Failure modes & resilience
- Org-create + OnboardingState init: **graph-side atomic** (same eager Cypher statement as TeamMeta — one graph transaction); no cross-store transaction is claimed. On graph failure (graph DOWN): node reads return explicit FLOW `status: 'unavailable'` marker per DM-2 — never fabricated defaults (P2 fix). On absent node (orphan case, graph UP): reads return defaults (idempotent no-op) + orphan sweep alongside `_journal_append_product`. NO claim of cross-store rollback (P1 fix).
- Agent install: config-write failure → teach-human fallback; connection verify retry with honest diagnostic.
- Decide: LLM-provider fail-closed surfaced honestly (existing 503 pattern), not silent skip.
- Capture delete: session + receipt cleanup in one operation; authz until W10.
- Backfill: idempotent, no re-onboarding of existing users, grandfathering preserved; fork defaults at READ time, persisted only on explicit opt-in.

---

# 6. Interfaces

### I-1 — Onboarding state (read surface KEEPS the envelope; store SPLIT — P1 fix)
```
GET  /v1/onboarding/state          → 200 {onboarding: {<legacy operational keys unchanged: github_*,
  session_recording, session_capture_receipt_*, install_probe_*, …>, fork, status, version,
  completed_steps[], member_progress}, email}   ← envelope + legacy keys PRESERVED (wire compat);
  status: 'unavailable' marker on FLOW keys when graph down (never fabricated defaults — P2 fix)
WRITE SURFACES (P2 fix — relationship pinned):
  PATCH /v1/onboarding/state   = dashboard/legacy wire-compat path (kept); allowlist re-hosted;
    jsonb-first, graph-second write order; sweep reconciles on graph failure
  POST /v1/onboarding/state/checkpoint = agent/internal path (new); idempotent keyed MERGE on
    {org_id, step_id}; team derived from AUTH CONTEXT (never client-supplied org_id — P2 fix);
    member_progress writes: user-scoped keyed MERGE on {org_id, user_id} under member_progress
    (per-step first-write-wins, EXEMPTED from canonical step_id rejection — keys are user_ids, not steps —
    P2 fix: pinned write surface so W7's invitee inline setup can persist per-member progress)
  DM-2 allowlist mirror: member_progress included in FLOW keys (per-user map-merge semantics)
  PER-KEY-TYPE SEMANTICS (P2 fix): step-edge keys (completed_steps, capture-disclosed, catalog-presented) =
    keyed MERGE first-write-wins, 200 no-op on replay; node-property keys (fork) = set-once (second write = 409 or
    read-only once set); last_decide_attempt = last-write-wins per attempt (200 on change; retry must be
    recordable — P2 fix), enum 'failed' | 'dismissed' | null (success clears to null). Allowlist maps each key to its semantics.
  COMPLETION ON THE WIRE (P2 fix): the merged GET projects graph status: 'complete' → onboarding_complete: true
  (wire-compat — keeps the plan's pervasive onboarding_complete assertions valid for NEW orgs); the legacy
  onboarding_complete key remains for grandfathered orgs. W5 (read surface) + W3 (WF-2 gate) child issues
  carry this pin (W11 remains the event emitter on the gate's edge transitions — P2 fix reattribution).
Errors: 401 (no team context), 404 (no org), 409 ONLY for node-level conflicts (concurrent init with
  different org_id) or set-once property re-write — NOT on step-edge/checkpoint re-write (idempotent no-op)
```

### I-2 — Org-create (W1/W9; extends #1877)
```
POST /v1/teams  (existing) → body gains nothing user-facing; backend:
  - name REQUIRED (validation: non-empty; 409 on duplicate)
  - OnboardingState node init INSIDE the create transaction
  - onboarding FIRES here (state armed)
Response: 201 {team_id, onboarding_state: {...}} (front-end renders fork card next)
```

### I-3 — Universal setup command (W2)
```
One command, 6 harnesses (harnesses.js HARNESS_NAMES preserved):
  Claude Code:   claude mcp add tortoise --transport http <url>
  Cursor:        config write (.cursor/mcp.json)
  Codex:         config write
  Pi:            config write (tortoise-config.json)
  Claude Desktop: config-file (manual — teach-human)
  Claude Web:     paste instruction (manual — teach-human)
Contract: every harness reaches a connected state verifiable via tortoise_health; result written back to onboarding state.
```

### I-4 — Seed + decide (W3)
```
MCP: tortoise_create_subject (existing) ×2 — org + user; edge memberOf via existing SDK
Hosted: anchor data from JWT (user_id, email, app_metadata) + teams.name; ask-once for gaps
tortoise-decide: existing skill (graph-scripts/decide.py / skills/tortoise-decide) — nudge, not re-implement.
  PROVISIONING (P2 fix): the decide protocol is defined in W2's SKILL.md as a GENERIC MCP-tool protocol
  (options→criteria→findings→IMPL/NAND→EP ranking via existing tortoise MCP tools) — it does NOT depend on
  the local tortoise-decide SKILL file being present, so all 6 harnesses reach the magic moment; the skill
  file is a convenience, not a prerequisite. DE2E-5 asserts decide-protocol availability for all 6.
Completion: fork-aware gate (see WF-2) — self: two Subjects + decide + connected; build: org-anchor +
  connected + catalog-once; compact: org-anchor Subject + harness-connected ("first point" = the org-anchor
  itself; alias note in J1/J5/WF-2). NOT a single self-only gate (P1 fix).
  SELF-HOSTED fork (P2 fix): selfhost node init defaults fork='self' — the fork card is never surfaced on
  self-hosted (no dashboard surfaces); DE2E-10 asserts the node carries fork='self'.
```

### I-5 — Capture disclosure (W6)
```
DELETE /v1/sessions/{session_id} → 200 {deleted: true} | 404
  - removes session + capture receipt (graph + receipt cleanup)
  - authz: team-member (until W10)
First-capture announcement: in-conversation one-liner from the capture path (no new endpoint)
```

### I-6 — Invite/fusion (W7, extends existing M6 infra)
```
Preserved: POST /v1/invites · GET /v1/invites/info · POST /v1/invites/accept ·
           GET /v1/invites/pending · POST /v1/invites/pending/{id}/accept ·
           DELETE /v1/invites/pending/{id} · DELETE /v1/invites/{id}
New (W7):
  - 3-path fusion choice (client-side presentation of existing mismatch cases)
  - OTP verify endpoint (proof-of-control) — REQUIRED on BOTH mismatch-override paths: fuse AND
    accept-with-mismatch (P1 fix — the invitee-email holder must prove control either way)
  - atomic new-user accept (account + membership in ONE call — SECURITY DEFINER RPC with conditional-update
    atomicity per provision_team/invitation_accept pattern; per-IP + per-email rate caps; token single-use)
  - admin resend/expire UI (uses existing endpoints)
  - COMPAT NOTE (P2 fix): existing POST /v1/invites/accept hard-403s on email mismatch. W7 turns the
    same-person-mismatch case into a 3-path response — declared as an ADDITIVE response contract with an
    EXPLICIT opt-in mechanism: `Accept: application/vnd.tortoise.onboarding+json;version=2` header (legacy
    default = 403 unchanged; opted-in clients receive the 3-path shape). DE2E-7 asserts the legacy 403 path
    is byte-unchanged when not opted in (P2 fix — mechanism pinned, not asserted-into-existence).
  - capacity: accept at member-cap/1-free-team entitlement → explicit error or surfaced upgrade path (never silent)
  - free-team 402 at org-create (P1 fix): multi-org compact is paid-tier-only; free user creating org B →
    explicit 402 upgrade surface in the wizard (never a silent dead-end); aligned with the accept-side capacity note
```

### I-7 — Builder catalog (W8)
```
GET  /v1/capabilities (or registry read via tool_registry extension)
  → 200 {modules: [{name, kind: indexer|extractor, description}]}  (pullable, accurate)
  → per-module note in module docstring (W8b)
```

### I-8 — Telemetry (W11)
```
Events emitted at write paths (hosted_api + MCP + self-hosted where applicable):
  onboarding_seed_complete {org_id, ts}
  onboarding_decide_complete {org_id, ts}
Dedup once per org (analytics.py pattern); dashboard + server records; no threshold
```

### Versioning & error strategy
- OnboardingState node versioned (bump on flow change); migrations versioned.
- All new/changed endpoints: 4xx for client errors (validation, authz), 5xx surfaced honestly (fail-closed on LLM), idempotent where writes (MERGE/never-overwrite).
- Backward compat: `/v1/onboarding/state` response shape unchanged (store swap invisible); invite endpoints unchanged (additive).

---

# 7. Detailed E2E Test Cases

> Aligned 1:1 with scope high-level E2E-1…E2E-12 (epic-verify checks this chain). Each is implementation-ready (setup + assertions). Test-design surface refs [#N] from `04-test-design.md`.

### DE2E-1 — One-sitting first-run [surfaces 1,3,4,5,7,8,17] — E2E-1
**Setup:** fresh Supabase test tenant + empty FalkorDB graph; mock agent (scripted).
**Steps:** (1) signup identity+email; (2) create org (name required, prefill shown); (3) fork card renders once, pick self-use; (4) run universal command (mock harness); (5) agent files Organization Subject + User Subject linked memberOf; (6) agent nudges decide; user answers one decision.
**Assert:** org+user Subjects exist with correct subclasses + memberOf edge; decide produced EP ranking; onboarding_complete = true; **no LEGACY #1643 form wizard surface appeared — only the 5 human steps rendered** (P2 fix — the new wizard is also 5 steps, so the assertion targets the legacy surface); **legacy #1643 wizard components remain ARCHIVED-not-deleted while the A0 gate is open (rollback path preserved)** (P2 fix — deletion would pass the surface-absence assertion but break A0 rollback); **OnboardingState node exists with version=1 and completed_steps matching the run, AND has the onboards edge → Organization Subject (org_subject_id set after seed)** (P2 fix — surface 1 write contracts + node↔anchor link asserted); W11 events fired once.

### DE2E-2 — Overview calm [9,16] — E2E-2
**Setup:** completed org.
**Steps:** open Overview.
**Assert:** exactly 3 elements (connection status, memory digest, next action); zero toggle components; each of github_connected/github_indexed/github_docs_indexed/session_recording reachable only via Settings → Memory sources; **copy sweep: Overview/Settings render "Organization" (never "team"/"workspace" in user-facing labels)** (P2 fix — surface 16 exercised).

### DE2E-3 — Org-name capture [3,16] — E2E-3
**Setup:** fresh user, no org.
**Steps:** org-create renders.
**Assert:** name field required (empty submit blocked with validation message); editable prefill (not silent username); 100% of non-invited orgs have explicit name.

### DE2E-4 — Ontology-precise seed [7,15] — E2E-4
**Setup:** connected agent; org with display_name in metadata.
**Steps:** seed runs.
**Assert:** Organization (Subject/organization) + User (Subject/naturalPerson) linked memberOf; neither filed as Object/Statement; existing person→naturalPerson normalized; no invented identity (name from API or asked, never placeholder); **same-name collision: two orgs/users with the same name → disambiguation asked, never silent MERGE of distinct identities** (P1 fix).

### DE2E-5 — Agent install 6 harnesses [5,6] — E2E-5
**Setup:** 6 harness configs (harnesses.js).
**Steps:** run universal command per harness.
**Assert:** 4 CLI harnesses self-install (config written correctly — validated by re-read); 2 manual harnesses receive correct teach-human steps; connection verified via tortoise_health; harness-connected checkpoint set; **install skill contracts: SKILL.md exists at the defined path, reads OnboardingState; AGENT_ONBOARDING.md archived — exactly ONE live onboarding script** (P2 fix — M8 asserted, a two-live-scripts regression must fail this test); **decide-protocol availability: SKILL.md's generic MCP-tool decide protocol (options→criteria→findings→EP ranking via existing tortoise MCP tools) produces a ranking on each of the 6 harnesses WITHOUT the local tortoise-decide skill file** (P2 fix — I-4's provisioning claim actually tested).

### DE2E-6 — Graph-held resumption [1,2,10] — E2E-6
**Setup:** (a) org where agent connected but no decide (incomplete, NEW flow); (b) legacy org with jsonb onboarding_state pre-seeded (for backfill assertions).
**Steps:** re-enter via Settings Setup guide; run backfill on (b).
**Assert:** resumes at "first decision" (never restarts); state read from OnboardingState node (not jsonb) for FLOW keys; Setup guide card renders same state; **legacy backfill: (b)'s jsonb → node is a no-op on re-run; grandfathered orgs keep onboarding_complete; operational keys (github_*, session_recording) still read from jsonb and work** (P1 fix — backfill now actually tested).

### DE2E-7 — Invite fusion [12] — E2E-7 (most failure-prone)
**Setup:** existing user A (email x); invite to email y (same person); no OTP yet.
**Steps:** click invite; also call POST /v1/invites/accept with a mismatch WITHOUT the v2 Accept header.
**Assert:** 3-path choice presented (fuse default, never silent); fuse path requires OTP; **accept-with-mismatch ALSO requires OTP — without OTP, blocked** (P1 fix); with OTP → fused identity (memberships/api_keys UNION, graph per-org); "accept-with-mismatch" records mismatch; different-person mismatch → error + sign-in-as-invitee; **pending-invites affordance (team switcher) + admin resend/expire, with expiry honored on the invite link** (P2 fix — surface 12 fully exercised); **legacy 403 path byte-unchanged when NOT opted in (no v2 header → 403 byte-identical to pre-W7)** (P2 fix — mechanism pinned, not asserted-into-existence).

### DE2E-8 — Atomic new-user accept [1, 12] — E2E-8
**Setup:** new user clicks invite.
**Steps:** pre-filled email → name+password → accept.
**Assert:** account + membership created in ONE action (no intermediate "create then accept" state); lands in org; no fork card at accept (fork is creator-only); **agent setup present as an inline SKIPPABLE first action — can skip without blocking; per-org onboarding state armed; invitee inline setup writes member_progress (user-scoped) WITHOUT advancing org-level steps; skipping never fakes org-level completion** (P2 fix — member_progress write surface + E2E-8's And-clause asserted); **consumed-token re-click → idempotent "already in org", no double membership** (P1 fix); **rate caps apply (per-IP/per-email)** (P2 fix).

### DE2E-9 — Builder catalog [13] — E2E-9
**Setup:** org with fork=build.
**Steps:** open build path.
**Assert:** catalog presented once (once-only flag); registry endpoint returns indexers+extractors (session recorder, session extractor, document indexer); every module carries the catalog-reference note.

### DE2E-10 — Self-hosted [15] — E2E-10
**Setup:** self-hosted instance (no Supabase).
**Steps:** agent setup on self-hosted.
**Assert:** two prompts (name, org) → two Subjects linked memberOf → one decide → no Supabase call; OnboardingState node inited **at selfhost SDK/API init (the chosen trigger — ONE owned write point; idempotent no-op if present)** and carries **fork='self'** (P2 fix — self-hosted never surfaces the fork card; the fork-aware gate evaluates).

### DE2E-11 — Capture disclosure [11] — E2E-11
**Setup:** self-use org, session recording on (default).
**Steps:** first capture; view/delete in Settings.
**Assert:** in-conversation announcement; DELETE /v1/sessions/{id} removes session + receipt; no consent re-gate (#1927 preserved — no new gate UI); member authz enforced.

### DE2E-12 — Cross-W full journey [1-17] — E2E-12 (W5 owns)
**Setup:** fresh tenant + empty graph.
**Steps:** signup → org → fork → connect → seed → decide (one sitting, self fork).
**Assert:** all DE2E-1…DE2E-6 pass in one sitting; W11 seed_complete + decide_complete fire once (dedup); onboarding_complete set only on aha + checklist done (dismissal alone never completes); walk-through friction test clean (no "what do I do now" moment); **fork-aware gate: build fork completes on org anchor + connected + catalog-once (no decide) (P1 fixes); compact org-B completes on seed-lite (org anchor) + harness-connected (P1 fix); org B never re-asks the fork card (P2 fix, per J5 fork inheritance)**.

### Negative cases (embedded where applicable)
- Concurrent org-creates → exactly one OnboardingState node (graph-side atomic).
- Concurrent invite-accepts same token → one membership (token single-use).
- Delete session during capture → no orphaned receipt.
- Fork re-ask after completion → never (per-org once; compact inherits).
- OTP wrong/expired → blocked with clear error.
- Checkpoint re-write (idempotent replay) → 200 no-op, NOT 409 (P2 fix).
- **LLM 503 during decide (P1 fix):** mock provider failure → honest surface (no silent skip), node records decide-attempted-failed (distinct from dismissed), Setup guide reachable again on retry, no stranded completion.
- **Free user creates org B (P1 fix):** 402 upgrade surface (explicit, never a silent dead-end); compact path is paid-tier-only; accept-side capacity note aligned with org-create-side 402.
- **Graph-down render (P2 fix):** mock graph failure → merged GET returns FLOW status 'unavailable'; Setup guide card renders DEGRADED state, never a false "N of 4 complete".
- **PATCH dual-store divergence (P2 fix):** jsonb write succeeds, graph write fails → orphan/divergence sweep reconciles; next read consistent; no lost fork/completed_steps.

---

# 8. Coherence Review + Risk Analysis

### Coherence checks (cross-substep)
- **Journeys ↔ Scope:** J1-J8 cover all 17 value-map capabilities (J7 = self-hosted W12, J8 = capture W6, J6 = legacy migration); E2E-1…E2E-12 each have a journey phase + DE2E counterpart.
- **Workflows ↔ Interfaces:** every workflow's failure mode has a defined error path in §6; every new interface has a workflow owner.
- **Data model ↔ Architecture:** OnboardingState node maps to A-1 component (graph); legacy operational keys stay in jsonb (store split); no new service.
- **Test design ↔ Plan:** all 17 surfaces from #1992 referenced across DE2E cases; every surface has ≥1 test in §7.
- **Rails:** Rail 1 (W5 node plumbing with W1/W2; migration later) — reflected in A-2.1 + DE2E-6 + J6. Rail 2 (launch slice W1-W5,W9,W11 vs follow-on W6/W7/W8/W12; W10 last) — reflected in the decomposition plan. Named couplings: fork-build→W8 placeholder (J2/P4); **join→W7 legacy defer at launch (P1 step 2 — NEW, P2 fix)**; capture-disclosed→W6 (DM-1/WF-4; **announcement fires at first capture, copy contract owned by W2, view/delete by W6 — P2 fix, timing pinned**).
- **Legacy-org interim node (P2 fix):** before W5 backfill lands (Rail 1), hosted legacy orgs (the owner's) have NO node — FLOW-key writes (fork card) use idempotent create-on-write (node absent → create then MERGE), reconciled with test-design surface 1's no-lazy-init rule by limiting create-on-write to grandfathered orgs only; new orgs always init in the org-create eager statement.

### Risk register
| Risk | Severity | Mitigation |
|------|----------|-----------|
| A0 falsified (wizard friction not representative) | Medium | W11 events + walk-through on first cohort; if low friction, rebuild was premature — but seed/decide improvements still valuable |
| W5 store migration destabilizes onboarding | Medium | Rail 1 + store SPLIT (flow→graph, operational→jsonb) + envelope preserved + idempotent keyed MERGE regression tests; thin vertical slice |
| "Agent knows its own harness" fails for some harness | Medium | Teach-human fallback for 2 manual harnesses; universal command is the handoff; chooser archived (recoverable) |
| Invite fusion privilege accumulation (security) | High | OTP proof-of-control on BOTH mismatch-override paths (fuse + accept-with-mismatch); permissions-consequence copy; regression test DE2E-7 |
| Invite-hijack via accept-with-mismatch (security) | High | OTP on the accept-with-mismatch path (P1 fix); DE2E-7 asserts blocked-without-OTP |
| Capture delete orphans graph data | Medium | Session + receipt cleanup in one op (I-5); DE2E-11 |
| Parallel merge conflicts on Settings (W4/W6/W9) | Medium | Single-owner R2-10 (W4 owns; W6/W9 consume) |
| Existing-org migration breaks (owner org) | Medium | Store split (operational keys untouched) + grandfathering + fork-default-at-read + idempotent backfill; J6 covers it |
| Universal command breaks a harness config | Medium | Config-write validation per harness; teach-human fallback; DE2E-5 all 6 |
| Completion semantics regress (complete w/o decide) | Medium | DE2E-12 asserts fork-aware gates; onboarding_complete set ONLY on the fork's gate (self: + decide; build: + catalog-once; compact: org-anchor Subject + harness-connected) |
| Anchor MERGE collisions corrupt identity | High | Collision check + disambiguation before MERGE (DM-3); DE2E-4 same-name assertion; org_id/user_id as stable refs |
| Checkpoint authz: cross-org write | High | Team derived from auth context, never client-supplied org_id (I-1 P2 fix); regression test |
| Atomic accept abuse (spam/account creation) | Medium | SECURITY DEFINER RPC + per-IP/per-email rate caps + token single-use (DM-4/I-6 P2 fix) |
| LLM provider failure at decide strands completion (P1 fix) | Medium | decide-attempted-failed recorded (distinct from dismissed); honest surface + retry affordance; card stays reachable; DE2E negative (mock 503) |
| Free-team 402 dead-ends multi-org compact (P1 fix) | Medium | Compact = paid-tier-only; explicit 402 upgrade surface at org-create (never silent); negative DE2E; aligned accept-side capacity |
| Graph-down renders false checklist (P2 fix) | Medium | status:'unavailable' FLOW marker + degraded card state (P5); DE2E for graph-down render |
| Decide protocol unreachable without local skill (P2 fix) | Medium | W2 SKILL.md defines generic MCP-tool protocol; skill file is convenience not prerequisite; DE2E-5 asserts all 6 |
| W11 dedup multi-worker double-fire (P2 fix) | Medium | Structural dedup: emit on edge NEW-CREATION transition (exact-once by construction); in-process set is fallback only |
| Legacy person subjects persist (P2 fix) | Low | One-time invariant sweep (or subclass-semantics queries) alongside seed-path normalization |
| PATCH dual-store divergence (P2 fix) | Medium | jsonb-first graph-second order + sweep reconciliation; negative test jsonb-ok/graph-fail |

### Improvement opportunities (reviewer-identified)
- First-capture announcement TIMING pinned (P2 fix): fires at FIRST CAPTURE (epic body W6 semantics — announced in-conversation, non-blocking, per WF-5/DE2E-11); the announcement's COPY CONTRACT is owned by W2's SKILL.md (agent-side, in-conversation); W6 owns Settings view/delete. The connect-consent fold remains a permitted future optimization ONLY if it changes nothing about the first-capture trigger (the fold would move the ANNOUNCEMENT UI, not the checkpoint timing).
- Consider a single "fork+connect" screen for product-literate re-entry (compact path) to minimize re-entry friction.
- W11 dedup via edge-creation transition doubles as the future funnel dashboard's data source (no new telemetry infra later).
- A0 review gate (P2 fix): schedule a first-real-cohort walk-through + W11 funnel read with an owner and decision date in the decomposition plan; legacy #1643 wizard surfaces are ARCHIVED (like the chooser), not deleted — rollback path preserved while A0 is open.

### Decomposition plan (feeds epic-decompose)
Launch slice (independently mergeable): W1, W2, W3, W4, W5, W9, W11.
Follow-on waves: W6, W7, W8, W12.
Last: W10 (needs RBAC first — explicitly deferred).
Dependency spine: W5-node-plumbing → W2 → W3 → W9; W4 (Settings owner) → W6/W9 consumers; W1 (wizard shell) → W2 fork card; W8 extends tool_registry (independent); W12 self-hosted (independent, small); W11 telemetry rides the write paths (W3/W8).

**A0 review gate (P2 fix — carried into decomposition):** attached to W11 (telemetry). First-real-cohort walk-through + W11 funnel read, OWNER: product owner, DECISION DATE: first cohort of ≥5 real orgs or 30 days after launch slice ships (whichever first). Decision: keep full agent-driven rebalance | partial revert (restore archived #1643 wizard) | proceed. Legacy wizard surfaces remain ARCHIVED (not deleted) until this gate passes, preserving rollback.

---

## Review-gate record

**Gates:** per-substep reviewers (dispatched via `task`) — recorded as they ran.

**Substep reviews (fresh-context, dispatched via task):**
- **§1-3 (journeys/workflows/prototype):** reviewer returned 9 issues (2 P1: fork-aware completion + state ownership; 7 P2) → all fixed in-doc; re-dispatched → 3 residuals fixed → CLEARED.
- **§4-6 (data model/architecture/interfaces):** reviewer code-verified against hosted_api.py/sdk.py/supabase_control.py/ONTOLOGY.md; returned 10 issues (4 P1: checkpoint contract #398 misattribution, store-swap backward compat, OTP-on-both-paths, cross-store transaction overclaim; 6 P2) → all fixed → CLEARED.
- **§7 (detailed E2E) — e2e-coverage reviewer:** verified 12/12 high-level E2E have DE2E counterparts + surface-bracket fidelity; returned 6 P2 assertion-level gaps → all fixed → CLEARED.
- **§8 coherence (parallel dispatch ×2: cross-substep-drift + risk-completeness):** cycle 1 → 9 + 10 issues (3 P1: compact-path unsatisfiable, LLM-503, free-team 402; 16 P2) → all fixed; cycle 2 → 7 P2 → fixed; cycle 3 → 7 P2 (incl. build-gate unification, capture-disclosed semantics, catalog-presented storage) → fixed; cycle 4 → 5 P2 → fixed; cycle 5 → 1 P1 (last_decide_attempt 409 contract) → fixed; cycle 6 → 2 P2 pins → fixed; cycle 7 → 2 P2 (DM-2 allowlist, W11 reattribution) → fixed; cycle 8 → 1 P2 (member_progress write surface) → fixed + synced to test-design #1992; cycle 9 → 3 P2 sync gaps → fixed; cycle 10 → 4 P2 labels/assertions → fixed; cycle 11 → 2 P2 → fixed. **Final: NO ISSUES FOUND — coherence CLEARED.**

**Test-design sync cycles (04-test-design.md ↔ plan):** 3 fix cycles syncing plan pins into the #1992 surface map (fork-aware gates, store split, OTP both paths, member_progress, onboards edge, graph-down, self-host fork, 402, collision, archived-not-deleted) → CLEARED.

## Human Gate #2 — Plan review

**APPROVED 2026-08-29 (in-session — user replied "proceed" to the plan review).**
