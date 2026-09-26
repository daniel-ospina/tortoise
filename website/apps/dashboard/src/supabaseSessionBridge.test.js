// supabaseSessionBridge.test.js — #3485 behavioural execution tests for the
// retained cross-subdomain session bridge (website/assets/supabase-session.js).
//
// WHY THIS FILE EXECUTES THE REAL SCRIPT. The pins it replaces were TEXT
// extractors — brace counting plus substring counts. Three review cycles each
// escaped them without being wrong: a `}` inside a string literal truncated a
// brace-counted body; a `//` inside a URL string corrupted comment stripping;
// `window['local'+'Storage']` dodged a `"localStorage" not in body` check;
// `legacyOk=true` dodged `count("legacyOk =")`; `!(stored.expires_at && …)`
// kept the `&&` count at 4 while inverting the polarity; and a bare
// `return null;` above the migration kept every ordering/if check green. Text
// is the wrong tool. This file loads the REAL bridge into a `node:vm` context
// with a minimal browser stub and asserts only OBSERVABLE behaviour — return
// values, cookie bytes, localStorage contents, history calls. None of the
// obfuscations above change behaviour, so none of them can save a mutant.
//
// The sandbox stubs exactly what the bridge touches: `window`/`window.location`,
// `window.localStorage`, `document.cookie` (a real name-addressed jar with the
// RFC 6265 4096-byte per-cookie cap, so an oversized write is DROPPED like a
// browser drops it) and `history.replaceState`. `URL`/`URLSearchParams` are
// passed in because a fresh vm context has neither.
//
// #4054 — WHY THIS SUITE IS RESTORED, AND WHY THE SUBJECT IS THE SHARED FILE.
// This file was deleted with the dashboard's COPY of the bridge
// (website/apps/dashboard/public/assets/supabase-session.js), but
// website/assets/supabase-session.js itself is RETAINED (and still served from
// premiselabs.co). Deleting the only executing test of a retained artifact is
// how it silently rots, so the suite is restored against the shared file.
//
// The dashboard no longer loads the bridge — it is BFF-migrated — and NEITHER
// DOES blog-admin: blog-admin ships its own adapter
// (website/apps/blog-admin/src/lib/supabase.ts) which only shares the COOKIE
// NAME; it has no <script src="/assets/supabase-session.js"> and no import of
// the bridge. The reasons the shared bridge is retained are different and
// concrete: tortoise/oauth.py's live consent-page client is a faithful inline
// PORT of its adapter (parity pinned by tests/test_cross_subdomain_cookie_sync.py),
// the dashboard's live `tt_claim_pending` marker helpers mirror its
// host-conditional helpers (same test), and this file is the subject of the #3503
// fragment-retention gate (tests/test_session_bridge_fragment_retention.py).
// It lives in the dashboard suite because that is the repo's node test runner —
// `.github/workflows/ci.yml` predicates the `dashboard-js-tests` job on a change
// to website/assets/supabase-session.js precisely so this suite runs for it.
// The path below resolves from here to website/assets/supabase-session.js.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const here = dirname(fileURLToPath(import.meta.url))
// website/apps/dashboard/src → ../../../assets/supabase-session.js
const BRIDGE_PATH = join(here, '..', '..', '..', 'assets', 'supabase-session.js')
const BRIDGE_SRC = readFileSync(BRIDGE_PATH, 'utf8')

const COOKIE_NAME = 'sb-tortoise-auth-token'
const PROD_LEGACY_KEY = 'sb-ybetwichurajbfswfeqa-auth-token'
const LOCAL_LEGACY_KEY = 'sb-127-auth-token'
const PROD_URL = 'https://ybetwichurajbfswfeqa.supabase.co'
const LOCAL_URL = 'http://127.0.0.1:54321'
const DAY_MS = 24 * 3600 * 1000
const COOKIE_BYTE_CAP = 4096 // RFC 6265 §6.1 minimum per-cookie size

// ── session fixtures ────────────────────────────────────────────────────────
let tokenSeq = 0
function session(overrides = {}) {
  tokenSeq += 1
  return {
    access_token: `access-${tokenSeq}`,
    refresh_token: `refresh-${tokenSeq}`,
    expires_at: Math.floor((Date.now() + DAY_MS) / 1000),
    token_type: 'bearer',
    ...overrides,
  }
}
const pastExpiry = () => Math.floor((Date.now() - 60_000) / 1000)
const futureExpiry = (ms = DAY_MS) => Math.floor((Date.now() + ms) / 1000)
// A valid JSON session that no browser will accept as a cookie: its encoded
// value exceeds the per-cookie cap, so the write is DROPPED (this is the real
// "oversized session silently refused" case the round-trip check exists for).
function oversizedSession() {
  return session({ access_token: 'A'.repeat(COOKIE_BYTE_CAP + 200) })
}

