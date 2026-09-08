# Epic Plan: Onboarding wizard — invite-aware, orientation removed, quick flow

> Epic #2534. See `docs/scoping/onboarding-invite-path-map.md` for path analysis and `docs/research/2026-09-07-onboarding-wizard-research-brief.md` for research brief.

---

## 1. User Journeys

### Journey A: New user (no org, no invite)

```
/auth → OAuth → app.premiselabs.co/ →
  welcomeMode=true, teams=[], welcomeHasOrg=false
  │
  Step 0: "Set up your Organization" (was Step 1, old Step 0 deleted)
  │  → Name input + "Create" button
  │  → Note: "Want to join an existing org? Ask your admin to invite you."
  │  → provisionInApp() → teams=[{org}], welcomeTeamReady=true
  │  → Continue to Step 1
  │
  Step 1: Fork card (was Step 2)
  │  → "self" or "build"
  │  → Continue to Step 2
  │
  Step 2: Connect (was Step 3)
  │  → Harness command + key
  │  → Continue to Step 3
  │
  Step 3: Done (was Step 4)
  │  → "Open my dashboard →"
```

### Journey B: Invited user (invite link in URL)

```
/auth → OAuth → app.premiselabs.co/?invite_token=XYZ →
  invite stashed, acceptStashedInvite() fires
  │
  POST /v1/invites/accept → 200 OK
  │  acceptStashedInvite() calls loadTeams() → teams=[{org}]
  │  welcomeHasOrg=true (from teams.length > 0)
  │
  Step 0: "Set up your Organization"
  │  → Read-only: "You accepted an invitation to **{org}**"
  │  → Continue to Step 1
  │
  Step 1: Fork card
  │  → Org already has fork set → read-only summary + Continue
  │  → (Or if first org, normal fork card)
  │
  Step 2: Connect → Step 3: Done
```

### Journey C: User with pending invites (no invite link)

```
/auth → OAuth → app.premiselabs.co/ →
  welcomeMode=true, teams=[]
  │
  Step 0: "Set up your Organization"
  │  → Name input
  │  → "─ or ─" divider
  │  → "You have a pending invitation to **{org}** → [Accept]"
  │  → "Create organization" button
  │
  If Accept clicked:
  │  POST /v1/invites/pending/:id/accept → 200 OK
  │  loadTeams() → teams=[{org}]
  │  welcomeHasOrg=true
  │  → Auto-advance to Step 1
  │
  If Create clicked:
  │  provisionInApp() as in Journey A
```

### Journey D: Returning user (has session + org)

```
app.premiselabs.co/ →
  welcomeMode=false (has prior session)
  showReentryCard at dashboard (if no points)
  → No wizard
```

---

## 2. Workflows

### Invite accept propagation

```
acceptStashedInvite() called at mount (L2754)
  │
  POST /v1/invites/accept { token }
  │
  ├── 200 OK
  │    ├── loadTeams()  ← NEW: currently just setBanner
  │    ├── setWelcomeTeamReady(true) ← NEW
  │    ├── setWizardStep(1) (skip org-create, go to fork) ← NEW for invite
  │    └── Accept from account menu (L3574) already does this — mirror it
  │
  ├── 409 (already member)
  │    └── Silent — loadTeams() handles it
  │
  └── Error
       └── Keep invite visible, show error inline
```

### Pending invites in wizard

```
Wizard Step 0 mounts
  │
  Fetch GET /v1/invites/pending (reuse loadPendingInvites from L3554)
  │
  ├── Has invites
  │    └── Render invite card below the name input
  │        Accept → POST /v1/invites/pending/:id/accept
  │                → loadTeams() → advance wizard
  │
  └── No invites
       └── Show note: "Want to join an existing org? Ask your admin to invite you."
```

---

## 3. Prototype (Markdown diagrams)

### Step 0 — No org, no invites (default)

```
┌──────────────────────────────────────────┐
│  Welcome to Tortoise                     │
│                                          │
│  Set up your Organization                │
│                                          │
│  Organization name                       │
│  ┌─────────────────────────────────┐    │
│  │ (input field)                   │    │
│  └─────────────────────────────────┘    │
│                                          │
│  Want to join an existing                │
│  organization instead? Ask your          │
│  admin to invite you to your             │
│  email, then reload this page.           │
│                                          │
│  [ ← Back ]        [ Create ]           │
└──────────────────────────────────────────┘
```

### Step 0 — Has pending invites

