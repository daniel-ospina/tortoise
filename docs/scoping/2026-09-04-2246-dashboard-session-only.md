---
title: "#2246 Dashboard session-only — drop held-key probe + guarded row (scope + verified plan)"
type: engineering
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-api-keys, tortoise-dashboard
created: 2026-09-04
---

# #2246 Scope — dashboard is session-only: no browser-held API key, uniform keys table, rows-routed connect

> Consolidated scoping (double-diamond + verification gates, issue-scoping v5.1). Companion
> ADR: `docs/adr/ADR-010-auth-planes-session-agent-key.md` (rides this PR). Issue: #2246.
> Complexity: standard. Team: epistemic-team. Worktree: `feat/2246-dashboard-session-only`.
> Server: **zero changes** (verified — no tortoise/*.py touched).

## 0. Problem (confirmed)

**In session-authed mode the browser must never hold or retain a plaintext API key, yet it
does today** — via the localStorage `tortoise_api_key` slot, `apiKey`/`apiKeyRef`/
`teamKeysRef` state, and the entire probe/adopt/drop/classify/install machinery built to
keep one credential alive — **even though every dashboard read already rides the session
JWT** (#1828/#2167; api() force-overrides Authorization when `sessionTokenRef` is set,
main.jsx L1177). The held key authenticates nothing the dashboard depends on.

Root-cause framing (retention elimination): the retention *is* the defect. Security
(durable plaintext in JS-readable localStorage = XSS-exfiltration liability — ADR-010
reason 1, OWASP browser-storage guidance), complexity (six pure helpers +
`apiKey`/`apiKeyRef`/`teamKeysRef` + slot writes all exist to manage an unused
credential), and UX (the "in use by this dashboard" note is inaccurate post-#2167 and
the rotate-only guarded row is confusing — ADR-010 reason 2) all collapse once the
browser stops retaining the key.

The fix is session-only with the **anon/key-login carve-out intact**: account-less
teams (Protect screen, claim-paste, key login via the /auth `POST /v1/session/login`
exchange) keep their existing key-in-browser bootstrap exactly as-is — no session
exists to protect there, and key login remains the documented bootstrap for
account-less users. Server, selfhost/registry lanes, and the `POST /v1/session/key`
endpoint (bootstrap/recovery purposes for non-dashboard consumers) are untouched.

### Why not alternative framings
- **Uniform-rows UX only** (symptom): leaves the probe + XSS surface + complexity in
  place; rows become uniform while adoption still runs.
- **XSS-hardening framing**: the driver, not the scope — doesn't decide table/connect UX.
- **#1701-precursor framing**: the destination; authorize-not-copy requires the OAuth
  endgame (separate issue).

### Falsification
Wrong if: any dashboard action in session mode requires key auth (no — #1828/#2167
moved reads to the JWT; `mintKey` rides the session); rows ever carry derivable
plaintext (no — server keeps hashes only); the anon/key-login carve-out regresses; the
connect step loses its same-session smoothness; or removing retention is not actually
what makes the table uniform (verified: `isActiveKey` fires only on the prefix of the
held plaintext — no held plaintext ⇒ no guarded row).

## 1. Boundary (what #2246 is NOT)

- **Server unchanged** — zero `tortoise/*.py` edits; GET /v1/team/keys list stays
  unfiltered (CLI/selfhost consumers); POST /v1/session/key + recovery purpose stay
  for non-dashboard consumers; POST /v1/session/login key-exchange on /auth untouched.
- **Anon/key-login bootstrap untouched** — claim-paste (its input IS `apiKey` state,
  main.jsx L4542/L4524), Protect screen, claimSignIn/claimEmailPassword/performClaim,
  sessionStorage `tt_claim_key` round-trip, the /auth key-login funnel.
- **recoverKey deletion is client-side only** — the function is UI-dead (zero callers;
  the "Lost your key?" affordance was removed in #1148). The endpoint it POSTs to stays.
- **Anon carve-out scope (precise)**: claim handlers/render (claimSignIn/
  claimEmailPassword/performClaim/Protect screen/paste screen) are untouched; the ONE
  shared-surface edit is the `apiKey` state initializer L694 (→ ''), which stops the
  claim-paste box from pre-filling legacy localStorage residue — the anon flow pastes
  deliberately (same as today's fresh-visit behavior; sessionStorage `tt_claim_key`
  prefill untouched). Stated here so I4's "byte-identical" wording is read as
  "claim handlers/render byte-identical".
- **NOT absorbing**: a last-used disclosure column (server rows DO carry `last_used_at`
  — hosted_api.py list serialization — but no dashboard UI renders it today; AWS IAM /
  GitHub PAT precedent shows last-used is informational, never suppressive). Deferred
  to a follow-up issue #2254; #2246 ships uniform rows per ADR-010 Decision 4 ("uniform table
  actions, no special rows"). Agent-kill protection via in-flow rotate/regenerate
  guidance + the 402-at-cap paste/rotate escape, not via row suppression.
- **Hard-blocked regions (no edits)**: wizard layout (~L4830-5060) beyond the gate copy
  branches + paste escape; claim/OAuth flows; welcome-card reveal flow beyond removing
  the persist writes; server keys list; #2230's region (another agent's worktree).
- **Out**: #1701 agent-OAuth (connect = authorize), key-scoping/per-agent keys (#2230),
  quota/402 redesign, onboarding-state server work.

## 2. Chosen solution — Purity rewrite: session-plane retention deletion to target state

One-shot deletion to the end-state (repo precedent: #2167 zero-bootstrap-mint shipped
the same pattern — pure-helper modules + static tripwire + e2e + dist rebuild).
Chosen over **encapsulate-then-delete** (a `keyLifetime.js` policy layer for ~4 real
call sites whose session policy would be "never hold" — an empty policy + router
scaffolding; better only if retention policy were an evolving knob) and **auth-plane
inversion** (lowering plaintext-key state into an anon subtree on a 6679-line file;
render-level early returns ~L4596-4710 already isolate the carve-out; better only if
planes interleaved mid-session).

### Session-authed predicate
The codebase's real signal: `sessionTokenRef.current` present + `authMode === 'session'`
(api() L1177 rides the JWT whenever the ref is set). `authMode` starts `'session'` and
flips to `'apikey'` ONLY in the sessionless claim-intent branch (L2409), so the two
coincide in every reachable state. All removed code sits in session-gated flows; the
anon claim/Protect surface is gated by early returns before the removed regions.

### Change map (main.jsx — current line anchors)
1. **Import L26** → `{ isManagedKey, durableConnectKey }`.
2. **`apiKey` state initializer L694** → `''` (no localStorage read; the claim-paste
   box no longer prefills legacy residue — the anon flow pastes deliberately, same as
   today's fresh-visit behavior; sessionStorage `tt_claim_key` prefill untouched).
3. **Session-mount one-shot residue purge** (session branch, after `sessionTokenRef`
   is confirmed, before `setAuthed(true)`): `localStorage.removeItem(KEY_STORAGE)` —
   pre-#2246 residue is cleaned once (issue: "inert residue that gets cleaned"),
   plus `setApiKey('')`/`apiKeyRef.current = ''`. The sessionless claim branch returns
   earlier (L2410) so the anon carve-out never hits the purge.
4. **Mount stored-key probe + adopt/drop block L2645-2710 + tail continuation
   L2754-2811** → deleted; replaced by: first-healthy-team pin (#1912 — required so
   completeLogin's reads are team-pinned for multi-membership users), the Round-9
   signed-out bail, `setAuthMode('session')`, `completeLogin('')`. The
   keep-suspended 5d belt is unreachable-in-production (all-suspended 403s the teams
   LIST itself → the existing teams-fetch catch renders the appeal card — verified
   L2524-2557); that catch is kept.
5. **#1906 first-timer provisioning persist L2601-2603 + teamKeysRef write L2632** →
   deleted (localStorage/setApiKey/apiKeyRef/teamKeysRef); **kept**: `setWelcomeKey`
   reveal (shown once, in-memory), #1623 `refreshTeam('', …)` + #1860 `loadAlerts`
   first-load wiring. Deliberate tradeoff: a reload mid-welcome loses the shown-once
   key (ADR shown-once fragility); recovery = + New key on the API Keys tab or the
   wizard connect step's mint (shown once again).
6. **Mount persist tail L2782-2786** → deleted (no `key` to persist; state already '').
7. **loadAll held-key classification L3218-3253** (classifyHeldKey drop/slot-truth/
   promotion) → deleted; keys rows landing kept (`managedKeys = keys.filter(isManagedKey)`
   render filter survives).
8. **wizardMintDurableKey install L3529-3532** → deleted (localStorage/teamKeysRef/
   apiKeyRef/setApiKey); kept: mint + `setWizardDurableKey` (shown once in the snippet)
   + `loadAll`. Re-entry after reload re-gates (accepted; the mint-402 handler routes
   to regenerate-in-tab + paste).
9. **regenerateKey H3 install L4172-4175** → deleted; kept mint-replacement (label
   carry-over) → revokeKey(old, {skipConfirm}) → `setNewKey` (shown once) → `loadAll`.
   Drop the now-dead `skipBootstrap` opt + comment. Rotate is now available on every
   durable row (uniform) and never installs the replacement into the browser. The
   rotate `confirm()` gains the row's name/prefix/created (same disclosure as trash).
10. **recoverKey L4310-4384** (UI-dead, zero callers) + `fallbackTeamIdRef` (L1118/
    L3174) + its comment refs (L2321/2746) → deleted. Endpoint stays.
11. **revokeKey heldKeyClearState legs L4274-4301** (slot/cache clear) → deleted; keep
    the mechanical DELETE + confirm + staleness guards + tail `loadAll`.
12. **teamKeysRef wholesale removal** — decl L1135 + every reference: logout reset
    L3184, provisioning L2632 (deleted), loadAll L3239/3253 (deleted), wizard mint
    (deleted), regen install (deleted), recoverKey (deleted), switchTeam cached-durable
    adopt L3632-3642 + session-only clear + revert `restoreKey` L3688-3690 (switch is
    JWT-gated L3615 — the adopt legs become dead with an empty cache; removed).
    **createKey** L4086-4111: drop the `activeKey`/`cachedForTeam` machinery and the
    post-mint staleness comparison entirely (it would compare '' to '' — permanent
    no-op); KEEP the `teamIdRef` identity guard (L4096-4097) + `setNewKey` + `loadAll`.
    Grep `teamKeysRef` → 0 after.
13. **Dead auth-plumbing cleanup**: `const headers = apiKey ? {Authorization…} : {}`
    (L1147) + api()'s `authHeaders = headers` default → remove (api() defaults to {} and
    the L1177 session override is the only Authorization source in session mode;
    key-mode claim flows use raw fetch). `completeLogin(key)`/`loadBackups(key)`/
    `refreshTeam(key)` keep their key params — inert fallback branches (welcome-mode
    sites L2133/2162/2175 pass `welcomeKey || ''`, vestigial under the JWT override).
    **SIGNED_OUT handler** (onAuthStateChange ~L2433): mirror logout() — null
    `sessionTokenRef.current` FIRST, then `setAuthed(false)` + `bounceToAuth()` — so a
    cross-tab/expired sign-out never leaves the stale-data zombie shell (the old
    key-auth fallback that kept a signed-out tab coherent is gone by design; PM-3 gate
    finding). Safety note: the subscription registers ONLY after boot `getSession()`
    resolves a strictly-valid session (claim/no-session branches return before
    registration; all claim/link/reauth round-trips are full-page; logout() unsubscribes
    before signOut) — do NOT hoist registration earlier (mid-exchange bounce risk).
14. **Keys table L6029-6060** → uniform: delete the `isActiveKey` "in use by this
    dashboard" note (L6032-6034) and the rotate-only/delete-suppressed fork
    (L6037-6055); every durable (non-revoked) row renders rename + toggle + trash +
    **Rotate** under the existing `isOwnerAdmin` action gating. Revoked rows terminal.
    The #2166/#2229 "held row" comments die with the branch.
    **Delete/rotate-confirm disclosure** (PM-1 mitigation): revokeKey's existing
    `confirm()` AND regenerateKey's rotate `confirm()` gain the row's
    name/prefix/created date so a one-click trash/rotate never silently kills an
    agent key the user cannot identify (rows are hash-only + names may be "—").
    Uniform actions per ADR-010; the confirm is disclosure, not suppression. The
    `last_used_at` usage column stays deferred (follow-up issue #2254).
    **Mobile**: wrap the keys `<table>` in a NEW `.keys-table-wrap`
    (`overflow-x:auto` + `min-width`; index.css additive — the class does not exist
    yet, #2166's own wrap did not ship) so 4 uniform actions don't crush 320-375px
    rows; add `scope="col"` to the `<thead>` `<th>`s while rewriting the row fork.
15. **Connect gate copy L4706 + ~L4890-5000** → `durableConnectKey` retains its FULL
    taxonomy (the paste escape ~L4984 validates pasted plaintext against
    bootstrap/revoked/disabled rows — MUST keep rejecting those); session mode passes
    apiKey `''` and resolves the gate from **rows** (see sessionKey.js below). Render
    copy branches for `bootstrap`/`revoked`/`disabled`/`unknown` are session-dead →
    removed; `'none'` reworded (drop the now-false "session keys expire within 24
    hours" clause — no bootstrap key exists in the session world); new
    `'rows-durable'` branch copy routes to create-here or rotate/regenerate in the API
    Keys tab (paste escape stays) — copy must NOT imply an existing row's key is
    embeddable (hashes only). **Role-aware gate copy**: `isOwnerAdmin` is in render
    scope — members get a paste/ask-an-owner variant instead of the create form
    (dashboard policy — corrected on review: the server POST /v1/team/keys has NO
    owner/admin role gate, so members CAN mint server-side; member create is gated
    CLIENT-side, consistent with rotate staying owner/admin-only via the
    server-gated revoke leg). Paste-error copy reworded (drop the 24h
    clause; bootstrap/revoked/disabled rejection stays). A dim "shown once" one-liner sits
    above the snippet when `wizardDurableKey`/welcomeKey supply the key. **wizardMint-
    DurableKey 403 catch** (~L3546): the dashboard_key_login=false wording is stale
    (session mints pass the flag); reword to the reachable member/role case — "your
    role can't manage keys — paste an existing durable key below (an owner or admin
    can create/rotate in the API Keys tab)". [Corrected on review: no member-role 403
    catch exists — the server has no role gate to 403 on, so the catch merges to a
    generic transport/suspension error and the member gate is client-side policy copy.]
16. **Overview/re-entry card ternaries L5721-5800** → apiKey truthiness becomes
    `(apiKey || welcomeKey)` EVERYWHERE it gates copy — the re-entry ternary L5726, its
    companion `{!apiKey && <Go to API Keys →>}` L5729, the first-data card ternary
    L5742 + its snippet-wrap, the curl-teaser span L5787 — and `firstDataSnippet`
    (L747-752) key source becomes `(welcomeKey || apiKey)` so the
    first-timer-exited-welcome case (welcomeKey in memory, apiKey `''`) keeps the
    honest "key is live" copy + a working curl everywhere; the zero-key returning case
    keeps the #2167 keyless copy.
17. **logout L3122-3124** → keep the wipe (hygiene + belt).
18. **Comment hygiene** (so static tripwire greps + readers stay honest): rewrite the
    import-header comment L24-25, switchTeam's classifyHeldKey comment L3652,
    regenerateKey's rule-5/loadAll comment L4160, the managedKeys comment L4391, and
    the stale '/v1/session/key' mention in the Session-auth header comment ~L1557-1562.

### sessionKey.js (pure module)
> Deleted exports note: `isActiveKey` appears in the main.jsx L24-25 import-header
> comment after deletion — the tripwire greps run against comment-stripped or updated
> source; the comment is rewritten (item 18) so a plain grep stays valid.
- **Deleted exports**: `isSessionKey`, `isActiveKey`, `classifyHeldKey`,
  `heldKeyClearState`, `nextRegenInstallState`, `probeClassifyStoredKey` (+ internal
  `rowTruthBad`/`prefixOf`). Verified: no consumer outside main.jsx + sessionKey.test.js;
  the anon/claim/Protect/key-login paths and registry-lane handling never import them.
  ("Retain where anon-mode needs them" → resolves to none today; the carve-out is the
  claim/Protect flows, which hold keys via sessionStorage/state, not this machinery.)
- **Kept + extended**:
  - `isManagedKey` — table filter (unchanged).
  - `durableConnectKey(welcomeKey, apiKey, keyRows)` — welcome + paste-validation
    taxonomy unchanged; NEW rows-resolution for the empty-apiKey (session) case:
    `usableDurableRows(keyRows)` = managed ∧ ¬revoked ∧ enabled!==false rows sorted
    most-recent-first (ISO `created_at` desc). When a usable durable exists →
    `{ key: '', durable: false, source: 'rows-durable' }` (drives gate copy/route ONLY —
    **never a derived key**: rows carry hashes, never plaintext); none → `{ key: '',
    durable: false, source: 'none' }`. Rows are excluded from "usable" when
    `created_via === 'bootstrap'` or `expires_at` set (isManagedKey) — the #1998 bug
    class stays closed.

### Tests
- **mintTripwire.test.js** — extend static guards (CI dashboard-js-tests): main.jsx
  must contain zero `probeClassifyStoredKey` / `classifyHeldKey` / `heldKeyClearState` /
  `nextRegenInstallState` / `isActiveKey` / `recoverKey` references, zero
  `localStorage.getItem(KEY_STORAGE)` / `.setItem(KEY_STORAGE`, zero bare
  `'/v1/session/key'` string (recoverKey deletion makes the bare grep safe — the
  bootstrap-purpose-only exemption is no longer needed).
- **sessionKey.test.js** — delete the H1-H4 / isSessionKey / isActiveKey suites (~30
  tests); keep isManagedKey + durableConnectKey (incl. paste-validation rejection of
  bootstrap/revoked/disabled); add rows-resolution tests (usable filter incl.
  disabled/revoked/bootstrap/expiring exclusions; most-recent-first ordering; rows
  never yield a key).
- **tests/e2e/test_keys_table_mixed.py** (CI RUN_DASHBOARD_E2E=1) — rework: no
  HELD_KEY adopt; the localStorage-seeded residue is **ignored** (mount purges it;
  row 3 renders as a uniform durable row — toggle/trash/rotate visible, no "in use by
  this dashboard"); assert **zero key-authed fetches** (Authorization header sniffing
  on /v1/team + /v1/team/keys + /backups = session JWT or absent, never `Bearer tt_`);
  every durable row uniform (rotate count = durable-row count; positive controls
  unchanged); rotate test reworked on a non-held row → replacement shown once,
  localStorage NOT rewritten, mint-before-revoke ordering kept, zero session-key
  mints.
- **tests/e2e/test_dashboard_gate.py** (opt-in, not CI-run) — probe-path tests
  reworked to session-only mechanisms: probe-401-drop → residue-ignored + session-only
  render; probe-403-suspended (incl. the L823 `stored-durable-on-suspended-team` belt
  test) → the F8 session-read 403 path / fresh-session multi-membership landing;
  logout wipe unchanged.
- **Python**: only the two e2e modules touched; `py_compile` check locally; the
  wrangler-harness e2e is CI-only (note in PR). Docker-lane pytest untouched (no
  server changes).
- **Build/dist**: `npm run build` in website/apps/dashboard → commit rebuilt dist (11
  tracked files; CI serves the committed dist — stale dist fails the job red).

### Docs
- ADR-010 needs front-matter (`title/type/domain/doc_status/subjects.team/created`) to
  pass the pre-flight doc-affiliation check (defaults: type `decisions`, domain
  `platform`, doc_status `live`). Front-matter only — content unchanged.
- docs/00_index.md: add ADR-010 row + this scoping doc row.

## 3. Acceptance criteria (mapped to issue Indicators + ADR)
1. **I1 (uniform rows)**: every durable key row shows identical owner actions
   (rotate + toggle + trash); no "in use by this dashboard" note; no rotate-only /
   delete-suppressed row (e2e + unit; grep `isActiveKey` → 0 in main.jsx).
2. **I2 (no key-authed mount fetch)**: the `GET /v1/team` stored-key probe is gone;
   session-mode requests carry the session JWT only (e2e header sniff + grep); the
   `tortoise_api_key` slot is purged on session mount and never read/written by
   main.jsx (static tripwire).
3. **I3 (rows-routed connect)**: `durableConnectKey` resolves from `keys[]` rows
   (durable ∧ ¬revoked ∧ ¬disabled) or routes to create/rotate; no apiKey dependency;
   same-session smoothness preserved (welcomeKey prefill + wizard mint shown once);
   paste escape still validates pasted keys against rows (bootstrap/revoked/disabled
   rejected).
4. **I4 (anon carve-out)**: claim handlers/render (claimSignIn/claimEmailPassword/
   performClaim/Protect screen/paste screen) are byte-identical — no edits to the anon
   flows; the only shared-surface edit is the `apiKey` state initializer L694 (→ '')
   which stops the claim-paste box pre-filling legacy residue (deliberate; §1).
   Key-login flows + POST /v1/session/key endpoint + recovery purpose preserved;
   recoverKey client function deleted (UI-dead).
5. **Zero server changes**; node --test green (190→~155); e2e reworked; dist rebuilt +
   committed; docs (ADR front-matter + index rows + this doc) committed.

## 4. Verification plan
`node --test website/apps/dashboard/src/*.test.js` green · `py_compile` on reworked
e2e · `npm run build` + dist committed · grep audits (no probe/isActiveKey/recoverKey/
KEY_STORAGE get/set in main.jsx; no key-authed Authorization in session code) ·
RUN_DASHBOARD_E2E suite reworked for CI (keys-table module is CI-run; gate module
opt-in — reworked, locally validated shape) · zero tortoise/*.py diff.

## 5. Known limitations (state in PR + issue)
- Cross-reload key reuse dies for returning users (wizard re-entry gates to mint/paste;
  free tier cap = 2 → 402 escape = regenerate-in-tab + paste). Same-session smoothness
  is preserved; ADR accepts shown-once fragility.
- First-timer mid-welcome reload loses the revealed key (shown-once; recovery = + New
  key / wizard mint).
- Residue from pre-#2246 localStorage is purged once on the next session mount; never
  probed/adopted in between.
- A cross-tab/expired SIGNED_OUT now bounces to /auth (mirrors logout) — the old
  key-auth fallback that kept a signed-out tab coherent is gone by design.
- Uniform one-click trash can delete a key an agent uses: the confirm dialog names the
  row; a last_used_at usage column is the filed follow-up #2254 (identification, not suppression).
- No per-row usage disclosure ships (uniform rows per ADR); last_used_at-based
  disclosure is filed follow-up #2254.
- e2e gate.py probe tests are opt-in (not CI-run); reworked + locally shape-checked.

## 6. Files touched (complete)
`website/apps/dashboard/src/main.jsx` | `sessionKey.js` | `sessionKey.test.js` |
`mintTripwire.test.js` | `website/apps/dashboard/src/index.css` (keys-table wrap) |
`tests/e2e/test_keys_table_mixed.py` | `tests/e2e/test_dashboard_gate.py`
(probe tests incl. L823 belt) | `website/apps/dashboard/dist/*` (rebuild) |
`docs/adr/ADR-010-…` (front-matter) | `docs/scoping/2026-09-04-2246-dashboard-session-only.md` |
`docs/00_index.md`. dist rebuild note: 13 tracked files (count verified).
ZERO-CHANGE: tortoise/*.py · website/signin.html · welcome.html · supabase-session.js ·
claim/Protect flows · POST /v1/session/key endpoint · registry/selfhost lanes.

## 7. Wiring
| Surface | Touch | Coverage |
|---|---|---|
| Data stores | none (no migration) | — |
| API | GET /v1/team/keys consumed unchanged (hashes only); no key-authed calls remain in session | e2e header sniff + grep |
| Auth | session predicate = sessionTokenRef/authMode; anon claim-paste input = apiKey state (kept) | e2e claim flows untouched |
| UI | mount probe removal; uniform keys table; connect gate copy; overview ternaries | unit + e2e + tripwire |
| Build | dist rebuild + commit (CI-served) | CI RUN_DASHBOARD_E2E |
| Docs | ADR-010 front-matter + index rows + scoping doc | pre-flight affiliation check |
| Parallel work | #2230 (another worktree, same keys region) — no overlap in this diff's regions; #1701 (OAuth endgame, context only); last-used disclosure follow-up #2254 filed | checkout guard + C2 |