// ── the browser stub ────────────────────────────────────────────────────────
function makeCookieJar({ cap = COOKIE_BYTE_CAP } = {}) {
  const jar = new Map()
  const writes = []
  const document = {
    get cookie() {
      return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ')
    },
    set cookie(header) {
      const text = String(header)
      const semi = text.indexOf(';')
      const pair = semi === -1 ? text : text.slice(0, semi)
      const attrs = semi === -1 ? '' : text.slice(semi)
      const eq = pair.indexOf('=')
      const name = pair.slice(0, eq)
      const value = pair.slice(eq + 1)
      if (/;\s*Max-Age=0/i.test(attrs)) {
        jar.delete(name)
        writes.push({ header: text, dropped: false, removed: true })
        return
      }
      // Browser parity: a serialized cookie above the per-cookie cap is dropped
      // silently — exactly the refusal `storeSession` reads back to detect.
      const dropped = name.length + value.length > cap
      writes.push({ header: text, dropped, removed: false })
      if (dropped) return
      jar.set(name, value)
    },
  }
  return { jar, document, writes }
}

function makeLocalStorage() {
  const map = new Map()
  return {
    map,
    api: {
      getItem: (k) => (map.has(String(k)) ? map.get(String(k)) : null),
      setItem: (k, v) => { map.set(String(k), String(v)) },
      removeItem: (k) => { map.delete(String(k)) },
      clear: () => map.clear(),
      key: (i) => [...map.keys()][i] ?? null,
      get length() { return map.size },
    },
  }
}

function makeSandbox({
  hostname = 'tortoise.premiselabs.co',
  hash = '',
  pathname = '/',
  search = '',
  origin,
  cap = COOKIE_BYTE_CAP,
  supabase,
} = {}) {
  const { jar, document, writes } = makeCookieJar({ cap })
  const ls = makeLocalStorage()
  const replaceStateCalls = []
  const locationReplaceCalls = []
  const location = {
    hostname,
    hash,
    pathname,
    search,
    origin: origin ?? `https://${hostname}`,
    replace: (u) => { locationReplaceCalls.push(String(u)) },
  }
  const window = { location, localStorage: ls.api }
  if (supabase !== undefined) window.supabase = supabase
  const history = { replaceState: (a, b, url) => { replaceStateCalls.push(String(url)) } }

  const sandbox = {
    window,
    document,
    history,
    URL,
    URLSearchParams,
    console,
  }
  vm.createContext(sandbox)
  vm.runInContext(BRIDGE_SRC, sandbox, { filename: BRIDGE_PATH })

  return {
    sandbox, window, document, jar, writes, ls, location, history,
    replaceStateCalls, locationReplaceCalls,
    bridge: window.__tortoiseSessionBridge,
  }
}

// Read a cookie the way the bridge does (name → URL-decoded value).
function cookieValue(sb, name = COOKIE_NAME) {
  const m = sb.document.cookie.match(
    new RegExp('(?:^|; )' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)')
  )
  return m ? decodeURIComponent(m[1]) : null
}
// Seed a cookie without going through the bridge (the "something already
// stored" precondition).
function seedCookie(sb, value, name = COOKIE_NAME) {
  sb.document.cookie = `${name}=${encodeURIComponent(value)}; Path=/`
}

// ── positive control / sandbox reachability ─────────────────────────────────

test('#3485: a valid, unexpired parent-domain cookie session IS returned', () => {
  const sb = makeSandbox()
  const s = session()
  seedCookie(sb, JSON.stringify(s))
  const got = sb.window.readValidSession()
  assert.ok(got, 'a valid future-dated cookie session must be returned (not null)')
  assert.equal(got.access_token, s.access_token, 'the cookie access token is returned')
  assert.equal(got.refresh_token, s.refresh_token, 'the cookie refresh token is returned')
})

test('#3485: the bridge exposes the gate helpers to window', () => {
  const sb = makeSandbox()
  for (const name of ['readValidSession', 'storeSession', 'clearStoredSession', 'bounceToAuth']) {
    assert.equal(typeof sb.window[name], 'function', `window.${name} must be callable by the gate`)
  }
  assert.equal(sb.bridge.COOKIE_NAME, COOKIE_NAME, 'the shared cookie name is exposed')
})

