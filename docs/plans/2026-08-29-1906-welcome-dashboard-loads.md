---
title: "#1906 — First-timer dashboard loads after wizard"
type: ux
domain: ux
doc_status: draft
created: 2026-08-29
ownedBy: epistemic-team
---

<!-- research-path: issue #1906 scoping comment (### Axis Research + verification cycles) — standalone, no epic brief -->

# #1906 — First-timer dashboard lies after wizard (keys/sessions/post-seed counts never load)

> **For Pi:** Implement this plan directly — 3 targeted edits in one file, no task decomposition needed.

**Goal:** The first-timer dashboard reflects reality after the wizard — API keys, sessions, and post-seed point counts load on the welcome path (not after a reload), and the shown-once revealed key is persisted so a reload reuses it instead of minting a different bootstrap key.

**Team:** epistemic-team
**Role:** product-implementer
**Complexity:** standard (UX)

**File:** `website/apps/dashboard/src/main.jsx` only. No new deps, no state additions, no schema/API changes.

## Problem (verified by 2 scope verifiers)

The first-timer branch of the mount gate (`~:1740-1790`) returns before `completeLogin`. Three gaps:

1. `loadAll` (keys+sessions, `~:2287`) never fires → Overview "API Keys" card (`keys.length`) stays 0 and sessions never load.
2. The provisioned branch's `refreshTeam(provisioned.api_key)` (`~:1772`) fires BEFORE the seed step → `team.point_count`/`team.graph_ready` are snapshot at 0/not-ready; nothing refetches after `wizardSeedGraph` writes the first point → "Data points 0" + graph-missing/empty-state card.
3. The revealed key lives only in `welcomeKey` state — `localStorage[KEY_STORAGE]` + `apiKey` are never set → reload mints a DIFFERENT bootstrap key and the shown-once key is lost (server plaintext already nulled, A13).

`finishWelcomeLoads` (`~:1355`) — the chokepoint for ALL welcome exits (`wizardComplete` `~:1332`, header "Open dashboard →" `~:3435`, free-tier "Start free" `~:3766`) — only runs `loadTeams` + `loadBackups` + `refreshOnboarding`.

## Design decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Persist the key at reveal (provisioned branch), not in `finishWelcomeLoads` | Persisting at reveal is strictly earlier — a reload mid-wizard (after the key was shown once) keeps it too. Both verifiers confirmed the reload stored-key probe (`~:1787-1810`) reuses `localStorage[KEY_STORAGE]` when it passes the `/v1/team` probe + membership check. |
| D2 | Guard the persist with `if (provisioned.api_key)` + try/catch | Verifier P2: `provisionInApp`'s consumed-reveal path returns `api_key: ''`; the codebase forbids falsy values in `KEY_STORAGE` ("a falsy value must never land in localStorage", `~:1897-1906`) and try/catches localStorage (private-mode throw). |
| D3 | Fire `loadAll` + `refreshTeam` in `finishWelcomeLoads` AFTER `await loadTeams()` | `loadTeams`' Round-8 fallback pins `teamIdRef`/`currentTeamId` (`~:2337-2339`); `loadAll`'s `?team_id=` pin + staleness guard (`teamIdRef.current !== _teamAtCall`, `~:2309`) need the pin. **Sequencing is load-bearing** — do NOT parallelize with the loadTeams await (verifier P2). Both calls `.catch(() => {})` fire-and-forget, preserving the "finishWelcomeLoads never rejects" invariant. |
| D4 | Re-fire `refreshTeam` after the seed step in `wizardSeedGraph` | Load-bearing for the mid-seed header-exit race: the header "Open dashboard →" is available on every wizard step, so `finishWelcomeLoads`' refreshTeam (D3) can snapshot `point_count=0` while the seed commit is in flight; Step 3's post-seed refire lands the correct count. (The Overview cards are dashboard-only — `!welcomeMode` — so this is a race fix, not an in-welcome re-render.) Idempotent — a third refreshTeam call is a guarded pure fetch; no mint/side effects. |
| D5 | `welcomeKey || ''` as the key arg | `loadAll`'s key param is documented-unused (session-authed reads); empty string keeps the call shape safe for the claim/error paths where `welcomeKey` is ''. |

## Implementation steps

### Step 1 — Provisioned branch: persist the shown-once key (~:1751)

After `setWelcomeGraphName(provisioned.graph_name || '')`, add:

```js
// #1906: persist the shown-once key NOW — a reload (even mid-wizard) must
// keep it; the mint path would mint a different bootstrap key and the
// revealed key is gone forever (atomic reveal+null, A13). Guard truthy
// (the consumed-reveal path returns api_key '') + private-mode try/catch
// ("a falsy value must never land in localStorage").
if (provisioned.api_key) {
  try { localStorage.setItem(KEY_STORAGE, provisioned.api_key) } catch { /* best-effort */ }
  setApiKey(provisioned.api_key)
  apiKeyRef.current = provisioned.api_key
}
```

### Step 2 — finishWelcomeLoads: loadAll + refreshTeam (~:1355)

Inside `finishWelcomeLoads`, after `loadBackups(...)` and BEFORE `await refreshOnboarding()`:

```js
// #1906: the first-timer path never ran loadAll — Overview 'API Keys'
// stayed 0 until reload. loadTeams just pinned currentTeamId (Round-8),
// so loadAll's ?team_id= targets the new team. Fire-and-forget like
// completeLogin's card loads (each loader carries its own staleness guard).
// #1906: refetch the team so 'Data points' reflects the seeded graph —
// team.point_count was captured at provisioning (pre-seed, 0).
loadAll(welcomeKey || '').catch(() => {})
refreshTeam(welcomeKey || '').catch(() => {})
```