```
┌──────────────────────────────────────────┐
│  Welcome to Tortoise                     │
│                                          │
│  Set up your Organization                │
│                                          │
│  Organization name                       │
│  ┌─────────────────────────────────┐    │
│  │ (input field)                   │    │
│  └─────────────────────────────────┘    │
│                                          │
│  ─────────── or ───────────              │
│                                          │
│  You have a pending invitation           │
│  to join **Acme Corp**                   │
│  [ Accept ]                              │
│                                          │
│  [ ← Back ]     [ Create organization ] │
└──────────────────────────────────────────┘
```

### Step 0 — Already has org (invited / returning member)

```
┌──────────────────────────────────────────┐
│  ✅ Your organization is set up           │
│                                          │
│  You accepted an invitation to           │
│  **Acme Corp**                           │
│                                          │
│  Next, choose how you'll use             │
│  Tortoise.                               │
│                                          │
│  [ ← Back ]          [ Continue → ]      │
└──────────────────────────────────────────┘
```

---

## 4. Data Model

No changes. No new entities, relationships, or RLS policies.

---

## 5. Architecture

No changes. No new components, services, or deployment topology.

---

## 6. Interfaces

No new API contracts. Existing endpoints reused:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /v1/invites/pending` | Existing | Fetch pending invites (reuse from account menu) |
| `POST /v1/invites/pending/:id/accept` | Existing | Accept invite (same pattern as account menu) |
| `POST /v1/onboarding/team` | Existing | provisionInApp (org-create submit) |

---

## 7. Detailed E2E Test Cases

### TC1: New user completes 4-step wizard
1. OAuth sign-in → lands on welcome mode
2. Step 0 shows org-create input
3. Type name → Create → org created
4. Step 1 shows fork card → pick "self"
5. Step 2 shows connect command
6. Step 3 shows "You're all set"
7. Click "Open my dashboard →" → dashboard renders

### TC2: Invited user via invite link
1. Navigate to `app.premiselabs.co/?invite_token=XYZ` (no session)
2. Bounce to `/auth` → OAuth → return to `/?invite_token=XYZ#access_token=...`
3. Invite auto-accepted → `loadTeams()` fires
4. Step 0 shows read-only "You accepted an invitation to **{org}**"
5. Continue to Step 1

### TC3: User with pending invites
1. OAuth sign-in (no `?invite_token=`)
2. User has pending invite on server
3. Step 0 shows invite card alongside name input
4. Click Accept → org loads → auto-advance to Step 1

### TC4: Orientation step removed — no regression
1. Existing `wizardArchived.test.js` updated to reflect 4 steps
2. Progress bar shows 4 dots (was 5)
3. Renumbered step labels match wizardFlow.js

### TC5: Returning user skips wizard
1. Session exists, org exists
2. Load `app.premiselabs.co/`
3. No welcome mode — dashboard renders directly
4. `showReentryCard` handles empty-graph state

---

## 8. Coherence Review

### Cross-substep consistency

| Check | Status |
|-------|--------|
| Journeys cover all 4 scenarios (A-D) | ✅ |
| Workflows match the account-menu invite pattern | ✅ (mirror L3574) |
| Prototype states match journey entry/exit | ✅ |
| No data model changes needed | ✅ |
| No architecture changes needed | ✅ |
| No new API endpoints needed | ✅ |
| E2E tests cover all journeys | ✅ |
| State variable `welcomeHasOrg` unaffected | ✅ |
| Back navigation (← Back buttons) preserved | ✅ |

### Risks

| Risk | Mitigation |
|------|------------|
| `loadTeams()` in `acceptStashedInvite()` races with mount effect `loadTeams()` | Guard with `if (!teamIdRef.current)` to prevent double-load |
| Pending invites fetch adds latency to Step 0 load | Load on Step 0 mount, not on page mount — acceptable since user is on Step 0 for a few seconds |
| Orientation removal confuses first-time users (no "what happens next" card) | Monitor Step 1 completion rate; add inline context to Step 0 sub-text if drop-off increases |
| `wizardStep` renumbering breaks wizard guards | All `wizardStep === N` checks must be re-audited; compile error on stale values |

### Implementation order

1. **#2536:** Remove orientation — renumber-only, no logic changes
2. **#2538:** Fix `acceptStashedInvite()` — add `loadTeams()`, minimal change
3. **#2537:** Invite-aware Step 1 — render changes + pending invites fetch
4. **#2539:** Copy review — polish after all structural changes are merged