// ── (1) a legacy-only localStorage session is never returned unshared ───────

test('#3485: a legacy-only session that CANNOT be shared is never returned', () => {
  const sb = makeSandbox()
  const legacy = JSON.stringify(oversizedSession())
  sb.ls.api.setItem(PROD_LEGACY_KEY, legacy)
  const got = sb.window.readValidSession()
  assert.equal(got, null,
    'the session lives only in origin-scoped localStorage and the cookie write did ' +
    'not land — reporting it IS the tortoise → app → tortoise loop')
  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the oversized cookie write must not have landed')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), legacy,
    'the only surviving copy of the credential must not be destroyed')
})

test('#3485: a legacy-only session that CAN be shared IS shared, then returned', () => {
  const sb = makeSandbox()
  const s = session()
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))
  const got = sb.window.readValidSession()
  assert.ok(got, 'a shareable legacy session must be migrated into the cookie and returned')
  assert.equal(got.access_token, s.access_token)
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, s.access_token,
    'the parent-domain cookie must carry the legacy session')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null,
    'the legacy copy is dropped once it is shared')
})

test('#3485: a non-session legacy value never poisons the shared cookie (gate path)', () => {
  const sb = makeSandbox()
  // Valid JSON, object-shaped, future-dated — but no access_token, so it is not
  // a session. It must never reach the parent-domain cookie; a cookie carrying
  // it would outrank a real session under the second legacy key.
  const junk = JSON.stringify({ expires_at: futureExpiry() })
  sb.ls.api.setItem(PROD_LEGACY_KEY, junk)
  const got = sb.window.readValidSession()
  assert.equal(got, null, 'a non-session must never be reported as a session')
  assert.equal(cookieValue(sb, COOKIE_NAME), null,
    'a value that is not a session must never be written to the parent-domain cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null, 'the junk is dropped')
})

// ── (2) expired / unparseable cookie → null ─────────────────────────────────

test('#3485: an expired, expiry-less, unparseable, or token-less cookie session yields null', () => {
  const cases = [
    ['expired', JSON.stringify(session({ expires_at: pastExpiry() }))],
    ['no expires_at', JSON.stringify({ access_token: 'a', refresh_token: 'r' })],
    ['not JSON', 'this-is-not-json'],
    ['no access_token', JSON.stringify({ expires_at: futureExpiry() })],
    ['no refresh_token', JSON.stringify({ access_token: 'a', expires_at: futureExpiry() })],
  ]
  for (const [label, raw] of cases) {
    const sb = makeSandbox()
    seedCookie(sb, raw)
    assert.equal(sb.window.readValidSession(), null, `${label}: must yield null`)
  }
})

// The cycle-5 P1 (found by a fresh reviewer): supabase-js's own _isValidSession
// additionally requires a `refresh_token` KEY, and its load path _removeSession()s
// the stored session when it is missing. The dashboard mounts supabase-js, so a
// cookie this bridge ACCEPTS but that consumer DELETES is a live loop: the head
// gate passes, the mount gate wipes the cookie, the app bounces to /auth, and the
// migration re-creates the same cookie from the legacy key kept because the
// unconfirmable write never dropped it. The gate must accept only what the
// consumer accepts.
test('#3485: a cookie without a refresh_token is NOT a session (the consumer would delete it)', () => {
  const sb = makeSandbox()
  seedCookie(sb, JSON.stringify({ access_token: 'access-1', expires_at: futureExpiry() }))
  assert.equal(sb.window.readValidSession(), null,
    'a cookie supabase-js would delete must never be reported as a session')
  // The same shape in localStorage is junk: it must not reach the shared cookie,
  // and it must not survive to be re-migrated on the next bounce.
  const sb2 = makeSandbox()
  sb2.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify({ access_token: 'access-2', expires_at: futureExpiry() }))
  assert.equal(sb2.window.readValidSession(), null, 'no refresh_token is still no session')
  assert.equal(cookieValue(sb2, COOKIE_NAME), null,
    'an unusable legacy value must never reach the parent-domain cookie')
  assert.equal(sb2.ls.api.getItem(PROD_LEGACY_KEY), null,
    'the unusable legacy key is dropped rather than re-migrated forever')
})

// ── (3) storeSession round-trip ─────────────────────────────────────────────

test('#3485: storeSession returns true only when the cookie now carries THAT session', () => {
  const sb = makeSandbox()
  const s = session()
  assert.equal(sb.window.storeSession(s), true, 'a durable write must report success')
  const stored = JSON.parse(cookieValue(sb, COOKIE_NAME))
  assert.equal(stored.access_token, s.access_token, 'the cookie carries the exact access token')
  assert.equal(stored.refresh_token, s.refresh_token, 'the cookie carries the exact refresh token')
})

