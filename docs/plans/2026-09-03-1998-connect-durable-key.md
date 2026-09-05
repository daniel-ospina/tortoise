# Plan — connect step must source a DURABLE API key (post-merge fold-in, #1998)

## Problem (finding, 2026-09-02, PR #2161 comment 5519054892 + issue #1998 comment)

PR #2161 (W2 #1998) MERGED **without** this fold-in. Post-merge gap:

- The wizard connect step (wizardStep 3, live tree) renders
  `UNIVERSAL_COMMAND[harness](harnessKey)` where `harnessKey = welcomeKey || apiKey || ''`
  (`main.jsx` line ~4531).
- `welcomeKey` = durable provisioned key revealed once at provisioning (first-timers) — OK.
- `apiKey` for **returning users** = 24h **bootstrap** key minted at login
  (`POST /v1/session/key` `purpose=bootstrap`, `expires_at = now+24h`, `created_via='bootstrap'`).
- Consequence: an agent configured with that value (literal in Claude Desktop/Web
  configs, or as the env-var value) **stops authenticating within 24 hours**.

## Fix (per finding)

At the connect step, when no durable key exists (or the current `apiKey` is a
bootstrap/session key), mint a durable `provisioned` key via the existing
`POST /v1/team/keys` (shown once — the reveal/key-card UX already exists),
OR route the user to create one in the API Keys tab. Keep env-var indirection.
Do NOT remove `POST /v1/session/key` bootstrap mint or change the selfhost lane.

## Design

### Detection (client, pure + testable)

`keys[]` state already carries each key row's `created_via` / `expires_at`
(`GET /v1/team/keys` returns ALL rows incl. bootstrap — `keys[]` stays
unfiltered in state per #2166 so `keyIdFromValue` prefix matching works).
The current `apiKey` is classified by prefix (`apiKey.slice(0,10)` ==
`row.key_prefix`):

- row exists && (created_via === 'bootstrap' || expires_at) → **session key** → not durable
- row exists && durable (created_via 'provisioned'/'recovery'/NULL + no expiry) → durable
- row revoked / disabled (enabled:false) → not usable → gate
- no row found (keys not loaded yet / stale) → **cannot confirm** → conservative:
  NOT durable (fail toward mint/route; never embed a possibly-24h key)

`welcomeKey` (first-timer provisioned durable key, A13) is always durable.

Pure helper in `sessionKey.js`:

```js
export function durableConnectKey(welcomeKey, apiKey, keyRows) {
  if (welcomeKey) return { key: welcomeKey, durable: true, source: 'welcome' }
  if (!apiKey) return { key: '', durable: false, source: 'none' }
  const row = (keyRows || []).find((k) => k.key_prefix === String(apiKey).slice(0, 10))
  if (!row) return { key: '', durable: false, source: 'unknown' }   // cannot confirm → gate
  if (row.created_via === 'bootstrap' || !!row.expires_at) return { key: '', durable: false, source: 'bootstrap' }
  return { key: apiKey, durable: true, source: 'durable' }
}
```

(Shape mirrors sessionKey.js's existing pure predicate style; unit-tested in
sessionKey.test.js with node --test — no harness needed.)

### main.jsx connect step (LIVE tree only, wizardStep === 3)

1. Compute `connectKey = durableConnectKey(welcomeKey, apiKey, keys)`.
2. `harnessKey` stays as the embed value **when durable**:
   - welcomeKey present → embed (unchanged first-timer path)
   - apiKey durable → embed apiKey
   - apiKey bootstrap/absent/unknown → **do NOT embed** — show the durable-key gate.
3. Gate UI replaces the current `!harnessKey` fallback text in the LIVE connect
   step (the archived legacy tree's `!harnessKey` branch is dead — not touched):
   - copy: setup command needs a durable API key (session keys expire in 24h);
   - primary button **"Create a durable setup key"** → `mintKey()` (POST
     /v1/team/keys, session-authed via `useSession: true`, returns plaintext
     once) → on success store plaintext in wizard state → render the command
     with the minted durable key (reveal-once: the command embeds it; the key
     is also listed in the API Keys tab for regeneration);
   - cap error (HTTP 402 → `max_api_keys` reached, free tier = 2) → message:
     "You've reached your plan's limit of API keys — create or regenerate one
     in the API Keys tab" + **Go to API Keys →** button (the existing
     `setWelcomeMode(false); setTab('keys')` route);
   - any other error → inline error text with retry.
4. `welcomeKey`-independent: minted durable key is kept in wizard-local state
   (`wizardDurableKey`) so the snippet + copy use it for the rest of the flow.

### Why no auto-mint on render

The connect step is reachable by returning users re-entering the wizard.
Auto-minting a team key on every render/visit would burn `max_api_keys`
(free tier = 2) without consent and create orphaned durable keys on skip.
Explicit button + cap-fallback to the API Keys tab is the finding's option 1
with option 2 as the escape — matches the existing API Keys tab create UX.

### Out of scope (recorded, no action)

- #2166 keys table durable-only — MERGED (#2175) already.
- #2167 drop dashboard bootstrap auto-mint — sequenced separately; this fix
  does NOT touch `POST /v1/session/key` or the selfhost lane.
- The env-var indirection is preserved (commands reference `$TORTOISE_API_KEY`
  or the literal in desktop/web configs — unchanged).

## Files

- `website/apps/dashboard/src/sessionKey.js` — add `durableConnectKey()` pure helper.
- `website/apps/dashboard/src/sessionKey.test.js` — unit tests (node --test).
- `website/apps/dashboard/src/main.jsx` — connect-step durable gate + mint handler.
- `website/apps/dashboard/dist/` — rebuilt bundle (JS change, #1148 convention).

## Tests

- JS: `node --test src/sessionKey.test.js` (new cases: bootstrap → gate,
  durable → embed, welcomeKey → embed, unknown/absent → gate, revoked durable
  → gate).
- Full dashboard JS: `node --test src/*.test.js src/*.test.jsx`.
- `npm run build` (vite) succeeds; dist committed.

## Rollout

Tier-2 PR (dashboard-only JS + dist; no python, no shared modules).
commit-workflow (VGATE) → push → PR → record-review → auto-merge.
