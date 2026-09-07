// keyExpiryTripwire.test.js — #2426 configurable API-key expiration static
// tripwires (CI-run via dashboard-js-tests, node --test zero-dep convention).
// The #2426 UX surface lives in main.jsx (component-scope helpers + JSX) and
// sessionKey.js (pure predicates, unit-tested in sessionKey.test.js) — the
// greps below make the FEATURE'S LOAD-BEARING SHAPES hard regressions:
//   1. the create form's expiry presets (30d DEFAULT / Custom / Never),
//      minted as expires_in days ONLY (never the expires_at body param);
//   2. the keys-table Expires column (Created | Expires | Status) fed by the
//      fmtExpiry derivation (Never / amber in-N-days / terminal expired);
//   3. rotate re-applying the old row's lifetime span + confirm copy stating
//      the replacement expiry; the show-once card echoing the mint expiry;
//   4. isManagedKey staying bootstrap-exclusion-only (an expiring durable
//      row must NEVER vanish from the table — the #2426 critical fix);
//   5. the wizard's Never-keys-only embed hint + expiring-paste rejection.
// A future edit that drops any of these regresses #2426 the same way the
// pre-fix dashboard hid every expiring key.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const mainJsx = readFileSync(join(here, 'main.jsx'), 'utf8')
const sessionKeyJs = readFileSync(join(here, 'sessionKey.js'), 'utf8')
const indexCss = readFileSync(join(here, 'index.css'), 'utf8')

// 1. Create-form expiry presets — 30d is the DEFAULT (market decision), the
// select exposes Custom + Never, and the mint payload is days-only.
test('#2426: create form defaults to the 30d preset with Custom + Never options', () => {
  assert.match(mainJsx, /newKeyExpiryPreset,\s*setNewKeyExpiryPreset\]\s*=\s*React\.useState\('30'\)/,
    'the expiry preset state must DEFAULT to 30d (market default — never None)')
  assert.match(mainJsx, /\{ id: 'custom', label: 'Custom date…' \}/, 'Custom preset option')
  assert.match(mainJsx, /\{ id: 'never', label: 'No expiration' \}/, 'Never preset option')
  assert.match(mainJsx, /newKeyExpiryPreset === 'custom' && \(/, 'Custom reveals the date input')
  assert.match(mainJsx, /type="date"/, 'Custom date input renders')
})