test('#3485: storeSession is false for a token-less, expired, or non-round-tripping write', () => {
  // token-less
  const sb1 = makeSandbox()
  assert.equal(sb1.window.storeSession({ access_token: 'a', expires_at: futureExpiry() }), false,
    'a session without a refresh_token is not stored')
  // expired
  const sb2 = makeSandbox()
  assert.equal(sb2.window.storeSession(session({ expires_at: pastExpiry() })), false,
    'an expired session must not be reported as stored')
  // refused write (oversized), nothing pre-existing
  const sb3 = makeSandbox()
  assert.equal(sb3.window.storeSession(oversizedSession()), false,
    'a write the browser refused must not report success')
  assert.equal(cookieValue(sb3, COOKIE_NAME), null, 'nothing landed')
})

test('#3485: a refused write must not read back a PRE-EXISTING cookie as success', () => {
  const sb = makeSandbox()
  const existing = session()
  seedCookie(sb, JSON.stringify(existing))
  const oversized = oversizedSession()
  assert.equal(sb.window.storeSession(oversized), false,
    'reading back whatever cookie happened to be there is not a round-trip')
  const stored = JSON.parse(cookieValue(sb, COOKIE_NAME))
  assert.equal(stored.access_token, existing.access_token,
    'the pre-existing session is untouched')
  assert.notEqual(stored.access_token, oversized.access_token,
    'the new session was never stored, so it must not be claimed as stored')
})

// ── (4) an expired legacy session never displaces / invalidates a cookie ─────

test('#3485: a valid cookie survives an EXPIRED legacy session in localStorage', () => {
  const sb = makeSandbox()
  const cookie = session({ access_token: 'cookie-live' })
  seedCookie(sb, JSON.stringify(cookie))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-dead', expires_at: pastExpiry() })))
  const got = sb.window.readValidSession()
  assert.ok(got, 'the valid parent-domain cookie must still be read')
  assert.equal(got.access_token, 'cookie-live', 'the expired legacy must not displace it')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'cookie-live',
    'the cookie bytes must be unchanged')
})

test('#3485: an expired legacy never overwrites a cookie merely for expiring later', () => {
  const sb = makeSandbox()
  const now = Date.now()
  // The cookie is present and carries an access_token (so it is "usable"), but
  // it is itself expired. The legacy session is ALSO expired, yet its
  // expires_at is later — without the "legacy must still be usable" guard the
  // later value would overwrite the cookie.
  seedCookie(sb, JSON.stringify(session({ access_token: 'cookie-older', expires_at: Math.floor((now - 7200_000) / 1000) })))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-newer', expires_at: Math.floor((now - 60_000) / 1000) })))
  sb.window.readValidSession()
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'cookie-older',
    'an expired legacy session must not displace the cookie just because it expires later')
})

test('#3485: a NEWER, still-usable legacy session DOES take over the cookie', () => {
  // The converse of the test above: a live legacy session with a later expiry
  // is a genuine refresh of a stale cookie and must win (this proves the guard
  // above refuses expired values, not every value).
  const sb = makeSandbox()
  seedCookie(sb, JSON.stringify(session({ access_token: 'cookie-stale', expires_at: futureExpiry(3600_000) })))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-fresh', expires_at: futureExpiry(2 * DAY_MS) })))
  const got = sb.window.readValidSession()
  assert.equal(got.access_token, 'legacy-fresh', 'a newer, unexpired legacy session must be adopted')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'legacy-fresh')
})

// ── (5) a corrupt legacy value must not stop the cookie being read ──────────

test('#3485: a valid-JSON legacy with an unpaired surrogate does not stop the cookie read', () => {
  const sb = makeSandbox()
  const cookie = session()
  seedCookie(sb, JSON.stringify(cookie))
  // JSON.parse accepts the lone high surrogate, but encodeURIComponent(value)
  // throws URIError on it — the corrupt-value path that must stay contained.
  const legacy =
    '{"access_token":"legacy-live","refresh_token":"r","expires_at":' +
    futureExpiry(2 * DAY_MS) + ',"note":"\uD800"}'
  assert.ok(legacy.includes('\uD800'), 'fixture must carry a real unpaired surrogate unit')
  assert.doesNotThrow(() => JSON.parse(legacy), 'the fixture is valid JSON (parses)')
  assert.throws(() => encodeURIComponent(legacy), /URIError|malformed/,
    'the fixture must trigger the URIError the bridge has to absorb')
  sb.ls.api.setItem(PROD_LEGACY_KEY, legacy)

  let got
  assert.doesNotThrow(() => { got = sb.window.readValidSession() },
    'a corrupt legacy value must never escape the bridge (never break the page)')
  assert.ok(got, 'the valid parent-domain cookie must still be read')
  assert.equal(got.access_token, cookie.access_token,
    'the corrupt value was never written; the cookie still holds the real session')
  assert.ok(!String(cookieValue(sb, COOKIE_NAME)).includes('legacy-live'),
    'a value that cannot be encoded must never reach the shared cookie')
})

