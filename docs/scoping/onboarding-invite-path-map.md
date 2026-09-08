# Onboarding + Invite Path Map

> Scope document for the onboarding/invite intersection (epic #1976 follow-up).
> Maps how user journeys through OAuth sign-in, the onboarding wizard, and invite flows — and where they miss each other.

## Entry Points

| Entry | URL | Auth state | Initial destination |
|-------|-----|------------|-------------------|
| **OAuth sign-in** | `tortoise.premiselabs.co/auth` → Google → `app.premiselabs.co/#access_token=...` | Fresh session | `app.premiselabs.co/` (welcome mode) |
| **Invite link** | `app.premiselabs.co/?invite_token=XYZ` | Any | Dashboard with stashed invite |
| **Direct access** | `app.premiselabs.co/` | No session | Bounced to `/auth` |

---

## Current Path: New user (no org, no invites)

```
/auth ──▶ Google OAuth
           │
           ▼ GoTrue callback → Site URL (app.premiselabs.co)
           │  302 with #access_token=...
           │
           ▼ supabase-session.js (head gate)
           │  #2529 fix: sync fragment → cookie
           │
           ▼ index.html gate: hasCallbackFragment=true → pass
           │
           ▼ React mount gate: getSession() → session found
           │
           ▼ welcomeMode = true (first-run heuristic)
           │
           ▼ loadTeams() → [] (no memberships yet)
           │  currentTeamId = null
           │  welcomeHasOrg = false
           │
           ▼ Wizard renders at Step 0 (Orientation)
               │
               ├── Step 0: "Here's what happens next…" → Continue
               │
               ├── Step 1: "Create your Organization"
               │     ┌──────────────────────────────────────┐
               │     │  Organization name                   │
               │     │  [ input field ]                    │
               │     │  [ Create ]                         │
               │     └──────────────────────────────────────┘
               │     → provisionInApp() → team created
               │     → welcomeTeamReady = true
               │     → loadTeams() → [team]
               │     → Continue
               │
               ├── Step 2: Fork card (self / build)
               │
               ├── Step 3: Connect (key shown once)
               │
               └── Step 4: Done (handoff to agent)
```

---

## Current Path: Invited user (invite link → OAuth → wizard)

```
Original URL:  app.premiselabs.co/?invite_token=XYZ
  │
  ├── No session? → bounceToAuth → OAuth sign-in
  │                  OAuth returns to app.premiselabs.co/?invite_token=XYZ#access_token=...
  │
  ▼ React mount gate (L2742)
     │  invite_token stashed in sessionStorage → URL stripped
     │
     ▼ Session found → acceptStashedInvite() fires (L2860)
        │
        ▼ POST /v1/invites/accept { token: stashedInvite }
           │
           ├── 200 OK → setBanner('Welcome to the team! Your membership is active.')
           │            ← ⛔ does NOT call loadTeams() or set welcomeTeamReady
           │
           ├── 409 (already member) → silent (no error)
           │
           └── error → setBanner(error message)
              │
              ▼ stashedInvite consumed in all cases (sessionStorage.removeItem)
                 │
                 ▼ welcomeMode = true (first-run heuristic — still fires because
                 │  user has no prior session history)
                 │
                 ▼ Wizard Step 0: Orientation → Continue
                    │
                    ▼ Wizard Step 1: org-create
                       │
                       welcomeHasOrg = ?
                       │
                       ├── IF loadTeams() effect already ran and populated teams[]
                       │   → welcomeHasOrg = true
                       │   → **READ-ONLY summary** (works correctly!):
                       │     "You're set up in [org name]. Next, choose how you'll use Tortoise."
                       │   → Continue to Step 2 (fork)
                       │   ✅ This path actually works — BUT the banner was
                       │      the only indication the invite was accepted.
                       │      No visual signal on the wizard itself.
                       │
                       └── IF loadTeams() hasn't propagated yet (race)
                           → welcomeHasOrg = false
                           → **ORG-CREATE INPUT** ← ⛔ GAP 2
                           → User is asked to create a NEW org they don't need
```

---

## Key Gaps

### Gap 1: `acceptStashedInvite()` doesn't propagate to wizard state

**File:** `main.jsx` L2754-2765

```javascript
// Current — just a banner:
if (inviteRes.ok) {
  setBanner('Welcome to the team! Your membership is active.')
  // ← No loadTeams(), no setWelcomeTeamReady(), no wizardStep advance
}
```

**Contrast with the account-menu accept** (L3578) which DOES:

```javascript
if (res?.team_id) {
  await loadTeams()
  switchTeam(res.team_id)
}
```

**Impact:** The teams list and `currentTeamId` only update when the asynchronous `loadTeams()` effect fires spontaneously — leaving a window where the wizard thinks there's no org.

### Gap 2: Wizard Step 1 has no "join invited org" affordance

**File:** `main.jsx` L5360-5440 (wizard step 1 render)

When `welcomeHasOrg = false`, Step 1 only shows the org-create input. The step sub-text *says*:

> *"Name your organization… Or accept an invitation to join one."*

But there's **no UI** to actually act on that. The pending-invites list only lives in the account menu dropdown — it's invisible on the wizard.

### Gap 3: No invite awareness for users arriving without `?invite_token=`

A user who signed up normally but has **pending invites** (someone invited them earlier, they never acted on it) goes through Path A — org-create — and creates a second org they don't need.

### Gap 4: Invite success is a banner, not part of the flow

A banner disappears on refresh or navigation. The wizard doesn't acknowledge "You joined [org name]" anywhere — the only confirmation is a transient toast.

---

## Current Path: Pending-invite accept (from account menu)

```
Account menu → Invites section
  │
  ▼ Click [Accept]
     │
     ▼ POST /v1/invites/pending/:id/accept
        │
        ├── 200 OK → loadTeams() → switchTeam(team_id)
        │             ˌ ˌ → team data loads for the invited org
        │
        ├── Error (cap reached, expired, etc.)
        │    → Error shown inline on the invite card
        │    → Invite stays in list
        │
        └── Decline → DELETE /v1/invites/pending/:id
             → Invite removed from list
```

This path does everything correctly — loads teams, switches to the invited org. **But it only works after the user is past onboarding** (dashboard rendering, not in welcome mode).

---

## Key State Variables

| Variable | Set by | Purpose |
|----------|--------|---------|
| `welcomeMode` | URL path `/welcome` or session mount heuristic | Shows wizard vs dashboard |
| `welcomeTeamReady` | `provisionInApp()` success | Org was just created this session |
| `teams` array | `loadTeams()` | All memberships (GET /v1/teams) |
| `welcomeHasOrg` | Computed: `welcomeTeamReady \|\| teams.length > 0` | Whether Step 1 is input or summary |
| `stashedInvite` | `sessionStorage.getItem('tortoise.inviteToken')` | In-flight invite from URL param |
| `pendingInvites` | `loadPendingInvites()` (on account menu open) | Pending invites not yet acted on |
| `currentTeamId` | `setCurrentTeamId()` in loadTeams fallback | Active team for data loading |

---

## Target Flow (ideal end state)

### Invited user with invite link

```
/auth → OAuth → app.premiselabs.co/?invite_token=XYZ#access_token=...
  │
  ▼ Session mount
  │  stash invite → acceptStashedInvite() → loadTeams() → switchTeam()
  │  → Team is loaded and selected before wizard renders Step 1
  │
  ▼ welcomeMode = true
  │  welcomeHasOrg = true (teams.length > 0)
  │
  ▶ Step 0: Orientation
      "You've been invited to [org name]. Welcome!"
      → Continue
  │
  ▶ Step 1: org-show (not org-create)
      ┌─────────────────────────────────────────┐
      │  ✅ You're a member of                   │
      │  **Acme Corp** — Free plan               │
      │                                         │
      │  Your workspace is ready.                │
      │  [ Continue ]                            │
      └─────────────────────────────────────────┘
      → Skip fork if org already has fork set
      → Or show fork card to choose
  │
  ▶ Step 2 (fork if needed → connect → done)
```

### User with pending invites (no invite link)

```
Step 1 (welcomeHasOrg = false):
  ┌─────────────────────────────────────────┐
  │  Organization name                      │
  │  [ input field                       ]  │
  │                                         │
  │  ─── or ───                           │
  │                                         │
  │  You have a pending invitation           │
  │  to join **Acme Corp**                  │
  │  [ Accept ]  [ View all ]               │
  │                                         │
  │  [ Create organization ]                │
  └─────────────────────────────────────────┘
```

### Wizard cues for invite-aware rendering

1. **Step 0 (orientation):** If a stashed invite exists or pending invites > 0, show "You've been invited to [org]" variant
2. **Step 1 (org-create → org-join):** Show both the name input AND the pending invite card
3. **Invite accept success:** Advance wizard state immediately (not just a banner) — `loadTeams()` → auto-advance to Step 2 if fork already set
4. **Re-entry for invited user:** Skip wizard entirely if onboarding is complete (already the case via `showReentryCard`)