### Step 3 — wizardSeedGraph: refreshTeam after seed (~:1283)

On the success path, right after `setWizardSeedDone(...)`:

```js
// #1906: the seeded point must show immediately — team.point_count was
// captured at provisioning (0). Re-fire the team refetch so the Overview
// 'Data points' card + graph-ready state reflect the seeded graph without
// waiting for reload.
refreshTeam(welcomeKey || '').catch(() => {})
```

## Verification

| Surface | Layer | Expected |
|---------|-------|----------|
| build | build | `cd website/apps/dashboard && npm run build` passes; `dist/` rebuilt (repo convention #1148) |
| code-level review | review | wiring matches completeLogin's card-load pattern; guards preserved; no returning-user risk (`welcomeKey === ''` → session-JWT reads) |
| first-timer overview | manual (out of scope this session) | post-wizard Overview shows real key count + seeded points |
| key persistence | manual (out of scope) | reload keeps the shown-once key |

## Code-review fix round (PR #1994 review gate)

Reviewers (guidance, bug shallow+deep, history, PR-comments, security, UX consistency+coverage) found no P0s. Fixes applied in the fixer loop:

| # | Finding | Resolution |
|---|---------|------------|
| R1 | P1 (history): welcome key never recorded in `teamKeysRef` — switchTeam away-and-back mints a DIFFERENT bootstrap key; revokeKey skips the localStorage re-mint branch | Provisioned branch's `refreshTeam(...).then` now records `teamKeysRef.current[t.team_id] = provisioned.api_key` (guarded truthy) alongside `loadAlerts` |
| R2 | P1/P2 (ux-consistency + bug-deep): header "Open dashboard →" clickable during provisioning → finishWelcomeLoads against a non-existent team → 'API Keys 0' + false error banner until reload | Header button `disabled={welcomeProvisioning}`; label canonicalized to "Open my dashboard →" (matches the wizard done-step CTA — terminology drift P2) |
| R3 | P1 (ux-coverage): step-0 "Go to API Keys →" exit (consumed-reveal path, api_key '') bypassed the chokepoint — no replaceState, no loads → API Keys tab lied "No keys yet.", eternal skeleton, /welcome URL stuck | Routed through the chokepoint: `replaceState('/') + setWelcomeMode(false) + setTab('keys') + finishWelcomeLoads()` (session-JWT loads need no key) |
| R4 | P2 (history): refreshTeam has 3 welcome-path producers with no ordering guard — a pre-seed point_count 0 response could land after the post-seed 1 | Monotonic `teamRefreshSeqRef` + optional `seq` param on refreshTeam: post-seed refire bumps the seq and tags its call; the exit's refreshTeam tags with the current seq — a stale-seq response is dropped before setTeam (mirrors the file's Round-N staleness pattern) |
| R5 | P2 (guidance): persist comment "the plaintext exists only here" self-contradictory with the persist 5 lines below | Reworded: server nulls its only plaintext copy on reveal (A13), so the client must persist at reveal time or the shown-once key is unrecoverable |
| R6 | P2 (history): extract a shared postAuthLoads helper so completeLogin/finishWelcomeLoads can't drift | **Pushed back (documented, not implemented):** the two paths are intentionally different shapes — completeLogin owns the team gate + auth flip + error card semantics; finishWelcomeLoads is fire-and-forget with the never-rejects invariant. Forcing a shared helper would couple the exit path to login error semantics (or force completeLogin to split its flow). Parity risk is real; the #1842/#1906 comments + this plan doc encode it. |
| R7 | P2 (ux-coverage): transient 0-state frame between exit and refetch landing; silent refreshTeam 5xx | **Partially pushed back:** the seq guard (R4) fixes the ordering; the brief pre-refire frame reflects the true state at exit time (mid-seed race only — normal seeded flow lands the post-seed refire before the user reaches the exit), and silent .catch on team refresh matches completeLogin's best-effort pattern (the re-entry card is the recovery affordance). |
| R8 | P2 (ux-coverage, re-review): provisioning-time refreshTeam was the last UNTAGGED welcome-path team-refresh producer — a pathologically slow pre-seed GET could land after the post-seed refire | Tagged it with the current seq (`refreshTeam(provisioned.api_key, undefined, teamRefreshSeqRef.current)`) — the seed bump drops any straggler, making the welcome-path guard total (one line, no normal-flow behavior change) |
| R9 | P2 (ux-coverage, re-review): header button re-enabled on the provisioning-error/claim cards (no team row → same no-team false banner) | `disabled={welcomeProvisioning || welcomeProvisionError}` — the error/claim cards' own CTAs (Try again / Go claim my team) own recovery; comment scoped accordingly (also resolves the P3 comment-rationale nit) |
| R10 | P3 (guidance, re-review): 3 pre-existing comments quoted the old "Open dashboard →" label | Updated to "Open my dashboard →" for terminology convergence |
| R11 | P2 (ux-consistency, re-review): legacy `website/invite-accept.html` still says "Open dashboard" | **Out of scope (documented, not changed):** pre-existing legacy page outside this PR's diff/flow; not a first-timer wizard surface |

## Failure modes

| Failure | Handling |
|---------|----------|
| `refreshTeam`/`loadAll` reject (transient 5xx) | `.catch(() => {})` — silent; cards show '—'/0 until next trigger (same as completeLogin's best-effort) |
| `loadTeams` fails → `teamIdRef` unpinned | `loadAll`/`refreshTeam` still run session-authed without `?team_id=` (null pin → guards pass) — verifier-confirmed |
| Stale switch mid-wizard | Both loaders' staleness guards drop the stale response; switchTeam's own loaders own data under the new selection |