// ── (6) the implicit-flow fragment is stripped only when the write landed ───

test('#3485: a landed fragment write strips the fragment exactly once', () => {
  const hash =
    '#access_token=' + encodeURIComponent('frag-access') +
    '&refresh_token=' + encodeURIComponent('frag-refresh') +
    '&expires_at=' + futureExpiry() + '&expires_in=3600&token_type=bearer'
  const sb = makeSandbox({ hash })
  assert.equal(sb.replaceStateCalls.length, 1, 'a session that landed in the cookie strips the fragment')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'frag-access')
})

test('#3485: a refused fragment write leaves the fragment untouched (the only copy survives)', () => {
  const hash =
    '#access_token=' + 'A'.repeat(COOKIE_BYTE_CAP + 200) +
    '&refresh_token=r&expires_at=' + futureExpiry()
  const sb = makeSandbox({ hash })
  assert.equal(sb.replaceStateCalls.length, 0,
    'storeSession returned false, so erasing the fragment would strand the visitor with nothing stored')
  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the oversized write did not land')
})

test('#3485: an EXPIRED fragment is not stripped (it was never stored)', () => {
  const hash = '#access_token=a&refresh_token=r&expires_at=' + pastExpiry()
  const sb = makeSandbox({ hash })
  assert.equal(sb.replaceStateCalls.length, 0,
    'an expired fragment must survive rather than be silently discarded')
})

// ── migrateLegacySession (the createTortoiseSupabaseClient / body path) ─────

test('#3485 (body path): a valid legacy session migrates to the cookie and is dropped', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({ ok: true }) } })
  const s = session()
  sb.ls.api.setItem(PROD_LEGACY_KEY, JSON.stringify(s))
  const client = sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.ok(client, 'the factory returns a client when window.supabase is loaded')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, s.access_token,
    'the legacy session must reach the shared cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null, 'the legacy copy is dropped once shared')
})

test('#3485 (body path): the local-host legacy key is also migrated', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const s = session()
  sb.ls.api.setItem(LOCAL_LEGACY_KEY, JSON.stringify(s))
  sb.window.createTortoiseSupabaseClient(LOCAL_URL, 'anon-key')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, s.access_token,
    'the supabase-js derived local key (sb-127-…) must migrate too')
})

test('#3485 (body path): a non-session legacy value never poisons the shared cookie', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const junk = JSON.stringify({ expires_at: futureExpiry() }) // object, no access_token
  sb.ls.api.setItem(PROD_LEGACY_KEY, junk)
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.equal(cookieValue(sb, COOKIE_NAME), null,
    'a value that is not a session must never be written to the parent-domain cookie')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), null, 'the junk is dropped')
})

test('#3485 (body path): a refused write never destroys the only copy', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const legacy = JSON.stringify(oversizedSession())
  sb.ls.api.setItem(PROD_LEGACY_KEY, legacy)
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.equal(cookieValue(sb, COOKIE_NAME), null, 'the oversized write did not land')
  assert.equal(sb.ls.api.getItem(PROD_LEGACY_KEY), legacy,
    'the only surviving copy of the credential must not be destroyed')
})

test('#3485 (body path): an expired legacy never displaces a valid cookie', () => {
  const sb = makeSandbox({ supabase: { createClient: () => ({}) } })
  const cookie = session({ access_token: 'cookie-live' })
  seedCookie(sb, JSON.stringify(cookie))
  sb.ls.api.setItem(PROD_LEGACY_KEY,
    JSON.stringify(session({ access_token: 'legacy-dead', expires_at: pastExpiry() })))
  sb.window.createTortoiseSupabaseClient(PROD_URL, 'anon-key')
  assert.equal(JSON.parse(cookieValue(sb, COOKIE_NAME)).access_token, 'cookie-live',
    'the body path must apply the same expiry guard as the gate path')
})
