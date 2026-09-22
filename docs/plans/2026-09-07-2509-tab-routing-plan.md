<!-- research-path: docs/scoping/2026-09-07-2509-tab-routing-scoping.md -->

# #2509: Dashboard Tab URL Routing Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Give each dashboard tab a proper URL so users can deep-link, use browser back/forward, and refresh without losing their place.

**Architecture:** Hash-based URL↔tab state sync (`#/overview`, `#/keys`, etc.) using `history.pushState`/`popstate`/`hashchange`. No router library — integrates with existing `history.replaceState` patterns and OAuth fragment handling.

---

### Task 1: Implement tab↔hash sync effect

**Intent:** Sync the React `tab` state to the URL hash and vice versa, enabling deep-linking and browser navigation.

**Acceptance:** 
- Tab switches update the URL hash via `history.pushState`
- Browser back/forward navigates between tabs via `popstate` listener
- Direct URL entry with `#/keys` loads the Keys tab
- OAuth fragments (`#access_token=...`) are NOT misinterpreted as tab hashes
- React strict-mode double-effect does not create duplicate history entries

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (around line 1232)

**Step 1: Replace the useState initial value with a hash-aware init**

Replace line 1232 (and add KNOWN_TABS at module scope near line 687):

Near line 687, add KNOWN_TABS:
```javascript
// #2509: known dashboard tab names for URL↔hash sync.
const KNOWN_TABS = ['overview', 'keys', 'graphs', 'members', 'billing', 'settings', 'profile']
```

Then replace line 1232:
```javascript
const [tab, setTab] = React.useState('overview')
```
with:
```javascript
const initialTab = (() => {
  // Use landingHash (captured at module scope before supabase.js init) to
  // avoid timing issues with OAuth fragment cleanup.
  const h = landingHash
  if (h.startsWith('#/')) {
    const candidate = h.slice(2)
    if (KNOWN_TABS.includes(candidate)) return candidate
  }
  return 'overview'
})()
const [tab, setTab] = React.useState(initialTab)
```

**Step 2: Add a useEffect to sync tab→hash on changes**

After the `[tab, setTab] = React.useState(initialTab)` line, add:

```javascript
// #2509: sync tab state → URL hash so each tab has a deep-linkable URL.
// Uses pushState so browser back/forward navigates through tab history.
// The useRef guard prevents React strict-mode double-effect from creating
// duplicate history entries on the initial mount (the initial tab is already
// reflected via the useState init above, no need for a second pushState).
const tabSyncRef = React.useRef(false)
React.useEffect(() => {
  if (!tabSyncRef.current) {
    // Initial mount — tab is already reflected in the URL by the useState
    // initializer reading from landingHash. Skip this render.
    tabSyncRef.current = true
    return
  }
  const hash = '#/' + tab
  if (window.location.hash !== hash) {
    window.history.pushState({ tab }, '', hash)
  }
}, [tab])
```

**Step 3: Add a popstate/hashchange listener to sync hash→tab**

Add after the useEffect:

```javascript
// #2509: sync URL hash → tab state on browser back/forward (popstate)
// or address-bar hash edits (hashchange).
React.useEffect(() => {
  function onHashChange() {
    const h = window.location.hash
    if (h.startsWith('#/')) {
      const candidate = h.slice(2)
      if (KNOWN_TABS.includes(candidate)) {
        setTab(candidate)
        setSelectedSessionId(null)
        setSessionDetail(null)
        return
      }
    }
    // If hash doesn't match a known tab, default to overview
    // (preserves OAuth fragments as-is — they use key=value syntax,
    // not #/ prefix).
    if (!h.startsWith('#/') && h.length > 0) {
      // OAuth fragment or unknown — don't override tab
      return
    }
    // Unknown/malformed hash — fallback to overview with replaceState
    // (replaceState instead of pushState to avoid creating a phantom history entry)
    setTab('overview')
    if (window.location.hash !== '#/overview') {
      window.history.replaceState({ tab: 'overview' }, '', '#/overview')
    }
  }
  window.addEventListener('popstate', onHashChange)
  window.addEventListener('hashchange', onHashChange)
  return () => {
    window.removeEventListener('popstate', onHashChange)
    window.removeEventListener('hashchange', onHashChange)
  }
}, [])
```

**Step 4: Verify typecheck**

```bash
cd website/apps/dashboard && npx tsc --noEmit
```

**Step 5: Verify the app builds**

```bash
cd website/apps/dashboard && npx vite build 2>&1 | tail -5
```

---

### Task 2: Update existing replaceState('/') calls to preserve tab hash

**Intent:** Welcome→dashboard transitions and other `replaceState({}, '', '/')` calls currently wipe the URL hash. Update them to include the current tab hash.

**Acceptance:** After navigating from welcome wizard to dashboard, the URL shows `/#keys` instead of bare `/`.

**Files:**
- Modify: `website/apps/dashboard/src/main.jsx` (6 sites)

**Step 1: Update finishWelcomeLoads (line ~2466)**

Find:
```javascript
window.history.replaceState({}, '', '/')
```
Replace with `'#/' + tab`:
```javascript
window.history.replaceState({}, '', '#/' + tab)
```

**Step 2: Update welcome→API Keys buttons (lines ~5473, ~5728, ~5943)**

Find each instance of:
```javascript
window.history.replaceState({}, '', '/')
```
These fire BEFORE `setTab('keys')` in the same expression, so `tab` is still the previous value. Use a literal:
```javascript
window.history.replaceState({}, '', '#/keys')
```

**Step 3: Update OAuth param cleanup calls to preserve hash (lines ~1790, ~1799, ~1697)**

Find and update the `replaceState` calls at lines 1790, 1799, and 1697 that construct URLs with `window.location.pathname + ...`.

The pattern needs to append `+ window.location.hash`:
```javascript
window.history.replaceState({}, '', window.location.pathname + (params.toString() ? `?${params}` : '') + window.location.hash)
```

**Step 4: Update invite token cleanup (line ~2618)**

Find:
```javascript
window.history.replaceState({}, '', window.location.pathname)
```
Replace with:
```javascript
window.history.replaceState({}, '', window.location.pathname + window.location.hash)
```

**Step 5: Verify typecheck and build**

Same commands as Task 1 Steps 4-5.

---

### Task 3: Manual verification

**Intent:** Confirm the feature works end-to-end.

**Steps:**
1. Start dev server: `cd website/apps/dashboard && npx vite --port 3000`
2. Open browser to `http://localhost:3000/`
3. Click each tab — verify URL changes to `#/keys`, `#/graphs`, etc.
4. Use browser back/forward — verify tabs switch correctly
5. Navigate to `http://localhost:3000/#/keys` directly — verify Keys tab loads
6. Navigate to `http://localhost:3000/?claim=1` — verify URL params still work
7. Navigate to `http://localhost:3000/?reauth=1` — verify URL params still work
8. Refresh on `#/settings` — verify Settings tab persists after refresh