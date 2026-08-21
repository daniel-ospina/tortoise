# Issue #1567 — Dashboard "Checking your session…" latency: parallelize the mount + optimistic chrome

**Level:** task · **complexity:** standard · **base:** origin/main (post-#1559)

## Confirmed Problem
Every dashboard load with a valid session shows a multi-second "Checking your session…" card. The mount effect chains blocking network calls before the first render of the app chrome:

```
getSession() → mintSessionKey() (POST /v1/session/key) → completeLogin():
    GET /v1/team → setAuthed(true) → Promise.all([loadAll (keys+sessions), loadTeams, loadBackups])
```

`authed` stays false until /v1/team resolves, and the checking card renders until `completeLogin`'s `finally` sets `setChecking(false)`. The user's words: "I never had this with a website that would take multiple seconds to know if I am logged in."

## Solution (converged)
Split the mount into (1) an instant gate/validation phase and (2) a parallelized hydrate phase:

1. **Gate phase (unchanged, instant):** head gate (sync cookie read) → mount effect `getSession` → no valid session → bounceToAuth / claim-paste (exactly as today). On a valid session: set `authed=true` + `checking=false` IMMEDIATELY and render the app chrome (header/nav shell) — the checking card disappears.
2. **Hydrate phase (parallel, in background):** mint + completeLogin's loads run concurrently and hydrate the cards as they arrive:
   - The mint must still gate key-scoped calls: `sessionTokenRef.current` + the minted key feed `apiKeyRef` before any key-scoped fetch fires (preserve the Round-9/10 staleness guards + the team-switcher refs).
   - `completeLogin` keeps its error semantics (#1559 mountError on failure — the error card replaces the shell), but no longer blocks first paint.
   - The team switcher + stale-closure guards (`teamIdRef`, `_teamAtCall`) must not regress: hydrate functions already re-check `teamIdRef.current` before landing data.

Concretely in `main.jsx`:
- In the mount effect's session branch: `setAuthed(true); setChecking(false)` right after the session validates (before the mint), then run mint + loads without `await`-blocking the render path (keep the refs + error paths).
- `checking` is no longer set for session holders; the shell/checking render remains only for the no-session flash (which navigates immediately) — the #1559 error card paths unchanged.
- The `!authed` render's "Checking your session…" branch stays for the pre-gate instant.

## Edge cases
- Mint failure after authed=true: the #1559 mountError card must still show (the hydrate phase sets it; the chrome renders behind it or is replaced by the card — pick: mountError replaces the whole app render, as today).
- SIGNED_OUT mid-mint: `!sessionTokenRef.current` guard still aborts the hydrate + shows the error card.
- Team switcher while hydrating: existing `_teamAtCall`/`teamIdRef` staleness guards are preserved (they already prevent landing stale data).
- The e2e (dashboard gate 9, loop 3, welcome) must stay green — the visible sequence changes (chrome first, cards later), so any test asserting "Checking your session…" must be updated to assert the chrome.

## Verifier-gate findings (folded in)
- **P0 (error-card reachability):** the #1559 mountError card renders only inside the `!authed` branch — with early `authed=true` the pre-hydrate failure paths (mint catch, `!key`, `!sessionTokenRef.current`) MUST ALSO `setAuthed(false)` so the card + Retry still render. `completeLogin`'s catch already does.
- **P1 (team-switch clobber):** the mount continuation's own writes (`localStorage.setItem(KEY_STORAGE, key)`, `setApiKey`, `apiKeyRef`, `setAuthMode`, `completeLogin`'s `setTeam`) can clobber a switch made while chrome is visible (multi-membership: mint-400 fallback populates teams early). Fix: after the mint capture `mintedTeamId`; before each state write and before `completeLogin`, bail if `teamIdRef.current` is set AND differs from `mintedTeamId` (the round-N staleness pattern).
- **P2 (null-capture race):** on a fresh session `teamIdRef.current` is null when the hydrate fires; `loadAll`'s `_teamAtCall` capture can race `loadTeams`' Round-8. The mint-time `mintedTeamId` from the P1 fix makes the bootstrap hydrate deterministic.
- **P2 (acknowledge, do not "fix"):** with `authed=true` early, the account blob briefly shows "No team" and the overview is empty during hydrate — cosmetic; do NOT gate chrome on `team` (that would reintroduce the latency).
- **No test asserts 'Checking your session…'** (only source + docs). `test_mint_429_shows_error_card_not_stuck_shell` stays green unmodified WITH the P0 fix (it fails without it). Update `docs/auth-architecture.md` §4.2 wording (P3).

## Rejected alternatives
- Server-side session bootstrap (add a /v1/session/init returning team+key in one call): fastest, but new API surface + the key must still cross to the client; bigger change than the perceived-latency problem justifies.
- Skeleton-only with no mint ordering change: doesn't remove the blocking chain.
- localStorage cache of team data: stale-data risk; the Round-10 staleness work exists because of this class.

## Complexity
| Domain | Rating |
|--------|--------|
| Architecture | medium |
| UX | medium |
| Security | low (no new surfaces; key handling unchanged) |
| Ontology | low |

## Verification (target)
Dashboard e2e suite green; manual: warm reload shows chrome immediately, cards hydrate; mint-failure still shows the #1559 error card.
