<!-- issue-scoping: v5.1 double diamond + verify -->
## Confirmed Problem
Dashboard tabs use React state only (`const [tab, setTab] = React.useState('overview')` at main.jsx:1232),
making them invisible to browser navigation. Users cannot:
- Deep-link to a specific tab (no sharable URLs like `/keys`)
- Use browser back/forward to navigate between tabs
- Refresh the page without losing their tab position
- Bookmark a specific dashboard view

The root cause is the absence of URL↔state synchronization. The fix addresses this root cause,
not the symptom (missing URLs).

**Scope boundary:** Deep-linking restores tab position but does not restore intra-tab sub-state
(selected session, session detail, etc.). These remain React-state-only and are lost on refresh —
accepted scope boundary for this issue.

## Verification Gates

### problem-verify: 2 verifiers, controller fixes applied, re-dispatching
### solution-verify: pending

## Plan

### Approach: Hash-based URL↔Tab State Sync

**Chosen:** Approach A — lightweight hash-based routing without a router library.

Tab internal names → URL fragment mapping (verbatim):
- `overview` → `#/overview`
- `keys` → `#/keys`
- `graphs` → `#/graphs`
- `members` → `#/members`
- `billing` → `#/billing`
- `settings` → `#/settings`
- `profile` → `#/profile`

**Mechanism:**
1. **Read tab from URL on mount:** Use the module-scope `landingHash` (captured at
   line 687, BEFORE supabase.js mutates the hash) to read the initial tab. If it starts
   with `#/` and the remainder matches a known tab (`overview`, `keys`, `graphs`,
   `members`, `billing`, `settings`, `profile`), use that tab as default. If no match,
   default to `'overview'`. Unknown hashes fall back safely to overview.
2. **Write tab to URL on change:** A `useEffect`-based sync: whenever `tab` state changes
   (after the initial render), call `history.pushState` (for user-initiated clicks) or
   `history.replaceState` (skip initial render via `useRef` guard).
3. **Handle back/forward:** Register a BOTH `popstate` AND `hashchange` event listener
   that syncs tab state from URL hash. `popstate` fires on back/forward navigation;
   `hashchange` fires on address-bar hash edits. Clean up on unmount.
4. **Guard against OAuth fragments:** Only react to hashes starting with `#/`.
   OAuth fragments (`#access_token=`, `#error=`, `#code=`) have `key=value` syntax and
   are handled separately by existing code.

**pushState vs replaceState:**
- `pushState` for user-initiated tab switches (each tab click creates a history entry)
- `replaceState` for the initial tab read from URL (no extra history entry on load)

**React strict-mode guard:** Use a `useRef` boolean (`initialSyncDone`) to skip the
hash-writing effect on the initial render. This prevents React 18 strict mode's
double-effect from creating duplicate history entries on mount.

**Existing replaceState calls audit:**
- Welcome→dashboard transitions (`replaceState({}, '', '/')` at lines 2467, 5473, 5728, 5943):
  These happen after `setTab()` has already been called. The URL reset must include the
  current tab hash. Update to `replaceState({}, '', '/#' + tab)`.
- OAuth cleanup (`replaceState({}, '', u.pathname + u.search + u.hash)` at line 1662):
  This preserves the hash already — safe.
- OAuth param cleanup (`replaceState({}, '', window.location.pathname + ...)` at lines 1790, 1799):
  These construct the URL from pathname + params, dropping hash. Must be updated to
  preserve the tab hash: `window.location.pathname + ... + window.location.hash`.
- Invite token cleanup (`replaceState({}, '', window.location.pathname)` at line 2618):
  Drops hash. Update to: `window.location.pathname + window.location.hash`.
- **Claim-flow replaceState calls (lines 2767, 2786, 2792, 2803):** SAFE — these fire
  during the mount-phase session check (`?claim=1` processing), BEFORE the user can
  interact with dashboard tabs. At that point, `window.location.hash` is either empty
  (no tab hash) or contains OAuth fragments (`#access_token=...`). No tab hash to preserve.
- **Welcome wizard "Open my dashboard" (line 5173):** SAFE — this fires during
  welcome-mode, before the dashboard tab nav is active. The transition resets to `/`
  followed by `setWelcomeMode(false)` and `finishWelcomeLoads()`. Tab defaults to
  `'overview'` after this.
