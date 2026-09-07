# Issue #2479 — Implementation Plan: Re-auth UX Fix

## Research Intake

Prior scoping consumed. External research: not needed (zero third-party deps, in-repo JSX changes only). Verified via hosted_api.py inspection: the unlink endpoint at `/v1/user/identity/unlink` DOES return 403 REAUTH_REQUIRED with detail `"Sign in again to continue (REAUTH_REQUIRED)"` when `_last_signin_fresh()` returns false (hosted_api.py:14587-14589).

## Integration Surface Map

| Surface | Type | Test Layer |
|---------|------|------------|
| `profile.jsx:ReauthDialog` — password field conditional on OAuth-only | UI | Manual/E2E (no profile.test file exists) |
| `profile.jsx:ProfileTab` — remove standalone button | UI | Manual/E2E |
| `main.jsx` — reactive re-auth from 403 handlers in handleAddEmail + handleUnlink | JS | Manual |
| `main.jsx` — reauthAttemptRef (retry limit) | JS | Manual (ref resets naturally on OAuth round-trip) |
| `identity.js` — keep `reauthStale()` (has unit tests, represents deliberate config constant) | Pure fn | Existing identity.test.js |

**Failure modes enumerated:**
- Empty/null providers (identityInv still null when ReauthDialog mounts) → map on empty array, fallback text shown
- Re-auth in-flight race (double click) → reauthBusy disables buttons
- Dialog dismissed mid-OAuth round-trip → onClose clears pendingReauthRef (intended abandon)
- OAuth round-trip switches accounts → beforeUidRef + tt_reauth_pending guard (existing code)

**Complexity rating:** Standard (two JSX components, one new ref, one handler update, no API/server changes)

## Changes

### Task 1: Remove standalone button + add reactive re-auth gate

**Intent:** Eliminate the dead-code "Re-authenticate now" button. Instead, 403 REAUTH_REQUIRED from change-email/unlink operations auto-opens the ReauthDialog with pending action context.

**Acceptance:** 
- Standalone button at profile.jsx:212 is GONE
- No proactive re-auth affordance anywhere (`onOpenReauth` prop removed entirely)
- 403 REAUTH_REQUIRED in handleAddEmail: already works (sets `pendingReauthRef.current`, opens dialog)
- 403 REAUTH_REQUIRED in handleUnlink: NEW — catch the 403, set `pendingReauthRef.current = { unlinkIdentityId }`, open dialog. After re-auth completes, re-execute the unlink.
- Retry limit: max 3 re-auth attempts per session via `reauthAttemptRef` — after 3 failed attempts, fallback message, dialog closed, pendingReauthRef cleared
- Retry limit only applies to password re-auth (OAuth round-trip causes full page navigation which naturally resets the ref — documented behavior, not a bug)
- Check `reauthAttemptRef.current >= 3` BEFORE calling `setReauthOpen(true)` — if blocked, clear pendingReauthRef and show fallback

**Files:** `profile.jsx`, `main.jsx`

#### Steps

1. **profile.jsx: Remove standalone button + onOpenReauth prop**
   - Remove the `<p className="dim small" style={{ marginTop: 16 }}>` block containing the `onOpenReauth` button (lines ~210-216)
   - Remove the `onOpenReauth` prop from ProfileTab's destructuring and prop forwarding entirely — it's dead code after button removal
   - The ProfileTab component no longer passes anything for re-auth; it only receives identity data and method management handlers

2. **main.jsx: Add reauthAttemptRef**
   - Add `const reauthAttemptRef = React.useRef(0)` alongside `pendingReauthRef` (line ~774)
   - Before any flow that calls `setReauthOpen(true)`, check `reauthAttemptRef.current >= 3`:
     - If blocked: clear `pendingReauthRef.current = null`, set `profileError` with "Re-authentication unavailable — try again later or contact support."
     - If allowed: proceed with setting pendingReauthRef and opening dialog normally

3. **main.jsx: Wire reactive re-auth into handleAddEmail**
   - Already wired: handleAddEmail sets `pendingReauthRef.current = { email, password }` then `setReauthOpen(true)` (lines ~1502-1507). Add the retry-limit gate check BEFORE this logic.

4. **main.jsx: Wire reactive re-auth into handleUnlink**
   - handleUnlink currently catches all errors generically (lines ~1519-1530). Add specific 403 REAUTH_REQUIRED handling:
     ```js
     async function handleUnlink(identityId) {
       setProfileBusy('unlink'); setProfileError('')
       try {
         await api('/v1/user/identity/unlink', { method: 'POST', useSession: true,
           headers: { 'Content-Type': 'application/json' },
           body: JSON.stringify({ identity_id: identityId }),
         })
         await fetchIdentity()
       } catch (e) {
         if (e.status === 403 && /REAUTH_REQUIRED/i.test(e.message)) {
           if (reauthAttemptRef.current >= 3) {
             setProfileError('Re-authentication failed — try again later or contact support.')
             return
           }
           pendingReauthRef.current = { unlinkIdentityId: identityId }
           setReauthOpen(true)
           return
         }
         setProfileError(e.message || 'Could not remove login method')
       } finally {
         setProfileBusy('')
       }
     }
     ```
   
   **Note:** The rest of handleUnlink's try block must NOT set `profileBusy` to `''` prematurely — the catch handler re-opens the dialog, and the profileBusy state is needed to show the loading state. The `setProfileBusy('')` in `finally` will fire AFTER the dialog opens, which is fine — the dialog has its own busy state (`reauthBusy`).

