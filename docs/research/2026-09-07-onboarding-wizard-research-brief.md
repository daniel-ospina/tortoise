# Epic Research Brief: Onboarding wizard — invite-aware, orientation removed, quick flow

## 1. Current State

The onboarding wizard has 5 steps: Orientation (Step 0), org-create (Step 1), fork card (Step 2), connect (Step 3), done (Step 4).

Invite handling has two paths:
- **Invite link** (`?invite_token=XYZ`) — stashed in sessionStorage, auto-accepted at mount via `acceptStashedInvite()`, but only sets a banner — no `loadTeams()` call, so wizard state is stale
- **Pending invites** (account menu) — correctly calls `loadTeams()` + `switchTeam()`, but only works post-onboarding

**Gap 4 (path map):** Invite acceptance produces only a dismissible banner; the wizard has no inline confirmation of which org was joined.

## 2. Key Code Locations

| Component | File | Lines |
|-----------|------|-------|
| Wizard steps definition | `website/apps/dashboard/src/wizardFlow.js` | Whole file |
| Wizard render + state | `website/apps/dashboard/src/main.jsx` | L5280-5450 (welcome card), L2742-2775 (invite accept) |
| Invite accept (account menu) | `website/apps/dashboard/src/main.jsx` | L3574 (loadTeams + switchTeam after accept) |
| Pending invites fetch | `website/apps/dashboard/src/main.jsx` | L3554 (loadPendingInvites) |
| loadTeams | `website/apps/dashboard/src/main.jsx` | L3524 |
| Welcome mode gate | `website/apps/dashboard/src/main.jsx` | L5281 |
| showReentryCard | `website/apps/dashboard/src/main.jsx` | L5240 |

## 3. Known Constraints

- `welcomeHasOrg` = `welcomeTeamReady || (Array.isArray(teams) && teams.length > 0)` — it drives whether Step 1 is an input or read-only summary
- Orientation step shares the `wizardStep` state variable — removing it means renumbering
- The account-menu invite accept flow (L3574) is the canonical pattern — `acceptStashedInvite()` (L2754) should mirror it
- Async effect order: `loadTeams()` fires in mount effect, but `acceptStashedInvite()` also fires in mount effect — order is not guaranteed

## 4. Key Unknowns

- Loading pending invites in the wizard adds a network call to onboarding load — acceptable latency?
- Build-fork users: do they need the connect step? The harness command installs the connector — if the fork is "build," do they install the harness too or get API access directly?
- Monitor wizard Step 1 completion rate before/after orientation removal — no tooling currently

## 5. External Research

No external research needed — this is a codebase restructure with no new dependencies or third-party integrations.

## Raw Notes

See `docs/scoping/onboarding-invite-path-map.md` for the full path analysis including all 4 identified gaps.