- **Claim error dismiss (line 6003):** SAFE — the error appears during initial load
  before tab navigation. No tab hash at this point.

**Programmatic tab navigation (OAuth → profile):** Handled automatically by the
`useEffect`-based approach — any `setTab()` call triggers the hash sync.

**Known limitation:** 
- Intra-tab sub-state (selected session, graph detail, etc.) is NOT restored on
  back/forward navigation — only the tab itself is restored. This is per the accepted
  scope boundary.
- React strict mode's double-effect behavior only affects development mode.
  The `useRef` guard prevents duplicate history entries in both development and production

## Clarifications
None required — no clarifying questions needed for this well-understood task.

## External Research (Phase 1.5 artifact)
### Axis Research
> **Trigger assessment:** axes all low (UX=low — same tabs, same behavior, URLs added
> underneath; Ontology=low — no data model changes; Architecture=low — pure client-side
> pattern, no architecture changes); no third-party deps; well-understood pattern (hash-based
> SPA routing is a standard web pattern with extensive precedent; codebase already uses
> `history.replaceState` in 6+ places). External research not demonstrated — skipped per
> activation rule.

### Integration Docs
N/A — no new dependencies or integrations.

## Rejected Alternatives
- **Approach B (path routing):** `/overview`, `/keys` as real paths. Requires Cloudflare Pages
  SPA fallback config. Riskier — breaks existing auth gate assumption that the app lives
  at `/`. The Cloudflare Pages `_redirects` file shows no SPA fallback rule. Significant
  effort for marginal benefit over hash routing.
- **Approach C (sessionStorage):** Persist tab to sessionStorage, read back on mount.
  No deep-linkability, no browser back/forward, no bookmark support. Doesn't meet O/I/T.

## Wiring Check
| Touch Point | Type | Covered By | Status |
|-------------|------|------------|--------|
| `const [tab, setTab] = React.useState('overview')` (line 1232) | State init | Use hash in initializer | ✅ |
| Nav buttons `onClick={() => setTab(...)}` (lines 6009-6019) | Tab switch | UseEffect syncs hash | ✅ |
| Account menu Profile button (line 6093) | Tab switch | UseEffect syncs hash | ✅ |
| OAuth link-flow → `setTab('profile')` (line 1652) | Programmatic | UseEffect syncs hash | ✅ |
| `replaceState({}, '', '/')` (lines 2467, 5473, 5728, 5943) | URL reset | Update to include tab hash | ✅ |
| `replaceState` param cleanup (lines 1662, 1697, 1790, 1799) | Param cleanup | Already preserves hash | ✅ |
| `replaceState` at line 2618 | Token cleanup | Preserve/pass-through hash | ✅ |
| `replaceState` claim flow (lines 2767, 2786, 2792, 2803) | Claim param cleanup | SAFE — fires before tab interaction, no tab hash | ✅ |
| `replaceState` welcome wizard (line 5173) | Welcome→dashboard | SAFE — fires before dashboard tab nav | ✅ |
| `replaceState` claim error dismiss (line 6003) | Error banner | SAFE — fires before tab nav | ✅ |
| `?reauth=1`, `?link_flow=...`, `?claim=1` params | URL params | Unaffected (hash only) | ✅ |
| OAuth `#access_token=...`, `#error=...` fragments | Auth flow | Guard: `#/` prefix only | ✅ |
| `landingHash` module scope (line 687) | OAuth check | Captured before React mounts, unaffected | ✅ |
| `oauthErrorParams()`/`oauthErrorHash()` | OAuth check | Read `landingHash` from scope, unaffected | ✅ |

## Review Cycle Log
Scope verify cycle 1 completed. Controller found Verifier B's P0 (OAuth hash conflict)
overblown — tab hashes use `#/key` syntax vs OAuth's `key=value`; no actual conflict.
Controller accepted P1 items (replaceState audit, push vs replace spec, welcome→dashboard
transition, tab naming convention) as valid gaps and folded them into this document.

## Complexity
| Domain | Rating |
|--------|--------|
| Complexity | standard |
| UX | low |
| Ontology | low |
| Architecture | low |