test('#2426: mintKey sends expires_in DAYS only — never the expires_at body param', () => {
  assert.match(mainJsx, /if \(expiresInDays != null && !Number\.isNaN\(expiresInDays\)\) payload\.expires_in = expiresInDays/,
    'mintKey must translate the preset to expires_in days')
  // The days-only path is deliberate (avoids the server's mutually-exclusive
  // dual-param validation entirely) — the create/rotate callers compute days.
  assert.match(mainJsx, /const days = newKeyExpiryPreset === 'never' \? null/, 'Never → no param')
  assert.match(mainJsx, /expiryDaysFromDate\(newKeyExpiryDate\)/, 'Custom → computed days')
  // No expiry-param mint body: every mintKey POST body is the payload object.
  assert.match(mainJsx, /body: JSON\.stringify\(payload\),/, 'mintKey body is the shared payload')
  // Never mints must keep the legacy byte-identical shape — never a body that
  // the server would read as an expiry request.
  const mintKeySlice = mainJsx.slice(mainJsx.indexOf('async function mintKey'))
  assert.ok(!/JSON\.stringify\(\{\s*(name\??[^}]*expires_at|expires_at)/.test(mintKeySlice),
    'mintKey must never JSON-stringify an expires_at body key')
})

test('#2426: Custom dates are clamped to the 1-366-day window (server 422 parity)', () => {
  assert.match(mainJsx, /KEY_MAX_EXPIRY_DAYS = 366/, '366-day ceiling constant')
  assert.match(mainJsx, /Math\.ceil\(\(t - Date\.now\(\)\) \/ _MS_PER_DAY\)/, 'date → whole-days conversion')
  assert.match(mainJsx, /return \(days >= 1 && days <= KEY_MAX_EXPIRY_DAYS\) \? days : null/, '1-366 clamp')
})

// 2. Keys-table Expires column (Created | Expires | Status) + fmtExpiry states.
test('#2426: the keys table has an Expires column between Created and Status', () => {
  const header = mainJsx.match(/<th scope="col">Name<\/th>.*<\/thead>/s)
  assert.ok(header, 'keys-table thead found')
  const cols = header[0].match(/<th scope="col">([^<]*)<\/th>/g) || []
  const names = cols.map((c) => c.replace(/<[^>]+>/g, ''))
  assert.ok(names.indexOf('Created') !== -1 && names.indexOf('Expires') !== -1 && names.indexOf('Status') !== -1,
    `Expires must sit between Created and Status, got: ${names}`)
  assert.ok(names.indexOf('Expires') > names.indexOf('Created'), 'Expires after Created')
  assert.ok(names.indexOf('Expires') < names.indexOf('Status'), 'Expires before Status')
  assert.match(mainJsx, /colSpan="6"/, 'empty-state colSpan widened for the 6th column')
})

test('#2426: fmtExpiry renders Never / amber in-N-days / terminal expired', () => {
  assert.match(mainJsx, /function fmtExpiry\(iso, now = Date\.now\(\)\)/, 'fmtExpiry helper exists')
  assert.match(mainJsx, /if \(!iso\) return \{ text: 'Never', cls: '' \}/, 'Never when null')
  assert.match(mainJsx, /text: 'expired', cls: 'expired'/, 'terminal expired state')
  assert.match(mainJsx, /days <= KEY_SOON_DAYS/, 'expiring-soon threshold')
  assert.match(mainJsx, /\$\{dateText\} · in \$\{days\} day/, 'amber in-N-days label')
  // Tombstone: an expired row is still RENDERED (no filter hides it).
  assert.match(mainJsx, /const ex = fmtExpiry\(k\.expires_at\)/, 'Expires cell renders from the row')
})

test('#2426: CSS states for expiring (amber) and expired (terminal red) exist', () => {
  assert.match(indexCss, /\.expiring \{ color: #fbbf24; \}/, 'amber .expiring rule')
  assert.match(indexCss, /\.expired \{ color: var\(--red\); \}/, 'terminal .expired rule')
})

// 3. Show-once card + rotate carry-over.
test('#2426: the show-once key card states the expiry (server echo / never)', () => {
  assert.match(mainJsx, /setNewKeyExpiresAt\(\(mk && mk\.expires_at\) \|\| null\)/,
    'create/rotate capture the server expiry echo')
  assert.match(mainJsx, /expires \{fmtExpiryDate\(newKeyExpiresAt\)\}/, 'card shows the expiry date')
  assert.match(mainJsx, /never expires/, 'card states Never explicitly')
})

test('#2426: rotate re-applies the old row lifetime span + confirm states replacement expiry', () => {
  assert.match(mainJsx, /function lifetimeDaysFromRow\(row\)/, 'lifetime-span helper exists')
  assert.match(mainJsx, /lifetimeDaysFromRow\(oldRow\)/, 'rotate mint passes the span as expires_in')
  assert.match(mainJsx, /The replacement never expires \(same as this key\)\./, 'Never stays Never in the confirm')
  assert.match(mainJsx, /The replacement expires \$/, 'confirm names the replacement expiry date')
})

// 4. isManagedKey — the CRITICAL pre-existing-bug fix: bootstrap-exclusion
// ONLY (any expiring durable must stay a table row).
test('#2426: isManagedKey excludes bootstrap only — no expires_at exclusion may return', () => {
  const start = sessionKeyJs.indexOf('export function isManagedKey')
  const body = sessionKeyJs.slice(start, sessionKeyJs.indexOf('export function', start + 10))
  assert.ok(!body.includes('expires_at'),
    `isManagedKey must be bootstrap-exclusion only (an expires_at check would hide every expiring key): ${body}`)
  assert.match(body, /created_via === 'bootstrap'/, 'bootstrap rows stay excluded')
  assert.match(body, /return !\(k\.created_via === 'bootstrap'\)/, 'bootstrap-only predicate')
  // The embed-safe derivations (usableDurableRows / durableConnectKey) keep
  // their own no-expiry filters — the embed surface stays Never-keys-only.
  const usable = sessionKeyJs.slice(sessionKeyJs.indexOf('export function usableDurableRows'))
  assert.match(usable, /!k\.expires_at/, 'usableDurableRows excludes expiring rows explicitly')
  assert.match(sessionKeyJs, /if \(row\.expires_at\) return \{ key: '', durable: false, source: 'expiring' \}/,
    'paste classifier reports source expiring (distinct from bootstrap)')
})

// 5. Wizard embed surface — Never-keys-only policy + the hint.
test('#2426: the connect wizard keeps Never-only embeds and hints to pick No expiration', () => {
  assert.match(mainJsx, /Keys embedded in agents should never expire — when you create or rotate one in the API Keys tab, choose <strong>No expiration<\/strong>\./,
    'owner/admin embed hint names the No expiration choice')
  assert.match(mainJsx, /check\.source === 'expiring'/, 'paste validation rejects expiring rows')
  assert.match(mainJsx, /It expires, and a key embedded in an agent must never expire/, 'truthful expiring reason')
})