5. **main.jsx: Retry limit + unlink re-execution in handleReauthPassword**
   - On success: check `pendingReauthRef.current`. If `pending.unlinkIdentityId` present, call `handleUnlink(pending.unlinkIdentityId)`. Then reset `reauthAttemptRef.current = 0`.
   - On failure: `reauthAttemptRef.current += 1`. If `reauthAttemptRef.current >= 3`: close dialog, clear pendingReauthRef, set profileError with fallback

6. **main.jsx: OAuth return — add setTab('profile') + unlink re-execution**
   - In the OAuth return effect's `reauth` branch (~line 1684), add `setTab('profile')` after `await fetchIdentity()` — consistent with how the `link_flow` branch does it at line 1681. This ensures the user lands on the Profile tab (where the ReauthDialog context lives) after OAuth round-trip.
   - Also in this branch: after re-auth completes (the OAuth round-trip was successful), the handler checks `pendingReauthRef.current` to resume the pending action. Add a check for `pending.unlinkIdentityId` — if present, call `handleUnlink(pending.unlinkIdentityId)` to re-execute the unlink now that the session is fresh.

7. **main.jsx: handleReauthPassword — unlink re-execution**
   - In `handleReauthPassword`, after successful re-auth, the handler checks `pendingReauthRef.current`. Currently handles `{email, password}` and `{email, promptPassword}`. Add a check for `pending.unlinkIdentityId`: if present, call `handleUnlink(pending.unlinkIdentityId)` to re-execute with fresh session.

### Task 2: OAuth-only ReauthDialog (no password field)

**Intent:** For accounts with no email+password method, show ONLY provider buttons (Google/GitHub) in ReauthDialog — no password field at all.

**Acceptance:**
- When `identityInv.methods` has at least one method with `provider === 'email'`: show password field as normal
- When NO method has `provider === 'email'` (OAuth-only): password field + form are HIDDEN. Only provider buttons (Google/GitHub) shown
- When `passwordMode=true`: show password field regardless of method type (user is setting new password after OAuth re-auth)

**Files:** `profile.jsx`

#### Steps

1. **profile.jsx ReauthDialog: Add `hasPasswordMethod` detection**
   - Compute inside ReauthDialog: `const hasPasswordMethod = providers.includes('email')` (providers is already the array of provider strings passed from main.jsx)
   - Predicate reference: scoping-output.md specifies `!identityInv.methods.some(m => m.provider === 'email')` — since `providers` is `identityInv.methods.map(x => x.provider)`, `providers.includes('email')` is equivalent.
   
2. **Conditional render in ReauthDialog**
   - When `!hasPasswordMethod && !passwordMode`: render ONLY the provider buttons section (Google/GitHub). No password form, no "Sign in again with your password above" text, no empty-state `<p>` for no providers.
   - When `hasPasswordMethod || passwordMode`: render password form + provider buttons as normal (existing behavior unchanged)

### Task 3: Keep reauthStale() — intentional preservation

**Intent:** The `reauthStale()` function in identity.js is a pure predicate with existing unit tests and represents a deliberate server-side config constant (15-min window). Keep it as-is. No code changes needed for this file.

**Rationale:**
- `reauthStale()` has 4 dedicated unit tests in identity.test.js
- The function encodes the `REAUTH_WINDOW_SECONDS = 900` constant as a default parameter — removing it would erase this config value from the codebase
- The function itself is not "dead code" in the strict sense — it's a pure helper used for offline defensive paths (server payload unavailable). Keeping it is the YAGNI-correct choice
- The scoping output already documents the window aggressiveness as a separate concern (Fix 3: config audit). Removing the client-side constant would not fix the config issue

**Acceptance:**
- identity.js exports `reauthStale()` — untouched
- identity.test.js tests for `reauthStale()` — untouched
- No changes to identity.js or its tests in this issue

### Task 4: Update ReauthDialog onClose — confirm intent

**Intent:** Confirm that the `onClose` handler clearing `pendingReauthRef.current` is deliberate: closing the dialog = abandoning the pending action. This is correct behavior for the reactive-only design.

- The existing `onClose={() => { setReauthOpen(false); pendingReauthRef.current = null; setReauthPasswordMode(false) }}` is preserved unchanged.

## OAuth round-trip behavior note

The retry limit (`reauthAttemptRef`) is per-session (React component lifetime). OAuth re-auth causes a full-page navigation → component remount → ref resets to 0. This means:
- Password re-auth respects the 3-attempt limit (same session)
- OAuth re-auth naturally resets the limit (full navigation)
- This is INTENTIONAL: an OAuth round-trip is a fresh sign-in, so the retry counter should reset

## Verification

1. Manual: check that standalone button no longer renders on Profile tab
2. Manual: trigger change-email with stale session → ReauthDialog opens automatically
3. Manual: trigger unlink with stale session → ReauthDialog opens with unlink context
4. Manual: dismiss dialog → pending action abandoned
5. Manual: fail password re-auth 3 times → fallback message, dialog closed, no further prompts

<!-- plan-review: cycles=1, status=clean, version=2.3.0 -->